from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import logging
from typing import Any

from application.summary.fallback import FallbackSummaryGenerator
from application.summary.models import DailySummary, SummaryGeneration
from utils.time_utils import utc_now

logger = logging.getLogger(__name__)


class ManualSummaryService:
    """Generate a summary only when explicitly called by the UI/user."""

    def __init__(
        self,
        aggregator: Any,
        llm_client: Any,
        store: Any,
        fallback: FallbackSummaryGenerator | None = None,
        retry_count: int = 1,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.aggregator = aggregator
        self.llm_client = llm_client
        self.store = store
        self.fallback = fallback or FallbackSummaryGenerator()
        self.retry_count = max(retry_count, 0)
        self.clock = clock
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="daily-summary",
        )

    def generate_today(self) -> SummaryGeneration:
        """Perform one explicit generation request and persist its result."""

        daily_data = self.aggregator.build_today()
        generated_at = self.clock()
        summary: DailySummary | None = None
        last_error: Exception | None = None

        for _attempt in range(self.retry_count + 1):
            try:
                summary = DailySummary.from_dict(
                    self.llm_client.generate(daily_data)
                )
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Remote daily summary failed: %s", exc)

        if summary is None:
            summary = self.fallback.generate(daily_data)
            source = "fallback"
            warning = str(last_error) if last_error else "Unknown LLM error."
        else:
            source = getattr(self.llm_client, "source_name", "llm")
            warning = None

        generation = SummaryGeneration(
            target_date=generated_at.date(),
            generated_at=generated_at,
            source=source,
            summary=summary,
            warning=warning,
        )
        self.store.save(generation)
        return generation

    def generate_today_async(self) -> Future[SummaryGeneration]:
        """Run a manual request off the Runtime and future UI threads."""

        return self._executor.submit(self.generate_today)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
