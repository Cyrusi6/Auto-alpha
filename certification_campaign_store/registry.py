"""Local JSON/JSONL registry for factor certification campaigns."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    CertifiedFactorPoolRecord,
    FactorCertificationCampaignRecord,
    FactorCertificationItemRecord,
)


class LocalFactorCertificationCampaignStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.registry_path = self.root_dir / "factor_certification_campaign_registry.json"
        self.campaigns_path = self.root_dir / "factor_certification_campaigns.jsonl"
        self.items_path = self.root_dir / "factor_certification_items.jsonl"
        self.report_path = self.root_dir / "factor_certification_campaign_report.json"
        self.certified_pool_path = self.root_dir / "certified_factor_pool.jsonl"
        self.leaderboard_path = self.root_dir / "certified_factor_leaderboard.jsonl"
        self.artifact_catalog_path = self.root_dir / "factor_certification_campaign_artifact_catalog.json"

    def register_campaign(self, record: FactorCertificationCampaignRecord) -> None:
        self._upsert_jsonl(self.campaigns_path, [record], "factor_certification_campaigns", "certification_campaign_id")
        self.write_registry()

    def write_items(self, records: Iterable[FactorCertificationItemRecord | dict[str, Any]]) -> None:
        write_jsonl_artifact(self.items_path, [_payload(row) for row in records], "factor_certification_items", "certification_campaign_store")
        self.write_registry()

    def write_certified_pool(self, records: Iterable[CertifiedFactorPoolRecord | dict[str, Any]]) -> None:
        write_jsonl_artifact(self.certified_pool_path, [_payload(row) for row in records], "certified_factor_pool", "certification_campaign_store")
        self.write_registry()

    def write_leaderboard(self, records: Iterable[CertifiedFactorPoolRecord | dict[str, Any]]) -> None:
        payloads = []
        for idx, row in enumerate(records):
            payload = _payload(row)
            payload["rank"] = idx + 1
            payloads.append(payload)
        write_jsonl_artifact(self.leaderboard_path, payloads, "certified_factor_leaderboard", "certification_campaign_store")
        self.write_registry()

    def load_campaigns(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.campaigns_path)

    def load_items(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.items_path)

    def load_certified_pool(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.certified_pool_path)

    def load_leaderboard(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.leaderboard_path)

    def write_registry(self) -> Path:
        campaigns = self.load_campaigns()
        items = self.load_items()
        pool = self.load_certified_pool()
        failed = [row for row in items if str(row.get("status")) in {"failed", "error"}]
        payload = {
            "status": "partial" if failed else ("ready" if pool else ("running" if items else "registered")),
            "campaign_count": len(campaigns),
            "item_count": len(items),
            "failed_item_count": len(failed),
            "certified_factor_pool_count": len(pool),
            "leaderboard_count": len(self.load_leaderboard()),
            "campaigns": campaigns,
            "paths": self.paths(),
        }
        return write_json_artifact(self.registry_path, payload, "factor_certification_campaign_registry", "certification_campaign_store")

    def paths(self) -> dict[str, str]:
        return {
            "factor_certification_campaign_registry_path": str(self.registry_path),
            "factor_certification_campaigns_path": str(self.campaigns_path),
            "factor_certification_items_path": str(self.items_path),
            "factor_certification_campaign_report_path": str(self.report_path),
            "certified_factor_pool_path": str(self.certified_pool_path),
            "certified_factor_leaderboard_path": str(self.leaderboard_path),
            "factor_certification_campaign_artifact_catalog_path": str(self.artifact_catalog_path),
        }

    def _upsert_jsonl(self, path: Path, records: Iterable[Any], artifact_type: str, key: str) -> None:
        current = {str(row.get(key)): row for row in _read_jsonl(path)}
        for record in records:
            payload = _payload(record)
            current[str(payload.get(key))] = payload
        write_jsonl_artifact(path, current.values(), artifact_type, "certification_campaign_store")


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
