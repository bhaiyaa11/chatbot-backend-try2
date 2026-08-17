# pipeline/stages/visuals_generator.py
"""
Storyboard IMAGE generation via Nano Banana (Gemini image models) on
either the Gemini API or Vertex AI.

Scenes are parsed from the final markdown script table produced by
CriticStage ('| Time (s) | Voice Over | Visuals |'). Each scene's
"Visuals" cell becomes an image prompt, styled per the user-selected
video_type (kept as the param name for route compatibility, even
though it now drives a still-frame style rather than a video style).
Each scene becomes one still image. There is no concatenation step —
images don't get stitched into a single asset the way video clips did.
"""

import os
import re
import time
import uuid
import base64
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from db.client import supabase

from config import get_genai_client

logger = logging.getLogger(__name__)


IMAGE_MODELS = {
    "fast": "gemini-3.1-flash-lite-image",
    "quality": "gemini-3.1-flash-image",
    "pro": "gemini-3-pro-image",
}
quality = "fast"

_genai_client = None
def _get_image_client():
    global _genai_client
    if _genai_client is None:
        _genai_client = get_genai_client(location="global")
    return _genai_client


# ── Scene parsing ────────────────────────────────────────────────────
@dataclass
class Scene:
    scene_number: int
    time_seconds: int
    voiceover: str
    visual_description: str


def parse_scenes_from_script(script_text: str) -> list[Scene]:
    scenes = []
    scene_num = 0
    for line in script_text.strip().split("\n"):
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or not cells[0]:
            continue
        if cells[1].lower().startswith("voice over"):
            continue
        try:
            t = int(re.sub(r"[^\d]", "", cells[0]))
        except ValueError:
            continue
        scene_num += 1
        scenes.append(Scene(
            scene_number=scene_num,
            time_seconds=t,
            voiceover=cells[1],
            visual_description=cells[2] if len(cells) > 2 else "",
        ))
    return scenes


# ── Per-video-type style injection ──────────────────────────────────────
VIDEO_TYPE_STYLE_PROMPTS = {
    "3d_animation": (
        "3D animated render, Pixar/Disney-style CGI, soft global illumination, "
        "stylized character/object modeling, smooth subsurface-scattered "
        "materials, cinematic lighting"
    ),
    "2d_animation": (
        "2D flat vector illustration, clean bold outlines, limited flat color "
        "palette, modern explainer-video style, simple shapes, no photorealism"
    ),
    "talking_head": (
        "photorealistic single presenter facing camera, medium shot, softbox "
        "studio lighting, shallow depth of field, corporate video setup, "
        "35mm lens look"
    ),
    "live_action_motion": (
        "photorealistic live action still, natural on-location lighting, "
        "documentary/corporate cinematography, composition with clean "
        "negative space for motion graphics overlays"
    ),
}

VIDEO_TYPE_LABELS = {
    "3d_animation": "3D Animation",
    "2d_animation": "2D Animation",
    "talking_head": "Talking Head",
    "live_action_motion": "Live Action + Motion Graphics",
}


def build_image_prompt(scene: Scene, video_type: str) -> str:
    style = VIDEO_TYPE_STYLE_PROMPTS.get(video_type, VIDEO_TYPE_STYLE_PROMPTS["live_action_motion"])
    return (
        f"{scene.visual_description.strip()}. "
        f"Style: {style}. 16:9 widescreen storyboard frame, no on-screen text, "
        f"no captions, no watermark or logo."
    )


# ── Single-image Nano Banana generation ──────────────────────────────
# Vertex's default quota for image models is often just a handful of
# requests per minute, especially on newer/preview-access projects — so
# 429 RESOURCE_EXHAUSTED is expected under normal use, not a bug. Retry
# with exponential backoff, and pace requests between scenes so a normal
# 7-scene storyboard doesn't burst past the limit in the first place.
IMAGE_GEN_MAX_RETRIES     = 5
IMAGE_GEN_BACKOFF_BASE_S  = 8    # first retry waits ~8s, then 16s, 32s, ...
IMAGE_GEN_BACKOFF_MAX_S   = 90
SCENE_PACING_DELAY_S      = 4    # gap between scenes, independent of retries


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc)
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


def _generate_image_sync(
    prompt: str,
    model_key: str,
    reference_image_bytes: bytes | None = None,
) -> bytes:

    client = _get_image_client()
    model = IMAGE_MODELS.get(
        model_key,
        IMAGE_MODELS["quality"],
    )

    # --------------------------------------------------------
    # Build request contents
    # --------------------------------------------------------

    contents = [prompt]

    if reference_image_bytes is not None:
        from google.genai import types

        contents = [
            types.Part.from_bytes(
                data=reference_image_bytes,
                mime_type="image/png",
            ),
            prompt,
        ]

    # --------------------------------------------------------
    # Generate image
    # --------------------------------------------------------

    last_exc = None

    for attempt in range(IMAGE_GEN_MAX_RETRIES):

        try:

            response = client.models.generate_content(
                model=model,
                contents=contents,
            )

            # ------------------------------------------------
            # DEBUG RESPONSE
            # ------------------------------------------------

            candidates = getattr(
                response,
                "candidates",
                None,
            )

            if not candidates:
                logger.error(
                    "[visuals_generator] Gemini returned "
                    "no candidates"
                )


                raise RuntimeError(
                    "Gemini returned no candidates"
                )

            # ------------------------------------------------
            # Extract parts
            # ------------------------------------------------

            parts = getattr(
                response,
                "parts",
                None,
            )

            if parts is None:

                content = getattr(
                    candidates[0],
                    "content",
                    None,
                )

                parts = getattr(
                    content,
                    "parts",
                    None,
                )


            if not parts:

                logger.error(
                    f"[visuals_generator] "
                    f"Candidate content: "
                    f"{candidates[0].content}"
                )

                raise RuntimeError(
                    "Gemini returned no content parts"
                )

            # ------------------------------------------------
            # Look for image
            # ------------------------------------------------

            for index, part in enumerate(parts):


                # --------------------------------------------
                # Image data
                # --------------------------------------------

                inline_data = getattr(
                    part,
                    "inline_data",
                    None,
                )

                if inline_data is not None:

                    data = inline_data.data

                    if isinstance(data, str):
                        return base64.b64decode(data)

                    return data

                # --------------------------------------------
                # Text response
                # --------------------------------------------

                text = getattr(
                    part,
                    "text",
                    None,
                )

                if text:
                    logger.warning(
                        "[visuals_generator] "
                        f"Gemini returned text instead "
                        f"of image: {text[:500]}"
                    )

            # ------------------------------------------------
            # Nothing usable
            # ------------------------------------------------

            logger.error(
                "[visuals_generator] "
                "No image data found in Gemini response"
            )

            logger.error(
                f"[visuals_generator] Full response: "
                f"{response}"
            )

            raise RuntimeError(
                "Nano Banana returned no image data"
            )

        except Exception as e:

            last_exc = e

            if (
                not _is_rate_limit_error(e)
                or attempt == IMAGE_GEN_MAX_RETRIES - 1
            ):
                raise

            wait_s = min(
                IMAGE_GEN_BACKOFF_BASE_S * (2 ** attempt),
                IMAGE_GEN_BACKOFF_MAX_S,
            )

            logger.warning(
                f"[visuals_generator] Rate limited "
                f"(attempt {attempt + 1}/"
                f"{IMAGE_GEN_MAX_RETRIES}), "
                f"retrying in {wait_s}s"
            )

            time.sleep(wait_s)

    raise last_exc



async def generate_scene_image(
    scene: Scene,
    video_type: str,
    model_key: str,
    output_dir: str,
    user_id: str,
    job_id: str,
) -> dict:

    prompt = build_image_prompt(
        scene,
        video_type,
    )

    image_bytes = await asyncio.to_thread(
        _generate_image_sync,
        prompt,
        model_key,
    )

    # ---------------------------------------------
    # Save image + metadata directly to Supabase DB
    # ---------------------------------------------

    _save_storyboard_scene(
        user_id=user_id,
        job_id=job_id,
        scene_number=scene.scene_number,
        image_data=image_bytes,
        caption=scene.visual_description[:120],
        prompt=prompt,
        status="idle",
    )

    return {
        "scene_number": scene.scene_number,
        "caption": scene.visual_description[:120],
        "prompt": prompt,
    }


    # --------------------------------------------------------
    # Upload image to Supabase Storage
    # --------------------------------------------------------

    storage_path = _upload_storyboard_image(
        user_id=user_id,
        job_id=job_id,
        scene_number=scene.scene_number,
        image_bytes=image_bytes,
    )

    # --------------------------------------------------------
    # Persist scene metadata
    # --------------------------------------------------------

    _save_storyboard_scene(
        user_id=user_id,
        job_id=job_id,
        scene_number=scene.scene_number,
        storage_path=storage_path,
        caption=scene.visual_description[:120],
        prompt=prompt,
        status="idle",
    )

    return {
        "storage_path": storage_path,
        "scene_number": scene.scene_number,
        "caption": scene.visual_description[:120],
        "prompt": prompt,
    }


@dataclass
class StoryboardJob:
    job_id: str
    status: str = "pending"
    total_scenes: int = 0
    completed: list = field(default_factory=list)
    error: str | None = None
    scene_status: dict = field(default_factory=dict)   # scene_number -> "idle" | "generating" | "error"


_jobs: dict[str, StoryboardJob] = {}


def get_job(job_id: str) -> StoryboardJob | None:
    return _jobs.get(job_id)


# SUPABASE_BUCKET = "storyboard-assets"


def _update_storyboard_job(job_id: str, **updates):
    """
    Update persistent storyboard job metadata in Supabase.
    """
    try:
        supabase.table("storyboard_jobs").update(
            updates
        ).eq(
            "job_id", job_id
        ).execute()
    except Exception as e:
        logger.error(
            f"[visuals_generator] Failed to update job "
            f"{job_id}: {e}"
        )


def _save_storyboard_scene(
    *,
    user_id: str,
    job_id: str,
    scene_number: int,
    image_data: bytes,
    caption: str,
    prompt: str,
    status: str = "idle",
):
    payload = {
        "job_id": job_id,
        "user_id": user_id,
        "scene_number": scene_number,
        "image_data": base64.b64encode(
            image_data
        ).decode("ascii"),
        "mime_type": "image/png",
        "caption": caption,
        "prompt": prompt,
        "status": status,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    try:
        supabase.table(
            "storyboard_scenes"
        ).upsert(
            payload,
            on_conflict="job_id,scene_number",
        ).execute()

    except Exception as e:
        logger.error(
            "[visuals_generator] Failed to save "
            f"scene {scene_number}: {e}"
        )
        raise


async def run_storyboard_job(
    job_id: str,
    script_text: str,
    video_type: str,
    model_key: str,
    output_dir: str,
    user_id: str,
):
    """
    Generate all storyboard scenes.

    Supabase is the persistent source of truth.
    _jobs remains as temporary runtime state so the
    existing application flow continues to work during
    generation.
    """

    job = StoryboardJob(
        job_id=job_id,
    )

    _jobs[job_id] = job

    try:
        # ----------------------------------------------------
        # Parse scenes
        # ----------------------------------------------------

        scenes = parse_scenes_from_script(
            script_text
        )

        job.total_scenes = len(scenes)

        # Persist total scene count
        _update_storyboard_job(
            job_id,
            status="running",
            total_scenes=len(scenes),
        )

        if not scenes:
            job.status = "error"
            job.error = (
                "No scenes could be parsed from the script"
            )

            _update_storyboard_job(
                job_id,
                status="error",
                error=job.error,
            )

            return

        # ----------------------------------------------------
        # Generate scenes
        # ----------------------------------------------------

        job.status = "running"

        for scene in scenes:

            job.scene_status[
                scene.scene_number
            ] = "generating"

            try:
                image = await generate_scene_image(
                    scene=scene,
                    video_type=video_type,
                    model_key=model_key,
                    output_dir=output_dir,
                    user_id=user_id,
                    job_id=job_id,
                )

                job.completed.append(image)

                job.scene_status[
                    scene.scene_number
                ] = "idle"

                logger.info(
                    "[visuals_generator] "
                    f"Scene {scene.scene_number} "
                    "saved to Supabase"
                )

            except Exception as e:

                job.scene_status[
                    scene.scene_number
                ] = "error"

                logger.error(
                    "[visuals_generator] "
                    f"Scene {scene.scene_number} "
                    f"failed: {e}"
                )

            finally:
                # Keep the existing pacing between scenes.
                await asyncio.sleep(
                    SCENE_PACING_DELAY_S
                )

        # ----------------------------------------------------
        # Final job status
        # ----------------------------------------------------

        if not job.completed:

            job.status = "error"
            job.error = (
                "All scenes failed to generate"
            )

            _update_storyboard_job(
                job_id,
                status="error",
                error=job.error,
            )

            return

        job.status = "done"

        _update_storyboard_job(
            job_id,
            status="done",
            total_scenes=job.total_scenes,
            error=None,
        )

        logger.info(
            "[visuals_generator] "
            f"Storyboard {job_id} completed: "
            f"{len(job.completed)}/{job.total_scenes} scenes"
        )

    except Exception as e:

        job.status = "error"
        job.error = str(e)

        _update_storyboard_job(
            job_id,
            status="error",
            error=job.error,
        )

        logger.error(
            "[visuals_generator] "
            f"Storyboard job {job_id} failed: {e}"
        )



async def run_scene_edit_job(
    job_id: str,
    scene_number: int,
    prompt: str,
    mode: str,
    video_type: str | None,
    model_key: str,
    output_dir: str,
    user_id: str,
):
    """
    Edit or regenerate a persisted storyboard scene.

    Supabase PostgreSQL is the persistent source of truth.
    Images are stored as Base64 text in storyboard_scenes.image_data.
    """

    try:
        # ----------------------------------------------------
        # Find the persisted scene
        # ----------------------------------------------------

        response = (
            supabase
            .table("storyboard_scenes")
            .select("*")
            .eq("job_id", job_id)
            .eq("user_id", user_id)
            .eq("scene_number", scene_number)
            .maybe_single()
            .execute()
        )

        scene = response.data

        if not scene:
            logger.error(
                "[visuals_generator] Scene not found: "
                f"job={job_id}, scene={scene_number}"
            )
            return

        # ----------------------------------------------------
        # Mark scene as generating
        # ----------------------------------------------------

        supabase.table("storyboard_scenes").update(
            {
                "status": "generating",
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        ).eq(
            "job_id", job_id
        ).eq(
            "user_id", user_id
        ).eq(
            "scene_number", scene_number
        ).execute()

        # ----------------------------------------------------
        # Prepare prompt
        # ----------------------------------------------------

        final_prompt = prompt.strip()
        reference_bytes = None

        # ----------------------------------------------------
        # EDIT
        # ----------------------------------------------------
        #
        # For an edit, use the existing image stored in
        # PostgreSQL as Base64 text.
        #

        if mode == "edit":

            existing_image_data = scene.get("image_data")

            if not existing_image_data:
                raise RuntimeError(
                    f"Scene {scene_number} has no stored image data"
                )

            reference_bytes = base64.b64decode(
                existing_image_data
            )

        # ----------------------------------------------------
        # REGENERATE
        # ----------------------------------------------------

        elif mode == "regenerate" and video_type:

            reference_bytes = None

            style = VIDEO_TYPE_STYLE_PROMPTS.get(
                video_type,
                VIDEO_TYPE_STYLE_PROMPTS[
                    "live_action_motion"
                ],
            )

            final_prompt = (
                f"{prompt.strip()}. "
                f"Style: {style}. "
                "16:9 widescreen storyboard frame, "
                "no on-screen text, no captions, "
                "no watermark or logo."
            )

        # ----------------------------------------------------
        # Validate mode
        # ----------------------------------------------------

        elif mode not in ("edit", "regenerate"):

            raise ValueError(
                "mode must be 'edit' or 'regenerate'"
            )

        # ----------------------------------------------------
        # Generate replacement image
        # ----------------------------------------------------

        image_bytes = await asyncio.to_thread(
            _generate_image_sync,
            final_prompt,
            model_key,
            reference_bytes,
        )

        if not image_bytes:
            raise RuntimeError(
                "Image generation returned no image data"
            )

        # ----------------------------------------------------
        # Save replacement directly to PostgreSQL
        # ----------------------------------------------------

        _save_storyboard_scene(
            user_id=user_id,
            job_id=job_id,
            scene_number=scene_number,
            image_data=image_bytes,
            caption=prompt.strip()[:120],
            prompt=final_prompt,
            status="idle",
        )

        # ----------------------------------------------------
        # Update temporary in-memory state if it exists
        # ----------------------------------------------------

        job = get_job(job_id)

        if job is not None:

            for item in job.completed:

                if item.get("scene_number") == scene_number:

                    item["caption"] = prompt.strip()[:120]
                    item["prompt"] = final_prompt

                    # No storage_path anymore.
                    # The persistent image lives in Supabase DB.

                    break

            job.scene_status[
                scene_number
            ] = "idle"

        logger.info(
            "[visuals_generator] "
            f"Scene {scene_number} updated successfully"
        )

    except Exception as e:

        logger.error(
            "[visuals_generator] "
            f"Scene {scene_number} edit failed: {e}"
        )

        # ----------------------------------------------------
        # Mark scene as error in Supabase
        # ----------------------------------------------------

        try:

            supabase.table(
                "storyboard_scenes"
            ).update(
                {
                    "status": "error",
                    "updated_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            ).eq(
                "job_id", job_id
            ).eq(
                "user_id", user_id
            ).eq(
                "scene_number", scene_number
            ).execute()

        except Exception as db_error:

            logger.error(
                "[visuals_generator] "
                f"Failed to mark scene {scene_number} "
                f"as error: {db_error}"
            )

        # ----------------------------------------------------
        # Update temporary in-memory state
        # ----------------------------------------------------

        job = get_job(job_id)

        if job is not None:
            job.scene_status[
                scene_number
            ] = "error"

