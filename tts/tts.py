# """
# INTELLIGENT CINEMATIC TTS SYSTEM

# FLOW:
# Critic Markdown Table
#         ↓
# Parse Table
#         ↓
# Extract Voice Over + Visuals
#         ↓
# Claude Sonnet 4.5 Metadata Extraction
#         ↓
# Duration Optimization
#         ↓
# ElevenLabs Scene Generation (PCM)
#         ↓
# Pure Python PCM Stitching → WAV
#         ↓
# Final Cinematic Voice Over (single file)

# IMPORTANT:
# - ONLY Voice Over column is narrated
# - Visuals are ONLY metadata context
# - No pydub / No ffmpeg
# - Single output file
# """

# import os
# import re
# import json
# import wave
# import struct
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
# # AUDIO CONFIG
# # FIX: Use PCM output so raw bytes can be stitched correctly.
# # MP3 bytes cannot be concatenated — they have headers/frames
# # that corrupt the file. PCM_22050 is raw samples with no header,
# # so we can stitch cleanly and wrap once in a WAV container.
# # ============================================================

# SAMPLE_RATE   = 22050   # Hz  (matches pcm_22050 from ElevenLabs)
# CHANNELS      = 1       # mono
# SAMPLE_WIDTH  = 2       # bytes per sample (16-bit)
# OUTPUT_FORMAT = "pcm_22050"

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
#     "indian_female":   {"voice_id": os.getenv("VOICE_INDIAN_FEMALE")},
#     "MARK_AMERICAN_MALE": {"voice_id": os.getenv("MARK")},
#     "KAIRA_AMERICAN_FEMALE": {"voice_id": os.getenv("KAIRA")}

# }

# # ============================================================
# # TABLE PARSER
# # ============================================================

# def parse_script_table(markdown_table: str) -> List[Dict]:

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
#             scenes.append({
#                 "time":       parts[1],
#                 "voice_over": parts[2],
#                 "visuals":    parts[3]
#             })
#         except Exception:
#             continue

#     # Target duration per scene
#     for i in range(len(scenes)):

#         current_time = int(scenes[i]["time"])

#         if i < len(scenes) - 1:
#             next_time = int(scenes[i + 1]["time"])
#             duration  = next_time - current_time
#         else:
#             duration = 7

#         scenes[i]["target_duration"] = duration

#     return scenes

# # ============================================================
# # CLAUDE VOICE DIRECTOR PROMPT
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
# The narrator ONLY reads the narration text.
# Visuals are ONLY emotional context.

# Return ONLY valid JSON. No markdown. No backticks.

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
# ) -> Dict:

#     prompt = VOICE_DIRECTOR_PROMPT.format(
#         voice_over=voice_over,
#         visuals=visuals,
#         target_duration=target_duration
#     )

#     try:
#         response = await claude.messages.create(
#             model="claude-sonnet-4-6",
#             max_tokens=500,
#             messages=[{"role": "user", "content": prompt}]
#         )

#         raw = response.content[0].text.strip()

#         # Strip markdown fences if present
#         raw = re.sub(
#             r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE
#         ).strip()

#         metadata = json.loads(raw)

#     except Exception as e:
#         print(f"  [metadata fallback] {e}")
#         metadata = {
#             "emotion":            "neutral",
#             "pace":               "moderate",
#             "cinematic_intensity":"medium",
#             "pause_behavior":     "natural",
#             "delivery_energy":    "balanced",
#             "narration_style":    "cinematic",
#             "speaking_speed":     "normal",
#             "transition_style":   "smooth"
#         }

#     return metadata

# # ============================================================
# # VOICE SETTINGS
# # ============================================================

# def build_voice_settings(metadata: Dict) -> VoiceSettings:

#     emotion   = metadata.get("emotion", "").lower()
#     intensity = metadata.get("cinematic_intensity", "").lower()

#     stability = 0.5
#     style     = 0.5

#     if "suspense" in emotion:
#         stability, style = 0.35, 0.85
#     elif "sad" in emotion:
#         stability, style = 0.7, 0.35
#     elif "high" in intensity:
#         stability, style = 0.3, 1.0

#     return VoiceSettings(
#         stability=stability,
#         similarity_boost=0.85,
#         style=style,
#         use_speaker_boost=True
#     )

# # ============================================================
# # SCENE AUDIO GENERATION
# # FIX: Request PCM format so bytes are raw audio samples.
# #      No headers, no frames — safe to concatenate directly.
# # ============================================================

# async def generate_scene_audio(
#     scene: Dict,
#     metadata: Dict,
#     voice_type: str,
#     scene_index: int
# ) -> bytes:

#     voice_agent    = VOICE_AGENTS[voice_type]
#     voice_settings = build_voice_settings(metadata)

#     # Pass the original voice_over text — untouched
#     narration_text = scene["voice_over"]

#     print(f"\n  Narration: {narration_text}")

#     pcm_bytes = b""

#     async for chunk in eleven_client.text_to_speech.convert(
#         voice_id=voice_agent["voice_id"],
#         model_id="eleven_multilingual_v2",
#         text=narration_text,
#         output_format=OUTPUT_FORMAT,      # pcm_22050 — raw samples
#         voice_settings=voice_settings
#     ):
#         if chunk:
#             pcm_bytes += chunk

#     return pcm_bytes

# # ============================================================
# # PURE PYTHON WAV STITCHING
# # FIX: All PCM chunks are concatenated into one byte stream,
# #      then wrapped in a single WAV header using Python's
# #      built-in `wave` module. No pydub. No ffmpeg. One file.
# # ============================================================

# def stitch_to_wav(pcm_buffers: List[bytes]) -> str:

#     # Concatenate all raw PCM samples in order
#     combined_pcm = b"".join(pcm_buffers)

#     output_path = os.path.join(OUTPUT_DIR, "final_voiceover.wav")

#     with wave.open(output_path, "wb") as wav_file:
#         wav_file.setnchannels(CHANNELS)
#         wav_file.setsampwidth(SAMPLE_WIDTH)
#         wav_file.setframerate(SAMPLE_RATE)
#         wav_file.writeframes(combined_pcm)

#     size_mb = os.path.getsize(output_path) / (1024 * 1024)
#     print(f"\n  Final file: {output_path} ({size_mb:.2f} MB)")

#     return output_path

# # ============================================================
# # MAIN PIPELINE
# # ============================================================

# async def generate_cinematic_voiceover(
#     final_script: str,
#     voice_type: str = "british_female"
# ) -> Dict:

#     print("\n===================================")
#     print("INTELLIGENT VOICE SYSTEM STARTED")
#     print("===================================\n")

#     # STEP 1 — Parse table
#     scenes = parse_script_table(final_script)
#     print(f"Parsed {len(scenes)} scenes.\n")

#     pcm_buffers = []

#     # STEP 2 — Process each scene
#     for idx, scene in enumerate(scenes):

#         print(f"Processing Scene {idx + 1} / {len(scenes)}")

#         # STEP 3 — Claude metadata
#         metadata = await generate_scene_metadata(
#             voice_over=scene["voice_over"],
#             visuals=scene["visuals"],
#             target_duration=scene["target_duration"]
#         )

#         print("  METADATA:", json.dumps(metadata))

#         # STEP 4 — Generate PCM audio for this scene
#         try:
#             pcm = await generate_scene_audio(
#                 scene=scene,
#                 metadata=metadata,
#                 voice_type=voice_type,
#                 scene_index=idx + 1
#             )
#             pcm_buffers.append(pcm)
#             print(f"  Scene {idx + 1} done ({len(pcm):,} PCM bytes)")

#         except Exception as e:
#             print(f"  ERROR on Scene {idx + 1}: {e}")

#     # STEP 5 — Stitch all PCM into one WAV
#     print("\nStitching all scenes into final_voiceover.wav...")

#     final_path = stitch_to_wav(pcm_buffers)

#     print("\n===================================")
#     print("VOICE GENERATION COMPLETE")
#     print("===================================\n")

#     return {
#         "success":     True,
#         "voice_type":  voice_type,
#         "scene_count": len(scenes),
#         "final_audio": final_path
#     }

# # ============================================================
# # TEST
# # ============================================================

# if __name__ == "__main__":

#     TEST_TABLE = """
# | Time (s) | Voice Over | Visuals |
# |----------|------------|---------|
# | 0 | It didn't arrive with a shout. It began as a whisper—a quiet hum in the dark, unnoticed. | Extreme close-up of a pulsing LED. |
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
Claude extracts Voice Over column → unified narration script
        ↓
Claude Sonnet 4.5 Metadata Extraction (whole script)
        ↓
ElevenLabs Single Generation (PCM)
        ↓
Pure Python PCM → WAV
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
import asyncio

from pathlib import Path
from typing import Dict

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
# PCM output so raw bytes can be stitched into a WAV cleanly.
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
    "british_male":          {"voice_id": os.getenv("VOICE_BRITISH_MALE")},
    "british_female":        {"voice_id": os.getenv("VOICE_BRITISH_FEMALE")},
    "american_male":         {"voice_id": os.getenv("VOICE_AMERICAN_MALE")},
    "american_female":       {"voice_id": os.getenv("VOICE_AMERICAN_FEMALE")},
    "indian_male":           {"voice_id": os.getenv("VOICE_INDIAN_MALE")},
    "indian_female":         {"voice_id": os.getenv("VOICE_INDIAN_FEMALE")},
    "MARK_AMERICAN_MALE":    {"voice_id": os.getenv("MARK")},
    "KAIRA_AMERICAN_FEMALE": {"voice_id": os.getenv("KAIRA")},
    "TANYA_AUSSIE_SOCIALMEDIA":                 {"voice_id": os.getenv("TANYA_AUSSIE_SOCIALMEDIA")},
    "MIKE_AUSSIE_SOCIALMEDIA":                 {"voice_id": os.getenv("MIKE_AUSSIE_SOCIALMEDIA")},
    "PETTER_AUSSIE_ADVERTISEMENT":                 {"voice_id": os.getenv("PETTER_AUSSIE_ADVERTISEMENT")},
    "BECCA_AUSSIE_ADVERTISEMENT":                 {"voice_id": os.getenv("BECCA_AUSSIE_ADVERTISEMENT")},
    "LILY_AUSSIE_CONVERSATIONAL":                 {"voice_id": os.getenv("LILY_AUSSIE_CONVERSATIONAL")},
    "SERENA_AMERICAN_SOCIALMEDIA":                 {"voice_id": os.getenv("SERENA_AMERICAN_SOCIALMEDIA")},
    "BLONDE_BRITISH_FEMALE":                 {"voice_id": os.getenv("BLONDE_BRITISH_FEMALE")},
    "EFFIE_BRITISH_ADVERTISEMENT":                 {"voice_id": os.getenv("EFFIE_BRITISH_ADVERTISEMENT")},
    "ASHER_BRITISH_SOCIALMEDIA":                 {"voice_id": os.getenv("ASHER_BRITISH_SOCIALMEDIA")},
}

# ============================================================
# STEP 1 — SCRIPT GENERATOR
# Ask Claude to read the Voice Over column and return a single
# clean narration script (no table markup, no scene headers).
# ============================================================

SCRIPT_GENERATOR_PROMPT = """
You are a cinematic script editor.

You are given a markdown table with columns: Time (s), Voice Over, Visuals.

Your ONLY job:
- Read ONLY the "Voice Over" column from every row in order.
- Join all Voice Over entries into one smooth, continuous narration script.
- Do NOT include timestamps, scene numbers, visuals, or any formatting.
- Do NOT rewrite, summarize, or change the wording.
- Do NOT add any introduction or commentary.
- Output ONLY the plain narration text, nothing else.

TABLE:
{table}
"""

async def generate_narration_script(markdown_table: str) -> str:
    """
    Ask Claude to extract and join the Voice Over column into
    a single continuous narration script.
    """

    prompt = SCRIPT_GENERATOR_PROMPT.format(table=markdown_table)

    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    script = response.content[0].text.strip()

    print("\n================================================")
    print("GENERATED NARRATION SCRIPT:")
    print(script)
    print("================================================\n")

    return script

# ============================================================
# STEP 2 — VOICE DIRECTOR METADATA
# Analyze the full script to set cinematic voice parameters.
# ============================================================

VOICE_DIRECTOR_PROMPT = """
You are an elite Hollywood AI voice director.

Analyze the full narration script below and determine the best
voice delivery parameters for a single continuous read-through.

Consider:
1. Overall emotional arc
2. Cinematic pacing
3. Intensity and energy
4. Natural delivery style

Return ONLY valid JSON. No markdown. No backticks.

NARRATION SCRIPT:
{script}

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

async def generate_script_metadata(script: str) -> Dict:
    """
    Ask Claude to produce voice direction metadata for the
    full narration script.
    """

    prompt = VOICE_DIRECTOR_PROMPT.format(script=script)

    try:
        response = await claude.messages.create(
            model="claude-sonnet-4-6",
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
            "emotion":             "neutral",
            "pace":                "moderate",
            "cinematic_intensity": "medium",
            "pause_behavior":      "natural",
            "delivery_energy":     "balanced",
            "narration_style":     "cinematic",
            "speaking_speed":      "normal",
            "transition_style":    "smooth",
        }

    return metadata

# ============================================================
# STEP 3 — VOICE SETTINGS
# Map metadata fields to ElevenLabs VoiceSettings values.
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
# STEP 4 — AUDIO GENERATION
# Send the full narration script to ElevenLabs in one call.
# ============================================================

async def generate_full_audio(
    script: str,
    metadata: Dict,
    voice_type: str,
) -> bytes:

    voice_agent    = VOICE_AGENTS[voice_type]
    voice_settings = build_voice_settings(metadata)

    pcm_bytes = b""

    async for chunk in eleven_client.text_to_speech.convert(
        voice_id=voice_agent["voice_id"],
        model_id="eleven_multilingual_v2",
        text=script,
        output_format=OUTPUT_FORMAT,   # pcm_22050 — raw samples
        voice_settings=voice_settings
    ):
        if chunk:
            pcm_bytes += chunk

    return pcm_bytes

# ============================================================
# STEP 5 — WAV EXPORT
# Wrap the raw PCM stream in a proper WAV header.
# ============================================================

def save_as_wav(pcm_bytes: bytes) -> str:

    output_path = os.path.join(OUTPUT_DIR, "final_voiceover.wav")

    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)

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

    # ----------------------------------------------------------
    # STEP 1 — Ask Claude to extract & join Voice Over column
    # ----------------------------------------------------------
    print("STEP 1: Generating narration script from table...")
    narration_script = await generate_narration_script(final_script)

    # ----------------------------------------------------------
    # STEP 2 — Ask Claude for voice direction metadata
    # ----------------------------------------------------------
    print("STEP 2: Generating voice direction metadata...")
    metadata = await generate_script_metadata(narration_script)
    print("  METADATA:", json.dumps(metadata, indent=2))

    # ----------------------------------------------------------
    # STEP 3 — Generate full audio in one ElevenLabs call
    # ----------------------------------------------------------
    print("\nSTEP 3: Generating audio...")
    try:
        pcm_bytes = await generate_full_audio(
            script=narration_script,
            metadata=metadata,
            voice_type=voice_type,
        )
        print(f"  Received {len(pcm_bytes):,} PCM bytes")
    except Exception as e:
        print(f"  ERROR during audio generation: {e}")
        raise

    # ----------------------------------------------------------
    # STEP 4 — Save as WAV
    # ----------------------------------------------------------
    print("\nSTEP 4: Saving final WAV file...")
    final_path = save_as_wav(pcm_bytes)

    print("\n===================================")
    print("VOICE GENERATION COMPLETE")
    print("===================================\n")

    return {
        "success":    True,
        "voice_type": voice_type,
        "final_audio": final_path,
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