"""Training loop for neural-guided formula search."""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

from factor_store import LocalFactorStore
from formula_search.models import FormulaCandidate
from model_core.alphagpt import AlphaGPT, StableRankMonitor
from model_core.vm import StackVM
from research import BatchFactorResearchRunner, BatchResearchConfig
from research.candidates import from_formula_search_candidates
from research.composite import build_composite_factor_matrix, register_composite_factor, select_approved_factors
from model_core.data_loader import AShareDataLoader

from .dataset import FormulaSequenceDataset
from .models import NeuralSearchConfig, NeuralSearchResult, NeuralTrainingStep, PolicySample
from .report import write_neural_search_report
from .reward import formula_reward_from_research_result
from .sampler import NeuralFormulaSampler


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
        self.device = _resolve_device(config.device)
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
        created_at = _utc_now()
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
            if record.status == "approved" and (record.batch_id or "").startswith(search_id)
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
        batch_config = BatchResearchConfig(
            data_dir=self.data_dir,
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            factor_store_dir=self.factor_store_dir,
            report_dir=self.report_dir,
            output_dir=str(self.output_dir / f"policy_step_{step}"),
            factor_transform=self.config.factor_transform,
            enable_gate=self.config.enable_gate,
            correlation_threshold=self.correlation_threshold,
            min_coverage=self.min_coverage,
            top_k=self.config.top_k,
            composite_method=self.config.composite_method,
            continue_on_error=True,
            disable_composite=True,
            batch_id=f"{search_id}_policy_{step}",
            search_id=search_id,
            matrix_cache_dir=self.config.matrix_cache_dir,
            use_matrix_cache=self.config.use_matrix_cache,
            use_batch_eval=self.config.use_batch_eval,
            batch_eval_output_dir=str(self.output_dir / f"policy_step_{step}" / "batch_eval") if self.config.use_batch_eval else None,
            batch_eval_chunk_size=self.config.batch_eval_chunk_size,
            batch_eval_device=self.config.batch_eval_device,
            use_eval_cache=self.config.use_eval_cache,
            eval_cache_dir=self.config.eval_cache_dir,
        )
        batch_result = BatchFactorResearchRunner(batch_config, from_formula_search_candidates(candidates)).run()
        by_hash = {result.candidate.formula_hash: result for result in batch_result.results}
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


def _resolve_device(device: str) -> torch.device:
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if str(device).startswith("cuda") and not torch.cuda.is_available():
        return torch.device("cpu")
    return torch.device(device)
