"""Offline supervised and preference pretraining for AlphaGPT."""

from __future__ import annotations

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

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from model_core.alphagpt import AlphaGPT, StableRankMonitor, count_parameters

from .models import (
    AlphaGPTCheckpointManifest,
    AlphaGPTPretrainConfig,
    AlphaGPTPretrainEpoch,
    AlphaGPTPretrainResult,
    PreferenceTrainingStep,
)


class AlphaGPTPretrainer:
    def __init__(self, config: AlphaGPTPretrainConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        if config.distributed and config.strict_cuda and not torch.cuda.is_available():
            raise RuntimeError("distributed cuda pretrain requested but CUDA is unavailable")
        self.device = _resolve_device(config.device)
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
        created_at = _utc_now()
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


def _resolve_device(device: str) -> torch.device:
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
