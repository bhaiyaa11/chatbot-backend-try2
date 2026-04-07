"""
memory — Persistent conversation memory layer.

Replaces all in-memory state (_sessions, LAST_SCRIPT, _script_cache)
with Supabase-backed persistent storage.

Modules:
    conversation_manager  — CRUD for conversations and messages
    context_assembler     — Builds LLM context from memory tiers
    summarizer            — Progressive summarization for long-term memory
    vector_memory         — Semantic retrieval via pgvector embeddings
"""

from memory.conversation_manager import ConversationManager
from memory.context_assembler import ContextAssembler
from memory.summarizer import ConversationSummarizer
from memory.vector_memory import VectorMemory

__all__ = [
    "ConversationManager",
    "ContextAssembler",
    "ConversationSummarizer",
    "VectorMemory",
]
