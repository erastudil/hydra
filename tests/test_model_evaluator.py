"""
Integration test suite for Hydra Desktop Model Performance Evaluator.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import concurrent.futures
import pytest

from desktop.model_evaluator import ModelPerformanceEvaluator, ModelBenchmarkSample


@pytest.fixture
def evaluator():
    ev = ModelPerformanceEvaluator()
    yield ev
    ev.clear()


def test_model_benchmark_sample_creation_and_throughput():
    """Verify sample attributes, generation throughput calculation, and dictionary export."""
    # 500 completion tokens in 5.0 seconds with 1.0s TTFT -> 500 / 4.0 = 125 tok/s
    s = ModelBenchmarkSample(
        model="glm 5.3 flash",
        provider="cheaperinference",
        prompt_tokens=150,
        completion_tokens=500,
        duration_sec=5.0,
        ttft_ms=1000.0,
        cost_usd=0.00025,
    )
    assert s.model == "glm 5.3 flash"
    assert s.provider == "cheaperinference"
    assert s.prompt_tokens == 150
    assert s.completion_tokens == 500
    assert s.total_tokens == 650
    assert s.throughput_tok_sec == 125.0
    assert s.cost_usd == 0.00025

    d = s.to_dict()
    assert d["model"] == "glm 5.3 flash"
    assert d["throughput_tok_sec"] == 125.0
    assert d["total_tokens"] == 650


def test_model_evaluator_percentiles_math(evaluator: ModelPerformanceEvaluator):
    """Verify linear interpolation math for percentiles against known sequences."""
    # Sequence [10.0, 20.0, 30.0, 40.0, 50.0]
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert evaluator.calculate_percentile(data, 0.0) == 10.0
    assert evaluator.calculate_percentile(data, 50.0) == 30.0
    assert evaluator.calculate_percentile(data, 100.0) == 50.0

    # Single element
    assert evaluator.calculate_percentile([42.0], 95.0) == 42.0

    # Empty list
    assert evaluator.calculate_percentile([], 50.0) == 0.0


def test_model_evaluator_tok_sec_throughput_distribution(evaluator: ModelPerformanceEvaluator):
    """Verify throughput statistics aggregation (min, mean, max, stddev)."""
    # 3 samples: 100 tok in 2s (50 tok/s), 200 tok in 2s (100 tok/s), 300 tok in 2s (150 tok/s)
    evaluator.record_sample("deepseek 4.1 flash", "openrouter", 50, 100, 2.0)
    evaluator.record_sample("deepseek 4.1 flash", "openrouter", 50, 200, 2.0)
    evaluator.record_sample("deepseek 4.1 flash", "openrouter", 50, 300, 2.0)

    tp = evaluator.get_throughput_metrics("deepseek 4.1 flash")
    assert tp["min"] == 50.0
    assert tp["mean"] == 100.0
    assert tp["max"] == 150.0
    assert tp["stddev"] == 50.0


def test_model_evaluator_ttft_and_jitter_metrics(evaluator: ModelPerformanceEvaluator):
    """Verify TTFT aggregation and successive difference jitter metrics."""
    evaluator.record_sample("mimo 2.6 flash", "cheaperinference", 100, 200, 1.0, ttft_ms=200.0)
    evaluator.record_sample("mimo 2.6 flash", "cheaperinference", 100, 200, 1.2, ttft_ms=250.0)
    evaluator.record_sample("mimo 2.6 flash", "cheaperinference", 100, 200, 1.1, ttft_ms=300.0)

    ttft = evaluator.get_ttft_metrics("mimo 2.6 flash")
    assert ttft["mean"] == 250.0
    assert ttft["p50"] == 250.0

    # Jitter: diff(1.2-1.0) = 0.2, diff(1.1-1.2) = 0.1 -> mean(0.2, 0.1) = 0.15
    jitter = evaluator.calculate_jitter("mimo 2.6 flash")
    assert jitter == 0.15


def test_model_evaluator_multi_model_comparison_and_rankings(evaluator: ModelPerformanceEvaluator):
    """Verify multi-model comparison, ranking generation, and winner selection."""
    # Model A: Fast generation throughput (200 tok/s, 1s duration)
    evaluator.record_sample("fast_model", "mock_prov", 50, 200, 1.0)

    # Model B: Moderate throughput (100 tok/s, 2s duration)
    evaluator.record_sample("steady_model", "mock_prov", 50, 200, 2.0)

    # Model C: Slow throughput (50 tok/s, 4s duration)
    evaluator.record_sample("deep_model", "mock_prov", 50, 200, 4.0)

    comp = evaluator.compare_models(["fast_model", "steady_model", "deep_model"])
    rankings = comp["rankings"]

    assert rankings["highest_throughput_model"] == "fast_model"
    assert rankings["lowest_latency_model"] == "fast_model"
    assert rankings["by_throughput_desc"] == ["fast_model", "steady_model", "deep_model"]
    assert rankings["by_latency_asc"] == ["fast_model", "steady_model", "deep_model"]


def test_model_evaluator_markdown_report_generation(evaluator: ModelPerformanceEvaluator):
    """Verify Markdown table generation with column headers and model rows."""
    evaluator.record_sample("sonnet 5.5", "anthropic", 100, 400, 2.5, ttft_ms=450.0)
    evaluator.record_sample("gpt-6.1", "openai", 100, 300, 1.8, ttft_ms=320.0)

    md = evaluator.render_markdown_report()
    assert "# Hydra Desktop Model Performance Benchmark" in md
    assert "| Model | Provider | Samples |" in md
    assert "`sonnet 5.5`" in md
    assert "`gpt-6.1`" in md
    assert "anthropic" in md
    assert "openai" in md


def test_model_evaluator_edge_cases_and_zero_protection(evaluator: ModelPerformanceEvaluator):
    """Verify division by zero safeguards and empty metric handling."""
    # Unrecorded model queries return default zero dicts
    assert evaluator.get_latency_metrics("non_existent")["mean"] == 0.0
    assert evaluator.get_throughput_metrics("non_existent")["mean"] == 0.0
    assert evaluator.get_ttft_metrics("non_existent")["mean"] == 0.0
    assert evaluator.calculate_jitter("non_existent") == 0.0

    # Static throughput with 0 duration protected
    tp_zero = ModelPerformanceEvaluator.calculate_throughput(tokens=100, duration_sec=0.0)
    assert tp_zero > 0.0

    # Summary of empty model
    summary = evaluator.get_model_summary("untracked")
    assert summary["sample_count"] == 0


def test_model_evaluator_concurrent_recording_thread_safety(evaluator: ModelPerformanceEvaluator):
    """Verify thread-safe concurrent sample recording without race conditions."""
    def worker(worker_id: int):
        model_name = f"model_{worker_id % 3}"
        for i in range(25):
            evaluator.record_sample(
                model=model_name,
                provider="concurrent_provider",
                prompt_tokens=50,
                completion_tokens=100,
                duration_sec=1.0 + (i * 0.01),
                ttft_ms=100.0,
            )
        return worker_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker, i) for i in range(6)]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() >= 0

    assert evaluator.total_samples == 6 * 25
    assert len(evaluator.tracked_models) == 3
