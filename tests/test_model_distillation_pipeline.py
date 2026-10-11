"""
Unit tests for Hydra Desktop Model Distillation Pipeline Subsystem.
Verifies teacher-student soft target cross-entropy loss, temperature scaling,
distillation alpha weighting, dynamic pruning schedules, and sparsity tracking.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import math
import pytest
from desktop.model_distillation_pipeline import (
    DistillationStepResult,
    ModelDistillationPipeline,
    PruningScheduleType,
    PruningScheduler,
    compute_cross_entropy,
    compute_kl_divergence,
    compute_soft_cross_entropy,
    get_distillation_pipeline,
    reset_distillation_pipeline,
    stable_softmax,
)


def test_stable_softmax_and_temperature_scaling():
    logits = [2.0, 1.0, 0.1]

    # Standard softmax (T=1.0)
    p_std = stable_softmax(logits, temperature=1.0)
    assert len(p_std) == 3
    assert sum(p_std) == pytest.approx(1.0)
    assert p_std[0] > p_std[1] > p_std[2]

    # High temperature (smoothing)
    p_high = stable_softmax(logits, temperature=10.0)
    assert sum(p_high) == pytest.approx(1.0)
    # Differences between probabilities are compressed at high temperature
    assert abs(p_high[0] - p_high[2]) < abs(p_std[0] - p_std[2])

    # Extreme logits numerical stability check
    huge_logits = [1000.0, 1001.0, 999.0]
    p_huge = stable_softmax(huge_logits, temperature=1.0)
    assert sum(p_huge) == pytest.approx(1.0)
    assert not any(math.isnan(x) for x in p_huge)


def test_cross_entropy_and_kl_divergence():
    p_student = [0.7, 0.2, 0.1]
    p_teacher = [0.8, 0.15, 0.05]

    # Hard cross-entropy loss
    hard_loss = compute_cross_entropy(p_student, target_index=0)
    assert hard_loss == pytest.approx(-math.log(0.7))

    # Soft cross-entropy loss
    soft_loss = compute_soft_cross_entropy(p_student, p_teacher)
    assert soft_loss > 0.0

    # KL divergence
    kl = compute_kl_divergence(p_student, p_teacher)
    assert kl >= 0.0

    # Identical distributions yield 0 KL
    assert compute_kl_divergence(p_teacher, p_teacher) == pytest.approx(0.0)


def test_pruning_scheduler_trajectories():
    total_steps = 100

    # Linear
    sched_linear = PruningScheduler(
        initial_sparsity=0.0,
        target_sparsity=0.5,
        total_steps=total_steps,
        schedule_type=PruningScheduleType.LINEAR,
    )
    assert sched_linear.get_sparsity(0) == 0.0
    assert sched_linear.get_sparsity(50) == pytest.approx(0.25)
    assert sched_linear.get_sparsity(100) == pytest.approx(0.5)

    # Polynomial
    sched_poly = PruningScheduler(
        initial_sparsity=0.0,
        target_sparsity=0.6,
        total_steps=total_steps,
        schedule_type=PruningScheduleType.POLYNOMIAL,
        exponent=3.0,
    )
    assert sched_poly.get_sparsity(0) == 0.0
    assert sched_poly.get_sparsity(100) == pytest.approx(0.6)
    # Polynomial starts slower than linear
    assert sched_poly.get_sparsity(50) < 0.3


def test_pruning_matrix_magnitude():
    weights = [
        [0.1, -0.9, 0.4],
        [0.8, 0.05, -0.2],
    ]
    # Total 6 elements. 50% target sparsity means 3 smallest elements zeroed: 0.05, 0.1, -0.2
    pruned, sparsity = PruningScheduler.prune_matrix(weights, target_sparsity=0.5)
    assert sparsity == pytest.approx(0.5)

    flat_pruned = [val for row in pruned for val in row]
    assert flat_pruned.count(0.0) == 3
    assert -0.9 in flat_pruned
    assert 0.8 in flat_pruned
    assert 0.4 in flat_pruned


def test_distillation_pipeline_train_step():
    pipeline = ModelDistillationPipeline(
        default_temperature=3.0,
        default_alpha=0.6,
    )

    student_logits = [2.0, 1.0, 0.1]
    teacher_logits = [2.5, 0.8, 0.2]
    ground_truth = 0

    step = pipeline.train_step(student_logits, teacher_logits, ground_truth, step_index=1)
    assert step.step_index == 1
    assert step.temperature == 3.0
    assert step.alpha == 0.6
    assert step.total_loss > 0.0
    assert step.student_top_class == 0
    assert step.teacher_top_class == 0
    assert step.ground_truth_class == 0

    data = step.to_dict()
    assert data["step_index"] == 1
    assert "total_loss" in data

    hist = pipeline.get_history()
    assert len(hist) == 1

    pipeline.reset()
    assert len(pipeline.get_history()) == 0


def test_singleton_pipeline_and_reset():
    p1 = get_distillation_pipeline()
    p2 = get_distillation_pipeline()
    assert p1 is p2

    p3 = reset_distillation_pipeline()
    assert p3 is not p1
    assert get_distillation_pipeline() is p3
