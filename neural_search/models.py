"""Dataclasses for neural-guided formula search."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class NeuralSearchConfig:
    seed: int = 42
    max_formula_len: int = 8
    min_formula_len: int = 2
    warmup_steps: int = 2
    policy_steps: int = 3
    batch_size: int = 4
    samples_per_step: int = 4
    learning_rate: float = 1e-3
    entropy_coef: float = 0.01
    value_coef: float = 0.1
    max_complexity: int = 24
    max_lookback: int = 10
    checkpoint_every: int = 1
    resume_checkpoint: str | None = None
    device: str = "cpu"
    factor_transform: str = "raw"
    enable_gate: bool = True
    top_k: int = 5
    composite_method: str = "rank_average"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicySample:
    tokens: list[int]
    names: list[str]
    log_prob: float
    entropy: float
    valid: bool
    reason: str
    complexity: int
    lookback: int
    source: str = "neural"
    generation: int = 0
    parent_hashes: list[str] = field(default_factory=list)
    formula_hash: str | None = None
    training_log_prob: Any = None
    training_entropy: Any = None
    training_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "names": self.names,
            "log_prob": float(self.log_prob),
            "entropy": float(self.entropy),
            "valid": bool(self.valid),
            "reason": self.reason,
            "complexity": int(self.complexity),
            "lookback": int(self.lookback),
            "source": self.source,
            "generation": int(self.generation),
            "parent_hashes": list(self.parent_hashes),
            "formula_hash": self.formula_hash,
        }


@dataclass(frozen=True)
class NeuralTrainingStep:
    step: int
    phase: str
    loss: float
    avg_reward: float
    best_reward: float
    valid_rate: float
    unique_rate: float
    stable_rank: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NeuralSearchCheckpointInfo:
    path: str
    step: int
    phase: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NeuralSearchResult:
    search_id: str
    config: dict[str, Any]
    training_history: list[dict[str, Any]]
    candidates_evaluated: int
    approved_factor_ids: list[str]
    composite_factor_id: str | None
    best_formulas: list[dict[str, Any]]
    checkpoint_paths: list[str]
    paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
