from pipeline.stages.base import BaseStage
from pipeline.contracts import VoiceOverOutput
from pipeline.llm_client import call_llm
from config import SYSTEM_PROMPTS, TOKEN_BUDGETS
from typing import List, Dict

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
    if human_truth:
        blocks.append(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"THE HUMAN TRUTH — this is the spine of the script.\n"
            f"Open with this. Everything else serves this.\n"
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
            f"Industry / Business Unit: {metadata.get('business_unit', '')}\n"
            f"Video Type: {metadata.get('video_type', '')}\n"
            f"Tone: {metadata.get('video_tone', '')}\n"
            f"Duration: {metadata.get('duration', '')}"
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


        raw, attempts, cache_hit = await call_llm("VOICE_OVER", contents)
        return VoiceOverOutput(**raw)
