import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv

from src.llm_analysis.client import LLMClient
from src.llm_analysis.first_pass import run_first_pass

load_dotenv()
logging.basicConfig(level=logging.WARNING, format="%(message)s")

SAMPLE_SIZE = 16
WORKER_COUNTS = [1, 2, 4, 6]

_STATUS_RE = re.compile(r"LLM API returned (\d+)")
_original_chat_completion = LLMClient.chat_completion


class _StatusCounter(logging.Handler):

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.counts: Dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        match = _STATUS_RE.search(record.getMessage())
        if match:
            status = match.group(1)
            self.counts[status] = self.counts.get(status, 0) + 1


@dataclass
class BenchmarkResult:
    workers: int
    total_seconds: float = 0.0
    processed: int = 0
    errors: int = 0
    call_durations: List[float] = field(default_factory=list)
    retry_status_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def avg_call_seconds(self) -> float:
        return sum(self.call_durations) / len(self.call_durations) if self.call_durations else 0.0

    @property
    def filings_per_min(self) -> float:
        return (self.processed / self.total_seconds) * 60 if self.total_seconds > 0 else 0.0


def _run_one(workers: int) -> BenchmarkResult:
    result = BenchmarkResult(workers=workers)

    def _timed_chat_completion(self, system_prompt, user_message):
        start = time.monotonic()
        try:
            return _original_chat_completion(self, system_prompt, user_message)
        finally:
            result.call_durations.append(time.monotonic() - start)

    counter = _StatusCounter()
    client_logger = logging.getLogger("src.llm_analysis.client")
    client_logger.addHandler(counter)

    LLMClient.chat_completion = _timed_chat_completion
    try:
        started = time.monotonic()
        processed, errors = run_first_pass(
            limit=SAMPLE_SIZE, status="all", order="recent", workers=workers,
        )
        result.total_seconds = time.monotonic() - started
    finally:
        LLMClient.chat_completion = _original_chat_completion
        client_logger.removeHandler(counter)

    result.processed = processed
    result.errors = errors
    result.retry_status_counts = counter.counts
    return result


def main() -> None:
    print(f"Benchmarking LLM first pass: {SAMPLE_SIZE} filings x workers={WORKER_COUNTS} (real API calls)\n")
    results = []
    for workers in WORKER_COUNTS:
        print(f"--- workers={workers} ---")
        r = _run_one(workers)
        results.append(r)
        print(
            f"  time={r.total_seconds:.1f}s  rate={r.filings_per_min:.1f} filings/min  "
            f"errors={r.errors}  429s={r.retry_status_counts.get('429', 0)}  "
            f"avg_call={r.avg_call_seconds:.2f}s  other_retries={ {k: v for k, v in r.retry_status_counts.items() if k != '429'} }\n"
        )

    print("\n=== Summary ===")
    header = f"{'workers':>7} | {'time(s)':>8} | {'filings/min':>12} | {'errors':>6} | {'429s':>5} | {'avg s/call':>10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.workers:>7} | {r.total_seconds:>8.1f} | {r.filings_per_min:>12.1f} | "
            f"{r.errors:>6} | {r.retry_status_counts.get('429', 0):>5} | {r.avg_call_seconds:>10.2f}"
        )


if __name__ == "__main__":
    main()
