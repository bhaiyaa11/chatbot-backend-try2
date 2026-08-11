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

from config import get_genai_client

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────
# "Nano Banana" is Google's branding for its Gemini native image models.
# As of mid-2026 it's a family of four, but NOT all of them are available
# on Vertex AI yet — the 3.1 Flash Image / Flash Lite Image models
# ("Nano Banana 2") currently 404 on Vertex's publisher-model endpoint
# even with a valid region; they appear to be Gemini-API-only for now.
# On Vertex, stick to what's confirmed on the global endpoint:
#   - gemini-2.5-flash-image        → works today, but Google has
#                                      announced it shuts down Oct 2 2026
#   - gemini-3-pro-image-preview    → "Nano Banana Pro", best text-in-image,
#                                      up to 4K (confirm exact preview
#                                      suffix in your Model Garden — Google
#                                      changes these)
#
# IMPORTANT: this also requires get_genai_client() to use location
# "global" or a specific supported region (e.g. "us-central1") — a bare
# "us" is not a valid Vertex location and will 404 regardless of model.
IMAGE_MODELS = {
    "fast":    "gemini-2.5-flash-image",
    "quality": "gemini-2.5-flash-image",
    "pro":     "gemini-3-pro-image-preview",
}

_genai_client = None
def _get_image_client():
    global _genai_client
    if _genai_client is None:
        # Vertex AI's image models are exposed on the "global" endpoint (or
        # specific regions like "us-central1") — a bare "us" 404s regardless
        # of model. Kept independent of STAGE_LOCATIONS/config.py's default
        # so this doesn't silently break if that default changes for the
        # text stages.
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


def _generate_image_sync(prompt: str, model_key: str) -> bytes:
    client = _get_image_client()
    model = IMAGE_MODELS.get(model_key, IMAGE_MODELS["quality"])

    last_exc = None
    for attempt in range(IMAGE_GEN_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[prompt],
            )
            break
        except Exception as e:
            last_exc = e
            if not _is_rate_limit_error(e) or attempt == IMAGE_GEN_MAX_RETRIES - 1:
                raise
            wait_s = min(IMAGE_GEN_BACKOFF_BASE_S * (2 ** attempt), IMAGE_GEN_BACKOFF_MAX_S)
            logger.warning(
                f"[visuals_generator] Rate limited (attempt {attempt + 1}/{IMAGE_GEN_MAX_RETRIES}), "
                f"retrying in {wait_s}s"
            )
            time.sleep(wait_s)
    else:
        raise last_exc

    parts = getattr(response, "parts", None)
    if parts is None:
        # Fallback for SDK versions where the convenience `.parts`
        # accessor isn't available.
        parts = response.candidates[0].content.parts

    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None:
            data = inline_data.data
            # Some SDK versions return raw bytes, others base64 text.
            if isinstance(data, str):
                return base64.b64decode(data)
            return data

    raise RuntimeError("Nano Banana returned no image data")


async def generate_scene_image(scene: Scene, video_type: str, model_key: str, output_dir: str) -> dict:
    prompt = build_image_prompt(scene, video_type)
    filename = f"{uuid.uuid4()}.png"
    local_path = os.path.join(output_dir, filename)

    image_bytes = await asyncio.to_thread(_generate_image_sync, prompt, model_key)

    with open(local_path, "wb") as f:
        f.write(image_bytes)

    return {
        "filename": filename,
        "scene_number": scene.scene_number,
        "caption": scene.visual_description[:120],
        "prompt": prompt,
    }


# ── Job orchestration (in-memory) ───────────────────────────────────
@dataclass
class StoryboardJob:
    job_id: str
    status: str = "pending"          # pending | running | done | error
    total_scenes: int = 0
    completed: list = field(default_factory=list)  # list of image dicts
    error: str | None = None


_jobs: dict[str, StoryboardJob] = {}


def get_job(job_id: str) -> StoryboardJob | None:
    return _jobs.get(job_id)


async def run_storyboard_job(job_id: str, script_text: str, video_type: str, model_key: str, output_dir: str):
    job = StoryboardJob(job_id=job_id)
    _jobs[job_id] = job

    scenes = parse_scenes_from_script(script_text)
    job.total_scenes = len(scenes)
    if not scenes:
        job.status = "error"
        job.error = "No scenes could be parsed from the script"
        return

    job.status = "running"
    os.makedirs(output_dir, exist_ok=True)

    for scene in scenes:
        try:
            image = await generate_scene_image(scene, video_type, model_key, output_dir)
            job.completed.append(image)
        except Exception as e:
            logger.error(f"[visuals_generator] Scene {scene.scene_number} failed: {e}")
        finally:
            # Small gap between scenes regardless of success/failure — keeps
            # a normal storyboard from bursting past Vertex's per-minute quota.
            await asyncio.sleep(SCENE_PACING_DELAY_S)

    if not job.completed:
        job.status = "error"
        job.error = "All scenes failed to generate"
        return

    job.status = "done"