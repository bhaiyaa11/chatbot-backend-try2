import json
import re
import logging
from pipeline.stages.base import BaseStage
from pipeline.contracts import VoiceOverOutput, VisualsOutput
from pipeline.llm_client import stream_llm, generate_text
from pipeline.few_shot import get_few_shot_examples
from config import TOKEN_BUDGETS

logger = logging.getLogger(__name__)

# Natural conversational speaking pace. Used to verify a segment's voiceover
# is actually speakable in its allotted time window — prompt instructions
# about word counts get ignored during rewrite passes, so this is checked
# deterministically instead of trusted blindly.
WORDS_PER_SEC = 2.3     
PACING_HEADROOM = 1.25  # allow 25% over the strict pace before flagging

EM_DASH_RE = re.compile(r"\s*[—–]\s*")


def _strip_dashes(text: str) -> str:
    """Deterministic safety net for the 'NO EM DASHES' rule — prompt
    instructions alone are not reliably followed by the model, especially
    across multiple rewrite passes, so em/en dashes are stripped post-hoc."""
    return EM_DASH_RE.sub(", ", text)


def _parse_table_rows(markdown_table: str) -> list[tuple[int, str]]:
    """Extracts (time_seconds, voiceover_text) from a
    '| Time (s) | Voice Over | Visuals |' markdown table."""
    rows = []
    for line in markdown_table.strip().split("\n"):
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or not cells[0]:
            continue
        try:
            t = int(re.sub(r"[^\d]", "", cells[0]))
        except ValueError:
            continue
        rows.append((t, cells[1]))
    # Drop the header row itself if it slipped through (e.g. "Time (s)")
    return [r for r in rows if not r[1].lower().startswith("voice over")]


def _check_pacing(script_text: str) -> dict | None:
    """Deterministic pacing check. Handles both the initial JSON draft
    (script.segments with time_start/time_end) and later markdown-table
    rewrites (rows keyed by start time, duration = gap to next row).
    Returns a synthetic issue dict (same shape as the LLM's issues) if any
    segment has more words than can naturally be spoken in its time window,
    or None if pacing looks fine."""
    segments = []  # (start, duration, word_count, sample_text)

    stripped = script_text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            for seg in data.get("script", {}).get("segments", []):
                start = seg.get("time_start", 0)
                end = seg.get("time_end", start)
                words = len(seg.get("voiceover", "").split())
                segments.append((start, max(end - start, 1), words, seg.get("voiceover", "")[:60]))
        except (json.JSONDecodeError, AttributeError):
            return None
    else:
        rows = _parse_table_rows(stripped)
        for i, (t, text) in enumerate(rows):
            words = len(text.split())
            duration = (rows[i + 1][0] - t) if i + 1 < len(rows) else max(words / WORDS_PER_SEC, 5)
            segments.append((t, max(duration, 1), words, text[:60]))

    violations = []
    for start, duration, words, sample in segments:
        max_words = round(duration * WORDS_PER_SEC * PACING_HEADROOM)
        if words > max_words:
            violations.append(
                f"segment at {start}s: {words} words but only {duration:.0f}s allotted "
                f"(max ~{max_words} words at natural pace) — \"{sample}...\""
            )

    if not violations:
        return None

    return {
        "segment_time": segments[0][0] if segments else 0,
        "issue_type": "pacing_violation",
        "current_text": "multiple segments",
        "suggested_fix": (
            "Trim voiceover word count to match natural speaking pace "
            f"(~{WORDS_PER_SEC} words/sec) for each segment's time window. "
            "Cut facts/detail rather than compressing sentences into run-ons."
        ),
        "reason": " | ".join(violations),
    }


def _parse_duration_seconds(raw) -> int | None:
    """Parses a duration value into whole seconds, unit-aware.

    Handles: 90, "90", "90s", "90 seconds", "2 minutes", "2 min", "1:30".
    Must NOT simply strip non-digit characters — "2 minutes" stripped of
    non-digits becomes "2", silently turning a 120s target into 2s. Kept
    in sync with the identical parser in voice_over.py.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)

    s = str(raw).strip().lower()
    if not s:
        return None

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

    if "min" in s:
        return int(round(number * 60))
    return int(round(number))


def _check_total_duration(script_text: str, target_seconds: int, tolerance_pct: float = 0.1) -> dict | None:
    """Sums up the script's actual total runtime and word count, and flags
    a synthetic issue (same shape as the LLM's issues) if the total misses
    the requested duration by more than tolerance_pct. This is the check
    that was previously missing — _check_pacing only validates that each
    segment's own word count fits its own time window, it never compares
    the whole script's length against what was actually requested."""
    stripped = script_text.strip()
    total_seconds = 0
    total_words = 0

    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            segs = data.get("script", {}).get("segments", [])
            if not segs:
                return None
            total_seconds = segs[-1].get("time_end", 0)
            total_words = sum(len(s.get("voiceover", "").split()) for s in segs)
        except (json.JSONDecodeError, AttributeError):
            return None
    else:
        rows = _parse_table_rows(stripped)
        if not rows:
            return None
        last_words = len(rows[-1][1].split())
        total_seconds = rows[-1][0] + max(last_words / WORDS_PER_SEC, 3)
        total_words = sum(len(text.split()) for _, text in rows)

    if not target_seconds or target_seconds <= 0 or not total_seconds:
        return None

    low, high = target_seconds * (1 - tolerance_pct), target_seconds * (1 + tolerance_pct)
    if low <= total_seconds <= high:
        return None

    direction = "short" if total_seconds < target_seconds else "long"
    fix = (
        "Add another beat, expand specific detail, or slow the pacing on "
        "existing segments to reach the target length. Do not pad with "
        "filler or repetition."
        if direction == "short" else
        "Cut a segment or tighten language across the script to hit the "
        "target runtime."
    )

    return {
        "segment_time": 0,
        "issue_type": "duration_mismatch",
        "current_text": f"total script runtime ~{total_seconds:.0f}s, {total_words} words",
        "suggested_fix": fix,
        "reason": (
            f"target duration is {target_seconds}s but the script runs "
            f"~{total_seconds:.0f}s ({total_words} words) — {direction} of target"
        ),
    }


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

# Used when NO research_brief is available. There's nothing to fact-check
# against, so this evaluates the script purely on craft/quality — hook,
# story flow, specificity vs generic language, originality, visual/VO
# redundancy, ending strength — and returns the SAME JSON schema as
# FACT_CHECK_PROMPT so it can drive the same rewrite loop.
GENERAL_QUALITY_PROMPT = """
You are a senior B2B video script critic. No project brief is available for
this script, so do NOT fact-check — evaluate it purely on craft and quality:
hook strength, narrative/story flow, emotional specificity vs generic
language, originality vs cliche, visual writing vs voiceover redundancy,
and ending strength.

Output STRICTLY as JSON — no fences:
{
  "issues": [
    {
      "segment_time": 0,
      "issue_type": "cliche | generic_language | weak_hook | redundant_visual | weak_ending | flat_pacing | overexplaining | pacing_violation",
      "current_text": "...",
      "suggested_fix": "...",
      "reason": "..."
    }
  ],
  "overall_score": 7,
  "hook_quality": "weak | adequate | strong",
  "client_presence": "absent | weak | strong"
}

"client_presence" here means how specific and grounded the script is in
concrete, real detail versus vague poetic filler — "absent" if the script
is generic enough to apply to almost any subject.

Be honest and critical, do not inflate scores. If the script genuinely has
no meaningful issues, return {"issues": [], "overall_score": 9, "hook_quality": "strong", "client_presence": "strong"}
"""

REWRITE_PROMPT = """
You are an elite B2B video scriptwriter. You will receive:
1. A draft script (VoiceOver + Visuals JSON, or a previously rewritten markdown table)
2. A fact-check report identifying specific issues
3. A project brief with verified facts
4. Few-shot examples of high-rated scripts
5. NO EM DASHES in your output

Rewrite the script fixing ONLY the issues in the fact-check report.
Do NOT change the narrative arc, tone, pacing, or ending. If the draft
is structured to stay unresolved, ambiguous, ironic, or open-ended,
preserve that structure exactly — fix facts and wording only, not the story shape.

PACING IS A HARD CONSTRAINT, NOT A SUGGESTION:
Each row's time gap to the next row is how many seconds that voiceover line
has to be SPOKEN aloud. Natural speaking pace is about 2.3 words per second.
A 7-second gap can hold roughly 16 words, not 60. If a fact-check issue asks
you to add a specific fact, date, name, or number, you MUST cut existing
words from that same segment to make room for it, or move the added detail
to the VISUALS column (on-screen text/graphic) instead of the voiceover.
Never let a segment's word count grow without also checking it still fits
its time window. When in doubt, cut the fact rather than break pacing.

If a fact-check issue has issue_type "duration_mismatch", it means the
TOTAL script runtime misses the requested duration. Fix this by adding or
cutting whole segments (not by cramming more words into an existing
segment's time window) so the final row's cumulative time lands within the
target range.

You MUST output ONLY a valid markdown table.


STRICT RULES:
- Output MUST start with "|"
- Output MUST contain a header separator row using "---"
- Every row MUST start and end with "|"
- NO text before or after the table
- NO explanations
- NO JSON
- NO markdown code blocks
- NO EM DASHES "-" in the response generated 
-140 words 60 seconds with 10 words plus minus
-180 words 90 seconds with 10 words plus minus
-220 words 120 seconds with 10 words plus minus
-260 words 150 seconds with 10 words plus minus

EXACT format:
| Time (s) | Voice Over | Visuals |
|----------|------------|---------|
| 0 | voiceover text here | visual description here |
| 5 | voiceover text here | visual description here |
- Preserve original meaning, do not add new facts
"""

# Used ONLY when no rewrite happened (script passed the quality/fact check
# clean, or the check itself failed) and we just need the original
# VoiceOver + Visuals JSON converted into the final markdown table.
# This must NOT critique, score, or flag issues.
FORMAT_ONLY_PROMPT = """
Convert the given VoiceOver + Visuals JSON into a markdown table. Do NOT
evaluate, critique, score, or comment on the script in any way. Do NOT
change any wording, facts, or structure. This is a pure format conversion.

STRICT RULES:
- Output MUST start with "|"
- Output MUST contain a header separator row using "---"
- Every row MUST start and end with "|"
- NO text before or after the table
- NO explanations, scores, flags, or commentary of any kind
- NO markdown code blocks
- NO EM DASHES "-" in the response generated

EXACT format:
| Time (s) | Voice Over | Visuals |
|----------|------------|---------|
| 0 | voiceover text here | visual description here |
| 5 | voiceover text here | visual description here |
"""


class CriticStage(BaseStage):
    name = "CRITIC"

    # How many fact-check → rewrite cycles to allow before giving up
    # and returning the best version we have.
    MAX_REWRITE_ITERATIONS = 1

    async def _run_quality_check(self, script_text: str, research_brief: dict) -> dict | None:
        """Always evaluates the script and returns the shared issues/score JSON.
        Uses fact-checking against the brief when one is available, otherwise
        falls back to a general craft/quality critique (same schema)."""
        if research_brief:
            project_intel = research_brief.get("project_intelligence", "")
            project_facts = research_brief.get("project_facts", "")
            check_contents = [
                FACT_CHECK_PROMPT,
                f"PROJECT BRIEF:\n{project_intel}\n\nMUST-INCLUDE FACTS:\n{project_facts}",
                f"DRAFT SCRIPT:\n{script_text}",
            ]
            mode = "fact-check"
        else:
            check_contents = [
                GENERAL_QUALITY_PROMPT,
                f"DRAFT SCRIPT:\n{script_text}",
            ]
            mode = "general quality critique"

        try:
            raw = await generate_text("CRITIC", check_contents)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()
            result = json.loads(cleaned)

            score    = result.get("overall_score", 10)
            issues   = result.get("issues", [])
            hook     = result.get("hook_quality", "adequate")
            presence = result.get("client_presence", "adequate")

            logger.info(
                f"[Critic] {mode} score: {score}/10 | "
                f"Issues: {len(issues)} | Hook: {hook} | "
                f"Presence: {presence}"
            )
            return result
        except Exception as e:
            logger.warning(f"[Critic] {mode} failed, skipping: {e}")
            return None

    @staticmethod
    def _needs_rewrite(fact_check_result: dict | None) -> bool:
        if fact_check_result is None:
            return False
        return (
            len(fact_check_result.get("issues", [])) > 0
            or fact_check_result.get("hook_quality") == "weak"
            or fact_check_result.get("client_presence") == "absent"
            or fact_check_result.get("overall_score", 10) < 8
        )

    async def _run_rewrite(
        self,
        script_text: str,
        fact_check_result: dict,
        metadata: dict,
        research_brief: dict,
    ) -> str:
        examples = await get_few_shot_examples(limit=2)
        project_intel = research_brief.get("project_intelligence", "") if research_brief else "Not provided"
        project_facts = research_brief.get("project_facts", "") if research_brief else "Not provided"
        client_name   = metadata.get("client", "") if metadata else ""

        rewrite_contents = [
            REWRITE_PROMPT,
            f"CLIENT: {client_name}\nPROJECT BRIEF:\n{project_intel}\n\nMUST-INCLUDE FACTS:\n{project_facts}",
            f"FACT-CHECK REPORT:\n{json.dumps(fact_check_result, indent=2)}",
            f"DRAFT SCRIPT:\n{script_text}",
        ]

        if examples:
            rewrite_contents.append(
                f"HIGH-RATED SCRIPT EXAMPLES (quality benchmark):\n{examples}"
            )

        total_chars = sum(len(p) for p in rewrite_contents)
        logger.info(
            f"[Critic] Rewrite payload: {len(rewrite_contents)} parts, "
            f"~{total_chars} chars (~{total_chars // 4} tokens est.)"
        )

        rewrite_raw = ""
        chunk_count = 0
        async for chunk in stream_llm("VOICE_OVER", rewrite_contents):
            chunk_count += 1
            rewrite_raw += chunk

        logger.info(f"[Critic] Rewrite stream yielded {chunk_count} chunk(s), {len(rewrite_raw)} char(s)")

        return _strip_dashes(rewrite_raw.strip())

    async def execute(
        self,
        voice_over: VoiceOverOutput,
        visuals: VisualsOutput,
        file_parts: list,
        metadata: dict = None,
        research_brief: dict = None,
        is_human_reviewed: bool = False,
    ) -> str:
        budget = TOKEN_BUDGETS["CRITIC"]
        media_parts = [p for p in file_parts if not isinstance(p, str)]

        combined = json.dumps({
            "script": voice_over.model_dump(),
            "visuals": visuals.model_dump(),
        }, indent=2)

        # ── Resolve the requested target duration once up front ─────
        # Accepts either "duration_seconds" (preferred, already an int)
        # or "duration" (legacy key, may be a string like "90" or "90s").
        target_duration = None
        if metadata:
            raw_duration = metadata.get("duration_seconds") or metadata.get("duration")
            target_duration = _parse_duration_seconds(raw_duration)
        if not target_duration:
            logger.warning(
                "[Critic] No usable target duration found in metadata — "
                "duration mismatch check will be skipped"
            )

        # ── Iterative fact-check → rewrite loop ──────────────────
        current_script = combined
        last_fact_check = None
        any_rewrite_happened = False

        best_script = combined
        best_score = -1
        prev_score = None
        prev_issue_count = None

        for iteration in range(1, self.MAX_REWRITE_ITERATIONS + 1):
            quality_result = await self._run_quality_check(current_script, research_brief)
            last_fact_check = quality_result

            if quality_result is None:
                logger.info(f"[Critic] Iteration {iteration}: quality check failed, stopping loop")
                break

            score = quality_result.get("overall_score", 0)
            issue_count = len(quality_result.get("issues", []))

            # Deterministic pacing check — the LLM-based check doesn't
            # reliably catch this (it's not something it's good at judging
            # from text alone), so verify the actual words-per-second math
            # and force a rewrite if any segment is unspeakable in its window.
            pacing_issue = _check_pacing(current_script)
            if pacing_issue:
                quality_result.setdefault("issues", []).append(pacing_issue)
                issue_count = len(quality_result["issues"])
                score = min(score, 5)  # a pacing violation alone is disqualifying
                quality_result["overall_score"] = score
                logger.warning(f"[Critic] Iteration {iteration}: pacing violation — {pacing_issue['reason']}")

            # Deterministic TOTAL duration check — catches the case where
            # every individual segment paces fine on its own, but the whole
            # script still runs short (or long) of the requested duration.
            if target_duration:
                duration_issue = _check_total_duration(current_script, target_duration)
                if duration_issue:
                    quality_result.setdefault("issues", []).append(duration_issue)
                    issue_count = len(quality_result["issues"])
                    score = min(score, 5)  # a duration miss alone is disqualifying
                    quality_result["overall_score"] = score
                    logger.warning(f"[Critic] Iteration {iteration}: duration mismatch — {duration_issue['reason']}")

            # Track the best-scoring version seen so far, in case a later
            # rewrite ever regresses quality instead of improving it.
            if score > best_score:
                best_score = score
                best_script = current_script

            if not self._needs_rewrite(quality_result):
                logger.info(f"[Critic] Iteration {iteration}: script passed quality check, stopping loop")
                break

            # Plateau check: compare against the PREVIOUS iteration's result
            # (not the running best, which trivially equals the current
            # result right after the update above). If nothing moved,
            # another rewrite pass is unlikely to help.
            if prev_score is not None and score == prev_score and issue_count == prev_issue_count:
                logger.info(
                    f"[Critic] Iteration {iteration}: no improvement over previous "
                    f"iteration (score {score}, {issue_count} issues) — stopping loop"
                )
                break

            prev_score, prev_issue_count = score, issue_count

            logger.info(
                f"[Critic] Iteration {iteration}: "
                f"{len(quality_result['issues'])} issue(s) found — rewriting"
            )

            try:
                rewritten = await self._run_rewrite(
                    current_script, quality_result, metadata, research_brief
                )
            except Exception as e:
                logger.warning(f"[Critic] Iteration {iteration}: rewrite failed, stopping loop: {e}")
                break

            if not rewritten:
                logger.warning(f"[Critic] Iteration {iteration}: rewrite returned empty, stopping loop")
                break

            current_script = rewritten
            any_rewrite_happened = True

            if iteration == self.MAX_REWRITE_ITERATIONS:
                logger.warning(
                    f"[Critic] Hit MAX_REWRITE_ITERATIONS ({self.MAX_REWRITE_ITERATIONS}) "
                    f"— returning latest rewrite even though issues may remain"
                )

        # Safety net: if the script we're about to return scored lower on its
        # own check than an earlier iteration did, prefer the earlier one.
        # (Normally current_script's last check IS the best, since score only
        # improves or plateaus — this only fires if a rewrite regressed quality.)
        if (
            last_fact_check is not None
            and last_fact_check.get("overall_score", 0) < best_score
            and best_script != current_script
        ):
            logger.warning(
                f"[Critic] Current script (score {last_fact_check.get('overall_score')}) "
                f"scored lower than an earlier iteration (score {best_score}) — reverting to it"
            )
            current_script = best_script
            any_rewrite_happened = best_script != combined

        # ── Format into markdown table ───────────────────────────
        # If the loop ended on a rewrite, current_script is already a markdown table.
        if any_rewrite_happened and current_script.strip().startswith("|"):
            logger.info("[Critic] Final script already a markdown table — skipping reformat")
            return _strip_dashes(current_script.strip())

        # Otherwise (no rewrite ever happened) just convert the original
        # JSON into the markdown table — no critique, no scoring.
        result = ""
        async for chunk in stream_llm("CRITIC", [FORMAT_ONLY_PROMPT] + media_parts + [current_script]):
            result += chunk

        return _strip_dashes(result.strip())