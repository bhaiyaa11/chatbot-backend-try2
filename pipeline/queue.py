import asyncio, logging, time
from typing import Callable, Any

logger = logging.getLogger(__name__)


class PipelineQueue:
    """
    Controls concurrent access to the LLM pipeline.
    - max_workers: pipelines running simultaneously
    - max_queue:   requests allowed to wait before rejecting
    """

    def __init__(self, max_workers: int = 5, max_queue: int = 20):
        self._semaphore = asyncio.Semaphore(max_workers)
        self._max_queue = max_queue
        self._queued = 0
        self._max_workers = max_workers

    @property
    def active_count(self) -> int:
        return self._max_workers - self._semaphore._value

    async def submit(self, fn: Callable, timeout: float = 180.0) -> Any:
        """
        Submit an async callable to the queue.
        Raises RuntimeError if at capacity or timed out.
        """
        if self._queued >= self._max_queue:
            logger.warning("[Queue] Rejected request — at max capacity")
            raise RuntimeError(
                "Server is at capacity. Please try again in a moment."
            )

        self._queued += 1
        wait_start = time.time()
        logger.info(f"[Queue] Queued. Active={self.active_count} Waiting={self._queued}")

        try:
            async with self._semaphore:
                self._queued -= 1
                wait_time = round(time.time() - wait_start, 2)
                logger.info(f"[Queue] Started after {wait_time}s wait")
                return await asyncio.wait_for(fn(), timeout=timeout)

        except asyncio.TimeoutError:
            self._queued = max(0, self._queued - 1)
            logger.error("[Queue] Request timed out")
            raise RuntimeError(
                "Request timed out. Try a shorter prompt or fewer files."
            )
        except Exception:
            self._queued = max(0, self._queued - 1)
            raise


# Singleton
pipeline_queue = PipelineQueue(
    max_workers=5,
    max_queue=20,
)