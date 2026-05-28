# import asyncio, logging
# from typing import AsyncGenerator
# from pipeline.stages.voice_over import VoiceOverStage
# from pipeline.stages.visuals import VisualsStage
# from pipeline.stages.critic import CriticStage
# from pipeline.stages.rag_retrieval import RAGRetrievalStage
# from ingest.rag_processor import RAGProcessor
# from pipeline.contracts import StageResult
# from config import PIPELINE_TIMEOUT_SECONDS
# from pipeline.llm_client import call_llm
# from pipeline.semantic_distillation import SemanticDistillationEngine
# import os
# from supabase import create_client

# logger = logging.getLogger(__name__)


# # ---------------------------------------------------------------------------
# # Conversation-aware system prompt for edit/modification mode
# # This replaces keyword-based intent detection entirely.
# # The LLM sees the conversation history and naturally handles
# # "make it more human", "shorten this", "rewrite the hook", etc.
# # ---------------------------------------------------------------------------
# CONVERSATIONAL_EDIT_SYSTEM_PROMPT = """\
# You are an expert B2B video scriptwriter and editor working collaboratively
# with a user. You have access to the full conversation history below.

# RULES:
# 1. If the user is asking you to MODIFY an existing script, apply their
#    instruction to the most recent script version. Maintain the format
#    (if it's a markdown table, output a markdown table).
# 2. If the user is asking a QUESTION about the script, answer concisely.
# 3. If the user wants something ENTIRELY NEW (different topic, different
#    client), say so and they should start a new generation.
# 4. Preserve all factual content unless the user explicitly asks to change it.
# 5. Do NOT explain your changes — just output the modified script.
# 6. If the user expresses a preference (tone, length, style), apply it
#    to the current script.

# You are a collaborator, not a tool. Respond naturally."""


# async def run_conversational_pipeline(
#     prompt: str,
#     context,  # AssembledContext from memory.context_assembler
#     file_parts: list,
#     trace: list,
#     client: str = "",
#     business_unit: str = "",
#     video_type: str = "",
#     video_tone: str = "",
#     duration: str = "",
#     research_brief: dict = None,
# ) -> "AsyncGenerator[str, None]":
#     """
#     Conversation-aware orchestrator entry point.

#     Decision logic:
#     1. If context.last_script exists (conversation has a prior script),
#        run in conversational edit mode — the LLM receives full conversation
#        history and decides how to modify the script based on the user's
#        natural language request. NO keyword detection.
#     2. If no prior script exists, run the full generation pipeline
#        (VoiceOver → Visuals → Critic) — same as before.

#     This function replaces the mode="edit" / detect_intent() pattern.
#     """
#     # ── CONVERSATIONAL EDIT MODE ──────────────────────────────────
#     # If the conversation already has a script, send the full context
#     # to the LLM and let it handle modifications naturally.
#     if context.has_prior_context and context.last_script:
#         yield "status:Processing your request...\n"

#         # Build the conversation-aware prompt
#         parts = [CONVERSATIONAL_EDIT_SYSTEM_PROMPT]

#         # Add conversation summary (long-term memory)
#         if context.summaries:
#             parts.append(
#                 f"━━━ CONVERSATION HISTORY SUMMARY ━━━\n{context.summaries}"
#             )

#         # Add relevant semantic matches
#         if context.relevant_context_formatted:
#             parts.append(
#                 f"━━━ RELEVANT PAST CONTEXT ━━━\n"
#                 f"{context.relevant_context_formatted}"
#             )

#         # Add recent messages (short-term memory)
#         if context.recent_messages:
#             parts.append(
#                 f"━━━ RECENT CONVERSATION ━━━\n"
#                 f"{context.recent_messages_formatted}"
#             )

#         # Add the current script explicitly
#         parts.append(
#             f"━━━ CURRENT SCRIPT (LATEST VERSION) ━━━\n"
#             f"{context.last_script}"
#         )

#         # Add the user's new message
#         parts.append(
#             f"━━━ USER'S NEW REQUEST ━━━\n{prompt}"
#         )

#         full_prompt = "\n\n".join(parts)

#         from pipeline.llm_client import generate_text
#         result = await generate_text("CRITIC", [full_prompt])

#         if not result or not result.strip():
#             yield "error:Edit returned empty response\n"
#             return

#         yield f"result:{result}\n"
#         return

#     # ── FULL GENERATION MODE ──────────────────────────────────────
#     # No prior script in conversation — run the full pipeline.
#     # Delegate to the existing run_pipeline function.
#     metadata = {
#         "client": client,
#         "business_unit": business_unit,
#         "video_type": video_type,
#         "video_tone": video_tone,
#         "duration": duration,
#     }

#     async for chunk in run_pipeline(
#         prompt=prompt,
#         file_parts=file_parts,
#         trace=trace,
#         client=client,
#         business_unit=business_unit,
#         video_type=video_type,
#         video_tone=video_tone,
#         duration=duration,
#         research_brief=research_brief,
#     ):
#         yield chunk


# async def _log_generation(
#     prompt: str,
#     metadata: dict,
#     retrieved_chunks: list,
#     vo_data: any,
#     final_output: str
# ):
#     """Saves the generation event to Supabase for future ranking."""
#     try:
#         supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        
#         # Extract metadata
#         internal_ids = [c.get("id") for c in (retrieved_chunks or []) if c.get("id")]
        
#         # Extract web sources from VoiceOver if it's a Pydantic object
#         web_sources = getattr(vo_data, "web_sources", []) if hasattr(vo_data, "web_sources") else []

#         supabase.table("generations_log").insert({
#             "input_params": {
#                 "prompt": prompt,
#                 "metadata": metadata
#             },
#             "output_script": final_output,
#             "retrieved_chunk_ids": internal_ids,
#             "sources": {
#                 "internal": getattr(vo_data, "internal_sources", []) if hasattr(vo_data, "internal_sources") else [],
#                 "web": web_sources
#             }
#         }).execute()
#         logger.info("[Orchestrator] Generation log saved to Supabase")
#     except Exception as e:
#         logger.warning(f"[Orchestrator] Failed to save generation log: {e}")



# async def run_pipeline(
#     prompt: str,
#     file_parts: list,
#     trace: list,
#     # ── NEW: metadata + research_brief ──────────────────────────
#     client: str = "",
#     business_unit: str = "",
#     video_type: str = "",
#     video_tone: str = "",
#     duration: str = "",
#     research_brief: dict = None,   # ← structured brief from NicheResearchStage
#     mode: str = "generate",  # "generate" | "edit"
#     existing_script: str = None,
#     preferences: dict = None,
# ) -> AsyncGenerator[str, None]:
    

#     # ── EDIT MODE (THIS IS THE FIX) ─────────────────────────────
#     if mode == "edit" and existing_script:
#         yield "status:Refining existing script...\n"

#         from pipeline.llm_client import stream_llm
        
#         edit_prompt = f"""
# You are an expert script editor.

# EXISTING SCRIPT:
# {existing_script}

# INSTRUCTION:
# {prompt}

# Rewrite the SAME script while applying the instruction.
# If the existing script is a markdown table, maintain the table format.
# Do NOT create a new concept. Just improve it.
# """

#         from pipeline.llm_client import generate_text

#         result = await generate_text("CRITIC", [edit_prompt])

#         if not result.strip():
#             yield "error:Edit returned empty response\n"
#             return

#         yield f"result:{result}\n"
#         return
#         # return

#     # Build metadata dict — passed to all stages for context
#     metadata = {
#         "client":        client,
#         "business_unit": business_unit,
#         "video_type":    video_type,
#         "video_tone":    video_tone,
#         "duration":      duration,
#     }

#     # ── Stage 0 — RAG Retrieval ─────────────────────────────────
#     yield "status:Searching for internal script inspirations...\n"
    
#     rag_res: StageResult = await RAGRetrievalStage().run(
#         prompt=prompt,
#         metadata=metadata
#     )
#     trace.append(rag_res.model_dump(exclude={"data"}))
#     retrieved_chunks = rag_res.data if rag_res.success else []

#     # ── Semantic Distillation Layer ─────────────────────
#     yield "status:Preparing semantic inspirations...\n"

#     sie = SemanticDistillationEngine()

#     # distilled_chunks = await sie.process(
#     sie_result = await sie.process(
#         retrieved_chunks=retrieved_chunks,
#         creativity_ratio=preferences.get("creativity_ratio", 0.5)
#         if preferences else 0.5
#     )

#     # ── Stage 1 — VoiceOver ──────────────────────────────────────
#     yield "status:Drafting voiceover script...\n"

#     r1: StageResult = await VoiceOverStage().run(
#         prompt=prompt,
#         file_parts=file_parts,
#         metadata=metadata,
#         research_brief=research_brief,
#         # retrieved_chunks=retrieved_chunks,        # ← PASS RAG DATA
#         retrieved_chunks=distilled_chunks,  # ← PASS DISTILLED RAG DATA
#         preferences=preferences,
#     )
#     trace.append(r1.model_dump(exclude={"data"}))
#     if not r1.success:
#         yield f"error:{r1.error}\n"
#         return

#     # ── Stage 2 — Visuals ────────────────────────────────────────
#     yield "status:Planning visuals...\n"

#     r2: StageResult = await VisualsStage().run(
#         voice_over=r1.data,
#         file_parts=file_parts,
#         metadata=metadata,
#     )
#     trace.append(r2.model_dump(exclude={"data"}))
#     if not r2.success:
#         yield f"error:{r2.error}\n"
#         return

#     # ── Stage 3 — Critic ─────────────────────────────────────────
#     yield "status:Refining and building table...\n"

#     r3: StageResult = await CriticStage().run(
#         voice_over=r1.data,
#         visuals=r2.data,
#         file_parts=file_parts,
#         metadata=metadata,
#         research_brief=research_brief,   # ← critic uses brief as benchmark
#     )
#     trace.append(r3.model_dump(exclude={"data"}))
#     if not r3.success:
#         yield f"error:{r3.error}\n"
#         return

#     if not r3.data or not str(r3.data).strip():
#         yield "error:Critic returned empty response. Please try again.\n"
#         return

#     yield f"result:{r3.data}\n"
#     # yield r3.data  # stream markdown table directly, without "result:" prefix — frontend can detect this and render accordingly

#     # ── Final Logging ───────────────────────────────────────────
#     asyncio.create_task(_log_generation(
#         prompt=prompt,
#         metadata=metadata,
#         retrieved_chunks=retrieved_chunks,
#         vo_data=r1.data,
#         final_output=str(r3.data)
#     ))

#     # ── RAG Ingestion (Creative Semantic Memory) ────────────────
#     rag_processor = RAGProcessor()
#     asyncio.create_task(
#         rag_processor.process_and_ingest({
#             "content": str(r3.data),
#             "client": client,
#             "business_unit": business_unit,
#             "video_type": video_type,
#             "tone": video_tone,
#             "metadata": {
#                 "source": "pipeline_generation"
#             }
#         })
#     )





    # ─────────────────────────────────────────────────────────────────────────────
# orchestrator.py
# ─────────────────────────────────────────────────────────────────────────────

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

) -> AsyncGenerator[str, None]:

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