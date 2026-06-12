import asyncio
import logging
import os

from typing import AsyncGenerator

from supabase import create_client

from pipeline.contracts import StageResult

from pipeline.stages.rag_retrieval import RAGRetrievalStage
from pipeline.stages.voice_over import VoiceOverStage
from pipeline.stages.visuals import VisualsStage
from pipeline.stages.critic import CriticStage

from pipeline.semantic_distillation import SemanticDistillationEngine

from ingest.rag_processor import RAGProcessor

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATIONAL EDIT SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

CONVERSATIONAL_EDIT_SYSTEM_PROMPT = """\
You are an expert B2B video scriptwriter and editor working collaboratively
with a user.

RULES:
1. Modify the latest script naturally.
2. Maintain formatting consistency.
3. Preserve factual accuracy unless asked otherwise.
4. Do NOT explain edits.
5. Apply tone/style/length requests naturally.
"""


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATIONAL PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def run_conversational_pipeline(
    prompt: str,
    context,
    file_parts: list,
    trace: list,
    client: str = "",
    business_unit: str = "",
    video_type: str = "",
    video_tone: str = "",
    duration: str = "",
    research_brief: dict = None,
    preferences: dict = None,
    approved_essences=None,
    approved_interpretations=None,
    creative_summary=None,
) -> AsyncGenerator[str, None]:

    # ─────────────────────────────────────────────────────────────────────
    # EDIT MODE
    # ─────────────────────────────────────────────────────────────────────

    if context.has_prior_context and context.last_script:

        yield "status:Processing your request...\n"

        parts = [CONVERSATIONAL_EDIT_SYSTEM_PROMPT]

        if context.summaries:
            parts.append(
                f"━━━ CONVERSATION SUMMARY ━━━\n{context.summaries}"
            )

        if context.relevant_context_formatted:
            parts.append(
                f"━━━ RELEVANT CONTEXT ━━━\n"
                f"{context.relevant_context_formatted}"
            )

        if context.recent_messages:
            parts.append(
                f"━━━ RECENT MESSAGES ━━━\n"
                f"{context.recent_messages_formatted}"
            )

        parts.append(
            f"━━━ CURRENT SCRIPT ━━━\n"
            f"{context.last_script}"
        )

        parts.append(
            f"━━━ USER REQUEST ━━━\n"
            f"{prompt}"
        )

        full_prompt = "\n\n".join(parts)

        from pipeline.llm_client import generate_text

        result = await generate_text(
            "CRITIC",
            [full_prompt]
        )

        if not result or not result.strip():
            yield "error:Edit returned empty response\n"
            return

        yield f"result:{result}\n"
        return

    # ─────────────────────────────────────────────────────────────────────
    # GENERATION MODE
    # ─────────────────────────────────────────────────────────────────────

    async for chunk in run_pipeline(
        prompt=prompt,
        file_parts=file_parts,
        trace=trace,
        client=client,
        business_unit=business_unit,
        video_type=video_type,
        video_tone=video_tone,
        duration=duration,
        research_brief=research_brief,
        preferences=preferences,
        approved_essences=approved_essences,
        approved_interpretations=approved_interpretations,
        creative_summary=creative_summary,
    ):
        yield chunk


# ─────────────────────────────────────────────────────────────────────────────
# GENERATION LOGGING
# ─────────────────────────────────────────────────────────────────────────────

async def _log_generation(
    prompt: str,
    metadata: dict,
    retrieved_chunks: list,
    vo_data: any,
    final_output: str
):

    try:

        supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )

        internal_ids = [
            c.get("id")
            for c in (retrieved_chunks or [])
            if c.get("id")
        ]

        web_sources = getattr(
            vo_data,
            "web_sources",
            []
        ) if hasattr(vo_data, "web_sources") else []

        supabase.table("generations_log").insert({

            "input_params": {
                "prompt": prompt,
                "metadata": metadata
            },

            "output_script": final_output,

            "retrieved_chunk_ids": internal_ids,

            "sources": {
                "internal": getattr(
                    vo_data,
                    "internal_sources",
                    []
                ) if hasattr(vo_data, "internal_sources") else [],

                "web": web_sources
            }

        }).execute()

        logger.info(
            "[Orchestrator] Generation log saved"
        )

    except Exception as e:

        logger.warning(
            f"[Orchestrator] Logging failed: {e}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATION PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

async def run_pipeline(
    prompt: str,
    file_parts: list,
    trace: list,

    client: str = "",
    business_unit: str = "",
    video_type: str = "",
    video_tone: str = "",
    duration: str = "",

    research_brief: dict = None,

    mode: str = "generate",
    existing_script: str = None,

    preferences: dict = None,
    approved_essences=None,
    approved_interpretations=None,
    creative_summary=None,

) -> AsyncGenerator[str, None]:
    
    logger.info(
        f"Pipeline approved essences: "
        f"{len(approved_essences or [])}"
    )

    logger.info(
        f"Pipeline approved interpretations: "
        f"{len(approved_interpretations or [])}"
    )

    logger.info(
        f"Pipeline creative summary exists: "
        f"{bool(creative_summary)}"
    )

    # ─────────────────────────────────────────────────────────────────────
    # EDIT MODE
    # ─────────────────────────────────────────────────────────────────────

    if mode == "edit" and existing_script:

        yield "status:Refining existing script...\n"

        edit_prompt = f"""
You are an expert script editor.

EXISTING SCRIPT:
{existing_script}

INSTRUCTION:
{prompt}

Rewrite the SAME script while applying the instruction.
Maintain formatting consistency.
Do NOT create a new concept.
"""

        from pipeline.llm_client import generate_text

        result = await generate_text(
            "CRITIC",
            [edit_prompt]
        )

        if not result.strip():
            yield "error:Edit returned empty response\n"
            return

        yield f"result:{result}\n"
        return

    # ─────────────────────────────────────────────────────────────────────
    # METADATA
    # ─────────────────────────────────────────────────────────────────────

    metadata = {
        "client": client,
        "business_unit": business_unit,
        "video_type": video_type,
        "video_tone": video_tone,
        "duration": duration,
    }

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 0 — RAG RETRIEVAL
    # ─────────────────────────────────────────────────────────────────────

    yield "status:Searching for internal inspirations...\n"

    rag_res: StageResult = await RAGRetrievalStage().run(
        prompt=prompt,
        metadata=metadata
    )

    trace.append(
        rag_res.model_dump(exclude={"data"})
    )

    retrieved_chunks = (
        rag_res.data
        if rag_res.success
        else []
    )
    print(
    f"\n[Orchestrator] Retrieved "
    f"{len(retrieved_chunks)} chunks\n"
)

    logger.info(
        f"[Orchestrator] Retrieved "
        f"{len(retrieved_chunks)} chunks"
    )

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 0.5 — SEMANTIC DISTILLATION ENGINE
    # ─────────────────────────────────────────────────────────────────────

    yield "status:Preparing semantic inspirations...\n"

    creativity_ratio = (
        preferences.get("creativity_ratio", 0.5)
        if preferences
        else 0.5
    )

    logger.info(
        f"[Orchestrator] Creativity ratio: "
        f"{creativity_ratio}"
    )
    print(
    f"\n[Orchestrator] Creativity Ratio = "
    f"{creativity_ratio}\n"
)

    sie = SemanticDistillationEngine()

    sie_result = await sie.process(
        retrieved_chunks=retrieved_chunks,
        creativity_ratio=creativity_ratio
    )

    semantic_inspiration = sie_result.get(
        "semantic_inspiration"
    )
    print("\n" + "=" * 60)
    print("[Orchestrator] SEMANTIC INSPIRATION")
    print(str(semantic_inspiration)[:2000])
    print("=" * 60 + "\n")

    compressed_chunks = sie_result.get(
        "compressed_chunks",
        []
    )

    logger.info(
        "[Orchestrator] Semantic inspiration prepared"
    )

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 1 — VOICEOVER
    # ─────────────────────────────────────────────────────────────────────

    yield "status:Drafting voiceover script...\n"

    r1: StageResult = await VoiceOverStage().run(
        prompt=prompt,
        file_parts=file_parts,
        metadata=metadata,

        research_brief=research_brief,

        semantic_inspiration=semantic_inspiration,

        preferences=preferences,
        approved_essences=
            approved_essences,

        approved_interpretations=
            approved_interpretations,

        creative_summary=
            creative_summary,
    )

    trace.append(
        r1.model_dump(exclude={"data"})
    )

    if not r1.success:
        yield f"error:{r1.error}\n"
        return

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 2 — VISUALS
    # ─────────────────────────────────────────────────────────────────────

    yield "status:Planning visuals...\n"

    r2: StageResult = await VisualsStage().run(
        voice_over=r1.data,
        file_parts=file_parts,
        metadata=metadata,
    )

    trace.append(
        r2.model_dump(exclude={"data"})
    )

    if not r2.success:
        yield f"error:{r2.error}\n"
        return

    # ─────────────────────────────────────────────────────────────────────
    # STAGE 3 — CRITIC
    # ─────────────────────────────────────────────────────────────────────

    yield "status:Refining and building table...\n"

    r3: StageResult = await CriticStage().run(
        voice_over=r1.data,
        visuals=r2.data,
        file_parts=file_parts,
        metadata=metadata,
        research_brief=research_brief,
    )

    trace.append(
        r3.model_dump(exclude={"data"})
    )

    if not r3.success:
        yield f"error:{r3.error}\n"
        return

    if not r3.data or not str(r3.data).strip():

        yield (
            "error:Critic returned empty response. "
            "Please try again.\n"
        )

        return

    # ─────────────────────────────────────────────────────────────────────
    # FINAL RESULT
    # ─────────────────────────────────────────────────────────────────────

    yield f"result:{r3.data}\n"

    # ─────────────────────────────────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────────────────────────────────

    asyncio.create_task(
        _log_generation(
            prompt=prompt,
            metadata=metadata,
            retrieved_chunks=retrieved_chunks,
            vo_data=r1.data,
            final_output=str(r3.data)
        )
    )

    # ─────────────────────────────────────────────────────────────────────
    # RAG INGESTION
    # ─────────────────────────────────────────────────────────────────────

    rag_processor = RAGProcessor()

    asyncio.create_task(
        rag_processor.process_and_ingest({

            "content": str(r3.data),

            "client": client,

            "business_unit": business_unit,

            "video_type": video_type,

            "tone": video_tone,

            "metadata": {
                "source": "pipeline_generation"
            }

        })
    )