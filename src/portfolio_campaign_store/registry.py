"""Local JSON/JSONL registry for portfolio campaigns."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    PortfolioCandidateItemRecord,
    PortfolioCertificationCampaignRecord,
    ProductionCandidateBundleRecord,
)


class LocalPortfolioCampaignStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.registry_path = self.root_dir / "portfolio_certification_campaign_registry.json"
        self.campaigns_path = self.root_dir / "portfolio_certification_campaigns.jsonl"
        self.items_path = self.root_dir / "portfolio_candidate_items.jsonl"
        self.report_path = self.root_dir / "portfolio_certification_campaign_report.json"
        self.bundle_path = self.root_dir / "production_candidate_bundle.jsonl"
        self.bundle_report_path = self.root_dir / "production_candidate_bundle_report.json"
        self.activation_queue_path = self.root_dir / "optimizer_policy_activation_queue.jsonl"
        self.artifact_catalog_path = self.root_dir / "portfolio_campaign_artifact_catalog.json"

    def register_campaign(self, record: PortfolioCertificationCampaignRecord) -> None:
        self._upsert_jsonl(self.campaigns_path, [record], "portfolio_certification_campaigns", "portfolio_campaign_id")
        self.write_registry()

    def write_items(self, records: Iterable[PortfolioCandidateItemRecord | dict[str, Any]]) -> None:
        write_jsonl_artifact(self.items_path, [_payload(row) for row in records], "portfolio_candidate_items", "portfolio_campaign_store")
        self.write_registry()

    def write_bundle(self, records: Iterable[ProductionCandidateBundleRecord | dict[str, Any]]) -> None:
        write_jsonl_artifact(self.bundle_path, [_payload(row) for row in records], "production_candidate_bundle", "portfolio_campaign_store")
        self.write_registry()

    def write_activation_queue(self, records: Iterable[dict[str, Any]]) -> None:
        write_jsonl_artifact(self.activation_queue_path, [dict(row) for row in records], "optimizer_policy_activation_queue", "portfolio_campaign_store")
        self.write_registry()

    def load_campaigns(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.campaigns_path)

    def load_items(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.items_path)

    def load_bundle(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.bundle_path)

    def load_activation_queue(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.activation_queue_path)

    def write_registry(self) -> Path:
        campaigns = self.load_campaigns()
        items = self.load_items()
        bundle = self.load_bundle()
        queue = self.load_activation_queue()
        failed = [row for row in items if str(row.get("status")) in {"failed", "error"}]
        payload = {
            "status": "partial" if failed else ("ready" if bundle else ("running" if items else "registered")),
            "campaign_count": len(campaigns),
            "item_count": len(items),
            "failed_item_count": len(failed),
            "production_candidate_bundle_count": len(bundle),
            "optimizer_policy_activation_queue_count": len(queue),
            "campaigns": campaigns,
            "paths": self.paths(),
        }
        return write_json_artifact(self.registry_path, payload, "portfolio_certification_campaign_registry", "portfolio_campaign_store")

    def paths(self) -> dict[str, str]:
        return {
            "portfolio_certification_campaign_registry_path": str(self.registry_path),
            "portfolio_certification_campaigns_path": str(self.campaigns_path),
            "portfolio_candidate_items_path": str(self.items_path),
            "portfolio_certification_campaign_report_path": str(self.report_path),
            "production_candidate_bundle_path": str(self.bundle_path),
            "production_candidate_bundle_report_path": str(self.bundle_report_path),
            "optimizer_policy_activation_queue_path": str(self.activation_queue_path),
            "portfolio_campaign_artifact_catalog_path": str(self.artifact_catalog_path),
        }

    def _upsert_jsonl(self, path: Path, records: Iterable[Any], artifact_type: str, key: str) -> None:
        current = {str(row.get(key)): row for row in _read_jsonl(path)}
        for record in records:
            payload = _payload(record)
            current[str(payload.get(key))] = payload
        write_jsonl_artifact(path, current.values(), artifact_type, "portfolio_campaign_store")


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
