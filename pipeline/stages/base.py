
import time, logging
from abc import ABC, abstractmethod
from pipeline.contracts import StageResult

logger = logging.getLogger(__name__)


class BaseStage(ABC):
    name: str = "base"

    async def run(self, **kwargs) -> StageResult:
        start = time.monotonic()
        try:
            result = await self.execute(**kwargs)
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(f"[{self.name}] ✅ Completed in {duration_ms}ms")
            return StageResult(
                stage=self.name,
                success=True,
                data=result,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(f"[{self.name}] ❌ Failed in {duration_ms}ms: {e}")
            return StageResult(
                stage=self.name,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    @abstractmethod
    async def execute(self, **kwargs):
        ...