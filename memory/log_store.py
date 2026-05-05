from collections import deque
from threading import Lock
from datetime import datetime, timezone

MAX_LOGS = 10

_lock = Lock()
LOG_STORE: deque = deque(maxlen=MAX_LOGS)


def add_log(log: dict) -> None:
    entry = {**log, "log_time": datetime.now(timezone.utc).isoformat()}
    with _lock:
        LOG_STORE.append(entry)


def get_logs() -> list[dict]:
    with _lock:
        return list(LOG_STORE)