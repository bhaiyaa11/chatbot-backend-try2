import hashlib, json, time, logging
from typing import Optional

logger = logging.getLogger(__name__)


class PipelineCache:
    """
    In-memory cache for LLM responses within a single request lifecycle.
    This is a legitimate optimization — NOT conversation state.
    
    Conversation state is handled by memory.ConversationManager.
    """

    def __init__(self, ttl_seconds: int = 86400):
        self._store: dict = {}
        self._ttl = ttl_seconds

    def _make_key(self, stage: str, contents: list) -> str:
        """Hash stage + string contents into a cache key."""
        serializable = []
        for c in contents:
            if isinstance(c, str):
                serializable.append(c)
            else:
                serializable.append(f"<{type(c).__name__}>")

        payload = json.dumps({"stage": stage, "contents": serializable}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, stage: str, contents: list) -> Optional[dict]:
        # Cache currently disabled — returns None always
        return None

    def set(self, stage: str, contents: list, data: dict):
        # Cache currently disabled
        pass

    def clear(self):
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


# Singleton used across the app
cache = PipelineCache(ttl_seconds=86400)