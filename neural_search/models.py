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
    corpus_sequence_path: str | None = None
    matrix_cache_dir: str | None = None
    use_matrix_cache: bool = False
    use_batch_eval: bool = False
    batch_eval_output_dir: str | None = None
    batch_eval_chunk_size: int = 32
    batch_eval_device: str = "auto"
    use_eval_cache: bool = False
    eval_cache_dir: str | None = None

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


@dataclass(frozen=True)
class AlphaGPTPretrainConfig:
    sequence_path: str
    output_dir: str
    preference_path: str | None = None
    seed: int = 42
    epochs: int = 1
    batch_size: int = 16
    learning_rate: float = 1e-3
    max_sequences: int | None = None
    preference_steps: int = 0
    preference_margin: float = 0.1
    checkpoint_every: int = 1
    resume_checkpoint: str | None = None
    device: str = "auto"
    amp: bool = False
    distributed: bool = False
    world_size: int = 1
    rank: int = 0
    local_rank: int = 0
    backend: str = "gloo"
    master_addr: str = "127.0.0.1"
    master_port: str = "29500"
    ddp_init_method: str | None = None
    ddp_find_unused_parameters: bool = False
    resource_report_path: str | None = None
    strict_cuda: bool = False
    save_rank0_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaGPTPretrainEpoch:
    epoch: int
    phase: str
    loss: float
    token_accuracy: float
    sequences_seen: int
    preference_pairs_seen: int
    stable_rank: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PreferenceTrainingStep:
    step: int
    loss: float
    preferred_log_prob: float
    rejected_log_prob: float
    margin: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaGPTCheckpointManifest:
    latest_checkpoint_path: str | None
    checkpoints: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaGPTPretrainResult:
    created_at: str
    status: str
    config: dict[str, Any]
    history: list[dict[str, Any]]
    preference_history: list[dict[str, Any]]
    checkpoint_manifest: dict[str, Any]
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
