"""
conversation_manager — CRUD operations for conversations and messages.

All database writes are idempotent. All reads use indexed queries.
This module is the single source of truth for conversation state,
replacing _sessions, LAST_SCRIPT, and _script_cache.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Any
from dataclasses import dataclass, field

from supabase import create_client, Client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes — lightweight, serializable representations
# ---------------------------------------------------------------------------

@dataclass
class Conversation:
    id: str
    title: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0
    is_archived: bool = False


@dataclass
class Message:
    id: str
    conversation_id: str
    chat_id: Optional[str] = None
    user_id: Optional[str] = None
    role: str = "user"
    content: str = ""
    message_type: str = "text"
    metadata: dict = field(default_factory=dict)
    token_count: int = 0
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# ConversationManager
# ---------------------------------------------------------------------------

class ConversationManager:
    """
    Handles all database operations for conversations and messages.
    
    Design principles:
    - All reads use indexed columns (conversation_id + created_at)
    - Writes are idempotent where possible (UUIDs, upserts)
    - No in-memory caching — every call goes to Supabase
    - Thread-safe: Supabase client handles connection pooling
    """

    # A default UUID to use if none is provided or if an invalid one (like "anonymous") is passed.
    # This matches an existing user ID in the current development database.
    DEFAULT_USER_ID = "77ac3136-57fb-49e2-bddf-a095d77931f1"

    def __init__(self, supabase_client: Client = None):
        self._client = supabase_client or create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY"),
        )

    def _is_valid_uuid(self, val: str) -> bool:
        if not val:
            return False
        try:
            uuid.UUID(str(val))
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def get_or_create_conversation(
        self,
        conversation_id: Optional[str],
        metadata: dict = None,
    ) -> Conversation:
        """
        Get an existing conversation or create a new one.
        
        If conversation_id is provided and exists, returns it.
        If conversation_id is provided but doesn't exist, creates with that ID.
        If conversation_id is empty/None, creates a new conversation.
        """
        if conversation_id:
            existing = await self.get_conversation(conversation_id)
            if existing:
                return existing

        return await self._create_conversation(
            conversation_id=conversation_id,
            metadata=metadata or {},
        )

    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Fetch a conversation by ID. Returns None if not found."""
        try:
            res = (
                self._client.table("conversations")
                .select("*")
                .eq("id", conversation_id)
                .limit(1)
                .execute()
            )
            if res.data:
                return self._to_conversation(res.data[0])
            return None
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to get conversation {conversation_id}: {e}")
            return None

    async def _create_conversation(
        self,
        conversation_id: Optional[str] = None,
        metadata: dict = None,
    ) -> Conversation:
        """Create a new conversation row."""
        row: dict[str, Any] = {
            "metadata": metadata or {},
        }
        if conversation_id:
            row["id"] = conversation_id

        try:
            res = self._client.table("conversations").insert(row).execute()
            conv = self._to_conversation(res.data[0])
            logger.info(f"[ConversationManager] Created conversation {conv.id}")
            return conv
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to create conversation: {e}")
            raise

    async def update_conversation_title(
        self, conversation_id: str, title: str
    ) -> None:
        """Set the human-readable title for a conversation."""
        try:
            self._client.table("conversations").update(
                {"title": title}
            ).eq("id", conversation_id).execute()
        except Exception as e:
            logger.warning(f"[ConversationManager] Failed to update title: {e}")

    async def update_conversation_metadata(
        self, conversation_id: str, metadata: dict
    ) -> None:
        """Merge new metadata into existing conversation metadata."""
        try:
            existing = await self.get_conversation(conversation_id)
            if existing:
                merged = {**existing.metadata, **metadata}
                self._client.table("conversations").update(
                    {"metadata": merged}
                ).eq("id", conversation_id).execute()
        except Exception as e:
            logger.warning(f"[ConversationManager] Failed to update metadata: {e}")

    async def list_conversations(
        self,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[Conversation]:
        """List conversations ordered by most recent activity."""
        try:
            query = (
                self._client.table("conversations")
                .select("*")
                .order("last_message_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            if not include_archived:
                query = query.eq("is_archived", False)
            
            res = query.execute()
            return [self._to_conversation(row) for row in (res.data or [])]
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to list conversations: {e}")
            return []

    async def archive_conversation(self, conversation_id: str) -> bool:
        """Soft-delete a conversation by setting is_archived=True."""
        try:
            self._client.table("conversations").update(
                {"is_archived": True}
            ).eq("id", conversation_id).execute()
            logger.info(f"[ConversationManager] Archived conversation {conversation_id}")
            return True
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to archive: {e}")
            return False

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: dict = None,
        chat_id: str = None,
        user_id: str = None,
    ) -> Message:
        """
        Save a message to the messages table.
        
        Links to both conversation_id (new system) and chat_id (backward compat).
        The database trigger auto-updates conversation.message_count and timestamps.
        user_id is required by the existing schema — defaults to DEFAULT_USER_ID if 
        not provided or if an invalid UUID (like "anonymous" or "system") is passed.
        """
        # Ensure user_id is a valid UUID or fallback to default
        if not self._is_valid_uuid(user_id):
            user_id = self.DEFAULT_USER_ID

        row = {
            "conversation_id": conversation_id,
            "chat_id": chat_id or conversation_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "message_type": message_type,
            "metadata": metadata or {},
            "token_count": self._estimate_tokens(content),
        }

        try:
            res = self._client.table("messages").insert(row).execute()
            msg = self._to_message(res.data[0])
            logger.debug(
                f"[ConversationManager] Saved {role} message "
                f"(conv={conversation_id}, type={message_type})"
            )
            return msg
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to save message: {e}")
            raise

    async def get_recent_messages(
        self,
        conversation_id: str,
        limit: int = 15,
    ) -> list[Message]:
        """
        Get the most recent messages for a conversation.
        Returns in chronological order (oldest first).
        
        This is the core short-term memory query.
        Uses idx_messages_conversation_time index.
        """
        try:
            res = (
                self._client.table("messages")
                .select("id, conversation_id, chat_id, role, content, message_type, metadata, token_count, created_at")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            messages = [self._to_message(row) for row in (res.data or [])]
            messages.reverse()  # Chronological order
            return messages
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to get recent messages: {e}")
            return []

    async def get_last_assistant_script(
        self, conversation_id: str
    ) -> Optional[str]:
        """
        Get the most recent assistant message that contains a script.
        Looks for message_type 'script_generation' or 'script_edit',
        falling back to the most recent 'assistant' role message.
        
        This replaces LAST_SCRIPT and _script_cache.
        """
        try:
            # First try: look for explicitly typed script messages
            res = (
                self._client.table("messages")
                .select("content")
                .eq("conversation_id", conversation_id)
                .eq("role", "assistant")
                .in_("message_type", ["script_generation", "script_edit"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["content"]

            # Fallback: get the most recent assistant message of any type
            res = (
                self._client.table("messages")
                .select("content")
                .eq("conversation_id", conversation_id)
                .eq("role", "assistant")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["content"]

            return None
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to get last script: {e}")
            return None

    async def get_message_count(self, conversation_id: str) -> int:
        """Get total message count for a conversation."""
        try:
            conv = await self.get_conversation(conversation_id)
            return conv.message_count if conv else 0
        except Exception:
            return 0

    async def get_messages_in_range(
        self,
        conversation_id: str,
        start_time: str,
        end_time: str,
    ) -> list[Message]:
        """Get messages within a time range. Used by the summarizer."""
        try:
            res = (
                self._client.table("messages")
                .select("id, conversation_id, chat_id, role, content, message_type, metadata, token_count, created_at")
                .eq("conversation_id", conversation_id)
                .gte("created_at", start_time)
                .lte("created_at", end_time)
                .order("created_at", desc=False)
                .execute()
            )
            return [self._to_message(row) for row in (res.data or [])]
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to get messages in range: {e}")
            return []

    # ------------------------------------------------------------------
    # Research Briefs (replaces _research_cache)
    # ------------------------------------------------------------------

    async def save_research_brief(
        self, short_id: str, data: dict, metadata: dict = None
    ) -> None:
        """Store a research brief in the database."""
        try:
            self._client.table("research_briefs").upsert({
                "short_id": short_id,
                "data": data,
                "metadata": metadata or {},
            }).execute()
            logger.info(f"[ConversationManager] Saved research brief {short_id}")
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to save research brief: {e}")

    async def get_research_brief(self, short_id: str) -> Optional[dict]:
        """Retrieve a research brief by its short ID."""
        try:
            res = (
                self._client.table("research_briefs")
                .select("data")
                .eq("short_id", short_id)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]["data"]
            return None
        except Exception as e:
            logger.error(f"[ConversationManager] Failed to get research brief: {e}")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token for English text."""
        return len(text) // 4 if text else 0

    @staticmethod
    def _to_conversation(row: dict) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row.get("title"),
            metadata=row.get("metadata", {}),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            message_count=row.get("message_count", 0),
            is_archived=row.get("is_archived", False),
        )

    @staticmethod
    def _to_message(row: dict) -> Message:
        return Message(
            id=row["id"],
            conversation_id=row.get("conversation_id", ""),
            chat_id=row.get("chat_id"),
            user_id=row.get("user_id"),
            role=row.get("role", "user"),
            content=row.get("content", ""),
            message_type=row.get("message_type", "text"),
            metadata=row.get("metadata", {}),
            token_count=row.get("token_count", 0),
            created_at=row.get("created_at"),
        )
