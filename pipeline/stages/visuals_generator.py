# # # pipeline/stages/visuals_generator.py
# # """
# # Storyboard VIDEO generation via Veo 3 on Vertex AI.

# # Scenes are parsed from the final markdown script table produced by
# # CriticStage ('| Time (s) | Voice Over | Visuals |' — see
# # critic.py::_parse_table_rows). Each scene's "Visuals" cell becomes a Veo
# # prompt, styled per the user-selected video_type. Veo 3 caps clips at 8s,
# # so each scene becomes its own short clip (not one continuous video) —
# # scenes longer than 8s in the script just get an 8s representative clip.

# # Generation is a Vertex long-running operation (30-120s per clip), so this
# # runs as a background job: kick off in visuals_generator.run_storyboard_job,
# # poll via job status, one scene generated at a time (sequential — Veo
# # quota is typically low, parallel calls will likely 429).
# # """

# # import os
# # import re
# # import time
# # import uuid
# # import asyncio
# # import logging
# # from dataclasses import dataclass, field

# # from google import genai
# # from google.genai.types import GenerateVideosConfig
# # from google.cloud import storage

# # logger = logging.getLogger(__name__)

# # # ── Config — fill in for your project ──────────────────────────────────
# # VEO_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "poc-script-genai")
# # VEO_LOCATION   = "us-central1"  # Veo 3 availability is region-limited; adjust if needed
# # VEO_OUTPUT_BUCKET = os.getenv("VEO_OUTPUT_BUCKET", "")  # e.g. "your-veo-output-bucket"

# # VEO_MODELS = {
# #     "quality": "veo-3.0-generate-001",
# #     "fast":    "veo-3.0-fast-generate-001",
# # }

# # _genai_client = None
# # def _get_veo_client():
# #     global _genai_client
# #     if _genai_client is None:
# #         _genai_client = genai.Client(vertexai=True, project=VEO_PROJECT_ID, location=VEO_LOCATION)
# #     return _genai_client


# import os
# import re
# import time
# import uuid
# import asyncio
# import logging
# from dataclasses import dataclass, field
# import subprocess
# import shutil

# from google.genai.types import GenerateVideosConfig
# from google.cloud import storage

# from config import get_genai_client, get_gcp_credentials

# logger = logging.getLogger(__name__)

# # ── Config ───────────────────────────────────────────────────────────
# VEO_LOCATION       = "us-central1"  # Veo 3 region availability differs from your text-model "us" — keep separate
# VEO_OUTPUT_BUCKET  = os.getenv("VEO_OUTPUT_BUCKET", "")

# VEO_MODELS = {
#     "quality": "veo-3.1-generate-001",
#     "fast":    "veo-3.1-fast-generate-001",
# }

# _genai_client = None
# def _get_veo_client():
#     global _genai_client
#     if _genai_client is None:
#         _genai_client = get_genai_client(location=VEO_LOCATION)
#     return _genai_client

# _storage_client = None
# def _get_storage_client():
#     global _storage_client
#     if _storage_client is None:
#         credentials = get_gcp_credentials()
#         if credentials:
#             _storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
#         else:
#             _storage_client = storage.Client()  # ADC fallback for local dev
#     return _storage_client


# # ── Scene parsing (same shape as critic.py, keeps Visuals column) ──────
# @dataclass
# class Scene:
#     scene_number: int
#     time_seconds: int
#     voiceover: str
#     visual_description: str


# def parse_scenes_from_script(script_text: str) -> list[Scene]:
#     scenes = []
#     scene_num = 0
#     for line in script_text.strip().split("\n"):
#         line = line.strip()
#         if not line.startswith("|") or "---" in line:
#             continue
#         cells = [c.strip() for c in line.strip("|").split("|")]
#         if len(cells) < 3 or not cells[0]:
#             continue
#         if cells[1].lower().startswith("voice over"):
#             continue
#         try:
#             t = int(re.sub(r"[^\d]", "", cells[0]))
#         except ValueError:
#             continue
#         scene_num += 1
#         scenes.append(Scene(
#             scene_number=scene_num,
#             time_seconds=t,
#             voiceover=cells[1],
#             visual_description=cells[2] if len(cells) > 2 else "",
#         ))
#     return scenes


# # ── Per-video-type style injection ──────────────────────────────────────
# VIDEO_TYPE_STYLE_PROMPTS = {
#     "3d_animation": (
#         "3D animated render, Pixar/Disney-style CGI, soft global illumination, "
#         "stylized character/object modeling, smooth subsurface-scattered "
#         "materials, cinematic camera movement"
#     ),
#     "2d_animation": (
#         "2D flat vector animation, clean bold outlines, limited flat color "
#         "palette, modern explainer-video motion design, simple shape "
#         "animation, no photorealism"
#     ),
#     "talking_head": (
#         "photorealistic single presenter speaking directly to camera, medium "
#         "shot, softbox studio lighting, shallow depth of field, natural "
#         "lip-sync and gestures, corporate video setup, 35mm lens look"
#     ),
#     "live_action_motion": (
#         "photorealistic live action footage, natural on-location lighting, "
#         "documentary/corporate cinematography, real camera movement, "
#         "composition with clean negative space for motion graphics overlays"
#     ),
# }

# VIDEO_TYPE_LABELS = {
#     "3d_animation": "3D Animation",
#     "2d_animation": "2D Animation",
#     "talking_head": "Talking Head",
#     "live_action_motion": "Live Action + Motion Graphics",
# }


# def build_veo_prompt(scene: Scene, video_type: str) -> str:
#     style = VIDEO_TYPE_STYLE_PROMPTS.get(video_type, VIDEO_TYPE_STYLE_PROMPTS["live_action_motion"])
#     return (
#         f"{scene.visual_description.strip()}. "
#         f"Style: {style}. 16:9 widescreen, no on-screen text, no captions, "
#         f"no watermark or logo."
#     )


# # ── Single-clip Veo generation (blocking call run via to_thread) ───────
# def _generate_clip_sync(prompt: str, model_key: str, gcs_prefix: str) -> str:
#     """Blocking: kicks off Veo generation, polls until done, returns the
#     resulting GCS URI of the video."""
#     client = _get_veo_client()
#     model = VEO_MODELS.get(model_key, VEO_MODELS["fast"])

#     operation = client.models.generate_videos(
#         model=model,
#         prompt=prompt,
#         config=GenerateVideosConfig(
#             aspect_ratio="16:9",
#             output_gcs_uri=gcs_prefix,
#             duration_seconds=8,
#         ),
#     )

#     # Poll — Veo generations typically take 30-120s
#     max_wait_s, waited = 300, 0
#     while not operation.done:
#         time.sleep(10)
#         waited += 10
#         if waited > max_wait_s:
#             raise TimeoutError(f"Veo generation exceeded {max_wait_s}s")
#         operation = client.operations.get(operation)

#     if operation.error:
#         raise RuntimeError(f"Veo generation failed: {operation.error}")

#     videos = operation.result.generated_videos
#     if not videos:
#         raise RuntimeError("Veo returned no video")

#     return videos[0].video.uri  # gs://...


# # def _download_gcs_to_local(gcs_uri: str, local_path: str) -> None:
# #     bucket_name, blob_path = gcs_uri.replace("gs://", "").split("/", 1)
# #     client = storage.Client(project=VEO_PROJECT_ID)
# #     bucket = client.bucket(bucket_name)
# #     blob = bucket.blob(blob_path)
# #     blob.download_to_filename(local_path)

# def _download_gcs_to_local(gcs_uri: str, local_path: str) -> None:
#     bucket_name, blob_path = gcs_uri.replace("gs://", "").split("/", 1)
#     client = _get_storage_client()
#     bucket = client.bucket(bucket_name)
#     blob = bucket.blob(blob_path)
#     blob.download_to_filename(local_path)



# def _concatenate_clips_sync(clip_paths: list[str], output_path: str) -> None:
#     """Concatenates clips in order using ffmpeg's concat demuxer (stream
#     copy — no re-encode, since all clips share the same Veo-generated
#     codec/resolution/framerate). Falls back to a re-encoding concat if
#     the fast path fails (e.g. subtly mismatched streams)."""
#     if shutil.which("ffmpeg") is None:
#         raise RuntimeError("ffmpeg is not installed or not on PATH")

#     list_file = output_path + ".txt"
#     with open(list_file, "w") as f:
#         for path in clip_paths:
#             # ffmpeg concat list format requires escaped single quotes
#             escaped = path.replace("'", "'\\''")
#             f.write(f"file '{escaped}'\n")

#     try:
#         # Fast path: stream copy, no re-encoding
#         result = subprocess.run(
#             ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
#              "-c", "copy", output_path],
#             capture_output=True, text=True,
#         )
#         if result.returncode != 0:
#             logger.warning(f"[visuals_generator] ffmpeg stream-copy concat failed, retrying with re-encode: {result.stderr[-500:]}")
#             # Fallback: re-encode (slower, but tolerant of minor stream mismatches)
#             result = subprocess.run(
#                 ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
#                  "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", output_path],
#                 capture_output=True, text=True,
#             )
#             if result.returncode != 0:
#                 raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-1000:]}")
#     finally:
#         if os.path.exists(list_file):
#             os.remove(list_file)


# async def generate_scene_clip(scene: Scene, video_type: str, model_key: str, output_dir: str) -> dict:
#     """Generates one scene's video clip, downloads it locally, returns
#     the metadata dict the frontend expects."""
#     if not VEO_OUTPUT_BUCKET:
#         raise RuntimeError("VEO_OUTPUT_BUCKET is not configured")

#     prompt = build_veo_prompt(scene, video_type)
#     filename = f"{uuid.uuid4()}.mp4"
#     local_path = os.path.join(output_dir, filename)
#     gcs_prefix = f"gs://{VEO_OUTPUT_BUCKET}/veo_output/{uuid.uuid4()}/"

#     gcs_uri = await asyncio.to_thread(_generate_clip_sync, prompt, model_key, gcs_prefix)
#     await asyncio.to_thread(_download_gcs_to_local, gcs_uri, local_path)

#     return {
#         "filename": filename,
#         "scene_number": scene.scene_number,
#         "caption": scene.visual_description[:120],
#         "prompt": prompt,
#     }


# # ── Job orchestration (in-memory — see note in index.py about scaling) ──
# # @dataclass
# # class StoryboardJob:
# #     job_id: str
# #     status: str = "pending"          # pending | running | done | error
# #     total_scenes: int = 0
# #     completed: list = field(default_factory=list)  # list of clip dicts
# #     error: str | None = None


# # _jobs: dict[str, StoryboardJob] = {}


# # def get_job(job_id: str) -> StoryboardJob | None:
# #     return _jobs.get(job_id)


# # async def run_storyboard_job(job_id: str, script_text: str, video_type: str, model_key: str, output_dir: str):
# #     job = StoryboardJob(job_id=job_id)
# #     _jobs[job_id] = job

# #     scenes = parse_scenes_from_script(script_text)
# #     job.total_scenes = len(scenes)
# #     if not scenes:
# #         job.status = "error"
# #         job.error = "No scenes could be parsed from the script"
# #         return

# #     job.status = "running"
# #     os.makedirs(output_dir, exist_ok=True)

# #     for scene in scenes:
# #         try:
# #             clip = await generate_scene_clip(scene, video_type, model_key, output_dir)
# #             job.completed.append(clip)
# #         except Exception as e:
# #             logger.error(f"[visuals_generator] Scene {scene.scene_number} failed: {e}")
# #             # Skip failed scenes, keep going — partial storyboard beats none
# #             continue

# #     job.status = "done"


# @dataclass
# class StoryboardJob:
#     job_id: str
#     status: str = "pending"          # pending | running | concatenating | done | error
#     total_scenes: int = 0
#     completed: list = field(default_factory=list)  # list of clip dicts
#     final_video: dict | None = None  # {"filename": ..., "url": ...}
#     error: str | None = None


# async def run_storyboard_job(job_id: str, script_text: str, video_type: str, model_key: str, output_dir: str):
#     job = StoryboardJob(job_id=job_id)
#     _jobs[job_id] = job

#     scenes = parse_scenes_from_script(script_text)
#     job.total_scenes = len(scenes)
#     if not scenes:
#         job.status = "error"
#         job.error = "No scenes could be parsed from the script"
#         return

#     job.status = "running"
#     os.makedirs(output_dir, exist_ok=True)

#     for scene in scenes:
#         try:
#             clip = await generate_scene_clip(scene, video_type, model_key, output_dir)
#             job.completed.append(clip)
#         except Exception as e:
#             logger.error(f"[visuals_generator] Scene {scene.scene_number} failed: {e}")
#             continue

#     # ── Concatenate all successfully-generated clips into one final video ──
#     if len(job.completed) >= 2:
#         job.status = "concatenating"
#         try:
#             ordered = sorted(job.completed, key=lambda c: c["scene_number"])
#             clip_paths = [os.path.join(output_dir, c["filename"]) for c in ordered]

#             final_filename = f"final_{uuid.uuid4()}.mp4"
#             final_path = os.path.join(output_dir, final_filename)

#             await asyncio.to_thread(_concatenate_clips_sync, clip_paths, final_path)

#             job.final_video = {"filename": final_filename}
#         except Exception as e:
#             logger.error(f"[visuals_generator] Concatenation failed: {e}")
#             # Not fatal — individual clips are still usable even if the combine step fails
#     elif len(job.completed) == 1:
#         # Only one clip — it IS the final video, no concat needed
#         job.final_video = {"filename": job.completed[0]["filename"]}

#     job.status = "done"


# pipeline/stages/visuals_generator.py
"""
Storyboard VIDEO generation via Veo 3.1 on Vertex AI.

Scenes are parsed from the final markdown script table produced by
CriticStage ('| Time (s) | Voice Over | Visuals |'). Each scene's
"Visuals" cell becomes a Veo prompt, styled per the user-selected
video_type. Each scene becomes an 8s clip; once all scenes are done,
they're concatenated into one final combined video via ffmpeg.
"""

import os
import re
import time
import uuid
import asyncio
import logging
import subprocess
import shutil
from dataclasses import dataclass, field

from google.genai.types import GenerateVideosConfig
from google.cloud import storage

from config import get_genai_client, get_gcp_credentials

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────
VEO_LOCATION       = "us-central1"
VEO_OUTPUT_BUCKET  = os.getenv("VEO_OUTPUT_BUCKET", "")

# veo-3.0-* models were retired June 30, 2026 — using the 3.1 successors
VEO_MODELS = {
    "quality": "veo-3.1-generate-001",
    "fast":    "veo-3.1-fast-generate-001",
}

_genai_client = None
def _get_veo_client():
    global _genai_client
    if _genai_client is None:
        _genai_client = get_genai_client(location=VEO_LOCATION)
    return _genai_client

_storage_client = None
def _get_storage_client():
    global _storage_client
    if _storage_client is None:
        credentials = get_gcp_credentials()
        if credentials:
            _storage_client = storage.Client(credentials=credentials, project=credentials.project_id)
        else:
            _storage_client = storage.Client()  # ADC fallback for local dev
    return _storage_client


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
        "materials, cinematic camera movement"
    ),
    "2d_animation": (
        "2D flat vector animation, clean bold outlines, limited flat color "
        "palette, modern explainer-video motion design, simple shape "
        "animation, no photorealism"
    ),
    "talking_head": (
        "photorealistic single presenter speaking directly to camera, medium "
        "shot, softbox studio lighting, shallow depth of field, natural "
        "lip-sync and gestures, corporate video setup, 35mm lens look"
    ),
    "live_action_motion": (
        "photorealistic live action footage, natural on-location lighting, "
        "documentary/corporate cinematography, real camera movement, "
        "composition with clean negative space for motion graphics overlays"
    ),
}

VIDEO_TYPE_LABELS = {
    "3d_animation": "3D Animation",
    "2d_animation": "2D Animation",
    "talking_head": "Talking Head",
    "live_action_motion": "Live Action + Motion Graphics",
}


def build_veo_prompt(scene: Scene, video_type: str) -> str:
    style = VIDEO_TYPE_STYLE_PROMPTS.get(video_type, VIDEO_TYPE_STYLE_PROMPTS["live_action_motion"])
    return (
        f"{scene.visual_description.strip()}. "
        f"Style: {style}. 16:9 widescreen, no on-screen text, no captions, "
        f"no watermark or logo."
    )


# ── Single-clip Veo generation ──────────────────────────────────────────
# def _generate_clip_sync(prompt: str, model_key: str, gcs_prefix: str) -> str:
#     client = _get_veo_client()
#     model = VEO_MODELS.get(model_key, VEO_MODELS["fast"])

#     operation = client.models.generate_videos(
#         model=model,
#         prompt=prompt,
#         config=GenerateVideosConfig(
#             aspect_ratio="16:9",
#             output_gcs_uri=gcs_prefix,
#             duration_seconds=8,
#         ),
#     )

#     max_wait_s, waited = 300, 0
#     while not operation.done:
#         time.sleep(10)
#         waited += 10
#         if waited > max_wait_s:
#             raise TimeoutError(f"Veo generation exceeded {max_wait_s}s")
#         operation = client.operations.get(operation)

#     if operation.error:
#         raise RuntimeError(f"Veo generation failed: {operation.error}")

#     videos = operation.result.generated_videos
#     if not videos:
#         raise RuntimeError("Veo returned no video")

#     return videos[0].video.uri  # gs://...

def _generate_clip_sync(prompt: str, model_key: str, gcs_prefix: str) -> str:
    client = _get_veo_client()
    model = VEO_MODELS.get(model_key, VEO_MODELS["fast"])

    operation = client.models.generate_videos(
        model=model,
        prompt=prompt,
        config=GenerateVideosConfig(
            aspect_ratio="16:9",
            output_gcs_uri=gcs_prefix,
            duration_seconds=8,
            number_of_videos=1,
        ),
    )

    max_wait_s, waited = 300, 0

    while not operation.done:
        time.sleep(10)
        waited += 10

        if waited > max_wait_s:
            raise TimeoutError(
                f"Veo generation exceeded {max_wait_s}s"
            )

        operation = client.operations.get(operation)

    if operation.error:
        raise RuntimeError(
            f"Veo generation failed: {operation.error}"
        )

    videos = operation.result.generated_videos

    if not videos:
        raise RuntimeError("Veo returned no video")

    return videos[0].video.uri


def _download_gcs_to_local(gcs_uri: str, local_path: str) -> None:
    bucket_name, blob_path = gcs_uri.replace("gs://", "").split("/", 1)
    client = _get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.download_to_filename(local_path)


async def generate_scene_clip(scene: Scene, video_type: str, model_key: str, output_dir: str) -> dict:
    if not VEO_OUTPUT_BUCKET:
        raise RuntimeError("VEO_OUTPUT_BUCKET is not configured")

    prompt = build_veo_prompt(scene, video_type)
    filename = f"{uuid.uuid4()}.mp4"
    local_path = os.path.join(output_dir, filename)
    gcs_prefix = f"gs://{VEO_OUTPUT_BUCKET}/veo_output/{uuid.uuid4()}/"

    gcs_uri = await asyncio.to_thread(_generate_clip_sync, prompt, model_key, gcs_prefix)
    await asyncio.to_thread(_download_gcs_to_local, gcs_uri, local_path)

    return {
        "filename": filename,
        "scene_number": scene.scene_number,
        "caption": scene.visual_description[:120],
        "prompt": prompt,
    }


# ── Concatenation ────────────────────────────────────────────────────
def _concatenate_clips_sync(clip_paths: list[str], output_path: str) -> None:
    """Concatenates clips in order via ffmpeg's concat demuxer (stream
    copy first; re-encode fallback if streams don't match cleanly)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for path in clip_paths:
            escaped = path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
             "-c", "copy", output_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.warning(f"[visuals_generator] ffmpeg stream-copy concat failed, retrying with re-encode: {result.stderr[-500:]}")
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                 "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", output_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-1000:]}")
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


# ── Job orchestration (in-memory) ───────────────────────────────────
@dataclass
class StoryboardJob:
    job_id: str
    status: str = "pending"          # pending | running | concatenating | done | error
    total_scenes: int = 0
    completed: list = field(default_factory=list)  # list of clip dicts
    final_video: dict | None = None  # {"filename": ...}
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
            clip = await generate_scene_clip(scene, video_type, model_key, output_dir)
            job.completed.append(clip)
        except Exception as e:
            logger.error(f"[visuals_generator] Scene {scene.scene_number} failed: {e}")
            continue

    if len(job.completed) >= 2:
        job.status = "concatenating"
        try:
            ordered = sorted(job.completed, key=lambda c: c["scene_number"])
            clip_paths = [os.path.join(output_dir, c["filename"]) for c in ordered]

            final_filename = f"final_{uuid.uuid4()}.mp4"
            final_path = os.path.join(output_dir, final_filename)

            await asyncio.to_thread(_concatenate_clips_sync, clip_paths, final_path)
            job.final_video = {"filename": final_filename}
        except Exception as e:
            logger.error(f"[visuals_generator] Concatenation failed: {e}")
    elif len(job.completed) == 1:
        job.final_video = {"filename": job.completed[0]["filename"]}

    job.status = "done"