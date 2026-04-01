import hashlib
import json
import logging
from typing import List, Dict, Optional
import asyncio

from google.genai import types
from supabase import create_client, Client
import os
from config import get_genai_client

logger = logging.getLogger(__name__)

# ==================================================
# CHUNKING CONFIG
# ==================================================

CHUNK_TYPES = ["hook", "cta", "framework", "insight", "body"]

class RAGProcessor:
    def __init__(self):
        self.supabase: Client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
        self.genai_client = get_genai_client(location="us-central1")
        self.embedding_model = "text-embedding-004"

    def _generate_hash(self, text: str) -> str:
        """SHA-256 hash for exact match deduplication."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get_embedding(self, text: str) -> List[float]:
        """Generate 768-dim embedding using Google GenAI."""
        try:
            response = await self.genai_client.aio.models.embed_content(
                model=self.embedding_model,
                contents=text,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
            )
            return response.embeddings[0].values
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return []

    async def is_duplicate(self, content_hash: str, embedding: List[float] = None) -> bool:
        """Check for exact (hash) or semantic (vector) duplicates."""
        # 1. Exact match check
        exact = self.supabase.table("scripts").select("id").eq("hash", content_hash).execute()
        if exact.data:
            return True

        # 2. Semantic matching check (Threshold > 0.95)
        if embedding:
            # We use an RPC 'match_chunks' if configured, or just skip for now
            # For this MVP, we'll stick to hash-based for full scripts
            pass
            
        return False

    async def process_and_ingest(self, script_data: Dict):
        """
        Main entry point for ingestion.
        script_data keys: content, client, business_unit, video_type, tone, chunks (List[Dict])
        """
        content = script_data.get("content", "")
        content_hash = self._generate_hash(content)

        if await self.is_duplicate(content_hash):
            logger.info(f"Skipping ingestion: Script already exists (hash={content_hash[:8]})")
            return None

        # 1. Insert into 'scripts' table
        script_row = {
            "content": content,
            "client": script_data.get("client"),
            "business_unit": script_data.get("business_unit"),
            "video_type": script_data.get("video_type"),
            "tone": script_data.get("tone"),
            "hash": content_hash,
            "metadata": script_data.get("metadata", {})
        }

        try:
            res = self.supabase.table("scripts").insert(script_row).execute()
            script_id = res.data[0]["id"]
            logger.info(f"Successfully ingested script metadata (id={script_id})")
        except Exception as e:
            logger.error(f"Failed to insert script: {e}")
            return None

        # 2. Process and insert chunks
        chunks = script_data.get("chunks", [])
        if not chunks:
            # Fallback: if no chunks provided, treat full script as one 'body' chunk
            chunks = [{"type": "body", "content": content}]

        chunk_insert_tasks = []
        for chunk in chunks:
            chunk_content = chunk.get("content", "")
            chunk_type = chunk.get("type", "body")
            
            if chunk_content:
                chunk_insert_tasks.append(self._process_single_chunk(script_id, chunk_type, chunk_content))

        if chunk_insert_tasks:
            await asyncio.gather(*chunk_insert_tasks)
            
        return script_id

    async def _process_single_chunk(self, script_id: str, chunk_type: str, content: str):
        """Internal helper to embed and save a single chunk."""
        embedding = await self.get_embedding(content)
        if not embedding:
            return

        chunk_row = {
            "script_id": script_id,
            "type": chunk_type,
            "content": content,
            "embedding": embedding,
            "metadata": {"length": len(content)}
        }

        try:
            self.supabase.table("script_chunks").insert(chunk_row).execute()
            logger.debug(f"Saved {chunk_type} chunk for script {script_id}")
        except Exception as e:
            logger.error(f"Failed to save chunk: {e}")
