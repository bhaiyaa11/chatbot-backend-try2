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
import base64

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
    "BLONDE_BRITISH_FEMALE":                 {"voice_id": os.getenv("BLONDE_BRITISH_FEMALE")},
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
You are an elite Hollywood voice director.

Analyze the narration and determine the ideal
ElevenLabs voice settings.

Return ONLY valid JSON.

Narration:
{script}

Return:

{{
    "stability": 0.0-1.0,
    "style": 0.0-1.0,
    "similarity_boost": 0.0-1.0,

    "performance_direction":
    "Detailed narration instructions for the voice actor"
}}

Guidelines:

- stability:
  lower = emotional / expressive
  higher = controlled / consistent

- style:
  lower = natural
  higher = dramatic cinematic performance

- similarity_boost:
  usually 0.75-0.95

- performance_direction:
  1-3 sentences explaining HOW the script
  should be performed.
"""


async def generate_script_metadata(script: str):

    prompt = VOICE_DIRECTOR_PROMPT.format(
        script=script
    )

    response = await claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    raw = response.content[0].text.strip()

    raw = re.sub(
        r"^```(?:json)?|```$",
        "",
        raw,
        flags=re.MULTILINE
    ).strip()

    return json.loads(raw)

# ============================================================
# STEP 3 — VOICE SETTINGS
# Map metadata fields to ElevenLabs VoiceSettings values.
# ============================================================


def build_voice_settings(metadata):

    return VoiceSettings(
        stability=float(
            metadata.get(
                "stability",
                0.5
            )
        ),

        style=float(
            metadata.get(
                "style",
                0.5
            )
        ),

        similarity_boost=float(
            metadata.get(
                "similarity_boost",
                0.85
            )
        ),

        use_speaker_boost=True
    )


import time

async def generate_cinematic_voiceover_stream(
    final_script: str,
    voice_type: str,
):
    total_start = time.time()

    print("\n========== TIMING ==========")

    step1_start = time.time()

    narration_script = await generate_narration_script(
        final_script
    )

    print(
        f"STEP 1 (Narration Script): "
        f"{time.time() - step1_start:.2f}s"
    )

    step2_start = time.time()

    metadata = await generate_script_metadata(
        narration_script
    )

    print(
        f"STEP 2 (Metadata): "
        f"{time.time() - step2_start:.2f}s"
    )

    tts_start = time.time()
    first_chunk = True

    async for chunk in stream_audio_chunks(
        narration_script,
        metadata,
        voice_type,
    ):
        if first_chunk:
            print(
                f"FIRST AUDIO CHUNK: "
                f"{time.time() - tts_start:.2f}s"
            )
            first_chunk = False

        yield chunk

    print(
        f"TOTAL REQUEST TIME: "
        f"{time.time() - total_start:.2f}s"
    )

    print("===========================\n")
# ============================================================
# STEP 4 — AUDIO GENERATION
# Send the full narration script to ElevenLabs in one call.
# ============================================================

async def stream_audio_chunks(
    script: str,
    metadata: Dict,
    voice_type: str,
):

    voice_agent = VOICE_AGENTS[voice_type]
    voice_settings = build_voice_settings(metadata)

    async for chunk in eleven_client.text_to_speech.convert(
        voice_id=voice_agent["voice_id"],
        directed_script = f"""
        VOICE PERFORMANCE DIRECTION:

        {metadata.get("performance_direction", "")}

        SCRIPT:

        {script}
        """,
        model_id="eleven_flash_v2_5",
        # text=script,
        
        output_format="mp3_44100_128",
        voice_settings=voice_settings
    ):
        if chunk:
            yield chunk

# async def generate_full_audio(
#     script: str,
#     metadata: Dict,
#     voice_type: str,
# ) -> bytes:

#     voice_agent    = VOICE_AGENTS[voice_type]
#     voice_settings = build_voice_settings(metadata)

#     pcm_bytes = b""

#     async for chunk in eleven_client.text_to_speech.convert(
#         voice_id=voice_agent["voice_id"],
#         model_id="eleven_multilingual_v2",
#         text=directed_script,
#         output_format=OUTPUT_FORMAT,   # pcm_22050 — raw samples
#         voice_settings=voice_settings
#     ):
#         if chunk:
#             pcm_bytes += chunk

#     return pcm_bytes


async def generate_full_audio(
    script: str,
    metadata: Dict,
    voice_type: str,
) -> Dict:

    voice_agent = VOICE_AGENTS[voice_type]
    voice_settings = build_voice_settings(metadata)

    tts_text = script

    response = await eleven_client.text_to_speech.convert_with_timestamps(
        voice_id=voice_agent["voice_id"],
        text=tts_text,
        model_id="eleven_multilingual_v2",
        output_format=OUTPUT_FORMAT,
        voice_settings=voice_settings,
    )

    audio_bytes = base64.b64decode(response.audio_base64)

    alignment = response.alignment

    return {
        "audio_bytes": audio_bytes,
        "alignment": alignment,
    }

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
    # try:
    #     pcm_bytes = await generate_full_audio(
    #         script=narration_script,
    #         metadata=metadata,
    #         voice_type=voice_type,
    #     )
    #     print(f"  Received {len(pcm_bytes):,} PCM bytes")
    # except Exception as e:
    #     print(f"  ERROR during audio generation: {e}")
    #     raise
    
    tts_result = await generate_full_audio(
        script=narration_script,
        metadata=metadata,
        voice_type=voice_type,
    )

    pcm_bytes = tts_result["audio_bytes"]
    alignment = tts_result["alignment"]

    print(f"  Received {len(pcm_bytes):,} PCM bytes")
    print(f"  Alignment received: {alignment is not None}")

    if alignment:
        print("\n========== ALIGNMENT TEST ==========")
        print("Characters:", alignment.characters[:20])
        print(
            "Start times:",
            alignment.character_start_times_seconds[:20]
        )
        print(
            "End times:",
            alignment.character_end_times_seconds[:20]
        )
        print("====================================\n")

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









#  "indian_female":         {"voice_id": os.getenv("VOICE_INDIAN_FEMALE")},
#     "MARK_AMERICAN_MALE":    {"voice_id": os.getenv("MARK")},
#     "KAIRA_AMERICAN_FEMALE": {"voice_id": os.getenv("KAIRA")},
#     "TANYA_AUSSIE_SOCIALMEDIA":                 {"voice_id": os.getenv("TANYA_AUSSIE_SOCIALMEDIA")},
#     "MIKE_AUSSIE_SOCIALMEDIA":                 {"voice_id": os.getenv("MIKE_AUSSIE_SOCIALMEDIA")},
#     "PETTER_AUSSIE_ADVERTISEMENT":                 {"voice_id": os.getenv("PETTER_AUSSIE_ADVERTISEMENT")},
#     "BECCA_AUSSIE_ADVERTISEMENT":                 {"voice_id": os.getenv("BECCA_AUSSIE_ADVERTISEMENT")},
#     "LILY_AUSSIE_CONVERSATIONAL":                 {"voice_id": os.getenv("LILY_AUSSIE_CONVERSATIONAL")},
#     "SERENA_AMERICAN_SOCIALMEDIA":                 {"voice_id": os.getenv("SERENA_AMERICAN_SOCIALMEDIA")},
#     "BLONDE_BRITISH_FEMALE":                 {"voice_id": os.getenv("BLONDE_BRITISH_FEMALE")},
#     "EFFIE_BRITISH_ADVERTISEMENT":                 {"voice_id": os.getenv("EFFIE_BRITISH_ADVERTISEMENT")},
#     "ASHER_BRITISH_SOCIALMEDIA":                 {"voice_id": os.getenv("ASHER_BRITISH_SOCIALMEDIA")},
#     "MR_DAVID_BRIT_CONVO_MALE_OLD":                 {"voice_id": os.getenv("MR_DAVID_BRIT_CONVO_MALE_OLD")}, 
#     "SAMMY_AEMRICAN_CONVO_NUETRAL_YOUNG":         {"voice_id": os.getenv("SAMMY_AEMRICAN_CONVO_NUETRAL_YOUNG")},
#     "ELLIS_BRIT_YOUNG_M_CONVO":         {"voice_id": os.getenv("ELLIS_BRIT_YOUNG_M_CONVO")},
#     "JAMES_BRIT_YOUNG_M_CONVO":         {"voice_id": os.getenv("JAMES_BRIT_YOUNG_M_CONVO")},
#     "JACK_BRIT_YOUNG_M_CONVO":         {"voice_id": os.getenv("JACK_BRIT_YOUNG_M_CONVO")},
#     "LLOYD_BRIT_YOUNG_M_SM":         {"voice_id": os.getenv("LLOYD_BRIT_YOUNG_M_SM")},
#     "JOSH_BRIT_YOUNG_M_SM":         {"voice_id": os.getenv("JOSH_BRIT_YOUNG_M_SM")},
#     "HARRY_BRIT_YOUNG_M_SM":         {"voice_id": os.getenv("HARRY_BRIT_YOUNG_M_SM")},
#     "ALFIE_BRIT_YOUNG_M_AD":         {"voice_id": os.getenv("ALFIE_BRIT_YOUNG_M_AD")},
#     "ROCK_BRIT_YOUNG_M_AD":         {"voice_id": os.getenv("ROCK_BRIT_YOUNG_M_AD")},
#     "JAMES_BRIT_YOUNG_M_AD":         {"voice_id": os.getenv("JAMES_BRIT_YOUNG_M_AD")},
#     "JAMES_BRIT_MID_M_CONVO":         {"voice_id": os.getenv("JAMES_BRIT_MID_M_CONVO")},
#     "FINN_BRIT_MID_M_CONVO":         {"voice_id": os.getenv("FINN_BRIT_MID_M_CONVO")},
#     "MARTIN_BRIT_MID_M_CONVO":         {"voice_id": os.getenv("MARTIN_BRIT_MID_M_CONVO")},
#     "DANIEL_BRIT_MID_M_SM": {"voice_id": os.getenv("DANIEL_BRIT_MID_M_SM")},
#     "MYSTERIOUS_BRIT_MID_M_SM": {"voice_id": os.getenv("MYSTERIOUS_BRIT_MID_M_SM")},
#     "EDMUND_BRIT_MID_M_SM": {"voice_id": os.getenv("EDMUND_BRIT_MID_M_SM")},
#     "RUSS_BRIT_MID_AD":{"voice_id": os.getenv("RUSS_BRIT_MID_AD")},
#     "CONOR_BRIT_MID_AD":{"voice_id": os.getenv("CONOR_BRIT_MID_AD")},
#     "CHRIS_BRIT_MID_AD":{"voice_id": os.getenv("CHRIS_BRIT_MID_AD")},
#     "grandpa_brit_ad":{"voice_id": os.getenv("grandpa_brit_ad")},
#     "JOE_brit_old_sm":{"voice_id": os.getenv("JOE_brit_old_sm")},
#     "DAN_brit_old_sm":{"voice_id": os.getenv("DAN_brit_old_sm")},
#     "sam_brit_ad":{"voice_id": os.getenv("sam_brit_ad")},
#     "KATRINA_BRIT_YOUNG_F_CONVO":         {"voice_id": os.getenv("KATRINA_BRIT_YOUNG_F_CONVO")},
#     "ABIGAIL_BRIT_YOUNG_F_CONVO":         {"voice_id": os.getenv("ABIGAIL_BRIT_YOUNG_F_CONVO")},
#     "Charlotte_BRIT_YOUNG_F_CONVO":{"voice_id":os.getenv("Charlotte_BRIT_YOUNG_F_CONVO")},
#     "PEACH_BRIT_YOUNG_F_SM":{"voice_id":os.getenv("PEACH_BRIT_YOUNG_F_SM")},
#     "KRISTY_BRIT_YOUNG_F_SM":{"voice_id":os.getenv("KRISTY_BRIT_YOUNG_F_SM")},
#     "EFFY_BRIT_YOUNG_F_AD":{"voice_id":os.getenv("EFFY_BRIT_YOUNG_F_AD")},
#     "PEPPER_BRIT_YOUNG_F_AD":{"voice_id":os.getenv("PEPPER_BRIT_YOUNG_F_AD")},
#     "SERENA_BRIT_YOUNG_F_AD":{"voice_id":os.getenv("SERENA_BRIT_YOUNG_F_AD")},
#     "PIA_BRIT_MID_F_CONVO":{"voice_id":os.getenv("PIA_BRIT_MID_F_CONVO")},
#     "VALORY_BRIT_MID_F_CONVO":{"voice_id":os.getenv("VALORY_BRIT_MID_F_CONVO")},
#     "KATIE_BRIT_MID_F_CONVO":{"voice_id":os.getenv("KATIE_BRIT_MID_F_CONVO")},
#     "AIR_BRIT_MID_F_SM":{"voice_id":os.getenv("AIR_BRIT_MID_F_SM")},
#     "SAMARA_BRIT_MID_F_SM":{"voice_id":os.getenv("SAMARA_BRIT_MID_F_SM")},
#     "IMOGEN_BRIT_MID_F_SM":{"voice_id":os.getenv("IMOGEN_BRIT_MID_F_SM")},
#     "VELVET_BRIT_MID_F_AD":{"voice_id":os.getenv("VELVET_BRIT_MID_F_AD")},
#     "EMILY_BRIT_MID_F_AD":{"voice_id":os.getenv("EMILY_BRIT_MID_F_AD")},
#     "BEATRICE_BRIT_OLD_CONVO":{"voice_id":os.getenv("BEATRICE_BRIT_OLD_CONVO")},
#     "JANE_BRIT_OLD_SM":{"voice_id":os.getenv("JANE_BRIT_OLD_SM")},
#     "ELEANOR_BRIT_OLD_AD":{"voice_id":os.getenv("ELEANOR_BRIT_OLD_AD")},
#     "EVELYN_BRIT_YOUNG_N_CONVO":{"voice_id":os.getenv("EVELYN_BRIT_YOUNG_N_CONVO")},
#     "MARSHAL_BRIT_MID_N_CONVO":{"voice_id":os.getenv("MARSHAL_BRIT_MID_N_CONVO")},
#     "DARCY_BRIT_MID_N_SM":{"voice_id":os.getenv("DARCY_BRIT_MID_N_SM")},
    