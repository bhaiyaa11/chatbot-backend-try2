import logging
from typing import List, Dict, Optional
from pipeline.stages.base import BaseStage
from google.genai import types
from supabase import create_client, Client
import os
from config import get_genai_client

logger = logging.getLogger(__name__)

class RAGRetrievalStage(BaseStage):
    name = "RAG_RETRIEVAL"

    def __init__(self):
        self.supabase: Client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY")
        )
        self.genai_client = get_genai_client(location="us-central1")
        self.embedding_model = "text-embedding-004"

    async def execute(
        self,
        prompt: str,
        metadata: Dict = None,
        match_threshold: float = 0.5,
        match_count: int = 5
    ) -> List[Dict]:
        """
        Retrieves relevant script chunks from Supabase using vector search.
        Filters by client, business_unit, and video_type if provided in metadata.
        """
        logger.info(f"[RAGRetrieval] Embedding prompt: {prompt[:50]}...")
        
        # 1. Generate embedding for the user prompt
        try:
            resp = await self.genai_client.aio.models.embed_content(
                model=self.embedding_model,
                contents=prompt,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
            )
            query_embedding = resp.embeddings[0].values
        except Exception as e:
            logger.error(f"[RAGRetrieval] Embedding failed: {e}")
            return []

        # 2. Call Supabase RPC 'match_chunks'
        # Prefiltering happens inside the RPC based on metadata_filter
        metadata_filter = {
            "client": metadata.get("client") if metadata else None,
            "business_unit": metadata.get("business_unit") if metadata else None,
            "video_type": metadata.get("video_type") if metadata else None
        }
        
        # Remove None values
        metadata_filter = {k: v for k, v in metadata_filter.items() if v}

        try:
            rpc_res = self.supabase.rpc(
                "match_chunks",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": match_threshold,
                    "match_count": match_count,
                    "metadata_filter": metadata_filter
                }
            ).execute()
            
            chunks = rpc_res.data or []
            logger.info(f"[RAGRetrieval] Found {len(chunks)} relevant chunks")
            return chunks
            
        except Exception as e:
            logger.error(f"[RAGRetrieval] Vector search failed: {e}")
            return []
