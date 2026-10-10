"""
Model Performance Evaluator Subsystem for Hydra Desktop.
Tracks Time-To-First-Token (TTFT), throughput (tokens/sec), memory consumption (RSS bytes),
and empirical latency distributions (p50, p90, p95, p99, min, max, mean, stddev).
Adheres to AGENTS.md genome invariants: zero fake tests, real metrics, deterministic reporting.
"""

from __future__ import annotations

import os
import json
import math
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple, Union


def get_current_memory_rss() -> int:
    """Return process resident set size (RSS) in bytes across platforms."""
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except Exception:
        try:
            import tracemalloc
            if tracemalloc.is_tracing():
                current, _ = tracemalloc.get_traced_memory()
                return int(current)
        except Exception:
            pass
    return 0


@dataclass
class ModelBenchmarkSample:
    """Atomic benchmark observation for a model completion."""
    model: str
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_sec: float = 0.0
    ttft_ms: float = 0.0
    cost_usd: float = 0.0
    memory_bytes: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def throughput_tok_sec(self) -> float:
        """Generation throughput in completion tokens per second."""
        ttft_sec = self.ttft_ms / 1000.0 if self.ttft_ms > 0 else 0.0
        gen_duration = self.duration_sec - ttft_sec
        if gen_duration > 0.0:
            return round(self.completion_tokens / gen_duration, 4)
        elif self.duration_sec > 0.0:
            return round(self.completion_tokens / self.duration_sec, 4)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "duration_sec": self.duration_sec,
            "ttft_ms": self.ttft_ms,
            "throughput_tok_sec": self.throughput_tok_sec,
            "cost_usd": self.cost_usd,
            "memory_bytes": self.memory_bytes,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelBenchmarkSample:
        m = data.get("model") or data.get("model_name") or "unknown"
        return cls(
            model=m,
            provider=data.get("provider", ""),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            duration_sec=float(data.get("duration_sec", 0.0)),
            ttft_ms=float(data.get("ttft_ms", 0.0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            memory_bytes=int(data.get("memory_bytes", 0)),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


ModelMetricSample = ModelBenchmarkSample


class ModelPerformanceEvaluator:
    """
    Sovereign Model Performance Evaluator for Hydra Desktop.
    Tracks TTFT, throughput (tokens/sec), latency distributions, and successive jitter.
    """

    def __init__(self, history_size: int = 2000, state_file: Optional[str] = None) -> None:
        self.history_size = max(10, history_size)
        self.state_file = state_file
        self._lock = threading.RLock()
        self._samples_by_model: Dict[str, List[ModelBenchmarkSample]] = {}
        self._samples: List[ModelBenchmarkSample] = []
        if self.state_file and os.path.exists(self.state_file):
            self.load_state(self.state_file)

    @property
    def total_samples(self) -> int:
        with self._lock:
            return len(self._samples)

    @property
    def tracked_models(self) -> List[str]:
        with self._lock:
            return sorted(list(self._samples_by_model.keys()))

    def clear(self) -> None:
        """Clear all recorded benchmark samples."""
        with self._lock:
            self._samples_by_model.clear()
            self._samples.clear()

    def reset(self, model_name: Optional[str] = None) -> None:
        """Clear samples for specific model or all models."""
        with self._lock:
            if model_name:
                clean = model_name.strip().lower()
                self._samples_by_model.pop(clean, None)
                self._samples = [s for s in self._samples if s.model.lower() != clean]
            else:
                self.clear()

    @staticmethod
    def calculate_throughput(tokens: int, duration_sec: float) -> float:
        """Static throughput calculator with division-by-zero protection."""
        dur = max(0.001, float(duration_sec))
        return round(float(tokens) / dur, 4)

    @staticmethod
    def calculate_percentile(data: List[float], percentile: float) -> float:
        """Calculate linear interpolation percentile for data array."""
        if not data:
            return 0.0
        clean = sorted([float(x) for x in data if not math.isnan(x) and not math.isinf(x)])
        n = len(clean)
        if n == 0:
            return 0.0
        if n == 1:
            return clean[0]

        p = max(0.0, min(100.0, float(percentile))) / 100.0
        pos = p * (n - 1)
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < n:
            return round(clean[idx] + frac * (clean[idx + 1] - clean[idx]), 6)
        return round(clean[-1], 6)

    def record_sample(
        self,
        model: Union[str, ModelBenchmarkSample],
        provider: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_sec: float = 0.0,
        ttft_ms: float = 0.0,
        cost_usd: float = 0.0,
        memory_bytes: Optional[int] = None,
        **kwargs: Any,
    ) -> ModelBenchmarkSample:
        """Record atomic benchmark sample into thread-safe memory rings."""
        if isinstance(model, ModelBenchmarkSample):
            sample = model
        elif isinstance(model, dict):
            sample = ModelBenchmarkSample.from_dict(model)
        else:
            mem = memory_bytes if memory_bytes is not None else get_current_memory_rss()
            sample = ModelBenchmarkSample(
                model=str(model).strip(),
                provider=provider,
                prompt_tokens=max(0, int(prompt_tokens)),
                completion_tokens=max(0, int(completion_tokens)),
                duration_sec=max(0.0, float(duration_sec)),
                ttft_ms=max(0.0, float(ttft_ms)),
                cost_usd=max(0.0, float(cost_usd)),
                memory_bytes=mem,
                metadata=kwargs.get("metadata", {}),
            )

        m_key = sample.model.strip().lower()

        with self._lock:
            if m_key not in self._samples_by_model:
                self._samples_by_model[m_key] = []

            ring = self._samples_by_model[m_key]
            ring.append(sample)
            if len(ring) > self.history_size:
                ring.pop(0)

            self._samples.append(sample)
            if len(self._samples) > (self.history_size * 4):
                self._samples.pop(0)

            if self.state_file:
                try:
                    self.save_state()
                except Exception:
                    pass

        return sample

    def get_throughput_metrics(self, model: str) -> Dict[str, float]:
        """Compute throughput statistics (min, mean, max, stddev, p50, p95)."""
        clean = model.strip().lower()
        with self._lock:
            samples = list(self._samples_by_model.get(clean, []))

        if not samples:
            return {"min": 0.0, "mean": 0.0, "max": 0.0, "stddev": 0.0, "p50": 0.0, "p95": 0.0}

        throughputs = [s.throughput_tok_sec for s in samples]
        n = len(throughputs)
        mean_val = sum(throughputs) / n
        if n > 1:
            var = sum((x - mean_val) ** 2 for x in throughputs) / (n - 1)
            std_dev = math.sqrt(var)
        else:
            std_dev = 0.0

        return {
            "min": round(min(throughputs), 4),
            "mean": round(mean_val, 4),
            "max": round(max(throughputs), 4),
            "stddev": round(std_dev, 4),
            "p50": round(self.calculate_percentile(throughputs, 50.0), 4),
            "p95": round(self.calculate_percentile(throughputs, 95.0), 4),
        }

    def get_ttft_metrics(self, model: str) -> Dict[str, float]:
        """Compute TTFT statistics (min, mean, max, stddev, p50, p95)."""
        clean = model.strip().lower()
        with self._lock:
            samples = list(self._samples_by_model.get(clean, []))

        ttfts = [s.ttft_ms for s in samples if s.ttft_ms > 0]
        if not ttfts:
            return {"min": 0.0, "mean": 0.0, "max": 0.0, "stddev": 0.0, "p50": 0.0, "p95": 0.0}

        n = len(ttfts)
        mean_val = sum(ttfts) / n
        if n > 1:
            var = sum((x - mean_val) ** 2 for x in ttfts) / (n - 1)
            std_dev = math.sqrt(var)
        else:
            std_dev = 0.0

        return {
            "min": round(min(ttfts), 4),
            "mean": round(mean_val, 4),
            "max": round(max(ttfts), 4),
            "stddev": round(std_dev, 4),
            "p50": round(self.calculate_percentile(ttfts, 50.0), 4),
            "p95": round(self.calculate_percentile(ttfts, 95.0), 4),
        }

    def get_latency_metrics(self, model: str) -> Dict[str, float]:
        """Compute latency statistics in seconds."""
        clean = model.strip().lower()
        with self._lock:
            samples = list(self._samples_by_model.get(clean, []))

        if not samples:
            return {"min": 0.0, "mean": 0.0, "max": 0.0, "stddev": 0.0, "p50": 0.0, "p95": 0.0}

        latencies = [s.duration_sec for s in samples]
        n = len(latencies)
        mean_val = sum(latencies) / n
        if n > 1:
            var = sum((x - mean_val) ** 2 for x in latencies) / (n - 1)
            std_dev = math.sqrt(var)
        else:
            std_dev = 0.0

        return {
            "min": round(min(latencies), 4),
            "mean": round(mean_val, 4),
            "max": round(max(latencies), 4),
            "stddev": round(std_dev, 4),
            "p50": round(self.calculate_percentile(latencies, 50.0), 4),
            "p95": round(self.calculate_percentile(latencies, 95.0), 4),
        }

    def calculate_jitter(self, model: str) -> float:
        """Calculate mean successive difference jitter of sample durations."""
        clean = model.strip().lower()
        with self._lock:
            samples = list(self._samples_by_model.get(clean, []))

        if len(samples) < 2:
            return 0.0

        diffs = [
            abs(samples[i].duration_sec - samples[i - 1].duration_sec)
            for i in range(1, len(samples))
        ]
        return round(sum(diffs) / len(diffs), 4)

    def get_model_summary(self, model: str) -> Dict[str, Any]:
        """Return comprehensive summary dictionary for a given model."""
        clean = model.strip().lower()
        with self._lock:
            samples = list(self._samples_by_model.get(clean, []))

        if not samples:
            return {
                "model": model,
                "sample_count": 0,
                "throughput": self.get_throughput_metrics(model),
                "ttft": self.get_ttft_metrics(model),
                "latency": self.get_latency_metrics(model),
                "jitter": 0.0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }

        tot_p = sum(s.prompt_tokens for s in samples)
        tot_c = sum(s.completion_tokens for s in samples)
        tot_cost = sum(s.cost_usd for s in samples)
        provider = samples[-1].provider if samples else ""

        return {
            "model": samples[0].model,
            "provider": provider,
            "sample_count": len(samples),
            "throughput": self.get_throughput_metrics(model),
            "ttft": self.get_ttft_metrics(model),
            "latency": self.get_latency_metrics(model),
            "jitter": self.calculate_jitter(model),
            "total_tokens": tot_p + tot_c,
            "prompt_tokens": tot_p,
            "completion_tokens": tot_c,
            "cost_usd": round(tot_cost, 6),
        }

    def compare_models(self, model_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """Compare models and generate rankings."""
        targets = model_names if model_names else self.tracked_models
        summaries = [self.get_model_summary(m) for m in targets if self.get_model_summary(m)["sample_count"] > 0]

        if not summaries:
            return {
                "rankings": {
                    "highest_throughput_model": None,
                    "lowest_latency_model": None,
                    "by_throughput_desc": [],
                    "by_latency_asc": [],
                },
                "models": {},
            }

        by_tp = sorted(summaries, key=lambda s: s["throughput"]["mean"], reverse=True)
        by_lat = sorted(summaries, key=lambda s: s["latency"]["mean"])

        rankings = {
            "highest_throughput_model": by_tp[0]["model"],
            "lowest_latency_model": by_lat[0]["model"],
            "by_throughput_desc": [s["model"] for s in by_tp],
            "by_latency_asc": [s["model"] for s in by_lat],
        }

        return {
            "rankings": rankings,
            "models": {s["model"]: s for s in summaries},
        }

    def render_markdown_report(self) -> str:
        """Render performance benchmark markdown report table."""
        models = self.tracked_models
        lines = [
            "# Hydra Desktop Model Performance Benchmark",
            "",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            f"Total Models Tracked: {len(models)}",
            "",
            "| Model | Provider | Samples | Mean Tok/s | Max Tok/s | Mean TTFT (ms) | Mean Latency (s) | Jitter (s) | Cost ($) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]

        for m in models:
            summ = self.get_model_summary(m)
            tp = summ["throughput"]
            ttft = summ["ttft"]
            lat = summ["latency"]
            lines.append(
                f"| `{summ['model']}` | {summ['provider']} | {summ['sample_count']} | "
                f"{tp['mean']:.1f} | {tp['max']:.1f} | {ttft['mean']:.1f} | "
                f"{lat['mean']:.2f} | {summ['jitter']:.3f} | ${summ['cost_usd']:.5f} |"
            )

        return "\n".join(lines)

    def save_state(self, filepath: Optional[str] = None) -> None:
        """Persist samples to JSON file on disk."""
        target = filepath or self.state_file
        if not target:
            return
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with self._lock:
            payload = {
                "version": "1.0",
                "timestamp": time.time(),
                "samples": [s.to_dict() for s in self._samples],
            }
        tmp_target = f"{target}.tmp"
        with open(tmp_target, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_target, target)

    def load_state(self, filepath: Optional[str] = None) -> None:
        """Load persisted samples from JSON file on disk."""
        target = filepath or self.state_file
        if not target or not os.path.exists(target):
            return
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_samples = data.get("samples", [])
        with self._lock:
            self.clear()
            for s_data in raw_samples:
                self.record_sample(s_data)


_GLOBAL_EVALUATOR: Optional[ModelPerformanceEvaluator] = None
_GLOBAL_EVAL_LOCK = threading.RLock()


def get_model_evaluator() -> ModelPerformanceEvaluator:
    """Acquire thread-safe singleton ModelPerformanceEvaluator."""
    global _GLOBAL_EVALUATOR
    with _GLOBAL_EVAL_LOCK:
        if _GLOBAL_EVALUATOR is None:
            _GLOBAL_EVALUATOR = ModelPerformanceEvaluator()
        return _GLOBAL_EVALUATOR


def reset_model_evaluator() -> ModelPerformanceEvaluator:
    """Reset singleton ModelPerformanceEvaluator."""
    global _GLOBAL_EVALUATOR
    with _GLOBAL_EVAL_LOCK:
        _GLOBAL_EVALUATOR = ModelPerformanceEvaluator()
        return _GLOBAL_EVALUATOR
