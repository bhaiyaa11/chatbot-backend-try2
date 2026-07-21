# from pipeline.stages.base import BaseStage
# from pipeline.contracts import VoiceOverOutput
# from pipeline.llm_client import call_llm
# from config import SYSTEM_PROMPTS, TOKEN_BUDGETS
# from typing import List, Dict

# import logging

# logger = logging.getLogger(__name__)

# # ==================================================
# # Human truth extraction prompt
# # ── Finds the narrative spine before writing starts
# # ==================================================

# HUMAN_TRUTH_PROMPT = """
# You are a documentary filmmaker, not a marketer.

# Based on this project intelligence:
# {project_intelligence}

# Answer these four questions in plain language. No marketing speak. No jargon.

# 1. WHAT ACTUALLY HAPPENED: In one sentence, what did this company physically
#    build, change, or create? Not what they "enabled" or "facilitated".

# 2. WHO ACTUALLY FELT IT: Which specific human being's life or work changed?
#    Give them a job title and a real-world before/after moment.

# 3. THE TENSION: What was at stake? What would have happened without this?

# 4. THE ONE LINE: Tell this story to someone on a train in 10 seconds.
#    No jargon. No company names. Just what happened and why it matters.

# Return as JSON — no fences:
# {{
#   "what_happened": "...",
#   "who_felt_it": "...",
#   "the_tension": "...",
#   "the_one_line": "..."
# }}
# """


# def _build_enriched_prompt(
#     prompt: str,
#     metadata: dict,
#     research_brief: dict,
#     human_truth: dict = None,
#     preferences: dict = None,
#     # retrieved_chunks: List[Dict] = None,
#     semantic_inspiration: Dict = None,
#     approved_essences=None,
#     approved_interpretations=None,
#     creative_summary=None,
# ) -> str:
#     blocks = []

#     if approved_essences:
#         blocks.append(
#             "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             "APPROVED ESSENCES\n"
#             "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             + "\n".join(
#                 f"- {e}"
#                 for e in approved_essences
#             )
#         )

#     if approved_interpretations:
#         blocks.append(
#             "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             "APPROVED INTERPRETATIONS\n"
#             "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             + "\n".join(
#                 f"- {i}"
#                 for i in approved_interpretations
#             )
#         )

#     if creative_summary:
#         blocks.append(
#             "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             "APPROVED CREATIVE SUMMARY\n"
#             "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             + creative_summary
#         )

#     # ── Block 0: Semantic Inspiration Engine ────────────────────
#     if semantic_inspiration:

#         blocks.append(
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"SEMANTIC INSPIRATION ENGINE\n"
#             f"(Abstract creative influence — NEVER copy literally)\n"
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"{semantic_inspiration}\n\n"
#             f"IMPORTANT:\n"
#             f"- Use this as abstract inspiration only\n"
#             f"- Never reproduce wording or structure\n"
#             f"- Use it to influence emotional tone,\n"
#             f"  pacing, narrative energy, themes,\n"
#             f"  and storytelling DNA\n"
#         )
#     # ── Block 0: Human truth — the narrative spine ───────────────
#     # if human_truth:
#     #     blocks.append(
#     #         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#     #         f"THE HUMAN TRUTH — this is the spine of the script.\n"
#     #         f"Open with this. Everything else serves this.\n"
#     #         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#     #         f"What actually happened: {human_truth.get('what_happened', '')}\n"
#     #         f"Who felt it: {human_truth.get('who_felt_it', '')}\n"
#     #         f"The tension: {human_truth.get('the_tension', '')}\n"
#     #         f"The one line: {human_truth.get('the_one_line', '')}"
#     #     )
#     # ── Block 0: Human truth — the narrative spine ───────────────
#     if human_truth:
#         blocks.append(
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"THE HUMAN TRUTH — this is the spine of the script.\n"
#             f"Everything in the script must serve this. This is the\n"
#             f"SUBJECT MATTER of your opening line — but the opening\n"
#             f"line's STRUCTURE must come from the WINNING HOOK PATTERNS\n"
#             f"in the research block below, not from this block.\n"
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"What actually happened: {human_truth.get('what_happened', '')}\n"
#             f"Who felt it: {human_truth.get('who_felt_it', '')}\n"
#             f"The tension: {human_truth.get('the_tension', '')}\n"
#             f"The one line: {human_truth.get('the_one_line', '')}"
#         )

#     # ── Block 1: Campaign brief ──────────────────────────────────
#     if metadata:
#         blocks.append(
#             f"CAMPAIGN BRIEF:\n"
#             f"Client: {metadata.get('client', '')}\n"
#             f"Industry / Business Unit: {metadata.get('industries', '')}\n"
#             f"Service Lines: {metadata.get('serviceLines', '')}\n"
#             f"Video Type: {metadata.get('video_type', '')}\n"
#             f"Tone: {metadata.get('video_tone', '')}\n"
#             f"Duration: {metadata.get('duration', '')}"
#         )

#     if research_brief:
#         # ── Block 2: Project facts ───────────────────────────────
#         project_intel = research_brief.get("project_intelligence", "")
#         project_facts = research_brief.get("project_facts", "")

#         if project_intel and "No additional project data found" not in project_intel:
#             blocks.append(
#                 f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#                 f"PROJECT FACTS — USE THESE. DO NOT INVENT OR REPLACE.\n"
#                 f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#                 f"{project_intel}\n\n"
#                 f"MUST-INCLUDE FACTS FROM RESEARCH:\n{project_facts}"
#             )

#         # ── Block 3: Niche intelligence ──────────────────────────
#         transcript_count = research_brief.get("transcript_count", 0)
#         pain_points  = "\n".join(f"  - {p}" for p in (research_brief.get("top_pain_points") or []))
#         hooks        = "\n".join(f"  - {h}" for h in (research_brief.get("winning_hooks") or []))
#         phrases      = ", ".join(research_brief.get("proven_phrases") or [])
#         tone_patterns = "\n".join(f"  - {t}" for t in (research_brief.get("tone_patterns") or []))
#         resonate     = ", ".join(research_brief.get("words_that_resonate") or [])
#         avoid        = ", ".join(research_brief.get("words_to_avoid") or [])

#         blocks.append(
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"NICHE RESEARCH INTELLIGENCE\n"
#             f"(From live web research + {transcript_count} real video transcript analyses)\n"
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"MARKET CONTEXT:\n{research_brief.get('niche_summary', '')}\n\n"
#             f"TOP BUYER PAIN POINTS (real, not guessed):\n{pain_points}\n\n"
#             f"WINNING HOOK PATTERNS (from top-performing content):\n{hooks}\n\n"
#             f"PROVEN PHRASES (words that actually resonated):\n{phrases}\n\n"
#             f"TONE PATTERNS:\n{tone_patterns}\n\n"
#             f"COMPETITOR LANDSCAPE:\n{research_brief.get('competitor_landscape', '')}\n\n"
#             f"RECOMMENDED CREATIVE ANGLE:\n{research_brief.get('recommended_angle', '')}\n\n"
#             f"WORDS THAT RESONATE: {resonate}\n"
#             f"WORDS TO AVOID: {avoid}\n\n"
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#             f"USE ALL OF THE ABOVE. This script must be unmistakably\n"
#             f"written for this specific project — not a generic template.\n"
#             f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
#         )

#     # ── Block 4: User prompt ─────────────────────────────────────
#     blocks.append(f"USER REQUEST:\n{prompt}")

#     # ── Block 5: Style guidelines (soft preferences) ───────────
#     if preferences:
#         lines = []
#         if preferences.get("tone"):
#             lines.append(f"- Tone: {preferences['tone']}")
#         if preferences.get("length"):
#             lines.append(f"- Length: {preferences['length']}")
#         if preferences.get("style"):
#             lines.append(f"- Style: {preferences['style']}")
        
#         if lines:
#             blocks.append("Style Guidelines:\n" + "\n".join(lines))

#     return "\n\n".join(blocks)


# class VoiceOverStage(BaseStage):
#     name = "VOICE_OVER"

#     async def execute(
#         self,
#         prompt: str,
#         file_parts: list,
#         metadata: dict = None,
#         research_brief: dict = None,
#         preferences: dict = None,
#         # retrieved_chunks: List[Dict] = None,
#         semantic_inspiration: Dict = None,
#         approved_essences=None,
#         approved_interpretations=None,
#         creative_summary=None,
#     ) -> VoiceOverOutput:

#         budget = TOKEN_BUDGETS["VOICE_OVER"]
#         logger.info(
#             f"VoiceOver approved essences: "
#             f"{len(approved_essences or [])}"
#         )

#         logger.info(
#             f"VoiceOver approved interpretations: "
#             f"{len(approved_interpretations or [])}"
#         )

#         logger.info(
#             f"VoiceOver creative summary exists: "
#             f"{bool(creative_summary)}"
#         )

#         # Separate and trim file parts
#         text_parts  = [p for p in file_parts if isinstance(p, str)]
#         media_parts = [p for p in file_parts if not isinstance(p, str)]

#         if text_parts:
#             per_file   = budget["file_budget"] // len(text_parts)
#             text_parts = [t[:per_file] for t in text_parts]


#         # ── Extract human truth before building prompt ────────────
#         human_truth = None
#         if research_brief:
#             project_intel = research_brief.get("project_intelligence", "")
#             if project_intel and "No additional project data found" not in project_intel:
#                 try:
#                     from pipeline.stages.niche_research import _call_model, _parse_json
#                     ht_raw = await _call_model(
#                         HUMAN_TRUTH_PROMPT.format(
#                             project_intelligence=project_intel[:6000]
#                         )
#                     )
#                     human_truth = _parse_json(ht_raw)
#                     logger.info(
#                         f"[VoiceOver] Human truth: {human_truth.get('the_one_line', '')}"
#                     )
#                 except Exception as e:
#                     logger.warning(f"[VoiceOver] Human truth extraction failed: {e}")

#         # ── Build enriched prompt ─────────────────────────────────
#         print("\n" + "=" * 60)
#         print("[VoiceOver] RECEIVED SEMANTIC INSPIRATION")
#         print(str(semantic_inspiration)[:2000])
#         print("=" * 60 + "\n")
#         trimmed_prompt  = prompt[:budget["prompt_budget"]]

#         enriched_prompt = _build_enriched_prompt(
#             prompt=trimmed_prompt,
#             metadata=metadata or {},
#             research_brief=research_brief,
#             human_truth=human_truth,
#             preferences=preferences,
#             # retrieved_chunks=retrieved_chunks,
#             semantic_inspiration=semantic_inspiration,
#             # approved_essences=None,
#             # approved_interpretations=None,
#             # creative_summary=None,
#             approved_essences=approved_essences,
#             approved_interpretations=approved_interpretations,
#             creative_summary=creative_summary,
#         )

#         # print(f"PROMPT SENT TO LLM:\n{enriched_prompt[:600]}...")
#         print(f"PROMPT SENT TO LLM:\n{enriched_prompt[:300]}...")

#         system_prompt = SYSTEM_PROMPTS["VOICE_OVER"]

#         # ── If files were uploaded, inject them as high-priority context ──
#         doc_context_parts = []
#         if text_parts:
#             doc_text = "\n\n".join(text_parts)
#             doc_context_parts = [
#                 "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#                 "UPLOADED DOCUMENT CONTENT — THIS IS YOUR PRIMARY SOURCE.\n"
#                 "Use ONLY facts from this document. Do NOT invent details.\n"
#                 "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
#                 f"{doc_text}"
#             ]

#         contents = [system_prompt] + doc_context_parts + media_parts + [enriched_prompt]


#         # raw, attempts, cache_hit = await call_llm("VOICE_OVER", contents)
#         # return VoiceOverOutput(**raw)
#         raw, attempts, cache_hit = await call_llm("VOICE_OVER", contents)
#         try:
#             print(f"[VoiceOver] OPENING SEGMENT: {raw['segments'][0]['voiceover']}")
#         except Exception:
#             pass
#         return VoiceOverOutput(**raw)

















































































from pipeline.stages.base import BaseStage
from pipeline.contracts import VoiceOverOutput
from pipeline.llm_client import call_llm
from config import SYSTEM_PROMPTS, TOKEN_BUDGETS
from typing import List, Dict

import re
import logging

logger = logging.getLogger(__name__)

# ==================================================
# Human truth extraction prompt
# ── Finds the narrative spine before writing starts
# ==================================================

HUMAN_TRUTH_PROMPT = """
You are a documentary filmmaker, not a marketer.

Based on this project intelligence:
{project_intelligence}

Answer these four questions in plain language. No marketing speak. No jargon.

1. WHAT ACTUALLY HAPPENED: In one sentence, what did this company physically
   build, change, or create? Not what they "enabled" or "facilitated".

2. WHO ACTUALLY FELT IT: Which specific human being's life or work changed?
   Give them a job title and a real-world before/after moment.

3. THE TENSION: What was at stake? What would have happened without this?

4. THE ONE LINE: Tell this story to someone on a train in 10 seconds.
   No jargon. No company names. Just what happened and why it matters.

Return as JSON — no fences:
{{
  "what_happened": "...",
  "who_felt_it": "...",
  "the_tension": "...",
  "the_one_line": "..."
}}
"""

# Natural conversational speaking pace used to translate a requested
# duration into a concrete word-count target for the model. Kept in sync
# with critic.py's WORDS_PER_SEC — both represent the same natural pace,
# just used at different stages (draft grounding vs post-hoc validation).
WORDS_PER_SEC = 2.3


def _duration_to_word_target(duration_seconds: int, words_per_sec: float = WORDS_PER_SEC) -> tuple[int, int]:
    """Converts a requested duration into a target word count + tolerance,
    so the model gets a concrete, checkable number instead of a bare
    'Duration: 90' line it has to do its own (unreliable) mental math on."""
    target = round(duration_seconds * words_per_sec)
    tolerance = max(round(target * 0.08), 8)
    return target, tolerance


def _parse_duration_seconds(raw) -> int | None:
    """Parses a duration value into whole seconds, unit-aware.

    Handles: 90, "90", "90s", "90 seconds", "2 minutes", "2 min", "1:30".
    IMPORTANT: this must NOT simply strip non-digit characters — doing so
    turns "2 minutes" into 2 (seconds) instead of 120, silently corrupting
    the duration target. Every numeric prefix is checked for a trailing
    unit before being converted.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)

    s = str(raw).strip().lower()
    if not s:
        return None

    # "mm:ss" style, e.g. "1:30" -> 90
    if ":" in s:
        parts = s.split(":")
        if len(parts) == 2:
            try:
                minutes, seconds = int(parts[0]), int(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                pass

    match = re.search(r"(\d+(?:\.\d+)?)", s)
    if not match:
        return None
    number = float(match.group(1))

    if "min" in s:  # "minute", "minutes", "min", "mins"
        return int(round(number * 60))
    return int(round(number))  # default: seconds ("s", "sec", "seconds", or bare number)


def _repair_segments(raw: dict) -> dict:
    """Gemini intermittently omits required fields on a segment — most
    commonly `time_end` (seen in production: 'segments.2.time_end Field
    required'). Previously this crashed the whole stage at
    VoiceOverOutput(**raw) with no recovery. Instead, backfill missing
    timing fields deterministically before validation:
      - missing time_start: use the previous segment's time_end (or 0)
      - missing time_end: use the next segment's time_start, or estimate
        from the segment's own word count at natural speaking pace
    This is a best-effort repair, not a silent cover-up — it's logged so
    the underlying model reliability issue stays visible.
    """
    segments = raw.get("segments") or []

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue

        if seg.get("time_start") is None:
            prev_end = segments[i - 1].get("time_end") if i > 0 else None
            seg["time_start"] = prev_end if prev_end is not None else 0
            logger.warning(
                f"[VoiceOver] Segment {i} missing time_start — "
                f"backfilled to {seg['time_start']}"
            )

        if seg.get("time_end") is None:
            next_start = segments[i + 1].get("time_start") if i + 1 < len(segments) else None
            if next_start is not None:
                seg["time_end"] = next_start
            else:
                words = len((seg.get("voiceover") or "").split())
                estimated_span = max(round(words / WORDS_PER_SEC), 1)
                seg["time_end"] = seg["time_start"] + estimated_span
            logger.warning(
                f"[VoiceOver] Segment {i} missing time_end — "
                f"backfilled to {seg['time_end']}"
            )

    return raw


def _build_enriched_prompt(
    prompt: str,
    metadata: dict,
    research_brief: dict,
    human_truth: dict = None,
    preferences: dict = None,
    # retrieved_chunks: List[Dict] = None,
    semantic_inspiration: Dict = None,
    approved_essences=None,
    approved_interpretations=None,
    creative_summary=None,
) -> str:
    blocks = []

    if approved_essences:
        blocks.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "APPROVED ESSENCES\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(
                f"- {e}"
                for e in approved_essences
            )
        )

    if approved_interpretations:
        blocks.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "APPROVED INTERPRETATIONS\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(
                f"- {i}"
                for i in approved_interpretations
            )
        )

    if creative_summary:
        blocks.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "APPROVED CREATIVE SUMMARY\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + creative_summary
        )

    # ── Block 0: Semantic Inspiration Engine ────────────────────
    if semantic_inspiration:

        blocks.append(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"SEMANTIC INSPIRATION ENGINE\n"
            f"(Abstract creative influence — NEVER copy literally)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{semantic_inspiration}\n\n"
            f"IMPORTANT:\n"
            f"- Use this as abstract inspiration only\n"
            f"- Never reproduce wording or structure\n"
            f"- Use it to influence emotional tone,\n"
            f"  pacing, narrative energy, themes,\n"
            f"  and storytelling DNA\n"
        )
    # ── Block 0: Human truth — the narrative spine ───────────────
    # if human_truth:
    #     blocks.append(
    #         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    #         f"THE HUMAN TRUTH — this is the spine of the script.\n"
    #         f"Open with this. Everything else serves this.\n"
    #         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    #         f"What actually happened: {human_truth.get('what_happened', '')}\n"
    #         f"Who felt it: {human_truth.get('who_felt_it', '')}\n"
    #         f"The tension: {human_truth.get('the_tension', '')}\n"
    #         f"The one line: {human_truth.get('the_one_line', '')}"
    #     )
    # ── Block 0: Human truth — the narrative spine ───────────────
    if human_truth:
        blocks.append(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"THE HUMAN TRUTH — this is the spine of the script.\n"
            f"Everything in the script must serve this. This is the\n"
            f"SUBJECT MATTER of your opening line — but the opening\n"
            f"line's STRUCTURE must come from the WINNING HOOK PATTERNS\n"
            f"in the research block below, not from this block.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"What actually happened: {human_truth.get('what_happened', '')}\n"
            f"Who felt it: {human_truth.get('who_felt_it', '')}\n"
            f"The tension: {human_truth.get('the_tension', '')}\n"
            f"The one line: {human_truth.get('the_one_line', '')}"
        )

    # ── Block 1: Campaign brief ──────────────────────────────────
    if metadata:
        blocks.append(
            f"CAMPAIGN BRIEF:\n"
            f"Client: {metadata.get('client', '')}\n"
            f"Industry / Business Unit: {metadata.get('industries', '')}\n"
            f"Service Lines: {metadata.get('serviceLines', '')}\n"
            f"Video Type: {metadata.get('video_type', '')}\n"
            f"Tone: {metadata.get('video_tone', '')}\n"
            f"Duration: {metadata.get('duration', '')}"
        )

    # ── Block 1b: Duration requirement — explicit, hard constraint ──
    # Translated into a concrete word-count target rather than left as a
    # bare "Duration: 90" line buried in the campaign brief above, which
    # the model was effectively ignoring in favor of the louder, more
    # heavily-emphasized blocks below (PROJECT FACTS, NICHE RESEARCH, etc).
    raw_duration = (metadata or {}).get("duration_seconds") or (metadata or {}).get("duration")
    duration_seconds = _parse_duration_seconds(raw_duration)

    if duration_seconds:
        target_words, tolerance = _duration_to_word_target(duration_seconds)
        blocks.append(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"DURATION REQUIREMENT — NON-NEGOTIABLE\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Target runtime: {duration_seconds} seconds.\n"
            f"Target total voiceover word count across ALL segments: "
            f"{target_words} words (±{tolerance}).\n"
            f"A script that comes in short is a FAILED script, even if the\n"
            f"story feels emotionally complete. If you are tempted to wrap\n"
            f"up early, instead expand with more specific detail, a second\n"
            f"beat, or slower pacing on existing segments — never with\n"
            f"filler or repetition.\n"
            f"Set \"duration_seconds\" and \"word_count\" in your JSON output\n"
            f"to match this target, and make sure the sum of your segments'\n"
            f"voiceover word counts actually adds up to it."
        )
    else:
        logger.warning(
            "[VoiceOver] No usable duration found in metadata — "
            "skipping explicit duration/word-count constraint block"
        )

    if research_brief:
        # ── Block 2: Project facts ───────────────────────────────
        project_intel = research_brief.get("project_intelligence", "")
        project_facts = research_brief.get("project_facts", "")

        if project_intel and "No additional project data found" not in project_intel:
            blocks.append(
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"PROJECT FACTS — USE THESE. DO NOT INVENT OR REPLACE.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{project_intel}\n\n"
                f"MUST-INCLUDE FACTS FROM RESEARCH:\n{project_facts}"
            )

        # ── Block 3: Niche intelligence ──────────────────────────
        transcript_count = research_brief.get("transcript_count", 0)
        pain_points  = "\n".join(f"  - {p}" for p in (research_brief.get("top_pain_points") or []))
        hooks        = "\n".join(f"  - {h}" for h in (research_brief.get("winning_hooks") or []))
        phrases      = ", ".join(research_brief.get("proven_phrases") or [])
        tone_patterns = "\n".join(f"  - {t}" for t in (research_brief.get("tone_patterns") or []))
        resonate     = ", ".join(research_brief.get("words_that_resonate") or [])
        avoid        = ", ".join(research_brief.get("words_to_avoid") or [])

        blocks.append(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"NICHE RESEARCH INTELLIGENCE\n"
            f"(From live web research + {transcript_count} real video transcript analyses)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"MARKET CONTEXT:\n{research_brief.get('niche_summary', '')}\n\n"
            f"TOP BUYER PAIN POINTS (real, not guessed):\n{pain_points}\n\n"
            f"WINNING HOOK PATTERNS (from top-performing content):\n{hooks}\n\n"
            f"PROVEN PHRASES (words that actually resonated):\n{phrases}\n\n"
            f"TONE PATTERNS:\n{tone_patterns}\n\n"
            f"COMPETITOR LANDSCAPE:\n{research_brief.get('competitor_landscape', '')}\n\n"
            f"RECOMMENDED CREATIVE ANGLE:\n{research_brief.get('recommended_angle', '')}\n\n"
            f"WORDS THAT RESONATE: {resonate}\n"
            f"WORDS TO AVOID: {avoid}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"USE ALL OF THE ABOVE. This script must be unmistakably\n"
            f"written for this specific project — not a generic template.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    # ── Block 4: User prompt ─────────────────────────────────────
    blocks.append(f"USER REQUEST:\n{prompt}")

    # ── Block 5: Style guidelines (soft preferences) ───────────
    if preferences:
        lines = []
        if preferences.get("tone"):
            lines.append(f"- Tone: {preferences['tone']}")
        if preferences.get("length"):
            lines.append(f"- Length: {preferences['length']}")
        if preferences.get("style"):
            lines.append(f"- Style: {preferences['style']}")
        
        if lines:
            blocks.append("Style Guidelines:\n" + "\n".join(lines))

    return "\n\n".join(blocks)


class VoiceOverStage(BaseStage):
    name = "VOICE_OVER"

    async def execute(
        self,
        prompt: str,
        file_parts: list,
        metadata: dict = None,
        research_brief: dict = None,
        preferences: dict = None,
        # retrieved_chunks: List[Dict] = None,
        semantic_inspiration: Dict = None,
        approved_essences=None,
        approved_interpretations=None,
        creative_summary=None,
    ) -> VoiceOverOutput:

        budget = TOKEN_BUDGETS["VOICE_OVER"]
        logger.info(
            f"VoiceOver approved essences: "
            f"{len(approved_essences or [])}"
        )

        logger.info(
            f"VoiceOver approved interpretations: "
            f"{len(approved_interpretations or [])}"
        )

        logger.info(
            f"VoiceOver creative summary exists: "
            f"{bool(creative_summary)}"
        )

        # Separate and trim file parts
        text_parts  = [p for p in file_parts if isinstance(p, str)]
        media_parts = [p for p in file_parts if not isinstance(p, str)]

        if text_parts:
            per_file   = budget["file_budget"] // len(text_parts)
            text_parts = [t[:per_file] for t in text_parts]


        # ── Extract human truth before building prompt ────────────
        human_truth = None
        if research_brief:
            project_intel = research_brief.get("project_intelligence", "")
            if project_intel and "No additional project data found" not in project_intel:
                try:
                    from pipeline.stages.niche_research import _call_model, _parse_json
                    ht_raw = await _call_model(
                        HUMAN_TRUTH_PROMPT.format(
                            project_intelligence=project_intel[:6000]
                        )
                    )
                    human_truth = _parse_json(ht_raw)
                    logger.info(
                        f"[VoiceOver] Human truth: {human_truth.get('the_one_line', '')}"
                    )
                except Exception as e:
                    logger.warning(f"[VoiceOver] Human truth extraction failed: {e}")

        # ── Build enriched prompt ─────────────────────────────────
        print("\n" + "=" * 60)
        print("[VoiceOver] RECEIVED SEMANTIC INSPIRATION")
        print(str(semantic_inspiration)[:2000])
        print("=" * 60 + "\n")
        trimmed_prompt  = prompt[:budget["prompt_budget"]]

        enriched_prompt = _build_enriched_prompt(
            prompt=trimmed_prompt,
            metadata=metadata or {},
            research_brief=research_brief,
            human_truth=human_truth,
            preferences=preferences,
            # retrieved_chunks=retrieved_chunks,
            semantic_inspiration=semantic_inspiration,
            # approved_essences=None,
            # approved_interpretations=None,
            # creative_summary=None,
            approved_essences=approved_essences,
            approved_interpretations=approved_interpretations,
            creative_summary=creative_summary,
        )

        # print(f"PROMPT SENT TO LLM:\n{enriched_prompt[:600]}...")
        print(f"PROMPT SENT TO LLM:\n{enriched_prompt[:300]}...")

        system_prompt = SYSTEM_PROMPTS["VOICE_OVER"]

        # ── If files were uploaded, inject them as high-priority context ──
        doc_context_parts = []
        if text_parts:
            doc_text = "\n\n".join(text_parts)
            doc_context_parts = [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "UPLOADED DOCUMENT CONTENT — THIS IS YOUR PRIMARY SOURCE.\n"
                "Use ONLY facts from this document. Do NOT invent details.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{doc_text}"
            ]

        contents = [system_prompt] + doc_context_parts + media_parts + [enriched_prompt]


        # raw, attempts, cache_hit = await call_llm("VOICE_OVER", contents)
        # return VoiceOverOutput(**raw)
        raw, attempts, cache_hit = await call_llm("VOICE_OVER", contents)
        try:
            print(f"[VoiceOver] OPENING SEGMENT: {raw['segments'][0]['voiceover']}")
        except Exception:
            pass

        try:
            raw = _repair_segments(raw)
        except Exception as e:
            logger.warning(f"[VoiceOver] Segment repair failed, passing through raw: {e}")

        return VoiceOverOutput(**raw)