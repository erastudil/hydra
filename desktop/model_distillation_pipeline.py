"""
Hydra Desktop Model Distillation Pipeline Subsystem.
Provides teacher-student soft target cross-entropy loss, temperature scaling,
distillation alpha weighting, dynamic pruning schedules, and sparsity tracking.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


class PruningScheduleType(str, Enum):
    """Supported weight pruning sparsity trajectory schedules."""
    LINEAR = "linear"
    POLYNOMIAL = "polynomial"
    EXPONENTIAL = "exponential"
    COSINE = "cosine"


def stable_softmax(logits: Sequence[float], temperature: float = 1.0) -> List[float]:
    """Compute numerically stable softmax probability distribution with temperature scaling."""
    t = max(1e-4, temperature)
    scaled = [x / t for x in logits]
    max_val = max(scaled)
    exps = [math.exp(x - max_val) for x in scaled]
    sum_exps = sum(exps)
    if sum_exps <= 0.0:
        return [1.0 / len(logits)] * len(logits)
    return [e / sum_exps for e in exps]


def compute_cross_entropy(probabilities: Sequence[float], target_index: int) -> float:
    """Compute hard cross-entropy loss against ground truth class index."""
    if target_index < 0 or target_index >= len(probabilities):
        raise IndexError(f"Target index {target_index} out of bounds")
    prob = max(1e-12, probabilities[target_index])
    return -math.log(prob)


def compute_soft_cross_entropy(
    student_probs: Sequence[float],
    teacher_probs: Sequence[float],
) -> float:
    """Compute soft target cross-entropy loss between student and teacher distributions."""
    if len(student_probs) != len(teacher_probs):
        raise ValueError("Student and teacher probability distributions dimension mismatch")
    loss = 0.0
    for p_t, p_s in zip(teacher_probs, student_probs):
        safe_ps = max(1e-12, p_s)
        loss -= p_t * math.log(safe_ps)
    return loss


def compute_kl_divergence(
    student_probs: Sequence[float],
    teacher_probs: Sequence[float],
) -> float:
    """Compute Kullback-Leibler divergence D_KL(P_teacher || P_student)."""
    if len(student_probs) != len(teacher_probs):
        raise ValueError("Student and teacher probability distributions dimension mismatch")
    kl = 0.0
    for p_t, p_s in zip(teacher_probs, student_probs):
        if p_t > 0.0:
            safe_ps = max(1e-12, p_s)
            kl += p_t * math.log(p_t / safe_ps)
    return max(0.0, kl)


@dataclass
class DistillationStepResult:
    """Telemetry record for single knowledge distillation optimization step."""
    step_index: int
    soft_loss: float
    hard_loss: float
    total_loss: float
    temperature: float
    alpha: float
    current_sparsity: float
    student_top_class: int
    teacher_top_class: int
    ground_truth_class: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize step result to dictionary representation."""
        return {
            "step_index": self.step_index,
            "soft_loss": round(self.soft_loss, 6),
            "hard_loss": round(self.hard_loss, 6),
            "total_loss": round(self.total_loss, 6),
            "temperature": round(self.temperature, 3),
            "alpha": round(self.alpha, 4),
            "current_sparsity": round(self.current_sparsity, 4),
            "student_top_class": self.student_top_class,
            "teacher_top_class": self.teacher_top_class,
            "ground_truth_class": self.ground_truth_class,
            "metadata": dict(self.metadata),
        }


class PruningScheduler:
    """Weight magnitude pruning and dynamic sparsity scheduler."""

    def __init__(
        self,
        initial_sparsity: float = 0.0,
        target_sparsity: float = 0.5,
        total_steps: int = 100,
        schedule_type: PruningScheduleType = PruningScheduleType.POLYNOMIAL,
        exponent: float = 3.0,
    ) -> None:
        self.initial_sparsity = max(0.0, min(0.99, initial_sparsity))
        self.target_sparsity = max(self.initial_sparsity, min(0.99, target_sparsity))
        self.total_steps = max(1, total_steps)
        self.schedule_type = schedule_type
        self.exponent = max(1.0, exponent)

    def get_sparsity(self, step: int) -> float:
        """Calculate targeted parameter sparsity ratio for given step index."""
        if step <= 0:
            return self.initial_sparsity
        if step >= self.total_steps:
            return self.target_sparsity

        progress = float(step) / float(self.total_steps)

        if self.schedule_type == PruningScheduleType.LINEAR:
            return self.initial_sparsity + (self.target_sparsity - self.initial_sparsity) * progress
        elif self.schedule_type == PruningScheduleType.POLYNOMIAL:
            # Polynomial acceleration schedule S_t = S_i + (S_f - S_i) * (t/T)^k
            delta = self.target_sparsity - self.initial_sparsity
            return self.initial_sparsity + delta * (progress ** self.exponent)
        elif self.schedule_type == PruningScheduleType.EXPONENTIAL:
            # Exponential decay towards target sparsity
            scale = 1.0 - math.exp(-3.0 * progress)
            return self.initial_sparsity + (self.target_sparsity - self.initial_sparsity) * scale
        elif self.schedule_type == PruningScheduleType.COSINE:
            # Cosine annealing schedule
            cos_factor = 0.5 * (1.0 - math.cos(math.pi * progress))
            return self.initial_sparsity + (self.target_sparsity - self.initial_sparsity) * cos_factor

        return self.initial_sparsity

    @staticmethod
    def prune_matrix(
        weights: Sequence[Sequence[float]],
        target_sparsity: float,
    ) -> Tuple[List[List[float]], float]:
        """Prune weight matrix by zeroing lowest magnitude parameters."""
        if not weights or not weights[0]:
            return [], 0.0

        flat_vals = [abs(val) for row in weights for val in row]
        total_params = len(flat_vals)
        if total_params == 0:
            return [], 0.0

        k = int(round(target_sparsity * total_params))
        k = max(0, min(total_params, k))

        if k == 0:
            pruned = [list(row) for row in weights]
            return pruned, 0.0

        sorted_vals = sorted(flat_vals)
        threshold = sorted_vals[k - 1]

        pruned_matrix: List[List[float]] = []
        zero_count = 0
        for row in weights:
            new_row: List[float] = []
            for val in row:
                if abs(val) <= threshold and zero_count < k:
                    new_row.append(0.0)
                    zero_count += 1
                else:
                    new_row.append(val)
            pruned_matrix.append(new_row)

        empirical_sparsity = float(zero_count) / float(total_params)
        return pruned_matrix, empirical_sparsity


class ModelDistillationPipeline:
    """
    Knowledge distillation pipeline orchestrator.
    Computes blended soft-target teacher loss and hard-target label loss,
    applies temperature scaling, adjusts distillation alpha, and tracks parameter sparsity.
    """

    def __init__(
        self,
        default_temperature: float = 2.0,
        default_alpha: float = 0.7,
        pruning_scheduler: Optional[PruningScheduler] = None,
    ) -> None:
        self.default_temperature = max(0.1, default_temperature)
        self.default_alpha = max(0.0, min(1.0, default_alpha))
        self.pruning_scheduler = pruning_scheduler or PruningScheduler()
        self._history: List[DistillationStepResult] = []
        self._lock = threading.RLock()

    def train_step(
        self,
        student_logits: Sequence[float],
        teacher_logits: Sequence[float],
        ground_truth_class: int,
        step_index: Optional[int] = None,
        temperature: Optional[float] = None,
        alpha: Optional[float] = None,
    ) -> DistillationStepResult:
        """Execute single distillation optimization step across student and teacher logits."""
        with self._lock:
            idx = step_index if step_index is not None else len(self._history)
            temp = temperature if temperature is not None else self.default_temperature
            weight_alpha = alpha if alpha is not None else self.default_alpha

            # Temperature-scaled soft probability distributions
            p_student_soft = stable_softmax(student_logits, temperature=temp)
            p_teacher_soft = stable_softmax(teacher_logits, temperature=temp)

            # Unscaled student probabilities for hard label loss
            p_student_hard = stable_softmax(student_logits, temperature=1.0)

            # Loss calculations
            soft_loss = compute_soft_cross_entropy(p_student_soft, p_teacher_soft)
            hard_loss = compute_cross_entropy(p_student_hard, ground_truth_class)

            # Hinton distillation loss formula: L = alpha * (T^2 * L_soft) + (1 - alpha) * L_hard
            scaled_soft_loss = (temp ** 2) * soft_loss
            total_loss = weight_alpha * scaled_soft_loss + (1.0 - weight_alpha) * hard_loss

            # Model predictions
            student_top = max(range(len(student_logits)), key=lambda i: student_logits[i])
            teacher_top = max(range(len(teacher_logits)), key=lambda i: teacher_logits[i])

            current_sparsity = self.pruning_scheduler.get_sparsity(idx)

            res = DistillationStepResult(
                step_index=idx,
                soft_loss=soft_loss,
                hard_loss=hard_loss,
                total_loss=total_loss,
                temperature=temp,
                alpha=weight_alpha,
                current_sparsity=current_sparsity,
                student_top_class=student_top,
                teacher_top_class=teacher_top,
                ground_truth_class=ground_truth_class,
            )

            self._history.append(res)
            return res

    def evaluate_model_loss(
        self,
        student_logits: Sequence[float],
        teacher_logits: Sequence[float],
        ground_truth_class: int,
        temperature: float = 2.0,
        alpha: float = 0.5,
    ) -> Tuple[float, float, float]:
        """Evaluate soft loss, hard loss, and combined total loss without recording history."""
        p_student_soft = stable_softmax(student_logits, temperature=temperature)
        p_teacher_soft = stable_softmax(teacher_logits, temperature=temperature)
        p_student_hard = stable_softmax(student_logits, temperature=1.0)

        soft_loss = compute_soft_cross_entropy(p_student_soft, p_teacher_soft)
        hard_loss = compute_cross_entropy(p_student_hard, ground_truth_class)
        scaled_soft = (temperature ** 2) * soft_loss
        total = alpha * scaled_soft + (1.0 - alpha) * hard_loss
        return soft_loss, hard_loss, total

    def prune_weights(
        self,
        weights: Sequence[Sequence[float]],
        step_index: int,
    ) -> Tuple[List[List[float]], float]:
        """Apply scheduled pruning mask to weights matrix based on step index."""
        target_sparsity = self.pruning_scheduler.get_sparsity(step_index)
        return PruningScheduler.prune_matrix(weights, target_sparsity)

    def get_history(self) -> List[DistillationStepResult]:
        """Retrieve chronological sequence of distillation step telemetry."""
        with self._lock:
            return list(self._history)

    def reset(self) -> None:
        """Clear distillation step history."""
        with self._lock:
            self._history.clear()


_GLOBAL_DISTILLATION_PIPELINE: Optional[ModelDistillationPipeline] = None
_GLOBAL_DISTILLATION_LOCK = threading.RLock()


def get_distillation_pipeline() -> ModelDistillationPipeline:
    """Acquire thread-safe singleton ModelDistillationPipeline."""
    global _GLOBAL_DISTILLATION_PIPELINE
    with _GLOBAL_DISTILLATION_LOCK:
        if _GLOBAL_DISTILLATION_PIPELINE is None:
            _GLOBAL_DISTILLATION_PIPELINE = ModelDistillationPipeline()
        return _GLOBAL_DISTILLATION_PIPELINE


def reset_distillation_pipeline() -> ModelDistillationPipeline:
    """Reset singleton ModelDistillationPipeline."""
    global _GLOBAL_DISTILLATION_PIPELINE
    with _GLOBAL_DISTILLATION_LOCK:
        _GLOBAL_DISTILLATION_PIPELINE = ModelDistillationPipeline()
        return _GLOBAL_DISTILLATION_PIPELINE
