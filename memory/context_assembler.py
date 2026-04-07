"""
context_assembler — Builds the LLM context from all memory tiers.

This is THE critical module. For every user message, it assembles:
1. Long-term memory (conversation summaries)
2. Short-term memory (last N messages verbatim)
3. Semantic memory (vector-similar past content)
4. Most recent script (if any, for edit context)

The assembled context is what enables natural language modifications
WITHOUT keyword detection — the LLM sees the full conversation and
naturally understands "make it more human" or "shorten this".
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from memory.conversation_manager import ConversationManager, Message
from memory.summarizer import ConversationSummarizer
from memory.vector_memory import VectorMemory

logger = logging.getLogger(__name__)


@dataclass
class AssembledContext:
    """The complete context passed to the LLM/orchestrator."""

    # Conversation metadata
    conversation_id: str = ""
    conversation_title: Optional[str] = None
    conversation_metadata: dict = field(default_factory=dict)

    # Memory tiers
    summaries: str = ""                          # Long-term: compressed history
    recent_messages: list[Message] = field(default_factory=list)  # Short-term: last N
    relevant_context: list[dict] = field(default_factory=list)    # Semantic: vector matches

    # Convenience
    last_script: Optional[str] = None            # Most recent assistant script
    has_prior_context: bool = False               # True if conversation has history
    total_context_tokens: int = 0                 # Token budget tracking

    @property
    def recent_messages_formatted(self) -> str:
        """Format recent messages as a readable conversation log."""
        lines = []
        for msg in self.recent_messages:
            role = "USER" if msg.role == "user" else "ASSISTANT"
            content = msg.content
            # Truncate very long scripts in context to save tokens
            if len(content) > 2000 and msg.role == "assistant":
                content = content[:1500] + "\n... [earlier output truncated for brevity] ..."
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)

    @property
    def relevant_context_formatted(self) -> str:
        """Format vector-retrieved context as reference snippets."""
        if not self.relevant_context:
            return ""
        snippets = []
        for ctx in self.relevant_context:
            content = ctx.get("content", "")
            if len(content) > 500:
                content = content[:400] + "..."
            snippets.append(
                f"[{ctx.get('content_type', 'unknown').upper()}] "
                f"(similarity: {ctx.get('similarity', 0):.2f}): {content}"
            )
        return "\n\n".join(snippets)


class ContextAssembler:
    """
    Assembles the complete context window for each LLM request.
    
    This replaces:
    - get_session() — no more in-memory sessions
    - detect_intent() — LLM infers from conversation history
    - LAST_SCRIPT lookup — retrieved from DB
    - Preference extraction — LLM reads preferences from history
    
    The context window budget is enforced to prevent exceeding
    the LLM's maximum input size.
    """

    RECENT_MESSAGE_LIMIT = 15           # Max recent messages to include
    MAX_CONTEXT_TOKENS = 25_000         # Hard ceiling for total context
    VECTOR_SEARCH_LIMIT = 3             # Max semantic matches to include
    VECTOR_SEARCH_THRESHOLD = 0.55      # Minimum similarity for matches

    def __init__(
        self,
        conversation_manager: ConversationManager,
        summarizer: ConversationSummarizer,
        vector_memory: VectorMemory,
    ):
        self._conv_mgr = conversation_manager
        self._summarizer = summarizer
        self._vector_mem = vector_memory

    async def assemble(
        self,
        conversation_id: str,
        current_prompt: str,
        include_vector_memory: bool = True,
    ) -> AssembledContext:
        """
        Build the complete context for an LLM request.
        
        Steps:
        1. Load conversation metadata
        2. Get conversation summaries (long-term memory)
        3. Get recent messages (short-term memory)
        4. Search for semantically relevant past content (vector memory)
        5. Find the most recent script output
        6. Enforce token budget
        
        Returns an AssembledContext with all tiers populated.
        """
        ctx = AssembledContext(conversation_id=conversation_id)

        # -- Step 1: Conversation metadata ---------------------------------
        conv = await self._conv_mgr.get_conversation(conversation_id)
        if conv:
            ctx.conversation_title = conv.title
            ctx.conversation_metadata = conv.metadata
            ctx.has_prior_context = conv.message_count > 0

        # -- Step 2: Long-term memory (summaries) --------------------------
        try:
            summary_data = await self._summarizer.get_latest_summary(conversation_id)
            if summary_data:
                ctx.summaries = summary_data.get("summary", "")
                logger.debug(
                    f"[ContextAssembler] Loaded summary "
                    f"({summary_data.get('messages_covered', 0)} msgs covered)"
                )
        except Exception as e:
            logger.warning(f"[ContextAssembler] Failed to load summaries: {e}")

        # -- Step 3: Short-term memory (recent messages) -------------------
        try:
            ctx.recent_messages = await self._conv_mgr.get_recent_messages(
                conversation_id, limit=self.RECENT_MESSAGE_LIMIT
            )
            logger.debug(
                f"[ContextAssembler] Loaded {len(ctx.recent_messages)} recent messages"
            )
        except Exception as e:
            logger.warning(f"[ContextAssembler] Failed to load recent messages: {e}")

        # -- Step 4: Semantic memory (vector search) -----------------------
        if include_vector_memory and current_prompt:
            try:
                ctx.relevant_context = await self._vector_mem.search_relevant(
                    query=current_prompt,
                    conversation_id=conversation_id,
                    limit=self.VECTOR_SEARCH_LIMIT,
                    threshold=self.VECTOR_SEARCH_THRESHOLD,
                )
                if ctx.relevant_context:
                    logger.debug(
                        f"[ContextAssembler] Found {len(ctx.relevant_context)} "
                        f"vector matches"
                    )
            except Exception as e:
                logger.warning(f"[ContextAssembler] Vector search failed: {e}")

        # -- Step 5: Find the most recent script ---------------------------
        try:
            ctx.last_script = await self._conv_mgr.get_last_assistant_script(
                conversation_id
            )
            if ctx.last_script:
                logger.debug("[ContextAssembler] Found existing script in history")
        except Exception as e:
            logger.warning(f"[ContextAssembler] Failed to get last script: {e}")

        # -- Step 6: Token budget enforcement ------------------------------
        ctx.total_context_tokens = self._estimate_total_tokens(ctx)
        if ctx.total_context_tokens > self.MAX_CONTEXT_TOKENS:
            self._trim_context(ctx)
            logger.info(
                f"[ContextAssembler] Trimmed context from "
                f"{ctx.total_context_tokens} to ~{self.MAX_CONTEXT_TOKENS} tokens"
            )

        logger.info(
            f"[ContextAssembler] Assembled context for conv={conversation_id}: "
            f"summary={'yes' if ctx.summaries else 'no'}, "
            f"messages={len(ctx.recent_messages)}, "
            f"vector_matches={len(ctx.relevant_context)}, "
            f"has_script={'yes' if ctx.last_script else 'no'}, "
            f"tokens≈{ctx.total_context_tokens}"
        )

        return ctx

    def _estimate_total_tokens(self, ctx: AssembledContext) -> int:
        """Rough token estimate for all context components."""
        total = 0
        total += len(ctx.summaries) // 4
        for msg in ctx.recent_messages:
            total += len(msg.content) // 4
        for match in ctx.relevant_context:
            total += len(match.get("content", "")) // 4
        if ctx.last_script:
            total += len(ctx.last_script) // 4
        return total

    def _trim_context(self, ctx: AssembledContext) -> None:
        """
        Trim context to fit within token budget.
        
        Priority (highest to lowest):
        1. Last script (never trimmed — essential for edits)
        2. Recent messages (trim from oldest)
        3. Summaries (truncated if needed)
        4. Vector matches (removed first)
        """
        # Remove vector matches first — they're supplementary
        while (
            ctx.relevant_context
            and self._estimate_total_tokens(ctx) > self.MAX_CONTEXT_TOKENS
        ):
            ctx.relevant_context.pop()

        # Remove oldest recent messages
        while (
            len(ctx.recent_messages) > 4  # Keep at minimum 4 recent messages
            and self._estimate_total_tokens(ctx) > self.MAX_CONTEXT_TOKENS
        ):
            ctx.recent_messages.pop(0)

        # Truncate summary as last resort
        if self._estimate_total_tokens(ctx) > self.MAX_CONTEXT_TOKENS:
            max_summary_chars = (self.MAX_CONTEXT_TOKENS - self._estimate_total_tokens(
                AssembledContext(
                    recent_messages=ctx.recent_messages,
                    relevant_context=ctx.relevant_context,
                    last_script=ctx.last_script,
                )
            )) * 4
            if max_summary_chars > 200:
                ctx.summaries = ctx.summaries[:max_summary_chars]
            else:
                ctx.summaries = ""

        ctx.total_context_tokens = self._estimate_total_tokens(ctx)
