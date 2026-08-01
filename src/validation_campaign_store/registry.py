"""Local JSON/JSONL registry for validation campaigns."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    FactorCertificationQueueRecord,
    ValidationCampaignRecord,
    ValidationCandidateRecord,
    ValidationCandidateResult,
    ValidationLeaderboardRecord,
    ValidationShardRecord,
)


class LocalValidationCampaignStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.registry_path = self.root_dir / "validation_campaign_registry.json"
        self.campaigns_path = self.root_dir / "validation_campaigns.jsonl"
        self.candidates_path = self.root_dir / "validation_candidates.jsonl"
        self.shards_path = self.root_dir / "validation_shards.jsonl"
        self.results_path = self.root_dir / "validation_candidate_results.jsonl"
        self.leaderboard_path = self.root_dir / "validation_leaderboard.jsonl"
        self.certification_queue_path = self.root_dir / "factor_certification_queue.jsonl"
        self.report_path = self.root_dir / "validation_campaign_store_report.json"
        self.artifact_catalog_path = self.root_dir / "validation_campaign_artifact_catalog.json"

    def register_campaign(self, record: ValidationCampaignRecord) -> None:
        self._upsert_jsonl(self.campaigns_path, [record], "validation_campaigns", "validation_campaign_id")
        self.write_registry()

    def write_candidates(self, records: Iterable[ValidationCandidateRecord | dict[str, Any]]) -> None:
        payloads = [_payload(record) for record in records]
        write_jsonl_artifact(self.candidates_path, payloads, "validation_candidates", "validation_campaign_store")
        self.write_registry()

    def write_shards(self, records: Iterable[ValidationShardRecord | dict[str, Any]]) -> None:
        payloads = [_payload(record) for record in records]
        write_jsonl_artifact(self.shards_path, payloads, "validation_shards", "validation_campaign_store")
        self.write_registry()

    def write_results(self, records: Iterable[ValidationCandidateResult | dict[str, Any]]) -> None:
        payloads = [_payload(record) for record in records]
        write_jsonl_artifact(self.results_path, payloads, "validation_candidate_results", "validation_campaign_store")
        self.write_registry()

    def record_shard_results(self, shard_id: str, rows: Iterable[dict[str, Any]]) -> Path:
        """Persist native validation results for a planned shard and mark it complete."""
        shards = self.load_shards()
        matches = [row for row in shards if str(row.get("shard_id")) == str(shard_id)]
        if len(matches) != 1:
            raise RuntimeError(f"validation_shard_not_found:{shard_id}")
        shard = matches[0]
        output_dir = Path(str(shard.get("output_dir") or ""))
        payloads = [dict(row) for row in rows]
        path = write_jsonl_artifact(output_dir / "validation_candidate_pool_results.jsonl", payloads, "validation_candidate_pool_results", "validation_campaign_store")
        updated = []
        for row in shards:
            if str(row.get("shard_id")) != str(shard_id):
                updated.append(row)
                continue
            updated.append({**row, "status": "success", "success_count": len(payloads), "failed_count": 0, "validation_lab_report_path": str(path)})
        self.write_shards(updated)
        return path

    def write_leaderboard(self, records: Iterable[ValidationLeaderboardRecord | dict[str, Any]]) -> None:
        payloads = [_payload(record) for record in records]
        write_jsonl_artifact(self.leaderboard_path, payloads, "validation_leaderboard", "validation_campaign_store")
        self.write_registry()

    def write_certification_queue(self, records: Iterable[FactorCertificationQueueRecord | dict[str, Any]]) -> None:
        payloads = [_payload(record) for record in records]
        write_jsonl_artifact(self.certification_queue_path, payloads, "factor_certification_queue", "validation_campaign_store")
        self.write_registry()

    def load_campaigns(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.campaigns_path)

    def load_candidates(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.candidates_path)

    def load_shards(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.shards_path)

    def load_results(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.results_path)

    def load_leaderboard(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.leaderboard_path)

    def load_certification_queue(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.certification_queue_path)

    def write_registry(self) -> Path:
        campaigns = self.load_campaigns()
        candidates = self.load_candidates()
        shards = self.load_shards()
        results = self.load_results()
        leaderboard = self.load_leaderboard()
        queue = self.load_certification_queue()
        failed_shards = [row for row in shards if str(row.get("status")) in {"failed", "error"}]
        payload = {
            "status": "partial" if failed_shards else ("ready" if leaderboard else ("running" if shards else "registered")),
            "validation_campaign_count": len(campaigns),
            "candidate_count": len(candidates),
            "shard_count": len(shards),
            "failed_shard_count": len(failed_shards),
            "result_count": len(results),
            "leaderboard_count": len(leaderboard),
            "certification_queue_count": len(queue),
            "campaigns": campaigns,
            "paths": self.paths(),
        }
        return write_json_artifact(self.registry_path, payload, "validation_campaign_registry", "validation_campaign_store")

    def paths(self) -> dict[str, str]:
        return {
            "validation_campaign_registry_path": str(self.registry_path),
            "validation_campaigns_path": str(self.campaigns_path),
            "validation_candidates_path": str(self.candidates_path),
            "validation_shards_path": str(self.shards_path),
            "validation_candidate_results_path": str(self.results_path),
            "validation_leaderboard_path": str(self.leaderboard_path),
            "factor_certification_queue_path": str(self.certification_queue_path),
            "validation_campaign_store_report_path": str(self.report_path),
            "validation_campaign_artifact_catalog_path": str(self.artifact_catalog_path),
        }

    def _upsert_jsonl(self, path: Path, records: Iterable[Any], artifact_type: str, key: str) -> None:
        current = {str(row.get(key)): row for row in _read_jsonl(path)}
        for record in records:
            payload = _payload(record)
            current[str(payload.get(key))] = payload
        write_jsonl_artifact(path, current.values(), artifact_type, "validation_campaign_store")


def _payload(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    if isinstance(record, dict):
        return dict(record)
    raise TypeError(f"unsupported record: {type(record)!r}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
