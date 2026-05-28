

# """
# INTELLIGENT CINEMATIC TTS SYSTEM
# """

# import os
# import re
# import json
# import asyncio

# from pathlib import Path
# from typing import Dict, List

# from dotenv import load_dotenv

# import anthropic

# from elevenlabs.client import AsyncElevenLabs
# from elevenlabs import VoiceSettings

# # ============================================================
# # LOAD ENV
# # ============================================================

# env_path = Path(__file__).resolve().parent.parent / ".env"
# load_dotenv(dotenv_path=env_path, override=True)

# # ============================================================
# # API KEYS
# # ============================================================

# ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
# ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# # ============================================================
# # OUTPUT DIR
# # ============================================================

# OUTPUT_DIR = "generated_audio"
# os.makedirs(OUTPUT_DIR, exist_ok=True)

# # ============================================================
# # CLIENTS
# # ============================================================

# claude        = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
# eleven_client = AsyncElevenLabs(api_key=ELEVENLABS_API_KEY)

# # ============================================================
# # VOICE AGENTS
# # ============================================================

# VOICE_AGENTS = {
#     "british_male":    {"voice_id": os.getenv("VOICE_BRITISH_MALE")},
#     "british_female":  {"voice_id": os.getenv("VOICE_BRITISH_FEMALE")},
#     "american_male":   {"voice_id": os.getenv("VOICE_AMERICAN_MALE")},
#     "american_female": {"voice_id": os.getenv("VOICE_AMERICAN_FEMALE")},
#     "indian_male":     {"voice_id": os.getenv("VOICE_INDIAN_MALE")},
#     "indian_female":   {"voice_id": os.getenv("VOICE_INDIAN_FEMALE")}
# }

# # ============================================================
# # SCENE CHUNKER
# # ============================================================

# def split_into_scenes(script: str) -> List[Dict]:
#     """
#     FIX: Split on SCENE markers using re.split with a capture group,
#     then zip labels with text blocks. This correctly handles all scenes
#     including Scene 1, with no off-by-one.
#     """
#     scenes = []

#     # Split into alternating [before, label, text, label, text, ...]
#     parts = re.split(r"(SCENE\s+\d+:?)", script)

#     # parts[0] is anything before first SCENE (discard)
#     # parts[1] = "SCENE 1:", parts[2] = text, parts[3] = "SCENE 2:", ...
#     i = 1
#     scene_counter = 1
#     while i < len(parts) - 1:
#         label = parts[i].strip()       # e.g. "SCENE 1:"
#         text  = parts[i + 1].strip()   # the actual scene text

#         num_match = re.search(r"\d+", label)
#         scene_id  = int(num_match.group()) if num_match else scene_counter

#         if text:
#             scenes.append({
#                 "scene_id": scene_id,
#                 "text": text
#             })

#         i += 2
#         scene_counter += 1

#     # fallback: no SCENE markers — split by paragraph
#     if not scenes:
#         paragraphs = script.split("\n\n")
#         scenes = [
#             {"scene_id": i + 1, "text": p.strip()}
#             for i, p in enumerate(paragraphs)
#             if p.strip()
#         ]

#     return scenes

# # ============================================================
# # CLAUDE VOICE DIRECTOR
# # ============================================================

# VOICE_DIRECTOR_PROMPT = """
# You are an elite Hollywood AI voice director.

# Analyze the scene and generate ONLY narration metadata.

# IMPORTANT:
# Do NOT rewrite the script.
# Do NOT add dialogue.
# Do NOT summarize.

# Return ONLY valid JSON with no extra text, no markdown, no backticks.

# Scene:
# {scene}

# Return this exact format:
# {{
#     "emotion": "...",
#     "pace": "...",
#     "cinematic_intensity": "...",
#     "pause_behavior": "...",
#     "delivery_energy": "...",
#     "narration_style": "..."
# }}
# """

# # ============================================================
# # METADATA GENERATION
# # ============================================================

# async def generate_scene_metadata(scene_text: str) -> Dict:

#     prompt = VOICE_DIRECTOR_PROMPT.format(scene=scene_text)

#     try:
#         response = await claude.messages.create(
#             model="claude-sonnet-4-6",
#             max_tokens=500,
#             messages=[{"role": "user", "content": prompt}]
#         )

#         raw = response.content[0].text.strip()

#         # Strip markdown fences if Claude wraps in ```json
#         raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()

#         metadata = json.loads(raw)

#     except Exception as e:
#         print(f"  [metadata fallback] {e}")
#         metadata = {
#             "emotion": "neutral",
#             "pace": "moderate",
#             "cinematic_intensity": "medium",
#             "pause_behavior": "natural",
#             "delivery_energy": "balanced",
#             "narration_style": "cinematic"
#         }

#     return metadata

# # ============================================================
# # BUILD VOICE SETTINGS
# # ============================================================

# def build_voice_settings(metadata: Dict) -> VoiceSettings:

#     emotion   = metadata.get("emotion", "").lower()
#     intensity = metadata.get("cinematic_intensity", "").lower()

#     stability = 0.5
#     style     = 0.5

#     if "suspense" in emotion:
#         stability, style = 0.35, 0.8
#     elif "sad" in emotion:
#         stability, style = 0.7, 0.4
#     elif "intense" in intensity:
#         stability, style = 0.3, 1.0

#     return VoiceSettings(
#         stability=stability,
#         similarity_boost=0.85,
#         style=style,
#         use_speaker_boost=True
#     )

# # ============================================================
# # GENERATE VOICE
# # ============================================================

# async def generate_scene_voice(
#     scene_text: str,
#     metadata: Dict,
#     voice_type: str,
#     scene_id: int
# ) -> str:

#     voice_agent    = VOICE_AGENTS[voice_type]
#     voice_settings = build_voice_settings(metadata)
#     output_path    = os.path.join(OUTPUT_DIR, f"scene_{scene_id}.mp3")

#     # FIX: newer ElevenLabs SDK returns an async_generator, not an awaitable.
#     # Collect all audio chunks by iterating, then write to file manually.
#     audio_chunks = []

#     async for chunk in eleven_client.text_to_speech.convert(
#         voice_id=voice_agent["voice_id"],
#         model_id="eleven_multilingual_v2",
#         text=scene_text,                   # original script — untouched
#         output_format="mp3_44100_128",
#         voice_settings=voice_settings
#     ):
#         if chunk:
#             audio_chunks.append(chunk)

#     with open(output_path, "wb") as f:
#         for chunk in audio_chunks:
#             f.write(chunk)

#     return output_path

# # ============================================================
# # MAIN PIPELINE
# # ============================================================

# async def generate_cinematic_voiceover(
#     final_script: str,
#     voice_type: str = "british_male"
# ) -> Dict:

#     print("\n===================================")
#     print("INTELLIGENT VOICE SYSTEM STARTED")
#     print("===================================\n")

#     scenes = split_into_scenes(final_script)
#     print(f"Detected {len(scenes)} scenes.\n")

#     generated_audio_files = []

#     for scene in scenes:

#         scene_id   = scene["scene_id"]
#         scene_text = scene["text"]

#         print(f"Processing Scene {scene_id}")

#         metadata = await generate_scene_metadata(scene_text)

#         print("\nMETADATA:")
#         print(json.dumps(metadata, indent=2))

#         try:
#             audio_path = await generate_scene_voice(
#                 scene_text=scene_text,
#                 metadata=metadata,
#                 voice_type=voice_type,
#                 scene_id=scene_id
#             )
#             generated_audio_files.append(audio_path)
#             print(f"Generated: {audio_path}\n")

#         except Exception as e:
#             print(f"ERROR on Scene {scene_id}: {e}\n")

#     print("===================================")
#     print("VOICE GENERATION COMPLETE")
#     print("===================================\n")

#     return {
#         "success": True,
#         "voice_type": voice_type,
#         "total_scenes": len(scenes),
#         "audio_files": generated_audio_files
#     }

# # ============================================================
# # TEST
# # ============================================================

# if __name__ == "__main__":

#     TEST_SCRIPT = """
#     SCENE 1:
#     The world had forgotten what silence sounded like.

#     SCENE 2:
#     Then the alarms started ringing across every city.

#     SCENE 3:
#     Humanity realized it was no longer alone.
#     """

#     result = asyncio.run(
#         generate_cinematic_voiceover(
#             final_script=TEST_SCRIPT,
#             voice_type="british_female"
#         )
#     )

#     print(result)






# # tts/tts.py

# """
# INTELLIGENT CINEMATIC TTS SYSTEM

# FLOW:
# Critic Markdown Table
#         ↓
# Parse Table
#         ↓
# Extract Voice Over + Visuals
#         ↓
# Claude Sonnet 4.6 Metadata Extraction
#         ↓
# Duration Optimization
#         ↓
# ElevenLabs Scene Generation
#         ↓
# Pure Python Audio Stitching
#         ↓
# Final Cinematic Voice Over

# IMPORTANT:
# - ONLY Voice Over column is narrated
# - Visuals are ONLY metadata context
# - No pydub
# - No ffmpeg
# - No external audio dependencies
# """

# import os
# import re
# import json
# import asyncio

# from pathlib import Path
# from typing import Dict, List

# from dotenv import load_dotenv

# import anthropic

# from elevenlabs.client import AsyncElevenLabs
# from elevenlabs import VoiceSettings

# # ============================================================
# # LOAD ENV
# # ============================================================

# env_path = Path(__file__).resolve().parent.parent / ".env"

# load_dotenv(dotenv_path=env_path, override=True)

# # ============================================================
# # API KEYS
# # ============================================================

# ANTHROPIC_API_KEY = os.getenv(
#     "ANTHROPIC_API_KEY"
# )

# ELEVENLABS_API_KEY = os.getenv(
#     "ELEVENLABS_API_KEY"
# )

# # ============================================================
# # OUTPUT DIR
# # ============================================================

# OUTPUT_DIR = "generated_audio"

# os.makedirs(OUTPUT_DIR, exist_ok=True)

# # ============================================================
# # CLIENTS
# # ============================================================

# claude = anthropic.AsyncAnthropic(
#     api_key=ANTHROPIC_API_KEY
# )

# eleven_client = AsyncElevenLabs(
#     api_key=ELEVENLABS_API_KEY
# )

# # ============================================================
# # VOICE AGENTS
# # ============================================================

# VOICE_AGENTS = {

#     "british_male": {
#         "voice_id": os.getenv(
#             "VOICE_BRITISH_MALE"
#         )
#     },

#     "british_female": {
#         "voice_id": os.getenv(
#             "VOICE_BRITISH_FEMALE"
#         )
#     },

#     "american_male": {
#         "voice_id": os.getenv(
#             "VOICE_AMERICAN_MALE"
#         )
#     },

#     "american_female": {
#         "voice_id": os.getenv(
#             "VOICE_AMERICAN_FEMALE"
#         )
#     },

#     "indian_male": {
#         "voice_id": os.getenv(
#             "VOICE_INDIAN_MALE"
#         )
#     },

#     "indian_female": {
#         "voice_id": os.getenv(
#             "VOICE_INDIAN_FEMALE"
#         )
#     }
# }

# # ============================================================
# # TABLE PARSER
# # ============================================================

# def parse_script_table(
#     markdown_table: str
# ):

#     scenes = []

#     lines = markdown_table.splitlines()

#     for line in lines:

#         line = line.strip()

#         if (
#             not line.startswith("|")
#             or "---" in line
#             or "Voice Over" in line
#         ):
#             continue

#         parts = [p.strip() for p in line.split("|")]

#         if len(parts) < 5:
#             continue

#         try:

#             scene = {

#                 "time": parts[1],

#                 "voice_over": parts[2],

#                 "visuals": parts[3]

#             }

#             scenes.append(scene)

#         except Exception:
#             continue

#     # --------------------------------------------------------
#     # Target duration calculation
#     # --------------------------------------------------------

#     for i in range(len(scenes)):

#         current_time = int(
#             scenes[i]["time"]
#         )

#         if i < len(scenes) - 1:

#             next_time = int(
#                 scenes[i + 1]["time"]
#             )

#             duration = next_time - current_time

#         else:
#             duration = 7

#         scenes[i]["target_duration"] = duration

#     return scenes

# # ============================================================
# # CLAUDE METADATA PROMPT
# # ============================================================

# VOICE_DIRECTOR_PROMPT = """
# You are an elite Hollywood AI voice director.

# Analyze:
# 1. narration
# 2. visuals
# 3. cinematic pacing
# 4. emotional intensity
# 5. scene energy

# IMPORTANT:
# The narrator ONLY reads narration text.

# Visuals are ONLY emotional context.

# You must optimize pacing so narration
# matches target duration naturally.

# Return ONLY valid JSON.

# NARRATION:
# {voice_over}

# VISUALS:
# {visuals}

# TARGET_DURATION_SECONDS:
# {target_duration}

# Return format:
# {{
#     "emotion": "...",
#     "pace": "...",
#     "cinematic_intensity": "...",
#     "pause_behavior": "...",
#     "delivery_energy": "...",
#     "narration_style": "...",
#     "speaking_speed": "...",
#     "transition_style": "..."
# }}
# """

# # ============================================================
# # METADATA GENERATION
# # ============================================================

# async def generate_scene_metadata(
#     voice_over: str,
#     visuals: str,
#     target_duration: int
# ):

#     prompt = VOICE_DIRECTOR_PROMPT.format(

#         voice_over=voice_over,

#         visuals=visuals,

#         target_duration=target_duration
#     )

#     try:

#         response = await claude.messages.create(

#             model="claude-sonnet-4-6",

#             max_tokens=500,

#             temperature=0.6,

#             messages=[
#                 {
#                     "role": "user",
#                     "content": prompt
#                 }
#             ]
#         )

#         raw = response.content[0].text.strip()

#         raw = re.sub(
#             r"^```(?:json)?|```$",
#             "",
#             raw,
#             flags=re.MULTILINE
#         ).strip()

#         metadata = json.loads(raw)

#     except Exception as e:

#         print(f"[metadata fallback] {e}")

#         metadata = {

#             "emotion": "neutral",

#             "pace": "moderate",

#             "cinematic_intensity": "medium",

#             "pause_behavior": "natural",

#             "delivery_energy": "balanced",

#             "narration_style": "cinematic",

#             "speaking_speed": "normal",

#             "transition_style": "smooth"
#         }

#     return metadata

# # ============================================================
# # DURATION OPTIMIZATION
# # ============================================================

# def optimize_voiceover_timing(
#     voice_over: str,
#     metadata: Dict,
#     target_duration: int
# ):

#     processed = voice_over

#     pace = metadata.get(
#         "pace",
#         ""
#     ).lower()

#     speaking_speed = metadata.get(
#         "speaking_speed",
#         ""
#     ).lower()

#     emotion = metadata.get(
#         "emotion",
#         ""
#     ).lower()

#     # --------------------------------------------------------
#     # Suspense pacing
#     # --------------------------------------------------------

#     if (
#         "slow" in pace
#         or "suspense" in emotion
#     ):

#         processed = processed.replace(
#             ". ",
#             "... "
#         )

#     # --------------------------------------------------------
#     # Fast pacing
#     # --------------------------------------------------------

#     if (
#         "fast" in pace
#         or "fast" in speaking_speed
#     ):

#         processed = processed.replace(
#             "...",
#             "."
#         )

#     # --------------------------------------------------------
#     # Emotional pauses
#     # --------------------------------------------------------

#     processed = processed.replace(
#         ",",
#         ", ..."
#     )

#     return processed

# # ============================================================
# # ELEVENLABS SETTINGS
# # ============================================================

# def build_voice_settings(
#     metadata: Dict
# ):

#     emotion = metadata.get(
#         "emotion",
#         ""
#     ).lower()

#     intensity = metadata.get(
#         "cinematic_intensity",
#         ""
#     ).lower()

#     stability = 0.5
#     style = 0.5

#     # --------------------------------------------------------
#     # Dynamic performance tuning
#     # --------------------------------------------------------

#     if "suspense" in emotion:

#         stability = 0.35
#         style = 0.85

#     elif "sad" in emotion:

#         stability = 0.7
#         style = 0.35

#     elif "high" in intensity:

#         stability = 0.3
#         style = 1.0

#     return VoiceSettings(

#         stability=stability,

#         similarity_boost=0.85,

#         style=style,

#         use_speaker_boost=True
#     )

# # ============================================================
# # SCENE AUDIO GENERATION
# # ============================================================

# async def generate_scene_audio(
#     scene: Dict,
#     metadata: Dict,
#     voice_type: str,
#     scene_index: int
# ):

#     voice_agent = VOICE_AGENTS[voice_type]

#     # --------------------------------------------------------
#     # Narration ONLY
#     # --------------------------------------------------------

#     narration_text = optimize_voiceover_timing(

#         voice_over=scene["voice_over"],

#         metadata=metadata,

#         target_duration=scene["target_duration"]
#     )

#     print("\n================================================")
#     print("FINAL NARRATION:")
#     print(narration_text)
#     print("================================================\n")

#     voice_settings = build_voice_settings(
#         metadata
#     )

#     audio_bytes = b""

#     async for chunk in eleven_client.text_to_speech.convert(

#         voice_id=voice_agent["voice_id"],

#         model_id="eleven_multilingual_v2",

#         text=narration_text,

#         output_format="mp3_44100_128",

#         voice_settings=voice_settings

#     ):

#         if chunk:

#             audio_bytes += chunk

#     # --------------------------------------------------------
#     # Save individual scene
#     # --------------------------------------------------------

#     scene_path = os.path.join(

#         OUTPUT_DIR,

#         f"scene_{scene_index}.mp3"
#     )

#     with open(scene_path, "wb") as f:

#         f.write(audio_bytes)

#     return {

#         "scene_path": scene_path,

#         "audio_bytes": audio_bytes
#     }

# # ============================================================
# # PURE PYTHON AUDIO STITCHING
# # ============================================================

# def stitch_audio_buffers(
#     audio_buffers: List[bytes]
# ):

#     final_audio = b""

#     for audio in audio_buffers:

#         final_audio += audio

#     output_path = os.path.join(

#         OUTPUT_DIR,

#         "final_voiceover.mp3"
#     )

#     with open(output_path, "wb") as f:

#         f.write(final_audio)

#     return output_path

# # ============================================================
# # MAIN PIPELINE
# # ============================================================

# async def generate_cinematic_voiceover(

#     final_script: str,

#     voice_type: str = "british_female"
# ):

#     print("\n===================================")
#     print("INTELLIGENT VOICE SYSTEM STARTED")
#     print("===================================\n")

#     # --------------------------------------------------------
#     # STEP 1 — Parse table
#     # --------------------------------------------------------

#     scenes = parse_script_table(
#         final_script
#     )

#     print(
#         f"Parsed {len(scenes)} scenes.\n"
#     )

#     generated_audio_files = []

#     audio_buffers = []

#     # --------------------------------------------------------
#     # STEP 2 — Process scenes
#     # --------------------------------------------------------

#     for idx, scene in enumerate(scenes):

#         print(
#             f"Processing Scene {idx + 1}"
#         )

#         # ----------------------------------------------------
#         # STEP 3 — Claude metadata
#         # ----------------------------------------------------

#         metadata = await generate_scene_metadata(

#             voice_over=scene["voice_over"],

#             visuals=scene["visuals"],

#             target_duration=scene["target_duration"]
#         )

#         print("\nMETADATA:")
#         print(
#             json.dumps(
#                 metadata,
#                 indent=2
#             )
#         )

#         # ----------------------------------------------------
#         # STEP 4 — Generate audio
#         # ----------------------------------------------------

#         audio_result = await generate_scene_audio(

#             scene=scene,

#             metadata=metadata,

#             voice_type=voice_type,

#             scene_index=idx + 1
#         )

#         generated_audio_files.append(
#             audio_result["scene_path"]
#         )

#         audio_buffers.append(
#             audio_result["audio_bytes"]
#         )

#         print(
#             f"Generated Scene {idx + 1}"
#         )

#     # --------------------------------------------------------
#     # STEP 5 — Final stitching
#     # --------------------------------------------------------

#     print("\nStitching cinematic narration...\n")

#     final_audio = stitch_audio_buffers(
#         audio_buffers
#     )

#     print("\n===================================")
#     print("VOICE GENERATION COMPLETE")
#     print("===================================\n")

#     return {

#         "success": True,

#         "voice_type": voice_type,

#         "scene_count": len(scenes),

#         "scene_audio_files": generated_audio_files,

#         "final_audio": final_audio
#     }

# # ============================================================
# # TEST
# # ============================================================

# if __name__ == "__main__":

#     TEST_TABLE = """
# | Time (s) | Voice Over | Visuals |
# |----------|------------|---------|
# | 0 | It didn’t arrive with a shout. It began as a whisper—a quiet hum in the dark, unnoticed. | Extreme close-up of a pulsing LED. |
# | 7 | It didn't just learn from code. It inhaled our digital existence. | Streams of glowing icons flow into a vortex. |
# | 16 | Now, it no longer responds. It anticipates. | A cinematic AI neural network expands. |
# """

#     result = asyncio.run(

#         generate_cinematic_voiceover(

#             final_script=TEST_TABLE,

#             voice_type="british_female"

#         )
#     )

#     print(result)










"""
INTELLIGENT CINEMATIC TTS SYSTEM

FLOW:
Critic Markdown Table
        ↓
Parse Table
        ↓
Extract Voice Over + Visuals
        ↓
Claude Sonnet 4.5 Metadata Extraction
        ↓
Duration Optimization
        ↓
ElevenLabs Scene Generation (PCM)
        ↓
Pure Python PCM Stitching → WAV
        ↓
Final Cinematic Voice Over (single file)

IMPORTANT:
- ONLY Voice Over column is narrated
- Visuals are ONLY metadata context
- No pydub / No ffmpeg
- Single output file
"""

import os
import re
import json
import wave
import struct
import asyncio

from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

import anthropic

from elevenlabs.client import AsyncElevenLabs
from elevenlabs import VoiceSettings

# ============================================================
# LOAD ENV
# ============================================================

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# ============================================================
# API KEYS
# ============================================================

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# ============================================================
# OUTPUT DIR
# ============================================================

OUTPUT_DIR = "generated_audio"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# AUDIO CONFIG
# FIX: Use PCM output so raw bytes can be stitched correctly.
# MP3 bytes cannot be concatenated — they have headers/frames
# that corrupt the file. PCM_22050 is raw samples with no header,
# so we can stitch cleanly and wrap once in a WAV container.
# ============================================================

SAMPLE_RATE   = 22050   # Hz  (matches pcm_22050 from ElevenLabs)
CHANNELS      = 1       # mono
SAMPLE_WIDTH  = 2       # bytes per sample (16-bit)
OUTPUT_FORMAT = "pcm_22050"

# ============================================================
# CLIENTS
# ============================================================

claude        = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
eleven_client = AsyncElevenLabs(api_key=ELEVENLABS_API_KEY)

# ============================================================
# VOICE AGENTS
# ============================================================

VOICE_AGENTS = {
    "british_male":    {"voice_id": os.getenv("VOICE_BRITISH_MALE")},
    "british_female":  {"voice_id": os.getenv("VOICE_BRITISH_FEMALE")},
    "american_male":   {"voice_id": os.getenv("VOICE_AMERICAN_MALE")},
    "american_female": {"voice_id": os.getenv("VOICE_AMERICAN_FEMALE")},
    "indian_male":     {"voice_id": os.getenv("VOICE_INDIAN_MALE")},
    "indian_female":   {"voice_id": os.getenv("VOICE_INDIAN_FEMALE")}
}

# ============================================================
# TABLE PARSER
# ============================================================

def parse_script_table(markdown_table: str) -> List[Dict]:

    scenes = []

    lines = markdown_table.splitlines()

    for line in lines:

        line = line.strip()

        if (
            not line.startswith("|")
            or "---" in line
            or "Voice Over" in line
        ):
            continue

        parts = [p.strip() for p in line.split("|")]

        if len(parts) < 5:
            continue

        try:
            scenes.append({
                "time":       parts[1],
                "voice_over": parts[2],
                "visuals":    parts[3]
            })
        except Exception:
            continue

    # Target duration per scene
    for i in range(len(scenes)):

        current_time = int(scenes[i]["time"])

        if i < len(scenes) - 1:
            next_time = int(scenes[i + 1]["time"])
            duration  = next_time - current_time
        else:
            duration = 7

        scenes[i]["target_duration"] = duration

    return scenes

# ============================================================
# CLAUDE VOICE DIRECTOR PROMPT
# ============================================================

VOICE_DIRECTOR_PROMPT = """
You are an elite Hollywood AI voice director.

Analyze:
1. narration
2. visuals
3. cinematic pacing
4. emotional intensity
5. scene energy

IMPORTANT:
The narrator ONLY reads the narration text.
Visuals are ONLY emotional context.

Return ONLY valid JSON. No markdown. No backticks.

NARRATION:
{voice_over}

VISUALS:
{visuals}

TARGET_DURATION_SECONDS:
{target_duration}

Return format:
{{
    "emotion": "...",
    "pace": "...",
    "cinematic_intensity": "...",
    "pause_behavior": "...",
    "delivery_energy": "...",
    "narration_style": "...",
    "speaking_speed": "...",
    "transition_style": "..."
}}
"""

# ============================================================
# METADATA GENERATION
# ============================================================

async def generate_scene_metadata(
    voice_over: str,
    visuals: str,
    target_duration: int
) -> Dict:

    prompt = VOICE_DIRECTOR_PROMPT.format(
        voice_over=voice_over,
        visuals=visuals,
        target_duration=target_duration
    )

    try:
        response = await claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        raw = re.sub(
            r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE
        ).strip()

        metadata = json.loads(raw)

    except Exception as e:
        print(f"  [metadata fallback] {e}")
        metadata = {
            "emotion":            "neutral",
            "pace":               "moderate",
            "cinematic_intensity":"medium",
            "pause_behavior":     "natural",
            "delivery_energy":    "balanced",
            "narration_style":    "cinematic",
            "speaking_speed":     "normal",
            "transition_style":   "smooth"
        }

    return metadata

# ============================================================
# VOICE SETTINGS
# ============================================================

def build_voice_settings(metadata: Dict) -> VoiceSettings:

    emotion   = metadata.get("emotion", "").lower()
    intensity = metadata.get("cinematic_intensity", "").lower()

    stability = 0.5
    style     = 0.5

    if "suspense" in emotion:
        stability, style = 0.35, 0.85
    elif "sad" in emotion:
        stability, style = 0.7, 0.35
    elif "high" in intensity:
        stability, style = 0.3, 1.0

    return VoiceSettings(
        stability=stability,
        similarity_boost=0.85,
        style=style,
        use_speaker_boost=True
    )

# ============================================================
# SCENE AUDIO GENERATION
# FIX: Request PCM format so bytes are raw audio samples.
#      No headers, no frames — safe to concatenate directly.
# ============================================================

async def generate_scene_audio(
    scene: Dict,
    metadata: Dict,
    voice_type: str,
    scene_index: int
) -> bytes:

    voice_agent    = VOICE_AGENTS[voice_type]
    voice_settings = build_voice_settings(metadata)

    # Pass the original voice_over text — untouched
    narration_text = scene["voice_over"]

    print(f"\n  Narration: {narration_text}")

    pcm_bytes = b""

    async for chunk in eleven_client.text_to_speech.convert(
        voice_id=voice_agent["voice_id"],
        model_id="eleven_multilingual_v2",
        text=narration_text,
        output_format=OUTPUT_FORMAT,      # pcm_22050 — raw samples
        voice_settings=voice_settings
    ):
        if chunk:
            pcm_bytes += chunk

    return pcm_bytes

# ============================================================
# PURE PYTHON WAV STITCHING
# FIX: All PCM chunks are concatenated into one byte stream,
#      then wrapped in a single WAV header using Python's
#      built-in `wave` module. No pydub. No ffmpeg. One file.
# ============================================================

def stitch_to_wav(pcm_buffers: List[bytes]) -> str:

    # Concatenate all raw PCM samples in order
    combined_pcm = b"".join(pcm_buffers)

    output_path = os.path.join(OUTPUT_DIR, "final_voiceover.wav")

    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(combined_pcm)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\n  Final file: {output_path} ({size_mb:.2f} MB)")

    return output_path

# ============================================================
# MAIN PIPELINE
# ============================================================

async def generate_cinematic_voiceover(
    final_script: str,
    voice_type: str = "british_female"
) -> Dict:

    print("\n===================================")
    print("INTELLIGENT VOICE SYSTEM STARTED")
    print("===================================\n")

    # STEP 1 — Parse table
    scenes = parse_script_table(final_script)
    print(f"Parsed {len(scenes)} scenes.\n")

    pcm_buffers = []

    # STEP 2 — Process each scene
    for idx, scene in enumerate(scenes):

        print(f"Processing Scene {idx + 1} / {len(scenes)}")

        # STEP 3 — Claude metadata
        metadata = await generate_scene_metadata(
            voice_over=scene["voice_over"],
            visuals=scene["visuals"],
            target_duration=scene["target_duration"]
        )

        print("  METADATA:", json.dumps(metadata))

        # STEP 4 — Generate PCM audio for this scene
        try:
            pcm = await generate_scene_audio(
                scene=scene,
                metadata=metadata,
                voice_type=voice_type,
                scene_index=idx + 1
            )
            pcm_buffers.append(pcm)
            print(f"  Scene {idx + 1} done ({len(pcm):,} PCM bytes)")

        except Exception as e:
            print(f"  ERROR on Scene {idx + 1}: {e}")

    # STEP 5 — Stitch all PCM into one WAV
    print("\nStitching all scenes into final_voiceover.wav...")

    final_path = stitch_to_wav(pcm_buffers)

    print("\n===================================")
    print("VOICE GENERATION COMPLETE")
    print("===================================\n")

    return {
        "success":     True,
        "voice_type":  voice_type,
        "scene_count": len(scenes),
        "final_audio": final_path
    }

# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    TEST_TABLE = """
| Time (s) | Voice Over | Visuals |
|----------|------------|---------|
| 0 | It didn't arrive with a shout. It began as a whisper—a quiet hum in the dark, unnoticed. | Extreme close-up of a pulsing LED. |
| 7 | It didn't just learn from code. It inhaled our digital existence. | Streams of glowing icons flow into a vortex. |
| 16 | Now, it no longer responds. It anticipates. | A cinematic AI neural network expands. |
"""

    result = asyncio.run(
        generate_cinematic_voiceover(
            final_script=TEST_TABLE,
            voice_type="british_female"
        )
    )

    print(result)