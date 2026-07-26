"""
MandiIQ — Real-time pipeline latency & throughput metrics.

Provides a thread-safe singleton PipelineMetrics object that records
per-step durations, row counts, API response times, failure rates,
and the last N pipeline run summaries.

All counters are lock-guarded for safe concurrent access from
background ingestion threads and the metrics-exposing API thread.

Usage:
    from mandi_rdd.core.metrics import pipeline_metrics

    with pipeline_metrics.step("fetch_prices"):
        records = fetch_all_prices(...)
        pipeline_metrics.record_rows("fetch_prices", len(records))

    pipeline_metrics.record_api_call("data.gov.in", 0.342, True)
    print(pipeline_metrics.to_prometheus())
"""

import json
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Optional

logger = logging.getLogger(__name__)


class _StepTimer:
    """Context manager that records step duration on exit."""

    def __init__(self, metrics: "PipelineMetrics", step_name: str):
        self._metrics = metrics
        self._step = step_name
        self._start: Optional[float] = None

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.monotonic() - self._start if self._start else 0.0
        success = exc_type is None
        self._metrics._record_step(self._step, duration, success)


class PipelineMetrics:
    """Thread-safe pipeline metrics collector.

    Thread safety: all public write methods acquire ``_lock`` before
    mutating shared state. The ``to_prometheus()`` read method also
    acquires the lock for a consistent snapshot.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # ── Step-level histograms (list of durations per step name) ──
        self._step_durations: dict[str, list[float]] = defaultdict(list)

        # ── Step-level Prometheus histogram buckets ──
        self._step_histogram: dict[str, list[int]] = defaultdict(
            lambda: [0] * len(self._step_buckets)
        )
        self._step_histogram_sum: dict[str, float] = defaultdict(float)

        # ── Step-level counters ──
        self._step_success: dict[str, int] = defaultdict(int)
        self._step_failure: dict[str, int] = defaultdict(int)

        # ── Row-level counters ──
        self._rows_fetched: dict[str, int] = defaultdict(int)
        self._rows_new: dict[str, int] = defaultdict(int)

        # ── API call tracking ──
        self._api_calls: dict[str, list[float]] = defaultdict(list)
        self._api_success: dict[str, int] = defaultdict(int)
        self._api_failure: dict[str, int] = defaultdict(int)

        # ── Pipeline run summaries (ring buffer, last 20 runs) ──
        self._run_summaries: deque[dict] = deque(maxlen=20)

        # ── Global counters ──
        self._pipeline_runs = 0
        self._pipeline_success = 0
        self._pipeline_failure = 0

    # Default histogram bucket boundaries in seconds (cumulative, +Inf always appended)
    _step_buckets: list[float] = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, float("inf")]

    # ── Public API ──

    def step(self, step_name: str) -> _StepTimer:
        """Context manager that times a pipeline step.

        Usage:
            with pipeline_metrics.step("fetch_prices"):
                do_work()
        """
        return _StepTimer(self, step_name)

    def record_rows(self, step_name: str, fetched: int, new: Optional[int] = None) -> None:
        """Record row counts for a pipeline step."""
        with self._lock:
            self._rows_fetched[step_name] += fetched
            if new is not None:
                self._rows_new[step_name] += new

    def record_api_call(self, endpoint: str, seconds: float, success: bool) -> None:
        """Record an outbound API call duration and outcome."""
        with self._lock:
            self._api_calls[endpoint].append(seconds)
            if success:
                self._api_success[endpoint] += 1
            else:
                self._api_failure[endpoint] += 1

    def record_pipeline_run(self, summary: dict) -> None:
        """Record a completed pipeline run summary."""
        with self._lock:
            self._pipeline_runs += 1
            if summary.get("status") == "ok":
                self._pipeline_success += 1
            else:
                self._pipeline_failure += 1
            self._run_summaries.append({
                "ts": time.time(),
                "duration_s": summary.get("duration_seconds"),
                "status": summary.get("status"),
                "steps": summary.get("steps", {}),
                "commodities": summary.get("commodities_analyzed", []),
            })

    def get_summary(self) -> dict:
        """Return a lightweight JSON-serializable summary of all metrics."""
        with self._lock:
            return {
                "pipeline_runs": self._pipeline_runs,
                "pipeline_success": self._pipeline_success,
                "pipeline_failure": self._pipeline_failure,
                "last_run": dict(self._run_summaries[-1]) if self._run_summaries else None,
                "step_count": len(self._step_durations),
            }

    # ── Prometheus export ──

    def to_prometheus(self) -> str:
        """Render all metrics as Prometheus text-format lines (no HELP/TYPE headers)."""
        with self._lock:
            lines: list[str] = []
            now = time.time()

            # ── Pipeline run counters ──
            lines.append("# HELP mandiiq_pipeline_runs_total Total pipeline ingestion runs.")
            lines.append("# TYPE mandiiq_pipeline_runs_total counter")
            lines.append(f"mandiiq_pipeline_runs_total {self._pipeline_runs}")
            lines.append("# HELP mandiiq_pipeline_success_total Successful pipeline runs.")
            lines.append("# TYPE mandiiq_pipeline_success_total counter")
            lines.append(f"mandiiq_pipeline_success_total {self._pipeline_success}")
            lines.append("# HELP mandiiq_pipeline_failure_total Failed pipeline runs.")
            lines.append("# TYPE mandiiq_pipeline_failure_total counter")
            lines.append(f"mandiiq_pipeline_failure_total {self._pipeline_failure}")

            # ── Step duration summary (mean, max, count per step) ──
            lines.append("# HELP mandiiq_step_duration_seconds Step execution time.")
            lines.append("# TYPE mandiiq_step_duration_seconds gauge")
            for step_name, durations in self._step_durations.items():
                if not durations:
                    continue
                _mean = sum(durations) / len(durations)
                _max = max(durations)
                _cnt = len(durations)
                _safe = step_name.replace(".", "_").replace("-", "_")
                lines.append(
                    f'mandiiq_step_duration_seconds{{step="{_safe}",quantile="mean"}} {_mean:.4f}'
                )
                lines.append(
                    f'mandiiq_step_duration_seconds{{step="{_safe}",quantile="max"}} {_max:.4f}'
                )
                lines.append(
                    f'mandiiq_step_duration_seconds{{step="{_safe}",quantile="count"}} {_cnt}'
                )

            # ── Step duration histograms (_bucket / _sum / _count) ──
            lines.append("# HELP mandiiq_step_duration_histogram_seconds Step execution time histogram.")
            lines.append("# TYPE mandiiq_step_duration_histogram_seconds histogram")
            for step_name in sorted(self._step_histogram.keys()):
                _safe = step_name.replace(".", "_").replace("-", "_")
                bucket_counts = self._step_histogram[step_name]
                _sum = self._step_histogram_sum.get(step_name, 0.0)
                _count = bucket_counts[-1]
                for i, le in enumerate(self._step_buckets):
                    le_str = "+Inf" if le == float("inf") else f"{le:.3f}"
                    lines.append(
                        f'mandiiq_step_duration_histogram_seconds_bucket{{step="{_safe}",le="{le_str}"}} {bucket_counts[i]}'
                    )
                lines.append(f'mandiiq_step_duration_histogram_seconds_sum{{step="{_safe}"}} {_sum:.4f}')
                lines.append(f'mandiiq_step_duration_histogram_seconds_count{{step="{_safe}"}} {_count}')

            # ── Step success / failure counters ──
            lines.append("# HELP mandiiq_step_outcome_total Step outcome (success/failure).")
            lines.append("# TYPE mandiiq_step_outcome_total counter")
            _all_steps = set(self._step_success.keys()) | set(self._step_failure.keys())
            for step_name in sorted(_all_steps):
                _safe = step_name.replace(".", "_").replace("-", "_")
                _ok = self._step_success.get(step_name, 0)
                _fail = self._step_failure.get(step_name, 0)
                lines.append(f'mandiiq_step_outcome_total{{step="{_safe}",result="success"}} {_ok}')
                lines.append(f'mandiiq_step_outcome_total{{step="{_safe}",result="failure"}} {_fail}')

            # ── Row counters per step ──
            lines.append("# HELP mandiiq_rows_total Rows fetched and new per step.")
            lines.append("# TYPE mandiiq_rows_total counter")
            for step_name in sorted(self._rows_fetched.keys()):
                _safe = step_name.replace(".", "_").replace("-", "_")
                _fetched = self._rows_fetched[step_name]
                _new = self._rows_new.get(step_name, 0)
                lines.append(f'mandiiq_rows_total{{step="{_safe}",kind="fetched"}} {_fetched}')
                lines.append(f'mandiiq_rows_total{{step="{_safe}",kind="new"}} {_new}')

            # ── API call duration ──
            lines.append("# HELP mandiiq_api_duration_seconds Outbound API call duration.")
            lines.append("# TYPE mandiiq_api_duration_seconds gauge")
            for endpoint, durations in self._api_calls.items():
                if not durations:
                    continue
                _mean = sum(durations) / len(durations)
                _max = max(durations)
                _cnt = len(durations)
                _safe = endpoint.replace(".", "_").replace("-", "_")
                lines.append(
                    f'mandiiq_api_duration_seconds{{endpoint="{_safe}",quantile="mean"}} {_mean:.4f}'
                )
                lines.append(
                    f'mandiiq_api_duration_seconds{{endpoint="{_safe}",quantile="max"}} {_max:.4f}'
                )
                lines.append(
                    f'mandiiq_api_duration_seconds{{endpoint="{_safe}",quantile="count"}} {_cnt}'
                )

            # ── API call success/failure counters ──
            lines.append("# HELP mandiiq_api_calls_total Outbound API call count.")
            lines.append("# TYPE mandiiq_api_calls_total counter")
            _all_apis = set(self._api_success.keys()) | set(self._api_failure.keys())
            for endpoint in sorted(_all_apis):
                _safe = endpoint.replace(".", "_").replace("-", "_")
                _ok = self._api_success.get(endpoint, 0)
                _fail = self._api_failure.get(endpoint, 0)
                lines.append(
                    f'mandiiq_api_calls_total{{endpoint="{_safe}",result="success"}} {_ok}'
                )
                lines.append(
                    f'mandiiq_api_calls_total{{endpoint="{_safe}",result="failure"}} {_fail}'
                )

            # ── Pipeline run duration (latest) ──
            if self._run_summaries:
                _last = self._run_summaries[-1]
                _dur = _last.get("duration_s") or 0
                _age = now - _last.get("ts", now)
                lines.append("# HELP mandiiq_last_pipeline_duration_seconds Latest pipeline run duration.")
                lines.append("# TYPE mandiiq_last_pipeline_duration_seconds gauge")
                lines.append(f"mandiiq_last_pipeline_duration_seconds {_dur:.1f}")
                lines.append("# HELP mandiiq_last_pipeline_run_age_seconds Seconds since last pipeline run.")
                lines.append("# TYPE mandiiq_last_pipeline_run_age_seconds gauge")
                lines.append(f"mandiiq_last_pipeline_run_age_seconds {_age:.1f}")

            return "\n".join(lines)

    # ── Internal helpers ──

    def _record_step(self, step_name: str, duration: float, success: bool) -> None:
        """Thread-safe step recording (called by _StepTimer).

        Records the raw duration, outcome, and updates Prometheus histogram
        buckets for the step.
        """
        with self._lock:
            self._step_durations[step_name].append(duration)
            self._step_histogram_sum[step_name] += duration
            # Update cumulative histogram buckets
            bucket_counts = self._step_histogram[step_name]
            for i, le in enumerate(self._step_buckets):
                if duration <= le:
                    bucket_counts[i] += 1
            if success:
                self._step_success[step_name] += 1
            else:
                self._step_failure[step_name] += 1


# Module-level singleton — imported by scheduler and API module.
pipeline_metrics = PipelineMetrics()
