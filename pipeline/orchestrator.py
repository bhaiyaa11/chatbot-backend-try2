import asyncio, logging
from typing import AsyncGenerator
from pipeline.stages.voice_over import VoiceOverStage
from pipeline.stages.visuals import VisualsStage
from pipeline.stages.critic import CriticStage
from pipeline.contracts import StageResult
from config import PIPELINE_TIMEOUT_SECONDS
from pipeline.llm_client import call_llm

logger = logging.getLogger(__name__)



async def run_pipeline(
    prompt: str,
    file_parts: list,
    trace: list,
    # ── NEW: metadata + research_brief ──────────────────────────
    client: str = "",
    business_unit: str = "",
    video_type: str = "",
    video_tone: str = "",
    duration: str = "",
    research_brief: dict = None,   # ← structured brief from NicheResearchStage
    mode: str = "generate",  # "generate" | "edit"
    existing_script: str = None,
    preferences: dict = None,
) -> AsyncGenerator[str, None]:
    

    # ── EDIT MODE (THIS IS THE FIX) ─────────────────────────────
    if mode == "edit" and existing_script:
        yield "status:Refining existing script...\n"

        from pipeline.llm_client import stream_llm
        
        edit_prompt = f"""
You are an expert script editor.

EXISTING SCRIPT:
{existing_script}

INSTRUCTION:
{prompt}

Rewrite the SAME script while applying the instruction.
If the existing script is a markdown table, maintain the table format.
Do NOT create a new concept. Just improve it.
"""

        from pipeline.llm_client import generate_text

        result = await generate_text("CRITIC", [edit_prompt])

        if not result.strip():
            yield "error:Edit returned empty response\n"
            return

        yield f"result:{result}\n"
        return
        # return

    # Build metadata dict — passed to all stages for context
    metadata = {
        "client":        client,
        "business_unit": business_unit,
        "video_type":    video_type,
        "video_tone":    video_tone,
        "duration":      duration,
    }

    # ── Stage 1 — VoiceOver ──────────────────────────────────────
    yield "status:Drafting voiceover script...\n"

    r1: StageResult = await VoiceOverStage().run(
        prompt=prompt,
        file_parts=file_parts,
        metadata=metadata,
        research_brief=research_brief,
        preferences=preferences,
    )
    trace.append(r1.model_dump(exclude={"data"}))
    if not r1.success:
        yield f"error:{r1.error}\n"
        return

    # ── Stage 2 — Visuals ────────────────────────────────────────
    yield "status:Planning visuals...\n"

    r2: StageResult = await VisualsStage().run(
        voice_over=r1.data,
        file_parts=file_parts,
        metadata=metadata,
    )
    trace.append(r2.model_dump(exclude={"data"}))
    if not r2.success:
        yield f"error:{r2.error}\n"
        return

    # ── Stage 3 — Critic ─────────────────────────────────────────
    yield "status:Refining and building table...\n"

    r3: StageResult = await CriticStage().run(
        voice_over=r1.data,
        visuals=r2.data,
        file_parts=file_parts,
        metadata=metadata,
        research_brief=research_brief,   # ← critic uses brief as benchmark
    )
    trace.append(r3.model_dump(exclude={"data"}))
    if not r3.success:
        yield f"error:{r3.error}\n"
        return

    if not r3.data or not str(r3.data).strip():
        yield "error:Critic returned empty response. Please try again.\n"
        return

    yield f"result:{r3.data}\n"
    # yield r3.data  # stream markdown table directly, without "result:" prefix — frontend can detect this and render accordingly





    