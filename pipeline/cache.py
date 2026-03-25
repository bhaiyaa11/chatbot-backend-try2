import hashlib, json, time, logging
from typing import Optional

logger = logging.getLogger(__name__)


class PipelineCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._store: dict = {}
        self._ttl = ttl_seconds

    def _make_key(self, stage: str, contents: list) -> str:
        """Hash stage + string contents into a cache key."""
        serializable = []
        for c in contents:
            if isinstance(c, str):
                serializable.append(c)
            else:
                # Part objects (images/video) — use type name as proxy
                # they are rarely identical so don't cache them deeply
                serializable.append(f"<{type(c).__name__}>")

        payload = json.dumps({"stage": stage, "contents": serializable}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, stage: str, contents: list) -> Optional[dict]:
        # key = self._make_key(stage, contents)
        # entry = self._store.get(key)

        # if not entry:
            return None

        # if time.time() - entry["ts"] > self._ttl:
        #     del self._store[key]
        #     logger.info(f"[Cache] Expired entry for stage={stage}")
        #     return None

        # logger.info(f"[Cache] HIT for stage={stage}")
        # return entry["data"]

    def set(self, stage: str, contents: list, data: dict):
        # key = self._make_key(stage, contents)
        # self._store[key] = {"data": data, "ts": time.time()}
        # logger.info(f"[Cache] SET for stage={stage}")
        pass
    def clear(self):
        self._store.clear()
    
    # ─────────────────────────────────────────────
# ✅ SCRIPT MEMORY (THIS IS WHAT YOU NEED)
# ─────────────────────────────────────────────

SCRIPT_STORE = {}

def save_script(script_id: str, content: str):
    SCRIPT_STORE[script_id] = content

def get_script(script_id: str):
    return SCRIPT_STORE.get(script_id)

    @property   
    def size(self) -> int:
        return len(self._store)


# Singleton used across the app
cache = PipelineCache(ttl_seconds=3600)