"""Portfolio campaign ingest, registry, scheduling, consolidation, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortfolioCertificationCampaignRecord:
    portfolio_campaign_id: str
    source_factor_certification_campaign_id: str | None
    certified_factor_pool_path: str
    data_freeze_id: str | None = None
    portfolio_policy_profile: str = "sample_lenient_portfolio"
    scenario_profile: str = "sample"
    factor_count: int = 0
    status: str = "registered"
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioCandidateItemRecord:
    item_id: str
    factor_id: str
    formula_hash: str = ""
    certified_factor_pool_rank: int = 0
    factor_store_dir: str = ""
    portfolio_lab_output_dir: str = ""
    portfolio_lab_report_path: str | None = None
    selected_portfolio_policy_path: str | None = None
    portfolio_certification_decision_path: str | None = None
    certified_portfolio_policy_path: str | None = None
    status: str = "pending"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionCandidateBundleRecord:
    production_candidate_bundle_id: str
    factor_id: str
    model_version_id: str | None
    portfolio_policy_id: str | None
    optimizer_policy_model_version_id: str | None
    factor_certification_status: str
    portfolio_certification_status: str
    validation_score: float = 0.0
    portfolio_score: float = 0.0
    scenario_pass_ratio: float = 0.0
    capacity_summary: dict[str, Any] = field(default_factory=dict)
    risk_summary: dict[str, Any] = field(default_factory=dict)
    settlement_summary: dict[str, Any] = field(default_factory=dict)
    readiness_status: str = "pending_activation_review"
    selected_for_activation_review: bool = True
    reason: str = ""
    artifact_refs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



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
        return _campaigns_registry_read_jsonl(self.campaigns_path)

    def load_items(self) -> list[dict[str, Any]]:
        return _campaigns_registry_read_jsonl(self.items_path)

    def load_bundle(self) -> list[dict[str, Any]]:
        return _campaigns_registry_read_jsonl(self.bundle_path)

    def load_activation_queue(self) -> list[dict[str, Any]]:
        return _campaigns_registry_read_jsonl(self.activation_queue_path)

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
        current = {str(row.get(key)): row for row in _campaigns_registry_read_jsonl(path)}
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


def _campaigns_registry_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def consolidate_portfolio_campaign(store_dir: str | Path) -> dict[str, Any]:
    store = LocalPortfolioCampaignStore(store_dir)
    bundle: list[Any] = []
    activation_queue: list[dict[str, Any]] = []
    ordered = sorted(bundle, key=lambda row: (row.portfolio_score, row.validation_score), reverse=True)
    store.write_bundle(ordered)
    store.write_activation_queue(activation_queue)
    report = {
        "status": "success",
        "item_count": len(store.load_items()),
        "production_candidate_bundle_count": len(ordered),
        "optimizer_policy_activation_queue_count": len(activation_queue),
        "best_production_candidate_score": max((row.portfolio_score for row in ordered), default=0.0),
        "legacy_portfolio_campaign_superseded_by": "portfolio_research",
        "shadow_only": True,
    }
    report_path = store.bundle_report_path
    write_json_artifact(report_path, report, "production_candidate_bundle_report", "portfolio_campaign_store")
    return {**report, "paths": store.paths()}

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def ingest_certified_factor_pool(
    store_dir: str | Path,
    certified_factor_pool_path: str | Path,
    *,
    portfolio_campaign_id: str | None = None,
    max_items: int | None = None,
    rank_range: str | None = None,
    family_filter: str | None = None,
    source_filter: str | None = None,
    portfolio_policy_profile: str = "sample_lenient_portfolio",
    scenario_profile: str = "sample",
) -> dict[str, Any]:
    path = Path(certified_factor_pool_path)
    source_rows = _campaigns_ingest_read_jsonl(path)
    invalid = [row for row in source_rows if str(row.get("certification_status") or "") != "factor_certified"]
    if invalid:
        raise ValueError("portfolio campaign accepts only factor_certified records")
    rows = [row for row in source_rows if row.get("selected_for_portfolio_lab", True)]
    rows = _apply_rank_range(rows, rank_range)
    rows = _apply_family_filter(rows, family_filter)
    rows = _apply_source_filter(rows, source_filter)
    if max_items and max_items > 0:
        rows = rows[:max_items]
    campaign_id = portfolio_campaign_id or f"portfolio_campaign_{_hash(str(path) + str(len(rows)))}"
    store = LocalPortfolioCampaignStore(store_dir)
    campaign = PortfolioCertificationCampaignRecord(
        portfolio_campaign_id=campaign_id,
        source_factor_certification_campaign_id=_source_campaign_id(rows),
        certified_factor_pool_path=str(path),
        portfolio_policy_profile=portfolio_policy_profile,
        scenario_profile=scenario_profile,
        factor_count=len(rows),
        status="registered",
        created_at=_utc_now(),
        metadata={"source_factor_count": len(rows)},
    )
    items = [_item_from_pool(row, campaign_id, idx + 1) for idx, row in enumerate(rows)]
    store.register_campaign(campaign)
    store.write_items(items)
    report = {
        "status": "success",
        "portfolio_campaign_id": campaign_id,
        "factor_count": len(rows),
        "item_count": len(items),
        "family_filter": family_filter or "",
        "source_filter": source_filter or "",
    }
    report_path = store.root_dir / "portfolio_campaign_ingest_report.json"
    write_json_artifact(report_path, report, "portfolio_campaign_ingest_report", "portfolio_campaign_store")
    return {**report, "paths": store.paths() | {"portfolio_campaign_ingest_report_path": str(report_path)}}


def _item_from_pool(row: dict[str, Any], campaign_id: str, idx: int) -> PortfolioCandidateItemRecord:
    return PortfolioCandidateItemRecord(
        item_id=f"pci_{campaign_id}_{idx:04d}_{row.get('factor_id')}",
        factor_id=str(row.get("factor_id")),
        formula_hash=str(row.get("formula_hash") or ""),
        certified_factor_pool_rank=int(row.get("priority", idx) or idx),
        factor_store_dir=str(row.get("factor_store_dir") or ""),
        status="pending",
        metadata={"certified_factor_pool_record": row},
    )


def _source_campaign_id(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        item = metadata.get("campaign_item", {}) if isinstance(metadata.get("campaign_item"), dict) else {}
        value = item.get("item_id")
        if value:
            return str(value).split("_000", 1)[0]
    return None


def _apply_rank_range(rows: list[dict[str, Any]], rank_range: str | None) -> list[dict[str, Any]]:
    if not rank_range:
        return rows
    start, _, end = rank_range.partition(":")
    lo = int(start or 1)
    hi = int(end or len(rows))
    return [row for row in rows if lo <= int(row.get("priority", row.get("certified_factor_pool_rank", 0)) or 0) <= hi]


def _apply_family_filter(rows: list[dict[str, Any]], family_filter: str | None) -> list[dict[str, Any]]:
    families = _split_filter(family_filter)
    if not families:
        return rows
    return [row for row in rows if families & _row_families(row)]


def _apply_source_filter(rows: list[dict[str, Any]], source_filter: str | None) -> list[dict[str, Any]]:
    sources = _split_filter(source_filter)
    if not sources:
        return rows
    return [row for row in rows if _row_source(row) in sources]


def _row_families(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    item = metadata.get("campaign_item", {}) if isinstance(metadata.get("campaign_item"), dict) else {}
    queue_item = item.get("metadata", {}).get("queue_item", {}) if isinstance(item.get("metadata"), dict) else {}
    values = row.get("family_tags") or item.get("family_tags") or queue_item.get("family_tags") or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if value}


def _row_source(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    item = metadata.get("campaign_item", {}) if isinstance(metadata.get("campaign_item"), dict) else {}
    queue_item = item.get("metadata", {}).get("queue_item", {}) if isinstance(item.get("metadata"), dict) else {}
    return str(
        row.get("source")
        or row.get("source_campaign_id")
        or item.get("source")
        or queue_item.get("source")
        or queue_item.get("source_campaign_id")
        or ""
    )


def _split_filter(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def _campaigns_ingest_read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def write_portfolio_campaign_report(store: LocalPortfolioCampaignStore, extra: dict[str, Any] | None = None) -> tuple[Path, Path]:
    campaigns = store.load_campaigns()
    items = store.load_items()
    bundle = store.load_bundle()
    queue = store.load_activation_queue()
    failed = [row for row in items if str(row.get("status")) in {"failed", "error"}]
    payload = {
        "status": "partial" if failed else ("ready" if bundle else ("running" if items else "registered")),
        "campaign_count": len(campaigns),
        "item_count": len(items),
        "failed_item_count": len(failed),
        "production_candidate_bundle_count": len(bundle),
        "optimizer_policy_activation_queue_count": len(queue),
        "best_production_candidate_score": max((float(row.get("portfolio_score", 0.0) or 0.0) for row in bundle), default=0.0),
        "campaigns": campaigns,
        "summary": {
            "production_candidate_bundle_empty": len(bundle) == 0,
            "optimizer_policy_activation_queue_pending": len(queue),
        },
        "paths": store.paths(),
        "extra": extra or {},
    }
    json_path = write_json_artifact(store.report_path, payload, "portfolio_certification_campaign_report", "portfolio_campaign_store")
    md_path = store.root_dir / "portfolio_certification_campaign_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    write_json_artifact(store.artifact_catalog_path, {"artifact_count": len(store.paths()), "artifacts": store.paths()}, "portfolio_campaign_artifact_catalog", "portfolio_campaign_store")
    store.write_registry()
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Portfolio Certification Campaign Report",
            "",
            f"- Status: {payload.get('status')}",
            f"- Items: {payload.get('item_count', 0)}",
            f"- Failed items: {payload.get('failed_item_count', 0)}",
            f"- Production candidates: {payload.get('production_candidate_bundle_count', 0)}",
            f"- Activation queue: {payload.get('optimizer_policy_activation_queue_count', 0)}",
            "",
        ]
    )

import contextlib
import io
import json
from pathlib import Path
from typing import Any

from auto_alpha.portfolio.construction.certification import main as run_portfolio_certify_main
from auto_alpha.portfolio.construction.lab import main as run_portfolio_lab_main



def run_portfolio_campaign(
    store_dir: str | Path,
    *,
    data_dir: str | Path | None = None,
    factor_store_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    max_items: int | None = None,
    resume: bool = False,
    dry_run: bool = False,
    scenario_profile: str = "sample",
    portfolio_policy_profile: str = "sample_lenient_portfolio",
    index_code: str = "000300.SH",
    as_of_date: str = "20240104",
    max_trials: int = 1,
) -> dict[str, Any]:
    store = LocalPortfolioCampaignStore(store_dir)
    output_root = Path(output_dir or store.root_dir / "items")
    output_root.mkdir(parents=True, exist_ok=True)
    items = store.load_items()
    if max_items and max_items > 0:
        items = items[:max_items]
    updated = []
    success = failed = skipped = 0
    for item in items:
        if resume and item.get("status") == "success":
            skipped += 1
            updated.append(item)
            continue
        if dry_run:
            updated.append({**item, "status": "planned", "portfolio_lab_output_dir": str(output_root / str(item.get("item_id")) / "lab")})
            continue
        result = _run_item(
            item,
            output_root,
            data_dir=str(data_dir or ""),
            factor_store_dir=str(factor_store_dir or item.get("factor_store_dir") or ""),
            scenario_profile=scenario_profile,
            portfolio_policy_profile=portfolio_policy_profile,
            index_code=index_code,
            as_of_date=as_of_date,
            max_trials=max_trials,
        )
        updated.append(result)
        if result.get("status") == "success":
            success += 1
        else:
            failed += 1
    store.write_items(updated)
    return {
        "status": "planned" if dry_run else ("partial" if failed else "success"),
        "item_count": len(items),
        "success_count": success,
        "failed_count": failed,
        "skipped_count": skipped,
        "paths": store.paths(),
    }


def _run_item(
    item: dict[str, Any],
    output_root: Path,
    *,
    data_dir: str,
    factor_store_dir: str,
    scenario_profile: str,
    portfolio_policy_profile: str,
    index_code: str,
    as_of_date: str,
    max_trials: int,
) -> dict[str, Any]:
    item_root = output_root / str(item.get("item_id"))
    lab_dir = item_root / "portfolio_lab"
    cert_dir = item_root / "portfolio_certification"
    pool_record = (item.get("metadata") or {}).get("certified_factor_pool_record", {}) if isinstance(item.get("metadata"), dict) else {}
    factor_certifacts = pool_record.get("certification_artifacts", {}) if isinstance(pool_record.get("certification_artifacts"), dict) else {}
    validation_artifacts = pool_record.get("validation_artifacts", {}) if isinstance(pool_record.get("validation_artifacts"), dict) else {}
    try:
        lab_argv = [
            "run",
            "--data-dir",
            data_dir,
            "--factor-store-dir",
            factor_store_dir,
            "--output-dir",
            str(lab_dir),
            "--factor-id",
            str(item.get("factor_id")),
            "--factor-type",
            "any",
            "--index-code",
            index_code,
            "--as-of-date",
            as_of_date,
            "--scenario-profile",
            scenario_profile,
            "--max-trials",
            str(max_trials),
        ]
        with contextlib.redirect_stdout(io.StringIO()) as lab_stdout:
            lab_exit = run_portfolio_lab_main(lab_argv)
        if lab_exit != 0:
            raise RuntimeError(lab_stdout.getvalue().strip() or f"portfolio lab exit code {lab_exit}")
        lab_payload = json.loads(lab_stdout.getvalue() or "{}")
        paths = lab_payload.get("paths", {}) if isinstance(lab_payload.get("paths"), dict) else {}
        selected_policy = paths.get("selected_portfolio_policy_path") or str(lab_dir / "selected_portfolio_policy.json")
        cert_argv = [
            "run",
            "--factor-store-dir",
            factor_store_dir,
            "--factor-id",
            str(item.get("factor_id")),
            "--factor-type",
            "any",
            "--selected-portfolio-policy-path",
            str(selected_policy),
            "--portfolio-lab-report-path",
            str(paths.get("portfolio_lab_report_path") or lab_dir / "portfolio_lab_report.json"),
            "--portfolio-robustness-report-path",
            str(paths.get("portfolio_robustness_report_path") or lab_dir / "portfolio_robustness_report.json"),
            "--output-dir",
            str(cert_dir),
            "--policy-profile",
            portfolio_policy_profile,
        ]
        if factor_certifacts.get("decision_path"):
            cert_argv.extend(["--factor-certification-decision-path", str(factor_certifacts["decision_path"])])
        if validation_artifacts.get("validation_lab_report_path"):
            cert_argv.extend(["--validation-lab-report-path", str(validation_artifacts["validation_lab_report_path"])])
        with contextlib.redirect_stdout(io.StringIO()) as cert_stdout:
            cert_exit = run_portfolio_certify_main(cert_argv)
        if cert_exit != 0:
            raise RuntimeError(cert_stdout.getvalue().strip() or f"portfolio certification exit code {cert_exit}")
        cert_payload = json.loads(cert_stdout.getvalue() or "{}")
        cert_paths = cert_payload.get("paths", {}) if isinstance(cert_payload.get("paths"), dict) else {}
        return {
            **item,
            "status": "success",
            "portfolio_lab_output_dir": str(lab_dir),
            "portfolio_lab_report_path": paths.get("portfolio_lab_report_path"),
            "selected_portfolio_policy_path": selected_policy,
            "portfolio_certification_decision_path": cert_paths.get("portfolio_certification_decision_path"),
            "certified_portfolio_policy_path": cert_paths.get("certified_portfolio_policy_path"),
            "error": None,
            "metadata": {**(item.get("metadata") or {}), "portfolio_lab_result": lab_payload, "portfolio_certification_result": cert_payload},
        }
    except Exception as exc:
        return {**item, "status": "failed", "portfolio_lab_output_dir": str(lab_dir), "error": str(exc)}

__all__ = ["LocalPortfolioCampaignStore"]

import argparse
import json
from pathlib import Path



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run portfolio certification campaign workflows.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["ingest", "plan", "run", "consolidate", "bundle", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--portfolio-campaign-store-dir", required=True)
    parser.add_argument("--portfolio-campaign-id")
    parser.add_argument("--certified-factor-pool-path")
    parser.add_argument("--data-dir")
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--rank-range")
    parser.add_argument("--family-filter")
    parser.add_argument("--source-filter")
    parser.add_argument("--portfolio-policy-profile", default="sample_lenient_portfolio")
    parser.add_argument("--scenario-profile", default="sample")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--max-trials", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-portfolio-ready", action="store_true")
    parser.add_argument("--research-readiness-decision-path")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        payload = {"status": "error", "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if payload.get("status") in {"success", "planned", "partial", "blocked", "ready"} else 1


def _run(args: argparse.Namespace) -> dict:
    store = LocalPortfolioCampaignStore(args.portfolio_campaign_store_dir)
    readiness = _readiness(args.research_readiness_decision_path)
    if args.require_portfolio_ready and not readiness["ready"]:
        json_path, md_path = write_portfolio_campaign_report(store, {"blocked_reason": "portfolio readiness not satisfied", "readiness": readiness})
        return {"status": "blocked", "blocked_reason": "portfolio readiness not satisfied", "paths": store.paths() | {"portfolio_certification_campaign_report_path": str(json_path), "portfolio_certification_campaign_report_md_path": str(md_path)}}
    if args.command == "smoke":
        payload = _smoke(args)
    elif args.command == "ingest":
        if not args.certified_factor_pool_path:
            raise ValueError("--certified-factor-pool-path is required")
        payload = ingest_certified_factor_pool(
            args.portfolio_campaign_store_dir,
            args.certified_factor_pool_path,
            portfolio_campaign_id=args.portfolio_campaign_id,
            max_items=args.max_items or None,
            rank_range=args.rank_range,
            family_filter=args.family_filter,
            source_filter=args.source_filter,
            portfolio_policy_profile=args.portfolio_policy_profile,
            scenario_profile=args.scenario_profile,
        )
    elif args.command == "plan":
        payload = run_portfolio_campaign(args.portfolio_campaign_store_dir, output_dir=args.output_dir, max_items=args.max_items or None, dry_run=True)
    elif args.command == "run":
        if args.certified_factor_pool_path and not store.load_items():
            ingest_certified_factor_pool(
                args.portfolio_campaign_store_dir,
                args.certified_factor_pool_path,
                portfolio_campaign_id=args.portfolio_campaign_id,
                max_items=args.max_items or None,
                rank_range=args.rank_range,
                family_filter=args.family_filter,
                source_filter=args.source_filter,
                portfolio_policy_profile=args.portfolio_policy_profile,
                scenario_profile=args.scenario_profile,
            )
        payload = run_portfolio_campaign(
            args.portfolio_campaign_store_dir,
            data_dir=args.data_dir,
            factor_store_dir=args.factor_store_dir,
            output_dir=args.output_dir,
            max_items=args.max_items or None,
            resume=args.resume,
            dry_run=args.dry_run,
            scenario_profile=args.scenario_profile,
            portfolio_policy_profile=args.portfolio_policy_profile,
            index_code=args.index_code,
            as_of_date=args.as_of_date,
            max_trials=args.max_trials,
        )
        if not args.dry_run:
            payload = payload | consolidate_portfolio_campaign(args.portfolio_campaign_store_dir)
    elif args.command in {"consolidate", "bundle"}:
        payload = consolidate_portfolio_campaign(args.portfolio_campaign_store_dir)
    else:
        raise ValueError(f"unsupported command: {args.command}")
    json_path, md_path = write_portfolio_campaign_report(store, {"last_command": args.command})
    payload.setdefault("paths", store.paths())
    payload["paths"] = payload["paths"] | {"portfolio_certification_campaign_report_path": str(json_path), "portfolio_certification_campaign_report_md_path": str(md_path)}
    return payload


def _smoke(args: argparse.Namespace) -> dict:
    root = Path(args.output_dir or args.portfolio_campaign_store_dir)
    root.mkdir(parents=True, exist_ok=True)
    pool = root / "certified_factor_pool.jsonl"
    rows = [
        {
            "certified_factor_pool_id": "cfp_smoke_0001",
            "factor_id": "factor_smoke_0001",
            "formula_hash": "hash_smoke_0001",
            "certification_status": "factor_certified",
            "validation_score": 1.0,
            "certification_score": 1.5,
            "priority": 1,
            "factor_store_dir": str(root / "factor_store"),
            "selected_for_portfolio_lab": True,
        }
    ]
    pool.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return ingest_certified_factor_pool(args.portfolio_campaign_store_dir, pool, portfolio_campaign_id=args.portfolio_campaign_id or "portfolio_campaign_smoke", max_items=1)


def _readiness(path: str | None) -> dict:
    if not path:
        return {"ready": True, "status": "not_required"}
    target = Path(path)
    if not target.exists():
        return {"ready": False, "status": "missing", "path": str(target)}
    payload = json.loads(target.read_text(encoding="utf-8"))
    ready = bool(payload.get("portfolio_ready") or payload.get("can_run_portfolio_campaign") or payload.get("ready_for_portfolio"))
    ready = ready or str(payload.get("status")) in {"ready", "portfolio_ready", "ready_for_portfolio", "pass"}
    return {"ready": ready, "status": payload.get("status"), "path": str(target)}


if __name__ == "__main__":
    raise SystemExit(main())
