"""
vector_memory — Semantic retrieval via pgvector embeddings.

Stores embeddings for user prompts, generated scripts, and edit
instructions. Enables cross-conversation "have we done this before?"
queries and within-conversation "what was the context?" retrieval.

Uses the same text-embedding-004 model (768-dim) already used
by RAGRetrievalStage for consistency.
"""

import logging
import os
from typing import Optional

from supabase import create_client, Client
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


class VectorMemory:
    """
    Stores and retrieves semantically relevant past context using
    pgvector embeddings in Supabase.
    
    All operations are designed to be non-blocking and fault-tolerant.
    If embedding generation or search fails, the system degrades
    gracefully to recent-messages-only context.
    """

    EMBEDDING_MODEL = "text-embedding-004"
    DEFAULT_THRESHOLD = 0.55
    DEFAULT_LIMIT = 5

    def __init__(self, supabase_client: Client = None):
        self._client = supabase_client or create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY"),
        )
        self._genai_client = None  # Lazy-loaded

    def _get_genai_client(self):
        """Lazy-load the GenAI client to avoid import-time failures."""
        if self._genai_client is None:
            from config import get_genai_client
            self._genai_client = get_genai_client(location="us-central1")
        return self._genai_client

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate a 768-dim embedding for the given text.
        Returns empty list on failure (caller should handle gracefully).
        """
        if not text or not text.strip():
            return []

        try:
            client = self._get_genai_client()
            # Truncate to avoid token limits on embedding model
            truncated = text[:8000]
            resp = await client.aio.models.embed_content(
                model=self.EMBEDDING_MODEL,
                contents=truncated,
                config=genai_types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT"
                ),
            )
            return resp.embeddings[0].values
        except Exception as e:
            logger.warning(f"[VectorMemory] Embedding generation failed: {e}")
            return []

    async def store_embedding(
        self,
        message_id: str,
        conversation_id: str,
        content: str,
        content_type: str,
    ) -> None:
        """
        Generate and store an embedding for a message.
        
        content_type must be one of:
        - 'user_prompt'
        - 'generated_script'
        - 'edit_instruction'
        
        This is designed to run as a background task (fire-and-forget).
        """
        embedding = await self.generate_embedding(content)
        if not embedding:
            logger.warning(
                f"[VectorMemory] Skipping store — empty embedding "
                f"(msg={message_id}, type={content_type})"
            )
            return

        try:
            self._client.table("message_embeddings").insert({
                "message_id": message_id,
                "conversation_id": conversation_id,
                "content_type": content_type,
                "embedding": embedding,
            }).execute()
            logger.debug(
                f"[VectorMemory] Stored {content_type} embedding "
                f"(msg={message_id})"
            )
        except Exception as e:
            logger.warning(f"[VectorMemory] Failed to store embedding: {e}")

    async def search_relevant(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        limit: int = None,
        threshold: float = None,
    ) -> list[dict]:
        """
        Search for semantically relevant past messages/scripts.
        
        If conversation_id is provided, searches within that conversation only.
        If None, searches across all conversations (cross-conversation memory).
        
        Returns list of dicts with keys:
        - message_id, conversation_id, content_type, similarity, content
        """
        limit = limit or self.DEFAULT_LIMIT
        threshold = threshold or self.DEFAULT_THRESHOLD

        # Generate query embedding
        try:
            client = self._get_genai_client()
            resp = await client.aio.models.embed_content(
                model=self.EMBEDDING_MODEL,
                contents=query[:8000],
                config=genai_types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY"
                ),
            )
            query_embedding = resp.embeddings[0].values
        except Exception as e:
            logger.warning(f"[VectorMemory] Query embedding failed: {e}")
            return []

        # Call Supabase RPC
        try:
            rpc_params = {
                "query_embedding": query_embedding,
                "match_threshold": threshold,
                "match_count": limit,
            }
            if conversation_id:
                rpc_params["target_conversation_id"] = conversation_id

            res = self._client.rpc(
                "match_message_embeddings", rpc_params
            ).execute()

            matches = res.data or []
            if not matches:
                return []

            # Enrich with actual message content
            message_ids = [m["message_id"] for m in matches]
            content_res = (
                self._client.table("messages")
                .select("id, content, role, message_type")
                .in_("id", message_ids)
                .execute()
            )
            content_map = {
                row["id"]: row for row in (content_res.data or [])
            }

            enriched = []
            for match in matches:
                msg_data = content_map.get(match["message_id"], {})
                enriched.append({
                    "message_id": match["message_id"],
                    "conversation_id": match["conversation_id"],
                    "content_type": match["content_type"],
                    "similarity": match["similarity"],
                    "content": msg_data.get("content", ""),
                    "role": msg_data.get("role", ""),
                    "message_type": msg_data.get("message_type", ""),
                })

            logger.info(
                f"[VectorMemory] Found {len(enriched)} relevant matches "
                f"(conv={conversation_id or 'all'})"
            )
            return enriched

        except Exception as e:
            logger.warning(f"[VectorMemory] Search failed: {e}")
            return []
