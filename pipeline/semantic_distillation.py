# import logging
# from typing import List, Dict

# logger = logging.getLogger(__name__)


# class SemanticDistillationEngine:
#     """
#     Receives retrieved RAG chunks and prepares them
#     for semantic influence processing.
#     """

#     async def process(
#         self,
#         retrieved_chunks: List[Dict],
#         creativity_ratio: float = 0.5,
#     ) -> List[Dict]:

#         logger.info(
#             f"[SIE] Received {len(retrieved_chunks)} chunks "
#             f"(creativity_ratio={creativity_ratio})"
#         )

#         # For now: pass through unchanged
#         return retrieved_chunks

# import logging
# from typing import List, Dict

# logger = logging.getLogger(__name__)


# class SemanticDistillationEngine:

#     async def process(
#         self,
#         retrieved_chunks: List[Dict],
#         creativity_ratio: float = 0.5,
#     ) -> List[Dict]:

#         logger.info("=" * 50)
#         logger.info("[SIE] SEMANTIC DISTILLATION STARTED")
#         logger.info(f"[SIE] Chunk count: {len(retrieved_chunks)}")
#         logger.info(f"[SIE] Creativity ratio: {creativity_ratio}")

#         if retrieved_chunks:
#             logger.info(
#                 f"[SIE] First chunk preview: "
#                 f"{retrieved_chunks[0].get('content', '')[:120]}"
#             )

#         logger.info("[SIE] Returning chunks unchanged")
#         logger.info("=" * 50)

#         return retrieved_chunks




import logging
import math
import re
from typing import List, Dict, Any

from anthropic import AsyncAnthropic
import os

logger = logging.getLogger(__name__)


class SemanticDistillationEngine:
    """
    Semantic Influence Engine (SIE)

    Pipeline:
    Retrieved Chunks
        ↓
    Semantic Proportional Compression
        ↓
    Claude Semantic Extraction
        ↓
    Semantic Inspiration Output
    """

    def __init__(self):

        self.anthropic = AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )

        self.model = "claude-sonnet-4-6"

    async def process(
        self,
        retrieved_chunks: List[Dict],
        creativity_ratio: float = 0.5,
    ) -> Dict[str, Any]:

        logger.info("=" * 60)
        logger.info("[SIE] SEMANTIC DISTILLATION STARTED")
        logger.info(f"[SIE] Chunk count: {len(retrieved_chunks)}")
        logger.info(f"[SIE] Creativity ratio: {creativity_ratio}")

        if not retrieved_chunks:
            logger.warning("[SIE] No retrieved chunks received")

            return {
                "semantic_inspiration": None,
                "compressed_chunks": [],
                "preservation_ratio": 0.0,
            }

        # ---------------------------------------------------------
        # 1. Compute preservation ratio
        # ---------------------------------------------------------

        preservation_ratio = max(
            0.0,
            min(1.0, 1.0 - creativity_ratio)
        )

        logger.info(
            f"[SIE] Preservation ratio: "
            f"{preservation_ratio:.2f}"
        )

        # ---------------------------------------------------------
        # 2. Compress retrieved chunks proportionally
        # ---------------------------------------------------------

        compressed_chunks = []

        for idx, chunk in enumerate(retrieved_chunks):

            content = chunk.get("content", "")

            compressed_text = self._compress_semantics(
                content=content,
                preservation_ratio=preservation_ratio
            )

            compressed_chunk = {
                **chunk,
                "compressed_content": compressed_text,
                "original_word_count": len(content.split()),
                "compressed_word_count": len(compressed_text.split())
            }

            compressed_chunks.append(compressed_chunk)

            logger.info(
                f"[SIE] Chunk {idx + 1}: "
                f"{compressed_chunk['original_word_count']} → "
                f"{compressed_chunk['compressed_word_count']} words"
            )

        # ---------------------------------------------------------
        # 3. Merge compressed semantic material
        # ---------------------------------------------------------

        merged_semantic_material = "\n\n".join([
            chunk["compressed_content"]
            for chunk in compressed_chunks
            if chunk["compressed_content"].strip()
        ])

        logger.info(
            f"[SIE] Total compressed semantic material length: "
            f"{len(merged_semantic_material)} chars"
        )

        # ---------------------------------------------------------
        # 4. Generate semantic inspiration using Claude
        # ---------------------------------------------------------

        semantic_inspiration = await self._generate_semantic_inspiration(
            compressed_semantic_material=merged_semantic_material,
            preservation_ratio=preservation_ratio,
            creativity_ratio=creativity_ratio
        )

        logger.info("[SIE] Semantic inspiration generated")

        logger.info("=" * 60)

        # ---------------------------------------------------------
        # 5. Return structured semantic influence package
        # ---------------------------------------------------------

        return {
            "semantic_inspiration": semantic_inspiration,
            "compressed_chunks": compressed_chunks,
            "preservation_ratio": preservation_ratio,
            "creativity_ratio": creativity_ratio,
        }

    # =============================================================
    # SEMANTIC COMPRESSION
    # =============================================================

    def _compress_semantics(
        self,
        content: str,
        preservation_ratio: float
    ) -> str:

        if not content.strip():
            return ""

        words = content.split()

        if preservation_ratio <= 0:
            preserve_count = 3
        else:
            preserve_count = max(
                3,
                math.ceil(len(words) * preservation_ratio)
            )

        # ---------------------------------------------------------
        # Simple semantic proportionality compression
        # ---------------------------------------------------------

        compressed_words = words[:preserve_count]

        compressed_text = " ".join(compressed_words)
        print(
            f"[SIE] {len(words)} → "
            f"{preserve_count} words preserved"
        )

        # Clean excessive spacing
        compressed_text = re.sub(r"\s+", " ", compressed_text).strip()

        return compressed_text

    # =============================================================
    # CLAUDE SEMANTIC EXTRACTION
    # =============================================================

    async def _generate_semantic_inspiration(
        self,
        compressed_semantic_material: str,
        preservation_ratio: float,
        creativity_ratio: float
    ) -> Dict[str, Any]:

        if not compressed_semantic_material.strip():

            logger.warning(
                "[SIE] Empty semantic material for Claude extraction"
            )

            return {}

        prompt = f"""
You are a Semantic Influence Extraction Engine.

Your task is NOT to rewrite or summarize.

Your task is to extract:

- emotional tone
- storytelling structure
- pacing
- thematic essence
- themes
- narrative energy
- abstract concepts
- messaging inspiration

IMPORTANT RULES:

1. DO NOT preserve original phrasing.
2. DO NOT rewrite the original script.
3. DO NOT summarize literally.
4. Extract only abstract semantic inspiration.
5. Return highly creative semantic abstractions.
6. Think like extracting creative DNA.
7. Focus on inspiration vectors, not text preservation.

SEMANTIC PRESERVATION RATIO:
{preservation_ratio:.2f}

CREATIVITY RATIO:
{creativity_ratio:.2f}

COMPRESSED SEMANTIC MATERIAL:
\"\"\"
{compressed_semantic_material}
\"\"\"

Return STRICT JSON only in this format:

{{
  "emotional_tone": [],
  "storytelling_structure": [],
  "pacing": [],
  "thematic_essence": [],
  "themes": [],
  "narrative_energy": [],
  "abstract_concepts": [],
  "messaging_inspiration": []
}}
"""

        try:

            logger.info("[SIE] Sending semantic extraction to Claude")
            print("\n" + "=" * 60)
            print("[SIE] COMPRESSED SEMANTIC MATERIAL")
            print(compressed_semantic_material[:1500])
            print("=" * 60 + "\n")

            response = await self.anthropic.messages.create(
                model=self.model,
                max_tokens=1200,
                temperature=0.9,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            result_text = response.content[0].text.strip()
            print("\n" + "=" * 60)
            print("[SIE] CLAUDE SEMANTIC OUTPUT")
            print(result_text[:2000])
            print("=" * 60 + "\n")

            logger.info("[SIE] Claude semantic extraction completed")

            return {
                "raw_semantic_inspiration": result_text
            }

        except Exception as e:

            logger.exception(
                "[SIE] Claude semantic extraction failed"
            )

            return {
                "error": str(e)
            }