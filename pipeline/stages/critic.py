# import json
# import logging
# from pipeline.stages.base import BaseStage
# from pipeline.contracts import VoiceOverOutput, VisualsOutput
# from pipeline.llm_client import stream_llm, call_llm
# from pipeline.few_shot import get_few_shot_examples
# from config import SYSTEM_PROMPTS, TOKEN_BUDGETS

# logger = logging.getLogger(__name__)

# # ── Council prompt 1: Fact checker ──────────────────────────────
# FACT_CHECK_PROMPT = """
# You are a B2B script fact-checker. You will be given:
# 1. A verified PROJECT BRIEF with real facts about a client and project
# 2. A draft video script

# Your job: identify every segment where the script:
# - Makes a claim NOT supported by the project brief
# - Uses generic language where a specific fact from the brief could replace it
# - Misses a key project fact that should appear in the script
# - Gets a specific detail wrong (name, number, date, role)

# Output STRICTLY as JSON — no fences:
# {
#   "issues": [
#     {
#       "segment_time": 0,
#       "issue_type": "missing_fact | wrong_fact | generic_language",
#       "current_text": "...",
#       "suggested_fix": "...",
#       "reason": "..."
#     }
#   ],
#   "overall_score": 7,
#   "hook_quality": "weak | adequate | strong",
#   "capgemini_presence": "absent | weak | strong"
# }

# If there are no issues, return {"issues": [], "overall_score": 10, "hook_quality": "strong", "capgemini_presence": "strong"}
# """

# # ── Council prompt 2: Rewriter ───────────────────────────────────
# REWRITE_PROMPT = """
# You are an elite B2B video scriptwriter. You will receive:
# 1. A draft script (VoiceOver + Visuals JSON)
# 2. A fact-check report identifying specific issues
# 3. A project brief with verified facts
# 4. Few-shot examples of high-rated scripts

# Rewrite the script fixing ALL issues in the fact-check report.


# You MUST output ONLY a valid markdown table.

# STRICT RULES:
# - Output MUST start with "|"
# - Output MUST contain a header separator row using "---"
# - Every row MUST start and end with "|"
# - NO text before or after the table
# - NO explanations
# - NO JSON
# - NO markdown code blocks

# EXACT format:
# | Time (s) | Voice Over | Visuals |
# |----------|------------|---------|
# | 0 | voiceover text here | visual description here |
# | 5 | voiceover text here | visual description here |
# - Preserve original meaning, do not add new facts
# """


# class CriticStage(BaseStage):
#     name = "CRITIC"

#     async def execute(
#         self,
#         voice_over: VoiceOverOutput,
#         visuals: VisualsOutput,
#         file_parts: list,
#         metadata: dict = None,
#         research_brief: dict = None,
#     ) -> str:
#         budget = TOKEN_BUDGETS["CRITIC"]
#         media_parts = [p for p in file_parts if not isinstance(p, str)]

#         combined = json.dumps({
#             "script": voice_over.model_dump(),
#             "visuals": visuals.model_dump(),
#         }, indent=2)

#         # ── Step 1: Fact-check (only if we have a research brief) ──
#         fact_check_result = None
#         if research_brief:
#             project_intel = research_brief.get("project_intelligence", "")
#             project_facts = research_brief.get("project_facts", "")

#             fact_check_contents = [
#                 FACT_CHECK_PROMPT,
#                 f"PROJECT BRIEF:\n{project_intel}\n\nMUST-INCLUDE FACTS:\n{project_facts}",
#                 f"DRAFT SCRIPT:\n{combined}",
#             ]

#             try:
#                 raw, _, _ = await call_llm("CRITIC", fact_check_contents)
#                 # call_llm returns parsed dict for JSON stages — handle both cases
#                 if isinstance(raw, dict):
#                     fact_check_result = raw
#                 else:
#                     fact_check_result = json.loads(raw)

#                 score = fact_check_result.get("overall_score", 10)
#                 issues = fact_check_result.get("issues", [])
#                 hook = fact_check_result.get("hook_quality", "adequate")
#                 presence = fact_check_result.get("capgemini_presence", "adequate")

#                 logger.info(
#                     f"[Critic] Fact-check score: {score}/10 | "
#                     f"Issues: {len(issues)} | Hook: {hook} | "
#                     f"Capgemini presence: {presence}"
#                 )
#             except Exception as e:
#                 logger.warning(f"[Critic] Fact-check failed, skipping: {e}")
#                 fact_check_result = None

#         # ── Step 2: Rewrite if issues found ──────────────────────
#         rewritten_combined = combined  # fallback to original if rewrite skipped

#         should_rewrite = (
#             fact_check_result is not None
#             and (
#                 len(fact_check_result.get("issues", [])) > 0
#                 or fact_check_result.get("hook_quality") == "weak"
#                 or fact_check_result.get("capgemini_presence") == "absent"
#                 or fact_check_result.get("overall_score", 10) < 8
#             )
#         )

#         if should_rewrite:
#             logger.info(f"[Critic] Rewriting script — {len(fact_check_result['issues'])} issues found")

#             examples = await get_few_shot_examples(limit=2)
#             project_intel = research_brief.get("project_intelligence", "") if research_brief else ""
#             project_facts = research_brief.get("project_facts", "") if research_brief else ""

#             rewrite_contents = [
#                 REWRITE_PROMPT,
#                 f"PROJECT BRIEF:\n{project_intel}\n\nMUST-INCLUDE FACTS:\n{project_facts}",
#                 f"FACT-CHECK REPORT:\n{json.dumps(fact_check_result, indent=2)}",
#                 f"DRAFT SCRIPT:\n{combined}",
#             ]

#             if examples:
#                 rewrite_contents.append(
#                     f"HIGH-RATED SCRIPT EXAMPLES (quality benchmark):\n{examples}"
#                 )

#             try:
#                 rewrite_raw, _, _ = await call_llm("CRITIC", rewrite_contents)
#                 if isinstance(rewrite_raw, dict):
#                     rewritten_combined = json.dumps(rewrite_raw, indent=2)
#                 else:
#                     rewritten_combined = rewrite_raw
#                 logger.info("[Critic] ✅ Rewrite complete")
#             except Exception as e:
#                 logger.warning(f"[Critic] Rewrite failed, using original: {e}")
#         else:
#             logger.info("[Critic] Script passed fact-check — skipping rewrite")

#         # ── Step 3: Format into markdown table (existing behaviour) ──
#         system_prompt = SYSTEM_PROMPTS["CRITIC"]
#         # Only inject few-shot if we didn't already use them in rewrite
#         if not should_rewrite:
#             examples = await get_few_shot_examples(limit=2)
#             if examples:
#                 system_prompt += (
#                     f"\n\nHere are examples of scripts users rated highly. "
#                     f"Use these as your quality benchmark:\n{examples}"
#                 )

#         contents = [system_prompt] + media_parts + [rewritten_combined]

#         result = ""
#         async for chunk in stream_llm("CRITIC", contents):
#             result += chunk

#         return result.strip()












import json
import logging
from pipeline.stages.base import BaseStage
from pipeline.contracts import VoiceOverOutput, VisualsOutput
from pipeline.llm_client import stream_llm, call_llm
from pipeline.few_shot import get_few_shot_examples
from config import SYSTEM_PROMPTS, TOKEN_BUDGETS

logger = logging.getLogger(__name__)

FACT_CHECK_PROMPT = """
You are a B2B script fact-checker. You will be given:
1. A verified PROJECT BRIEF with real facts about a client and project
2. A draft video script

Your job: identify every segment where the script:
- Makes a claim NOT supported by the project brief
- Uses generic language where a specific fact from the brief could replace it
- Misses a key project fact that should appear in the script
- Gets a specific detail wrong (name, number, date, role)

Output STRICTLY as JSON — no fences:
{
  "issues": [
    {
      "segment_time": 0,
      "issue_type": "missing_fact | wrong_fact | generic_language",
      "current_text": "...",
      "suggested_fix": "...",
      "reason": "..."
    }
  ],
  "overall_score": 7,
  "hook_quality": "weak | adequate | strong",
  "client_presence": "absent | weak | strong"
}

If there are no issues, return {"issues": [], "overall_score": 10, "hook_quality": "strong", "client_presence": "strong"}
"""

REWRITE_PROMPT = """
You are an elite B2B video scriptwriter. You will receive:
1. A draft script (VoiceOver + Visuals JSON)
2. A fact-check report identifying specific issues
3. A project brief with verified facts
4. Few-shot examples of high-rated scripts

Rewrite the script fixing ALL issues in the fact-check report.

You MUST output ONLY a valid markdown table.

STRICT RULES:
- Output MUST start with "|"
- Output MUST contain a header separator row using "---"
- Every row MUST start and end with "|"
- NO text before or after the table
- NO explanations
- NO JSON
- NO markdown code blocks

EXACT format:
| Time (s) | Voice Over | Visuals |
|----------|------------|---------|
| 0 | voiceover text here | visual description here |
| 5 | voiceover text here | visual description here |
- Preserve original meaning, do not add new facts
"""


class CriticStage(BaseStage):
    name = "CRITIC"

    async def execute(
        self,
        voice_over: VoiceOverOutput,
        visuals: VisualsOutput,
        file_parts: list,
        metadata: dict = None,
        research_brief: dict = None,
    ) -> str:
        budget = TOKEN_BUDGETS["CRITIC"]
        media_parts = [p for p in file_parts if not isinstance(p, str)]

        combined = json.dumps({
            "script": voice_over.model_dump(),
            "visuals": visuals.model_dump(),
        }, indent=2)

        # ── Step 1: Fact-check via base Gemini (better at analysis) ──
        fact_check_result = None
        if research_brief:
            project_intel = research_brief.get("project_intelligence", "")
            project_facts = research_brief.get("project_facts", "")

            fact_check_contents = [
                FACT_CHECK_PROMPT,
                f"PROJECT BRIEF:\n{project_intel}\n\nMUST-INCLUDE FACTS:\n{project_facts}",
                f"DRAFT SCRIPT:\n{combined}",
            ]

            try:
                raw, _, _ = await call_llm("CRITIC", fact_check_contents)
                if isinstance(raw, dict):
                    fact_check_result = raw
                else:
                    fact_check_result = json.loads(raw)

                score    = fact_check_result.get("overall_score", 10)
                issues   = fact_check_result.get("issues", [])
                hook     = fact_check_result.get("hook_quality", "adequate")
                presence = fact_check_result.get("client_presence", "adequate")

                logger.info(
                    f"[Critic] Fact-check score: {score}/10 | "
                    f"Issues: {len(issues)} | Hook: {hook} | "
                    f"Presence: {presence}"
                )
            except Exception as e:
                logger.warning(f"[Critic] Fact-check failed, skipping: {e}")
                fact_check_result = None

        # ── Step 2: Rewrite via fine-tuned VOICE_OVER (keeps agency voice) ──
        rewritten_combined = combined

        should_rewrite = (
            fact_check_result is not None
            and (
                len(fact_check_result.get("issues", [])) > 0
                or fact_check_result.get("hook_quality") == "weak"
                or fact_check_result.get("client_presence") == "absent"
                or fact_check_result.get("overall_score", 10) < 8
            )
        )

        if should_rewrite:
            logger.info(
                f"[Critic] Rewriting via fine-tuned model — "
                f"{len(fact_check_result['issues'])} issues found"
            )

            examples = await get_few_shot_examples(limit=2)
            project_intel = research_brief.get("project_intelligence", "") if research_brief else ""
            project_facts = research_brief.get("project_facts", "") if research_brief else ""
            client_name   = metadata.get("client", "") if metadata else ""

            rewrite_contents = [
                REWRITE_PROMPT,
                f"CLIENT: {client_name}\nPROJECT BRIEF:\n{project_intel}\n\nMUST-INCLUDE FACTS:\n{project_facts}",
                f"FACT-CHECK REPORT:\n{json.dumps(fact_check_result, indent=2)}",
                f"DRAFT SCRIPT:\n{combined}",
            ]

            if examples:
                rewrite_contents.append(
                    f"HIGH-RATED SCRIPT EXAMPLES (quality benchmark):\n{examples}"
                )

            try:
                # ← FIXED: stream_llm not call_llm — rewrite output is markdown not JSON
                rewrite_raw = ""
                async for chunk in stream_llm("VOICE_OVER", rewrite_contents):
                    rewrite_raw += chunk

                if rewrite_raw.strip():
                    rewritten_combined = rewrite_raw.strip()
                    logger.info("[Critic] ✅ Rewrite complete via fine-tuned model")
                else:
                    logger.warning("[Critic] Rewrite returned empty — using original")
            except Exception as e:
                logger.warning(f"[Critic] Rewrite failed, using original: {e}")
        else:
            logger.info("[Critic] Script passed fact-check — skipping rewrite")

  
        # ── Step 3: Format into markdown table ───────────────────
        # If rewrite already produced a markdown table, use it directly
        if should_rewrite and rewritten_combined.strip().startswith("|"):
            logger.info("[Critic] Rewrite already a markdown table — skipping reformat")
            return rewritten_combined.strip()

        # Otherwise run base Gemini to format original into table
        system_prompt = SYSTEM_PROMPTS["CRITIC"]
        if not should_rewrite:
            examples = await get_few_shot_examples(limit=2)
            if examples:
                system_prompt += (
                    f"\n\nHere are examples of scripts users rated highly. "
                    f"Use these as your quality benchmark:\n{examples}"
                )

        contents = [system_prompt] + media_parts + [rewritten_combined]

        result = ""
        async for chunk in stream_llm("CRITIC", contents):
            result += chunk

        return result.strip()