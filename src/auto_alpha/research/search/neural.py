"""Neural formula models, masks, rewards, datasets, sampling, training, and pretraining."""

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
    evaluation_output_dir: str | None = None
    evaluation_chunk_size: int = 32
    evaluation_device: str = "auto"
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

import random

import torch

from auto_alpha.research.formulas.operators import operator_arity
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB


def build_action_mask(prefix_tokens: list[int], max_formula_len: int, min_formula_len: int = 1) -> torch.Tensor:
    mask = torch.zeros(FORMULA_VOCAB.size, dtype=torch.bool)
    if len(prefix_tokens) >= max_formula_len:
        return mask
    depth = _stack_depth(prefix_tokens)
    remaining_after_next = max_formula_len - len(prefix_tokens) - 1
    if not prefix_tokens:
        mask[: FORMULA_VOCAB.feature_count] = True
        return mask
    if depth < 0:
        return mask
    if remaining_after_next <= 0:
        if depth == 1:
            _allow_unary(mask)
        if depth == 2:
            _allow_binary(mask)
        return mask
    if depth >= 0:
        mask[: FORMULA_VOCAB.feature_count] = True
    if depth >= 1:
        _allow_unary(mask)
    if depth >= 2:
        _allow_binary(mask)
    if len(prefix_tokens) + 1 < min_formula_len:
        mask[FORMULA_VOCAB.operator_offset :] = False
    return mask


def masked_sample(logits: torch.Tensor, mask: torch.Tensor, rng: random.Random | None = None) -> int:
    allowed = torch.nonzero(mask.to(dtype=torch.bool), as_tuple=False).flatten().tolist()
    if not allowed:
        raise ValueError("no available actions for current prefix")
    if rng is not None:
        values = torch.softmax(logits.detach().cpu()[allowed], dim=-1).tolist()
        total = sum(values)
        threshold = rng.random() * total
        running = 0.0
        for token, value in zip(allowed, values):
            running += value
            if running >= threshold:
                return int(token)
        return int(allowed[-1])
    masked = logits.detach().clone()
    masked[~mask.to(device=logits.device, dtype=torch.bool)] = -1e9
    return int(torch.multinomial(torch.softmax(masked, dim=-1), 1).item())


def explain_available_actions(prefix_tokens: list[int]) -> list[str]:
    mask = build_action_mask(prefix_tokens, max_formula_len=max(len(prefix_tokens) + 1, 2))
    return [FORMULA_VOCAB.token_name(token) for token in torch.nonzero(mask, as_tuple=False).flatten().tolist()]


def _stack_depth(tokens: list[int]) -> int:
    valid, _reason = StackVM().validate_with_reason(tokens)
    if valid:
        return 1
    depth = 0
    for token in tokens:
        token = int(token)
        if 0 <= token < FORMULA_VOCAB.feature_count:
            depth += 1
        elif FORMULA_VOCAB.operator_offset <= token < FORMULA_VOCAB.size:
            arity = operator_arity(token, FORMULA_VOCAB.operator_offset)
            if depth < arity:
                return -1
            depth = depth - arity + 1
        else:
            return -1
    return depth


def _allow_unary(mask: torch.Tensor) -> None:
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        if operator_arity(token, FORMULA_VOCAB.operator_offset) == 1:
            mask[token] = True


def _allow_binary(mask: torch.Tensor) -> None:
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        if operator_arity(token, FORMULA_VOCAB.operator_offset) == 2:
            mask[token] = True

from typing import Any


INVALID_REWARD = -1.0


def formula_reward_from_research_result(result: Any, invalid_reward: float = INVALID_REWARD) -> float:
    if result is None:
        return float(invalid_reward)
    status = getattr(result, "status", None)
    score = float(getattr(result, "score", 0.0) or 0.0)
    if status == "error":
        return float(invalid_reward)
    if status == "skipped_existing":
        return 0.0
    return float(score)

import json
from pathlib import Path
from typing import Iterable

import torch

from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.search.formulas import generate_seed_formulas
from auto_alpha.research.formulas.candidates import default_candidates
from auto_alpha.research.formulas.candidates import load_candidates_json


def load_formula_records_from_store(store: LocalFactorStore) -> list[list[int]]:
    return [record.formula_tokens for record in store.load_factors()]


def load_candidates_from_json(path: str | Path) -> list[list[int]]:
    return [candidate.formula_tokens for candidate in load_candidates_json(path)]


def build_supervised_sequences(formulas: Iterable[list[int]]) -> list[tuple[list[int], int]]:
    sequences: list[tuple[list[int], int]] = []
    for formula in formulas:
        tokens = [int(token) for token in formula]
        for index in range(1, len(tokens)):
            sequences.append((tokens[:index], tokens[index]))
    return sequences


class FormulaSequenceDataset:
    def __init__(self, formulas: Iterable[list[int]]):
        self.samples = build_supervised_sequences(formulas)

    @classmethod
    def from_defaults(cls, store: LocalFactorStore | None = None, candidates_json: str | Path | None = None) -> "FormulaSequenceDataset":
        formulas: list[list[int]] = []
        formulas.extend(candidate.formula_tokens for candidate in default_candidates())
        formulas.extend(candidate.formula_tokens for candidate in generate_seed_formulas())
        if store is not None:
            formulas.extend(load_formula_records_from_store(store))
        if candidates_json is not None:
            formulas.extend(load_candidates_from_json(candidates_json))
        return cls(formulas)

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "FormulaSequenceDataset":
        formulas: list[list[int]] = []
        samples: list[tuple[list[int], int]] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            prefix = payload.get("prefix_tokens")
            target = payload.get("target_token")
            if isinstance(prefix, list) and prefix and target is not None:
                samples.append(([int(token) for token in prefix], int(target)))
            elif isinstance(payload.get("formula_tokens"), list):
                formulas.append([int(token) for token in payload["formula_tokens"]])
        dataset = cls(formulas)
        dataset.samples.extend(samples)
        return dataset

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        prefix, target = self.samples[index]
        return torch.tensor(prefix, dtype=torch.long), torch.tensor(target, dtype=torch.long)

    def to_jsonl(self, path: str | Path) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for prefix, target in self.samples:
                handle.write(json.dumps({"prefix": prefix, "target": target}, ensure_ascii=False))
                handle.write("\n")
        return output_path

import random

import torch
import torch.nn.functional as F

from auto_alpha.research.factors.store import stable_formula_hash
from auto_alpha.research.formulas.alphagpt import AlphaGPT
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB



FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


class NeuralFormulaSampler:
    def __init__(
        self,
        model: AlphaGPT,
        seed: int = 42,
        max_formula_len: int = 8,
        min_formula_len: int = 2,
        max_complexity: int = 24,
        max_lookback: int = 10,
        temperature: float = 1.0,
        top_k_tokens: int | None = None,
    ):
        self.model = model
        self.rng = random.Random(seed)
        self.max_formula_len = int(max_formula_len)
        self.min_formula_len = int(min_formula_len)
        self.max_complexity = int(max_complexity)
        self.max_lookback = int(max_lookback)
        self.temperature = float(temperature)
        self.top_k_tokens = top_k_tokens
        self.vm = StackVM()
        self.torch_generator = torch.Generator(device=next(model.parameters()).device)
        self.torch_generator.manual_seed(int(seed))

    def sample_formula(self, track_grad: bool = False, generation: int = 0) -> PolicySample:
        context = torch.enable_grad() if track_grad else torch.no_grad()
        with context:
            return self._sample(track_grad=track_grad, generation=generation)

    def sample_batch(self, count: int, track_grad: bool = False, generation: int = 0) -> list[PolicySample]:
        return [self.sample_formula(track_grad=track_grad, generation=generation) for _ in range(max(0, count))]

    def _sample(self, track_grad: bool, generation: int) -> PolicySample:
        tokens: list[int] = []
        log_probs = []
        entropies = []
        values = []
        device = next(self.model.parameters()).device
        for _ in range(self.max_formula_len):
            mask = build_action_mask(tokens, self.max_formula_len, self.min_formula_len).to(device)
            if not bool(mask.any()):
                break
            if not tokens:
                allowed = torch.nonzero(mask, as_tuple=False).flatten().tolist()
                token = int(self.rng.choice(allowed))
            else:
                prefix = torch.tensor([tokens], dtype=torch.long, device=device)
                logits, value, _task_probs = self.model(prefix)
                logits = logits[0] / max(self.temperature, 1e-6)
                logits = logits.masked_fill(~mask, -1e9)
                if self.top_k_tokens is not None and self.top_k_tokens > 0:
                    logits = _top_k_filter(logits, self.top_k_tokens)
                probs = F.softmax(logits, dim=-1)
                token_tensor = torch.multinomial(probs, 1, generator=self.torch_generator).reshape(())
                token = int(token_tensor.item())
                distribution = torch.distributions.Categorical(probs=probs)
                log_probs.append(distribution.log_prob(token_tensor))
                entropies.append(distribution.entropy())
                values.append(value.reshape(()))
            tokens.append(token)
            valid, _reason = self.vm.validate_with_reason(tokens)
            if valid and len(tokens) >= self.min_formula_len and len(tokens) < self.max_formula_len and self.rng.random() < 0.35:
                break
        valid, reason = self.vm.validate_with_reason(tokens)
        names = self.vm.canonical_formula(tokens)
        complexity = self.vm.formula_complexity(tokens)
        lookback = self.vm.formula_lookback(tokens)
        if complexity > self.max_complexity:
            valid, reason = False, "complexity_above_limit"
        if lookback > self.max_lookback:
            valid, reason = False, "lookback_above_limit"
        formula_hash = stable_formula_hash(tokens, names, FEATURE_VERSION, OPERATOR_VERSION) if valid else None
        log_prob_tensor = torch.stack(log_probs).sum() if log_probs else torch.tensor(0.0, device=device, requires_grad=track_grad)
        entropy_tensor = torch.stack(entropies).mean() if entropies else torch.tensor(0.0, device=device)
        value_tensor = torch.stack(values).mean() if values else torch.tensor(0.0, device=device, requires_grad=track_grad)
        return PolicySample(
            tokens=tokens,
            names=names,
            log_prob=float(log_prob_tensor.detach().cpu().item()),
            entropy=float(entropy_tensor.detach().cpu().item()),
            valid=bool(valid),
            reason=reason,
            complexity=int(complexity),
            lookback=int(lookback),
            generation=generation,
            formula_hash=formula_hash,
            training_log_prob=log_prob_tensor,
            training_entropy=entropy_tensor,
            training_value=value_tensor,
        )


def _top_k_filter(logits: torch.Tensor, top_k: int) -> torch.Tensor:
    if top_k >= logits.numel():
        return logits
    values, _indices = torch.topk(logits, top_k)
    threshold = values[-1]
    return logits.masked_fill(logits < threshold, -1e9)

import json
from pathlib import Path



def write_neural_search_report(result: NeuralSearchResult, output_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / "neural_search_result.json"
    history_path = root / "neural_training_history.jsonl"
    report_path = root / "neural_search_report.md"
    payload = result.to_dict()
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    with history_path.open("w", encoding="utf-8") as handle:
        for row in result.training_history:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    report_path.write_text(_render_markdown(payload), encoding="utf-8")
    return result_path, history_path, report_path


def _render_markdown(payload: dict) -> str:
    lines = [
        "# Neural Formula Search Report",
        "",
        f"- search_id: `{payload.get('search_id')}`",
        f"- candidates_evaluated: {payload.get('candidates_evaluated', 0)}",
        f"- composite_factor_id: `{payload.get('composite_factor_id')}`",
        "",
        "## Training Summary",
        "",
        "| Step | Phase | Loss | Avg Reward | Best Reward | Valid Rate | Unique Rate | Stable Rank |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload.get("training_history", []):
        lines.append(
            f"| {row.get('step')} | {row.get('phase')} | {float(row.get('loss', 0.0)):.6f} | "
            f"{float(row.get('avg_reward', 0.0)):.6f} | {float(row.get('best_reward', 0.0)):.6f} | "
            f"{float(row.get('valid_rate', 0.0)):.6f} | {float(row.get('unique_rate', 0.0)):.6f} | "
            f"{float(row.get('stable_rank', 0.0)):.6f} |"
        )
    lines.extend(["", "## Best Formulas", "", "| Formula | Reward | Status | Factor |", "| --- | ---: | --- | --- |"])
    for item in payload.get("best_formulas", [])[:20]:
        formula = " ".join(item.get("formula", []))
        lines.append(f"| `{formula}` | {float(item.get('reward', 0.0)):.6f} | {item.get('status')} | `{item.get('factor_id')}` |")
    lines.extend(["", "## Checkpoints", ""])
    for path in payload.get("checkpoint_paths", []):
        lines.append(f"- `{path}`")
    return "\n".join(lines) + "\n"

import json
import random
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from auto_alpha.research.factors.store import LocalFactorStore, has_positive_oos_evidence
from auto_alpha.research.search.formulas import FormulaCandidate
from auto_alpha.research.formulas.alphagpt import AlphaGPT
from auto_alpha.research.formulas.alphagpt import StableRankMonitor
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.factors.composite import build_composite_factor_matrix
from auto_alpha.research.factors.composite import register_composite_factor
from auto_alpha.research.factors.composite import select_approved_factors
from auto_alpha.research.formulas.evaluator import FormulaBatchEvalConfig, FormulaBatchEvaluator
from auto_alpha.research.formulas.candidates import from_formula_search_candidates
from auto_alpha.research.formulas.data_loader import AShareDataLoader



class NeuralFormulaTrainer:
    def __init__(
        self,
        config: NeuralSearchConfig,
        data_dir: str,
        universe_name: str | None,
        universe_file: str | None,
        factor_store_dir: str,
        report_dir: str,
        output_dir: str,
        candidates_json: str | None = None,
        correlation_threshold: float = 0.95,
        min_coverage: float = 0.8,
    ):
        self.config = config
        self.data_dir = data_dir
        self.universe_name = universe_name
        self.universe_file = universe_file
        self.factor_store_dir = factor_store_dir
        self.report_dir = report_dir
        self.output_dir = Path(output_dir)
        self.candidates_json = candidates_json
        self.correlation_threshold = correlation_threshold
        self.min_coverage = min_coverage
        self.device = _search_trainer_resolve_device(config.device)
        torch.manual_seed(config.seed)
        random.seed(config.seed)
        if config.resume_checkpoint:
            self.model, _metadata = AlphaGPT.load_checkpoint(config.resume_checkpoint, device=self.device)
        else:
            self.model = AlphaGPT().to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=config.learning_rate)
        self.rank_monitor = StableRankMonitor(self.model)
        self.store = LocalFactorStore(factor_store_dir)
        self.history: list[NeuralTrainingStep] = []
        self.best_reward = -float("inf")
        self.best_formulas: list[dict[str, Any]] = []
        self.checkpoints: list[str] = []
        self.baseline = 0.0
        self.vm = StackVM()

    def supervised_warmup(self) -> list[NeuralTrainingStep]:
        dataset = (
            FormulaSequenceDataset.from_jsonl(self.config.corpus_sequence_path)
            if self.config.corpus_sequence_path
            else FormulaSequenceDataset.from_defaults(self.store, self.candidates_json)
        )
        if len(dataset) == 0:
            return []
        for step in range(max(self.config.warmup_steps, 0)):
            losses = []
            for item_idx in range(max(self.config.batch_size, 1)):
                prefix, target = dataset[(step * max(self.config.batch_size, 1) + item_idx) % len(dataset)]
                prefix = prefix.unsqueeze(0).to(self.device)
                target = target.unsqueeze(0).to(self.device)
                logits, _value, _task_probs = self.model(prefix)
                losses.append(F.cross_entropy(logits, target))
            loss = torch.stack(losses).mean()
            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.optimizer.step()
            row = NeuralTrainingStep(
                step=step,
                phase="warmup",
                loss=float(loss.detach().cpu().item()),
                avg_reward=0.0,
                best_reward=float(self.best_reward if self.best_reward > -1e100 else 0.0),
                valid_rate=1.0,
                unique_rate=1.0,
                stable_rank=float(self.rank_monitor.compute()),
            )
            self.history.append(row)
        return self.history

    def policy_search_step(self, step: int, search_id: str) -> NeuralTrainingStep:
        sampler = NeuralFormulaSampler(
            self.model,
            seed=self.config.seed + step,
            max_formula_len=self.config.max_formula_len,
            min_formula_len=self.config.min_formula_len,
            max_complexity=self.config.max_complexity,
            max_lookback=self.config.max_lookback,
        )
        samples = sampler.sample_batch(self.config.samples_per_step, track_grad=True, generation=step)
        results = self._evaluate_samples(search_id, step, samples)
        rewards = []
        policy_terms = []
        value_terms = []
        entropy_terms = []
        for sample, result in zip(samples, results):
            reward = formula_reward_from_research_result(result) if sample.valid else -1.0
            rewards.append(float(reward))
            reward_tensor = torch.tensor(float(reward), dtype=torch.float32, device=self.device)
            self.baseline = 0.9 * self.baseline + 0.1 * float(reward)
            advantage = reward_tensor - torch.tensor(self.baseline, dtype=torch.float32, device=self.device)
            if sample.training_log_prob is not None:
                policy_terms.append(-sample.training_log_prob * advantage.detach())
            if sample.training_value is not None:
                value_terms.append(F.mse_loss(sample.training_value.reshape(()), reward_tensor))
            if sample.training_entropy is not None:
                entropy_terms.append(sample.training_entropy)
            self._record_best(sample, result, reward)
        loss = torch.stack(policy_terms).mean() if policy_terms else torch.tensor(0.0, device=self.device, requires_grad=True)
        if value_terms:
            loss = loss + self.config.value_coef * torch.stack(value_terms).mean()
        if entropy_terms:
            loss = loss - self.config.entropy_coef * torch.stack(entropy_terms).mean()
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
        self.optimizer.step()
        unique_hashes = {sample.formula_hash for sample in samples if sample.formula_hash}
        row = NeuralTrainingStep(
            step=step,
            phase="policy",
            loss=float(loss.detach().cpu().item()),
            avg_reward=float(sum(rewards) / len(rewards) if rewards else 0.0),
            best_reward=float(self.best_reward if self.best_reward > -1e100 else 0.0),
            valid_rate=float(sum(1 for sample in samples if sample.valid) / len(samples) if samples else 0.0),
            unique_rate=float(len(unique_hashes) / len(samples) if samples else 0.0),
            stable_rank=float(self.rank_monitor.compute()),
        )
        self.history.append(row)
        return row

    def train(self) -> NeuralSearchResult:
        created_at = _search_trainer_utc_now()
        search_id = f"neural_{self.config.seed}_{_safe_time(created_at)}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.supervised_warmup()
        for step in range(max(self.config.policy_steps, 0)):
            self.policy_search_step(step, search_id)
            if self.config.checkpoint_every > 0 and (step + 1) % self.config.checkpoint_every == 0:
                self.checkpoints.append(str(self.save_checkpoint(step, "policy")))
        composite_id = self._register_composite(search_id, created_at)
        paths = {
            "neural_search_result_path": str(self.output_dir / "neural_search_result.json"),
            "neural_training_history_path": str(self.output_dir / "neural_training_history.jsonl"),
            "neural_search_report_path": str(self.output_dir / "neural_search_report.md"),
            "checkpoint_dir": str(self.output_dir / "checkpoints"),
        }
        approved = [
            record.factor_id
            for record in self.store.load_factors()
            if has_positive_oos_evidence(record) and (record.batch_id or "").startswith(search_id)
        ]
        result = NeuralSearchResult(
            search_id=search_id,
            config=self.config.to_dict()
            | {
                "data_dir": self.data_dir,
                "universe_name": self.universe_name,
                "universe_file": self.universe_file,
                "factor_store_dir": self.factor_store_dir,
                "report_dir": self.report_dir,
                "output_dir": str(self.output_dir),
            },
            training_history=[row.to_dict() for row in self.history],
            candidates_evaluated=len(self.best_formulas),
            approved_factor_ids=approved,
            composite_factor_id=composite_id,
            best_formulas=sorted(self.best_formulas, key=lambda item: item["reward"], reverse=True)[: self.config.top_k],
            checkpoint_paths=self.checkpoints,
            paths=paths,
        )
        write_neural_search_report(result, self.output_dir)
        return result

    def save_checkpoint(self, step: int, phase: str) -> Path:
        path = self.output_dir / "checkpoints" / f"checkpoint_{phase}_{step}.pt"
        return self.model.save_checkpoint(
            path,
            metadata={
                "step": step,
                "phase": phase,
                "config": self.config.to_dict(),
            },
        )

    def load_checkpoint(self, path: str | Path) -> None:
        self.model, _metadata = AlphaGPT.load_checkpoint(path, device=self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.config.learning_rate)

    def _evaluate_samples(self, search_id: str, step: int, samples: list[PolicySample]):
        valid_samples = [sample for sample in samples if sample.valid]
        if not valid_samples:
            return [None for _sample in samples]
        candidates = [
            FormulaCandidate(
                formula_tokens=sample.tokens,
                formula_names=sample.names,
                formula_hash=sample.formula_hash or "",
                complexity=sample.complexity,
                lookback=sample.lookback,
                source="neural",
                parent_hashes=[],
                generation=step,
                validation_reason=sample.reason,
            )
            for sample in valid_samples
        ]
        evaluation_dir = (
            Path(self.config.evaluation_output_dir) / f"policy_step_{step}"
            if self.config.evaluation_output_dir
            else self.output_dir / f"policy_step_{step}"
        )
        batch_config = FormulaBatchEvalConfig(
            data_dir=self.data_dir,
            factor_store_dir=self.factor_store_dir,
            report_dir=self.report_dir,
            output_dir=str(evaluation_dir),
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            matrix_cache_dir=self.config.matrix_cache_dir,
            use_matrix_cache=self.config.use_matrix_cache,
            device=self.config.evaluation_device,
            factor_transform=self.config.factor_transform,
            enable_gate=self.config.enable_gate,
            correlation_threshold=self.correlation_threshold,
            min_coverage=self.min_coverage,
            chunk_size=self.config.evaluation_chunk_size,
            use_eval_cache=self.config.use_eval_cache,
            eval_cache_dir=self.config.eval_cache_dir,
            skip_existing=True,
            register_approved=True,
            continue_on_error=True,
            batch_id=f"{search_id}_policy_{step}",
        )
        requests = from_formula_search_candidates(candidates)
        requests = [
            replace(
                request,
                metadata=dict(request.metadata or {}) | {"search_id": search_id, "generation": step},
            )
            for request in requests
        ]
        batch_result = FormulaBatchEvaluator(batch_config).run(requests)
        by_hash = {result.request.formula_hash: result for result in batch_result.results}
        return [by_hash.get(sample.formula_hash) if sample.valid else None for sample in samples]

    def _record_best(self, sample: PolicySample, result: Any, reward: float) -> None:
        self.best_reward = max(self.best_reward, float(reward))
        payload = {
            "formula": sample.names,
            "tokens": sample.tokens,
            "reward": float(reward),
            "valid": sample.valid,
            "reason": sample.reason,
            "status": getattr(result, "status", "invalid") if result is not None else "invalid",
            "factor_id": getattr(result, "factor_id", None) if result is not None else None,
            "score": float(getattr(result, "score", 0.0) or 0.0) if result is not None else 0.0,
        }
        self.best_formulas.append(payload)

    def _register_composite(self, search_id: str, created_at: str) -> str | None:
        factor_ids = select_approved_factors(
            self.store,
            max_factors=max(self.config.top_k, 0),
            max_pairwise_corr=0.95,
        )
        if not factor_ids:
            return None
        loader = AShareDataLoader(
            data_dir=self.data_dir,
            device="cpu",
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            matrix_cache_dir=self.config.matrix_cache_dir,
            use_matrix_cache=self.config.use_matrix_cache,
        ).load_data()
        values = build_composite_factor_matrix(
            self.store,
            factor_ids,
            loader.ts_codes,
            loader.trade_dates,
            method=self.config.composite_method,
        )
        info = register_composite_factor(
            self.store,
            factor_ids,
            loader.ts_codes,
            loader.trade_dates,
            values,
            method=self.config.composite_method,
            batch_id=search_id,
            created_at=created_at,
        )
        return str(info.get("factor_id"))


def _search_trainer_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


def _search_trainer_resolve_device(device: str) -> torch.device:
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if str(device).startswith("cuda") and not torch.cuda.is_available():
        return torch.device("cpu")
    return torch.device(device)

import hashlib
import json
import os
import random
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact
from auto_alpha.research.formulas.alphagpt import AlphaGPT
from auto_alpha.research.formulas.alphagpt import StableRankMonitor
from auto_alpha.research.formulas.alphagpt import count_parameters



class AlphaGPTPretrainer:
    def __init__(self, config: AlphaGPTPretrainConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        if config.distributed and config.strict_cuda and not torch.cuda.is_available():
            raise RuntimeError("distributed cuda pretrain requested but CUDA is unavailable")
        self.device = _search_pretrain_resolve_device(config.device)
        self.amp_enabled = bool(config.amp and self.device.type == "cuda")
        torch.manual_seed(config.seed)
        random.seed(config.seed)
        if config.resume_checkpoint:
            self.model, _metadata = AlphaGPT.load_checkpoint(config.resume_checkpoint, device=self.device)
        else:
            self.model = AlphaGPT().to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=config.learning_rate)
        self.rank_monitor = StableRankMonitor(self.model)
        self.history: list[AlphaGPTPretrainEpoch] = []
        self.preference_history: list[PreferenceTrainingStep] = []
        self.checkpoints: list[dict[str, Any]] = []

    def train(self) -> AlphaGPTPretrainResult:
        created_at = _search_pretrain_utc_now()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        sequences = _load_sequences(self.config.sequence_path, self.config.max_sequences)
        if not sequences:
            raise ValueError("formula sequence corpus is empty")
        for epoch in range(max(0, self.config.epochs)):
            row = self._train_epoch(epoch, sequences)
            self.history.append(row)
            if self.config.checkpoint_every > 0 and (epoch + 1) % self.config.checkpoint_every == 0:
                self._save_checkpoint(epoch, "supervised")
        preferences = _load_preferences(self.config.preference_path)
        for step in range(max(0, self.config.preference_steps)):
            if not preferences:
                break
            self.preference_history.append(self._train_preference_step(step, preferences))
        if not self.checkpoints:
            self._save_checkpoint(max(self.config.epochs - 1, 0), "final")
        latest = self.checkpoints[-1]["path"] if self.checkpoints else None
        manifest = AlphaGPTCheckpointManifest(latest_checkpoint_path=latest, checkpoints=self.checkpoints)
        paths = {
            "alphagpt_pretrain_result_path": str(self.output_dir / "alphagpt_pretrain_result.json"),
            "alphagpt_pretrain_history_path": str(self.output_dir / "alphagpt_pretrain_history.jsonl"),
            "alphagpt_pretrain_report_path": str(self.output_dir / "alphagpt_pretrain_report.md"),
            "checkpoint_manifest_path": str(self.output_dir / "checkpoint_manifest.json"),
            "latest_checkpoint_path": str(self.output_dir / "checkpoints" / "latest.pt"),
            "distributed_training_report_path": str(self.output_dir / "distributed_training_report.json"),
        }
        distributed_report = self._distributed_payload()
        result = AlphaGPTPretrainResult(
            created_at=created_at,
            status="success",
            config=self.config.to_dict() | {"device_resolved": str(self.device), "amp_enabled": self.amp_enabled},
            history=[row.to_dict() for row in self.history],
            preference_history=[row.to_dict() for row in self.preference_history],
            checkpoint_manifest=manifest.to_dict(),
            paths=paths,
            summary={
                "parameters": count_parameters(self.model),
                "sequences": len(sequences),
                "preference_pairs": len(preferences),
                "epochs": len(self.history),
                "preference_steps": len(self.preference_history),
                "latest_checkpoint_path": latest,
                "distributed": bool(self.config.distributed),
                "world_size": int(self.config.world_size),
                "fallback_to_cpu": bool(distributed_report.get("fallback_to_cpu", False)),
            },
        )
        self._write_outputs(result)
        return result

    def _train_epoch(self, epoch: int, sequences: list[dict[str, Any]]) -> AlphaGPTPretrainEpoch:
        rng = random.Random(self.config.seed + epoch)
        shuffled = list(sequences)
        rng.shuffle(shuffled)
        losses: list[torch.Tensor] = []
        correct = 0
        seen = 0
        by_length: dict[int, list[dict[str, Any]]] = {}
        for row in shuffled:
            by_length.setdefault(len(row["prefix_tokens"]), []).append(row)
        for rows in by_length.values():
            for batch in _chunks(rows, max(1, self.config.batch_size)):
                prefix = torch.tensor([row["prefix_tokens"] for row in batch], dtype=torch.long, device=self.device)
                target = torch.tensor([int(row["target_token"]) for row in batch], dtype=torch.long, device=self.device)
                with torch.autocast(device_type="cuda", enabled=self.amp_enabled):
                    logits, _value, _task_probs = self.model(prefix)
                    loss = F.cross_entropy(logits, target)
                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                self.optimizer.step()
                losses.append(loss.detach())
                correct += int((torch.argmax(logits.detach(), dim=-1) == target).sum().item())
                seen += len(batch)
        return AlphaGPTPretrainEpoch(
            epoch=epoch,
            phase="supervised",
            loss=float(torch.stack(losses).mean().cpu().item()) if losses else 0.0,
            token_accuracy=float(correct / seen) if seen else 0.0,
            sequences_seen=seen,
            preference_pairs_seen=0,
            stable_rank=float(self.rank_monitor.compute()),
        )

    def _train_preference_step(self, step: int, preferences: list[dict[str, Any]]) -> PreferenceTrainingStep:
        rng = random.Random(self.config.seed + 1000 + step)
        pair = preferences[rng.randrange(len(preferences))]
        preferred = [int(token) for token in pair.get("preferred_tokens", [])]
        rejected = [int(token) for token in pair.get("rejected_tokens", [])]
        preferred_lp = self._sequence_log_prob(preferred)
        rejected_lp = self._sequence_log_prob(rejected)
        target = torch.tensor(float(self.config.preference_margin), dtype=torch.float32, device=self.device)
        loss = F.softplus(target - (preferred_lp - rejected_lp))
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
        self.optimizer.step()
        return PreferenceTrainingStep(
            step=step,
            loss=float(loss.detach().cpu().item()),
            preferred_log_prob=float(preferred_lp.detach().cpu().item()),
            rejected_log_prob=float(rejected_lp.detach().cpu().item()),
            margin=float((preferred_lp - rejected_lp).detach().cpu().item()),
        )

    def _sequence_log_prob(self, tokens: list[int]) -> torch.Tensor:
        if len(tokens) < 2:
            return torch.tensor(0.0, device=self.device, requires_grad=True)
        terms = []
        for position in range(1, len(tokens)):
            prefix = torch.tensor([tokens[:position]], dtype=torch.long, device=self.device)
            target = torch.tensor([tokens[position]], dtype=torch.long, device=self.device)
            logits, _value, _task_probs = self.model(prefix)
            terms.append(F.log_softmax(logits, dim=-1).gather(1, target.reshape(1, 1)).reshape(()))
        return torch.stack(terms).sum()

    def _save_checkpoint(self, epoch: int, phase: str) -> None:
        path = self.output_dir / "checkpoints" / f"alphagpt_{phase}_{epoch}.pt"
        self.model.save_checkpoint(
            path,
            metadata={"phase": phase, "epoch": epoch, "config": self.config.to_dict() | {"device_resolved": str(self.device)}},
        )
        latest = self.output_dir / "checkpoints" / "latest.pt"
        latest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, latest)
        self.checkpoints.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "epoch": epoch,
                "phase": phase,
                "latest_path": str(latest),
            }
        )

    def _write_outputs(self, result: AlphaGPTPretrainResult) -> None:
        write_json_artifact(self.output_dir / "alphagpt_pretrain_result.json", result.to_dict(), "alphagpt_pretrain_result", "neural_search")
        write_jsonl_artifact(
            self.output_dir / "alphagpt_pretrain_history.jsonl",
            result.history,
            "alphagpt_pretrain_history",
            "neural_search",
        )
        checkpoint_manifest = result.checkpoint_manifest | {
            "distributed": bool(self.config.distributed),
            "world_size": int(self.config.world_size),
            "rank0_only": bool(self.config.save_rank0_only),
            "device_count_detected": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "amp_enabled": bool(self.amp_enabled),
            "fallback_to_cpu": bool(self.config.distributed and self.device.type == "cpu"),
            "resource_report_path": self.config.resource_report_path,
        }
        write_json_artifact(self.output_dir / "checkpoint_manifest.json", checkpoint_manifest, "alphagpt_checkpoint_manifest", "neural_search")
        distributed_payload = self._distributed_payload()
        write_json_artifact(
            self.output_dir / "distributed_training_report.json",
            distributed_payload,
            "distributed_training_report",
            "neural_search",
        )
        if self.config.resource_report_path:
            write_json_artifact(self.config.resource_report_path, distributed_payload, "resource_usage_report", "neural_search")
        (self.output_dir / "alphagpt_pretrain_report.md").write_text(_render_report(result), encoding="utf-8")

    def _distributed_payload(self) -> dict[str, Any]:
        return {
            "distributed": bool(self.config.distributed),
            "world_size": int(self.config.world_size),
            "rank": int(self.config.rank),
            "local_rank": int(self.config.local_rank),
            "backend": self.config.backend,
            "device_resolved": str(self.device),
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count_detected": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "rank0_only": bool(self.config.save_rank0_only),
            "fallback_to_cpu": bool(self.config.distributed and self.device.type == "cpu"),
            "distributed_skipped_reason": "cuda_unavailable" if self.config.distributed and self.device.type == "cpu" else "",
            "resource_report_path": self.config.resource_report_path,
        }


def _load_sequences(path: str | Path, max_records: int | None) -> list[dict[str, Any]]:
    result = []
    for row in _read_jsonl(Path(path)):
        prefix = row.get("prefix_tokens")
        if not isinstance(prefix, list) or not prefix or row.get("target_token") is None:
            continue
        result.append({"prefix_tokens": [int(token) for token in prefix], "target_token": int(row["target_token"])})
        if max_records is not None and len(result) >= max_records:
            break
    return result


def _load_preferences(path: str | Path | None) -> list[dict[str, Any]]:
    return _read_jsonl(Path(path)) if path else []


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _chunks(items: list[dict[str, Any]], size: int):
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _search_pretrain_resolve_device(device: str) -> torch.device:
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if str(device).startswith("cuda") and not torch.cuda.is_available():
        return torch.device("cpu")
    return torch.device(device)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_report(result: AlphaGPTPretrainResult) -> str:
    lines = [
        "# AlphaGPT Pretrain Report",
        "",
        f"- status: `{result.status}`",
        f"- sequences: {result.summary.get('sequences', 0)}",
        f"- latest_checkpoint: `{result.summary.get('latest_checkpoint_path')}`",
        "",
        "| epoch | phase | loss | accuracy | sequences | stable_rank |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in result.history:
        lines.append(
            f"| {row.get('epoch')} | {row.get('phase')} | {float(row.get('loss', 0.0)):.6f} | "
            f"{float(row.get('token_accuracy', 0.0)):.6f} | {row.get('sequences_seen', 0)} | "
            f"{float(row.get('stable_rank', 0.0)):.6f} |"
        )
    if result.preference_history:
        lines.extend(["", "## Preference Steps", "", "| step | loss | margin |", "| ---: | ---: | ---: |"])
        for row in result.preference_history:
            lines.append(f"| {row.get('step')} | {float(row.get('loss', 0.0)):.6f} | {float(row.get('margin', 0.0)):.6f} |")
    return "\n".join(lines) + "\n"


def _search_pretrain_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

__all__ = [
    "FormulaSequenceDataset",
    "AlphaGPTCheckpointManifest",
    "AlphaGPTPretrainConfig",
    "AlphaGPTPretrainEpoch",
    "AlphaGPTPretrainResult",
    "AlphaGPTPretrainer",
    "NeuralFormulaSampler",
    "NeuralFormulaTrainer",
    "NeuralSearchCheckpointInfo",
    "NeuralSearchConfig",
    "NeuralSearchResult",
    "NeuralTrainingStep",
    "PolicySample",
    "PreferenceTrainingStep",
    "build_action_mask",
    "build_supervised_sequences",
    "explain_available_actions",
    "formula_reward_from_research_result",
    "load_formula_records_from_store",
    "masked_sample",
]
