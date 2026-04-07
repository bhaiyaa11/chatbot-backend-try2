"""
summarizer — Progressive summarization for long-term memory.

When a conversation exceeds SUMMARY_THRESHOLD messages, older messages
are compressed into summaries. Recent messages (last RECENT_LIMIT) are
always kept verbatim. Summaries are chained: each new summary
incorporates the previous summary + newly aged-out messages.

This ensures conversations of any length maintain coherent context
without exceeding the LLM's context window.
"""

import logging
import os
from typing import Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Summarization prompt — designed for factual compression, not creative writing
# ---------------------------------------------------------------------------
SUMMARIZE_PROMPT = """\
You are summarizing a conversation between a user and an AI script-writing assistant.

Your summary must preserve:
1. ALL specific requests the user made (topics, clients, styles, tones)
2. ALL scripts or content that was generated (key themes, not full text)
3. ALL modifications the user requested (and how the output changed)
4. ANY preferences the user expressed (tone preferences, length, style)
5. The current state — what is the latest version of any output?

Previous summary (if any):
{previous_summary}

New messages to incorporate:
{messages_text}

Write a detailed, factual summary in 300-500 words.
Focus on WHAT happened, not how you feel about it.
Do NOT include phrases like "the user asked" — just state the facts.
Output the summary text only, no headers or formatting."""


class ConversationSummarizer:
    """
    Implements progressive summarization for long-term memory.
    
    Algorithm:
    1. Check if conversation has more than SUMMARY_THRESHOLD messages
    2. If yes, identify messages older than the RECENT_LIMIT window
    3. Load the most recent existing summary (if any)
    4. Feed old summary + new-to-summarize messages into the LLM
    5. Store the new summary with the time range it covers
    6. The ContextAssembler then uses: summary + recent messages
    
    Summaries are append-only — we never delete old summaries, just
    create new ones that supersede them. This provides an audit trail.
    """

    SUMMARY_THRESHOLD = 30   # Start summarizing after this many messages
    RECENT_LIMIT = 15        # Always keep this many recent messages verbatim

    def __init__(self, supabase_client: Client = None):
        self._client = supabase_client or create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY"),
        )

    async def should_summarize(self, conversation_id: str) -> bool:
        """
        Check if a conversation has enough messages to warrant summarization.
        Returns True if message_count > SUMMARY_THRESHOLD.
        """
        try:
            res = (
                self._client.table("conversations")
                .select("message_count")
                .eq("id", conversation_id)
                .limit(1)
                .execute()
            )
            if res.data:
                count = res.data[0].get("message_count", 0)
                return count > self.SUMMARY_THRESHOLD
            return False
        except Exception as e:
            logger.warning(f"[Summarizer] Failed to check message count: {e}")
            return False

    async def get_latest_summary(self, conversation_id: str) -> Optional[dict]:
        """
        Get the most recent summary for a conversation.
        Returns dict with keys: summary, message_range_end, messages_covered
        or None if no summary exists.
        """
        try:
            res = (
                self._client.table("conversation_summaries")
                .select("summary, message_range_start, message_range_end, messages_covered")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]
            return None
        except Exception as e:
            logger.warning(f"[Summarizer] Failed to get latest summary: {e}")
            return None

    async def summarize(self, conversation_id: str) -> Optional[str]:
        """
        Run progressive summarization for a conversation.
        
        1. Gets existing summary (if any)
        2. Loads unsummarized messages (between last summary end and recent window)
        3. Generates new summary via LLM
        4. Stores the summary in conversation_summaries table
        
        Returns the new summary text, or None on failure.
        """
        try:
            # 1. Get all messages in chronological order
            all_messages_res = (
                self._client.table("messages")
                .select("id, role, content, message_type, created_at")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=False)
                .execute()
            )
            all_messages = all_messages_res.data or []

            if len(all_messages) <= self.SUMMARY_THRESHOLD:
                return None

            # 2. Split: messages to summarize vs recent messages to keep
            messages_to_summarize = all_messages[:-self.RECENT_LIMIT]
            
            if not messages_to_summarize:
                return None

            # 3. Get existing summary 
            existing_summary = await self.get_latest_summary(conversation_id)
            previous_summary_text = ""
            
            if existing_summary:
                previous_summary_text = existing_summary["summary"]
                # Only summarize messages newer than the last summary
                last_summary_end = existing_summary["message_range_end"]
                messages_to_summarize = [
                    m for m in messages_to_summarize
                    if m["created_at"] > last_summary_end
                ]

            if not messages_to_summarize:
                logger.debug("[Summarizer] No new messages to summarize")
                return previous_summary_text or None

            # 4. Format messages for the summarization prompt
            formatted_messages = []
            for msg in messages_to_summarize:
                role_label = "USER" if msg["role"] == "user" else "ASSISTANT"
                # Truncate very long messages (scripts) to save tokens
                content = msg["content"]
                if len(content) > 1000:
                    content = content[:800] + "\n... [truncated] ..."
                formatted_messages.append(f"[{role_label}]: {content}")

            messages_text = "\n\n".join(formatted_messages)

            # 5. Call LLM to generate summary
            prompt = SUMMARIZE_PROMPT.format(
                previous_summary=previous_summary_text or "(No previous summary — this is the first one)",
                messages_text=messages_text,
            )

            from pipeline.llm_client import generate_text
            summary_text = await generate_text("CRITIC", [prompt])

            if not summary_text or not summary_text.strip():
                logger.warning("[Summarizer] LLM returned empty summary")
                return None

            summary_text = summary_text.strip()

            # 6. Store the new summary
            range_start = messages_to_summarize[0]["created_at"]
            range_end = messages_to_summarize[-1]["created_at"]

            self._client.table("conversation_summaries").insert({
                "conversation_id": conversation_id,
                "summary": summary_text,
                "message_range_start": range_start,
                "message_range_end": range_end,
                "messages_covered": len(messages_to_summarize),
            }).execute()

            logger.info(
                f"[Summarizer] Created summary for conversation {conversation_id} "
                f"({len(messages_to_summarize)} messages compressed)"
            )
            return summary_text

        except Exception as e:
            logger.error(f"[Summarizer] Summarization failed: {e}")
            return None
