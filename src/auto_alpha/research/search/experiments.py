"""Experiment store models, registry, ingest, consolidation, leaderboard, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AlphaExperimentRecord:
    experiment_id: str
    campaign_id: str
    campaign_name: str
    data_freeze_id: str | None = None
    data_freeze_hash: str | None = None
    feature_set_name: str | None = None
    feature_set_hash: str | None = None
    matrix_cache_id: str | None = None
    matrix_cache_hash: str | None = None
    candidate_budget: int = 0
    shard_count: int = 0
    compute_run_id: str | None = None
    status: str = "registered"
    created_at: str = ""
    source_paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaShardRecord:
    shard_id: str
    experiment_id: str
    shard_index: int
    shard_count: int
    formula_count: int = 0
    evaluated_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    factor_store_dir: str | None = None
    batch_eval_result_path: str | None = None
    eval_results_path: str | None = None
    compute_job_id: str | None = None
    status: str = "registered"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaConsolidatedFactorRecord:
    consolidated_factor_id: str
    factor_id: str
    formula_hash: str
    feature_version: str
    operator_version: str
    campaign_id: str
    shard_id: str
    source: str
    status: str
    score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    coverage: float = 0.0
    correlation_cluster_id: str | None = None
    family_tags: list[str] = field(default_factory=list)
    novelty_score: float = 0.0
    diversity_group: str | None = None
    selected_for_validation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaLeaderboardRecord:
    rank: int
    factor_id: str
    formula_hash: str
    final_score: float
    score_components: dict[str, float]
    validation_ready: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlphaStoreWriteResult:
    path: str
    records: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



class LocalAlphaExperimentStore:
    """Small JSON/JSONL warehouse for campaign-level Alpha Factory outputs."""

    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.experiments_path = self.root_dir / "alpha_experiments.jsonl"
        self.shards_path = self.root_dir / "alpha_shards.jsonl"
        self.consolidated_path = self.root_dir / "alpha_consolidated_factors.jsonl"
        self.leaderboard_path = self.root_dir / "alpha_leaderboard.jsonl"
        self.registry_path = self.root_dir / "alpha_experiment_registry.json"
        self.validation_pool_path = self.root_dir / "alpha_validation_candidate_pool.jsonl"
        self.report_path = self.root_dir / "alpha_experiment_store_report.json"

    def register_experiment(self, record: AlphaExperimentRecord) -> AlphaStoreWriteResult:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        records = self.load_experiments()
        payloads = [item.to_dict() for item in records if item.experiment_id != record.experiment_id]
        payloads.append(record.to_dict())
        write_jsonl_artifact(self.experiments_path, payloads, "alpha_experiments", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.experiments_path), 1)

    def register_shard(self, record: AlphaShardRecord) -> AlphaStoreWriteResult:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        records = self.load_shards()
        payloads = [item.to_dict() for item in records if item.shard_id != record.shard_id]
        payloads.append(record.to_dict())
        write_jsonl_artifact(self.shards_path, payloads, "alpha_shards", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.shards_path), 1)

    def write_consolidated_factors(self, records: Iterable[AlphaConsolidatedFactorRecord | dict[str, Any]]) -> AlphaStoreWriteResult:
        payloads = [_to_payload(item) for item in records]
        write_jsonl_artifact(self.consolidated_path, payloads, "alpha_consolidated_factors", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.consolidated_path), len(payloads))

    def write_leaderboard(self, records: Iterable[AlphaLeaderboardRecord | dict[str, Any]]) -> AlphaStoreWriteResult:
        payloads = [_to_payload(item) for item in records]
        write_jsonl_artifact(self.leaderboard_path, payloads, "alpha_leaderboard", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.leaderboard_path), len(payloads))

    def write_validation_candidate_pool(self, records: Iterable[dict[str, Any]]) -> AlphaStoreWriteResult:
        payloads = [dict(item) for item in records]
        write_jsonl_artifact(self.validation_pool_path, payloads, "alpha_validation_candidate_pool", "alpha_experiment_store")
        self.write_registry_summary()
        return AlphaStoreWriteResult(str(self.validation_pool_path), len(payloads))

    def load_experiments(self) -> list[AlphaExperimentRecord]:
        return [AlphaExperimentRecord(**_experiment_defaults(row)) for row in _read_jsonl(self.experiments_path)]

    def load_shards(self) -> list[AlphaShardRecord]:
        return [AlphaShardRecord(**_shard_defaults(row)) for row in _read_jsonl(self.shards_path)]

    def load_consolidated_factors(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.consolidated_path)

    def load_leaderboard(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.leaderboard_path)

    def load_validation_candidate_pool(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.validation_pool_path)

    def write_registry_summary(self) -> Path:
        experiments = [item.to_dict() for item in self.load_experiments()]
        shards = [item.to_dict() for item in self.load_shards()]
        consolidated = self.load_consolidated_factors()
        leaderboard = self.load_leaderboard()
        payload = {
            "status": _registry_status(experiments, shards, leaderboard),
            "experiment_count": len(experiments),
            "shard_count": len(shards),
            "consolidated_factor_count": len(consolidated),
            "leaderboard_count": len(leaderboard),
            "validation_candidate_count": len(self.load_validation_candidate_pool()),
            "experiments": experiments,
            "paths": {
                "alpha_experiments_path": str(self.experiments_path),
                "alpha_shards_path": str(self.shards_path),
                "alpha_consolidated_factors_path": str(self.consolidated_path),
                "alpha_leaderboard_path": str(self.leaderboard_path),
                "alpha_validation_candidate_pool_path": str(self.validation_pool_path),
            },
        }
        return write_json_artifact(self.registry_path, payload, "alpha_experiment_registry", "alpha_experiment_store")


def _to_payload(record: object) -> dict[str, Any]:
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


def _experiment_defaults(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.setdefault("data_freeze_id", None)
    payload.setdefault("data_freeze_hash", None)
    payload.setdefault("feature_set_name", None)
    payload.setdefault("feature_set_hash", None)
    payload.setdefault("matrix_cache_id", None)
    payload.setdefault("matrix_cache_hash", None)
    payload.setdefault("candidate_budget", 0)
    payload.setdefault("shard_count", 0)
    payload.setdefault("compute_run_id", None)
    payload.setdefault("status", "registered")
    payload.setdefault("created_at", "")
    payload.setdefault("source_paths", {})
    payload.setdefault("metadata", {})
    return payload


def _shard_defaults(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.setdefault("formula_count", 0)
    payload.setdefault("evaluated_count", 0)
    payload.setdefault("approved_count", 0)
    payload.setdefault("rejected_count", 0)
    payload.setdefault("error_count", 0)
    payload.setdefault("factor_store_dir", None)
    payload.setdefault("batch_eval_result_path", None)
    payload.setdefault("eval_results_path", None)
    payload.setdefault("compute_job_id", None)
    payload.setdefault("status", "registered")
    payload.setdefault("error", None)
    payload.setdefault("metadata", {})
    return payload


def _registry_status(experiments: list[dict[str, Any]], shards: list[dict[str, Any]], leaderboard: list[dict[str, Any]]) -> str:
    if any(str(item.get("status")) in {"failed", "error"} for item in shards):
        return "partial"
    if experiments and leaderboard:
        return "ready"
    if experiments:
        return "registered"
    return "empty"

import json
from pathlib import Path
from typing import Any

from auto_alpha.research.factors.store import LocalFactorStore



def ingest_alpha_factory_run(
    store_dir: str | Path,
    *,
    campaign_report_path: str | Path | None = None,
    campaign_manifest_path: str | Path | None = None,
    paths: dict[str, str] | None = None,
    shard_factor_store_dirs: list[str | Path] | None = None,
    experiment_id: str | None = None,
    consolidate_shards: bool = False,
    consolidated_factor_store_dir: str | Path | None = None,
    write_leaderboard_flag: bool = False,
    validation_candidate_pool_dir: str | Path | None = None,
    leaderboard_top_k: int = 100,
    max_validation_candidates: int = 50,
    previous_experiment_dirs: list[str | Path] | None = None,
) -> dict[str, Any]:
    store = LocalAlphaExperimentStore(store_dir)
    report = _read_json(campaign_report_path)
    manifest = _read_json(campaign_manifest_path) or _read_json((paths or {}).get("alpha_campaign_manifest_path"))
    campaign_id = str(report.get("campaign_id") or manifest.get("campaign_id") or experiment_id or "alpha_campaign")
    record = AlphaExperimentRecord(
        experiment_id=experiment_id or campaign_id,
        campaign_id=campaign_id,
        campaign_name=str(manifest.get("campaign_name") or "alpha_campaign"),
        data_freeze_id=manifest.get("data_freeze_id"),
        data_freeze_hash=manifest.get("data_freeze_hash"),
        feature_set_name=manifest.get("feature_set_name"),
        feature_set_hash=manifest.get("feature_version"),
        candidate_budget=int((manifest.get("generator_budgets") or {}).get("candidate_budget", 0) or 0),
        shard_count=int((manifest.get("compute_config") or {}).get("shard_count", 0) or 0),
        compute_run_id=(report.get("summary") or {}).get("compute_run_report_path"),
        status=str(report.get("status") or "registered"),
        created_at=str(manifest.get("created_at") or report.get("created_at") or ""),
        source_paths={k: str(v) for k, v in (paths or {}).items() if v},
        metadata={"summary": report.get("summary", {}), "warnings": report.get("warnings", [])},
    )
    store.register_experiment(record)

    discovered = list(shard_factor_store_dirs or [])
    discovered.extend(discover_shard_factor_stores(paths, Path(campaign_report_path).parent if campaign_report_path else None))
    if not discovered and paths and paths.get("factor_store_dir"):
        discovered.append(paths["factor_store_dir"])
    unique_dirs = _unique_paths(discovered)
    for idx, shard_dir in enumerate(unique_dirs):
        shard = _shard_record(shard_dir, record.experiment_id, idx, len(unique_dirs), paths or {})
        store.register_shard(shard)

    output_factor_store_dir = Path(consolidated_factor_store_dir) if consolidated_factor_store_dir else store.root_dir / "consolidated_factor_store"
    dedup_report: dict[str, Any] = {}
    if consolidate_shards and unique_dirs:
        dedup_report = consolidate_factor_stores(
            unique_dirs,
            output_factor_store_dir,
            experiment_id=record.experiment_id,
            campaign_id=campaign_id,
            report_dir=store.root_dir,
        )
        store.write_consolidated_factors(dedup_report.get("consolidated_factors", []))
    factor_store_for_leaderboard = output_factor_store_dir if (output_factor_store_dir / "factors.jsonl").exists() else None
    if write_leaderboard_flag and factor_store_for_leaderboard:
        leaderboard = build_leaderboard_from_factor_store(factor_store_for_leaderboard, top_k=leaderboard_top_k, campaign_id=campaign_id)
        store.write_leaderboard(leaderboard)
        write_leaderboard(leaderboard, store.root_dir)
        pool_dir = Path(validation_candidate_pool_dir) if validation_candidate_pool_dir else store.root_dir
        pool_path, pool_records = write_validation_candidate_pool(
            leaderboard,
            pool_dir,
            max_candidates=max_validation_candidates,
            factor_store_dir=str(factor_store_for_leaderboard),
        )
        if pool_dir != store.root_dir:
            store.write_validation_candidate_pool(pool_records)
        else:
            store.write_validation_candidate_pool(pool_records)
    report_json, report_md = write_store_report(store, {"dedup_report": dedup_report, "previous_experiment_dirs": [str(p) for p in previous_experiment_dirs or []]})
    store.write_registry_summary()
    return {
        "status": "success",
        "experiment_id": record.experiment_id,
        "campaign_id": campaign_id,
        "shard_count": len(unique_dirs),
        "consolidated_factor_store_dir": str(output_factor_store_dir),
        "dedup_report": dedup_report,
        "paths": {
            "alpha_experiment_registry_path": str(store.registry_path),
            "alpha_experiment_store_report_path": str(report_json),
            "alpha_experiment_store_report_md_path": str(report_md),
            "alpha_experiments_path": str(store.experiments_path),
            "alpha_shards_path": str(store.shards_path),
            "alpha_consolidated_factors_path": str(store.consolidated_path),
            "alpha_leaderboard_path": str(store.leaderboard_path),
            "alpha_validation_candidate_pool_path": str(store.validation_pool_path),
            "alpha_factor_dedup_report_path": str(store.root_dir / "alpha_factor_dedup_report.json"),
        },
    }


def _shard_record(shard_dir: str | Path, experiment_id: str, idx: int, count: int, paths: dict[str, str]) -> AlphaShardRecord:
    store_dir = Path(shard_dir)
    store = LocalFactorStore(store_dir)
    factors = store.load_factors()
    status_counts: dict[str, int] = {}
    for factor in factors:
        status_counts[factor.status] = status_counts.get(factor.status, 0) + 1
    output_dir = store_dir.parent / "output" if store_dir.name == "factor_store" else store_dir.parent
    result_path = output_dir / "formula_batch_eval_result.json"
    eval_results_path = output_dir / "formula_eval_results.jsonl"
    payload = _read_json(result_path)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return AlphaShardRecord(
        shard_id=f"{experiment_id}_shard_{idx:04d}",
        experiment_id=experiment_id,
        shard_index=idx,
        shard_count=count,
        formula_count=len(factors),
        evaluated_count=int(summary.get("total", len(factors)) or len(factors)),
        approved_count=status_counts.get("validation_candidate", 0),
        rejected_count=status_counts.get("research_rejected", 0),
        error_count=status_counts.get("error", 0),
        factor_store_dir=str(store_dir),
        batch_eval_result_path=str(result_path) if result_path.exists() else None,
        eval_results_path=str(eval_results_path) if eval_results_path.exists() else None,
        compute_job_id=_compute_job_id(paths, idx),
        status="success" if result_path.exists() or factors else "registered",
        metadata={"status_counts": status_counts},
    )


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _unique_paths(paths: list[str | Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        target = Path(path)
        key = str(target.resolve()) if target.exists() else str(target)
        if key not in seen and target.exists():
            seen.add(key)
            unique.append(target)
    return unique


def _compute_job_id(paths: dict[str, str], idx: int) -> str | None:
    runs_path = paths.get("compute_job_runs_path")
    if not runs_path or not Path(runs_path).exists():
        return None
    rows = [json.loads(line) for line in Path(runs_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if idx < len(rows):
        return str(rows[idx].get("job_id") or "")
    return None

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def compare_experiment_stores(current_store_dir: str | Path, previous_store_dirs: list[str | Path], output_dir: str | Path | None = None) -> dict[str, Any]:
    current = LocalAlphaExperimentStore(current_store_dir)
    current_rows = current.load_consolidated_factors()
    current_hashes = {str(row.get("formula_hash")) for row in current_rows if row.get("formula_hash")}
    previous_summary: list[dict[str, Any]] = []
    all_previous_hashes: set[str] = set()
    for store_dir in previous_store_dirs:
        store = LocalAlphaExperimentStore(store_dir)
        rows = store.load_consolidated_factors()
        hashes = {str(row.get("formula_hash")) for row in rows if row.get("formula_hash")}
        all_previous_hashes.update(hashes)
        previous_summary.append(
            {
                "store_dir": str(store_dir),
                "factor_count": len(rows),
                "overlap_count": len(current_hashes & hashes),
            }
        )
    payload = {
        "status": "success",
        "current_store_dir": str(current_store_dir),
        "previous_store_count": len(previous_store_dirs),
        "current_factor_count": len(current_rows),
        "previous_unique_formula_count": len(all_previous_hashes),
        "overlap_count": len(current_hashes & all_previous_hashes),
        "new_formula_count": len(current_hashes - all_previous_hashes),
        "previous": previous_summary,
    }
    if output_dir:
        write_json_artifact(Path(output_dir) / "alpha_campaign_comparison_report.json", payload, "alpha_campaign_comparison_report", "alpha_experiment_store")
    return payload

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact
from auto_alpha.research.factors.store import ExperimentRecord, FactorRecord, LocalFactorStore



STATUS_PRIORITY = {
    "validation_candidate": 6,
    "historical_replay_passed": 5,
    "approved": 5,
    "production_candidate": 5,
    "research_evaluated": 4,
    "candidate": 4,
    "research_rejected": 3,
    "rejected": 3,
    "composite_unvalidated": 2,
    "error": 2,
    "skipped": 1,
}


def discover_shard_factor_stores(paths: dict[str, str] | None = None, root_dir: str | Path | None = None) -> list[Path]:
    discovered: list[Path] = []
    for value in (paths or {}).values():
        path = Path(str(value))
        if path.name == "factor_store" and path.exists():
            discovered.append(path)
        if path.name == "output" and (path.parent / "factor_store").exists():
            discovered.append(path.parent / "factor_store")
    if root_dir:
        root = Path(root_dir)
        for path in sorted(root.glob("**/factor_store")):
            if (path / "factors.jsonl").exists():
                discovered.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in discovered:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def consolidate_factor_stores(
    shard_factor_store_dirs: list[str | Path],
    output_factor_store_dir: str | Path,
    *,
    experiment_id: str = "",
    campaign_id: str = "",
    report_dir: str | Path | None = None,
) -> dict[str, Any]:
    output_store_dir = Path(output_factor_store_dir)
    output_values_dir = output_store_dir / "factor_values"
    output_store_dir.mkdir(parents=True, exist_ok=True)
    output_values_dir.mkdir(parents=True, exist_ok=True)

    inputs = [Path(path) for path in shard_factor_store_dirs if Path(path).exists()]
    candidates: dict[tuple[str, str, str], list[tuple[FactorRecord, Path]]] = {}
    input_factor_count = 0
    input_value_count = 0
    for store_dir in inputs:
        store = LocalFactorStore(store_dir)
        for factor in store.load_factors():
            input_factor_count += 1
            key = (factor.formula_hash, factor.feature_version or "", factor.operator_version or "")
            candidates.setdefault(key, []).append((factor, store_dir))
        values_dir = store_dir / "factor_values"
        input_value_count += len(list(values_dir.glob("*.jsonl"))) if values_dir.exists() else 0

    selected: list[tuple[FactorRecord, Path, list[tuple[FactorRecord, Path]]]] = []
    duplicate_count = 0
    for records in candidates.values():
        ordered = sorted(records, key=lambda item: _factor_rank(item[0]), reverse=True)
        selected.append((ordered[0][0], ordered[0][1], ordered))
        duplicate_count += max(0, len(records) - 1)

    used_factor_ids: dict[str, str] = {}
    merged_factors: list[FactorRecord] = []
    consolidated: list[AlphaConsolidatedFactorRecord] = []
    factor_id_map: dict[tuple[str, str], str] = {}
    conflict_count = 0
    skipped_count = 0
    values_copied = 0

    for factor, source_dir, duplicate_records in selected:
        original_factor_id = factor.factor_id
        new_factor_id = _dedupe_factor_id(factor, used_factor_ids)
        factor_id_map[(str(source_dir), original_factor_id)] = new_factor_id
        source_refs = [
            {
                "factor_id": item.factor_id,
                "factor_store_dir": str(store_dir),
                "status": item.status,
                "score": _score(item),
            }
            for item, store_dir in duplicate_records
        ]
        metadata = dict(factor.metadata or {})
        metadata.update(
            {
                "alpha_experiment_id": experiment_id,
                "alpha_campaign_id": campaign_id,
                "source_factor_id": original_factor_id,
                "source_factor_store_dir": str(source_dir),
                "source_refs": source_refs,
                "duplicate_source_count": len(source_refs),
            }
        )
        merged = FactorRecord(
            factor_id=new_factor_id,
            formula=factor.formula,
            formula_tokens=factor.formula_tokens,
            formula_hash=factor.formula_hash,
            feature_version=factor.feature_version,
            operator_version=factor.operator_version,
            lookback_days=factor.lookback_days,
            created_at=factor.created_at,
            status=factor.status,
            description=factor.description,
            metrics=factor.metrics,
            transform_method=factor.transform_method,
            gate_status=factor.gate_status,
            gate_reasons=factor.gate_reasons,
            metadata=metadata,
            parent_factor_ids=factor.parent_factor_ids,
            factor_type=factor.factor_type,
            batch_id=factor.batch_id or experiment_id or None,
        )
        merged_factors.append(merged)
        consolidated.append(
            AlphaConsolidatedFactorRecord(
                consolidated_factor_id=f"consolidated_{factor.formula_hash[:16]}",
                factor_id=new_factor_id,
                formula_hash=factor.formula_hash,
                feature_version=factor.feature_version,
                operator_version=factor.operator_version,
                campaign_id=campaign_id,
                shard_id=_source_shard_id(source_dir),
                source=str(source_dir),
                status=factor.status,
                score=_score(factor),
                metrics=dict(factor.metrics or {}),
                coverage=_coverage(factor),
                family_tags=list(metadata.get("alpha_family_tags", metadata.get("family_tags", [])) or []),
                novelty_score=float(metadata.get("novelty_score", 0.0) or 0.0),
                diversity_group=str(metadata.get("diversity_group", "") or ""),
                metadata=metadata,
            )
        )
        source_values = source_dir / "factor_values" / f"{original_factor_id}.jsonl"
        if not source_values.exists():
            skipped_count += 1
            continue
        target_values = output_values_dir / f"{new_factor_id}.jsonl"
        if target_values.exists() and _sha256(target_values) != _sha256(source_values):
            conflict_count += 1
            continue
        _copy_factor_values(source_values, target_values, original_factor_id, new_factor_id)
        values_copied += 1

    _write_factor_records(output_store_dir / "factors.jsonl", merged_factors)
    _write_experiments(inputs, output_store_dir / "experiments.jsonl", factor_id_map)

    report = {
        "status": "warning" if conflict_count else "success",
        "experiment_id": experiment_id,
        "campaign_id": campaign_id,
        "input_shard_count": len(inputs),
        "input_factor_count": input_factor_count,
        "unique_formula_count": len(candidates),
        "duplicate_count": duplicate_count,
        "merged_factor_count": len(merged_factors),
        "factor_values_file_count": values_copied,
        "input_factor_values_file_count": input_value_count,
        "conflict_count": conflict_count,
        "skipped_count": skipped_count,
        "output_factor_store_dir": str(output_store_dir),
        "source_factor_store_dirs": [str(path) for path in inputs],
    }
    target_report_dir = Path(report_dir) if report_dir else output_store_dir
    report_path = write_json_artifact(target_report_dir / "alpha_factor_dedup_report.json", report, "alpha_factor_dedup_report", "alpha_experiment_store")
    report["alpha_factor_dedup_report_path"] = str(report_path)
    report["consolidated_factors"] = [item.to_dict() for item in consolidated]
    return report


def _factor_rank(factor: FactorRecord) -> tuple[int, float, str]:
    return (STATUS_PRIORITY.get(str(factor.status), 0), _score(factor), factor.factor_id)


def _score(factor: FactorRecord) -> float:
    metrics = factor.metrics or {}
    metadata = factor.metadata or {}
    for key in ("score", "final_score", "full_eval_score", "rank_ic_ir", "rank_ic"):
        if key in metrics:
            return float(metrics.get(key) or 0.0)
        if key in metadata:
            return float(metadata.get(key) or 0.0)
    return 0.0


def _coverage(factor: FactorRecord) -> float:
    metrics = factor.metrics or {}
    return float(metrics.get("coverage", metrics.get("coverage_ratio", 0.0)) or 0.0)


def _dedupe_factor_id(factor: FactorRecord, used: dict[str, str]) -> str:
    existing_hash = used.get(factor.factor_id)
    if existing_hash is None:
        used[factor.factor_id] = factor.formula_hash
        return factor.factor_id
    if existing_hash == factor.formula_hash:
        return factor.factor_id
    new_id = f"factor_{factor.formula_hash[:16]}"
    suffix = 1
    candidate = new_id
    while candidate in used and used[candidate] != factor.formula_hash:
        suffix += 1
        candidate = f"{new_id}_{suffix}"
    used[candidate] = factor.formula_hash
    return candidate


def _source_shard_id(source_dir: Path) -> str:
    parent = source_dir.parent.name if source_dir.name == "factor_store" else source_dir.name
    return parent or "shard"


def _copy_factor_values(source: Path, target: Path, old_factor_id: str, new_factor_id: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if old_factor_id == new_factor_id:
        shutil.copyfile(source, target)
        return
    with source.open("r", encoding="utf-8") as src, target.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            row["factor_id"] = new_factor_id
            dst.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_factor_records(path: Path, records: list[FactorRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=lambda item: (item.formula_hash, item.factor_id)):
            handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True) + "\n")


def _write_experiments(inputs: list[Path], path: Path, factor_id_map: dict[tuple[str, str], str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for source_dir in inputs:
        for experiment in LocalFactorStore(source_dir).load_experiments():
            factor_id = factor_id_map.get((str(source_dir), experiment.factor_id))
            if not factor_id:
                continue
            payload = asdict(
                ExperimentRecord(
                    experiment_id=experiment.experiment_id,
                    factor_id=factor_id,
                    data_dir=experiment.data_dir,
                    output_dir=experiment.output_dir,
                    train_dates=experiment.train_dates,
                    valid_dates=experiment.valid_dates,
                    test_dates=experiment.test_dates,
                    metrics_by_split=experiment.metrics_by_split,
                    created_at=experiment.created_at,
                    notes=experiment.notes,
                )
            )
            rows.append(payload)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_jsonl_artifact
from auto_alpha.research.search.evaluation import bounded_factor_score
from auto_alpha.research.factors.store import FactorRecord, LocalFactorStore, has_positive_oos_evidence, validation_admission_reason



def build_leaderboard_from_factor_store(
    factor_store_dir: str | Path,
    *,
    top_k: int = 100,
    campaign_id: str = "",
) -> list[AlphaLeaderboardRecord]:
    store = LocalFactorStore(factor_store_dir)
    return build_leaderboard(store.load_factors(), top_k=top_k, factor_store_dir=str(factor_store_dir), campaign_id=campaign_id)


def build_leaderboard(
    factors: list[FactorRecord],
    *,
    top_k: int = 100,
    factor_store_dir: str = "",
    campaign_id: str = "",
) -> list[AlphaLeaderboardRecord]:
    rows: list[AlphaLeaderboardRecord] = []
    for factor in factors:
        components = _score_components(factor)
        final = components["standardized_final_score"]
        metadata = dict(factor.metadata or {})
        metadata.update(
            {
                "factor_store_dir": factor_store_dir,
                "campaign_id": campaign_id or metadata.get("alpha_campaign_id", ""),
                "feature_version": factor.feature_version,
                "formula_names": list(factor.formula),
                "factor_values_path": str(Path(factor_store_dir) / "factor_values" / f"{factor.factor_id}.jsonl") if factor_store_dir else "",
                "factor_status": factor.status,
            }
        )
        rows.append(
            AlphaLeaderboardRecord(
                rank=0,
                factor_id=factor.factor_id,
                formula_hash=factor.formula_hash,
                final_score=float(final),
                score_components=components,
                validation_ready=has_positive_oos_evidence(factor),
                reason=_leaderboard_reason(factor, components),
                metadata=metadata,
            )
        )
    ordered = sorted(rows, key=lambda row: (row.validation_ready, row.final_score, row.factor_id), reverse=True)
    limited = ordered[: max(0, int(top_k or len(ordered)))]
    return [
        AlphaLeaderboardRecord(
            rank=idx + 1,
            factor_id=row.factor_id,
            formula_hash=row.formula_hash,
            final_score=row.final_score,
            score_components=row.score_components,
            validation_ready=row.validation_ready,
            reason=row.reason,
            metadata=row.metadata,
        )
        for idx, row in enumerate(limited)
    ]


def write_leaderboard(records: list[AlphaLeaderboardRecord], output_dir: str | Path) -> Path:
    return write_jsonl_artifact(Path(output_dir) / "alpha_leaderboard.jsonl", records, "alpha_leaderboard", "alpha_experiment_store")


def write_validation_candidate_pool(
    leaderboard: list[AlphaLeaderboardRecord],
    output_dir: str | Path,
    *,
    max_candidates: int = 50,
    factor_store_dir: str = "",
) -> tuple[Path, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    seen_family: dict[str, int] = {}
    for row in leaderboard:
        if not row.validation_ready:
            continue
        metadata = dict(row.metadata or {})
        family = _family(metadata)
        if seen_family.get(family, 0) >= 10:
            continue
        seen_family[family] = seen_family.get(family, 0) + 1
        store_dir = metadata.get("factor_store_dir") or factor_store_dir
        records.append(
            {
                "factor_id": row.factor_id,
                "formula_hash": row.formula_hash,
                "formula_names": metadata.get("formula_names", []),
                "feature_version": metadata.get("feature_version", ""),
                "source_campaign": metadata.get("campaign_id", ""),
                "rank": row.rank,
                "final_score": row.final_score,
                "score_components": row.score_components,
                "factor_store_dir": str(store_dir),
                "factor_values_path": metadata.get("factor_values_path")
                or str(Path(str(store_dir)) / "factor_values" / f"{row.factor_id}.jsonl"),
                "recommended_validation_split": "walk_forward_long_history",
                "family": family,
                "metadata": metadata,
                "factor_status": metadata.get("factor_status", ""),
            }
        )
        if len(records) >= max(0, int(max_candidates)):
            break
    path = write_jsonl_artifact(Path(output_dir) / "alpha_validation_candidate_pool.jsonl", records, "alpha_validation_candidate_pool", "alpha_experiment_store")
    return path, records


def load_candidate_pool(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _score_components(factor: FactorRecord) -> dict[str, float]:
    metrics = factor.metrics or {}
    metadata = factor.metadata or {}
    complexity = float(metadata.get("formula_complexity", metadata.get("complexity", len(factor.formula_tokens))) or 0.0)
    max_corr = abs(float(metadata.get("max_abs_correlation", 0.0) or 0.0))
    leakage_status = str(metadata.get("leakage_status", metadata.get("pit_status", "passed")) or "passed")
    campaign_score = metadata.get("final_score")
    score_method = str(((metadata.get("score_components") or {}).get("score_method") or ""))
    if campaign_score is not None and score_method == "dimensionless_cohort_multi_objective_v1":
        standardized = float(campaign_score)
    else:
        standardized, _ = bounded_factor_score(metrics)
    return {
        "standardized_final_score": float(standardized),
        "base_score": float(standardized),
        "coverage": float(metrics.get("coverage", metrics.get("coverage_ratio", 0.0)) or 0.0),
        "turnover": float(metrics.get("turnover", 0.0) or 0.0),
        "complexity": complexity,
        "correlation_penalty": max(0.0, max_corr - 0.80),
        "novelty": float(metadata.get("novelty_score", 0.0) or 0.0),
        "diversity": 1.0 if metadata.get("diversity_group") else 0.0,
        "pit_penalty": 0.0 if leakage_status in {"passed", "ok", "ready", ""} else 1.0,
    }


def _leaderboard_reason(factor: FactorRecord, components: dict[str, float]) -> str:
    admission_reason = validation_admission_reason(factor)
    if admission_reason != "validation_candidate_admitted":
        return admission_reason
    if components["correlation_penalty"] > 0:
        return "correlation penalty applied"
    if components["pit_penalty"] > 0:
        return "PIT readiness penalty applied"
    return "validation ready"


def _family(metadata: dict[str, Any]) -> str:
    tags = metadata.get("alpha_family_tags") or metadata.get("family_tags") or []
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return str(metadata.get("family", "general") or "general")

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def write_store_report(store: LocalAlphaExperimentStore, extra: dict[str, Any] | None = None) -> tuple[Path, Path]:
    experiments = [item.to_dict() for item in store.load_experiments()]
    shards = [item.to_dict() for item in store.load_shards()]
    consolidated = store.load_consolidated_factors()
    leaderboard = store.load_leaderboard()
    candidate_pool = store.load_validation_candidate_pool()
    failed_shards = [row for row in shards if str(row.get("status")) in {"failed", "error"}]
    payload = {
        "status": "partial" if failed_shards else ("ready" if leaderboard else "registered"),
        "experiment_count": len(experiments),
        "shard_count": len(shards),
        "failed_shard_count": len(failed_shards),
        "consolidated_factor_count": len(consolidated),
        "leaderboard_count": len(leaderboard),
        "validation_candidate_count": len(candidate_pool),
        "experiments": experiments,
        "summary": {
            "leaderboard_empty": len(leaderboard) == 0,
            "validation_pool_ready": len(candidate_pool) > 0,
        },
        "paths": {
            "alpha_experiment_registry_path": str(store.registry_path),
            "alpha_experiments_path": str(store.experiments_path),
            "alpha_shards_path": str(store.shards_path),
            "alpha_consolidated_factors_path": str(store.consolidated_path),
            "alpha_leaderboard_path": str(store.leaderboard_path),
            "alpha_validation_candidate_pool_path": str(store.validation_pool_path),
        },
        "extra": extra or {},
    }
    json_path = write_json_artifact(store.report_path, payload, "alpha_experiment_store_report", "alpha_experiment_store")
    md_path = store.root_dir / "alpha_experiment_store_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Alpha Experiment Store Report",
            "",
            f"- Status: {payload.get('status')}",
            f"- Experiments: {payload.get('experiment_count', 0)}",
            f"- Shards: {payload.get('shard_count', 0)}",
            f"- Failed shards: {payload.get('failed_shard_count', 0)}",
            f"- Consolidated factors: {payload.get('consolidated_factor_count', 0)}",
            f"- Leaderboard rows: {payload.get('leaderboard_count', 0)}",
            f"- Validation candidates: {payload.get('validation_candidate_count', 0)}",
            "",
        ]
    )

import argparse
import json
from pathlib import Path

from auto_alpha.research.factors.store import FactorRecord, LocalFactorStore



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local Alpha Factory experiment store artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--store-dir", required=True)
    register.add_argument("--experiment-id", required=True)
    register.add_argument("--campaign-id", required=True)
    register.add_argument("--campaign-name", default="alpha_campaign")
    register.add_argument("--status", default="registered")
    register.add_argument("--pretty", action="store_true")

    ingest = sub.add_parser("ingest")
    _add_store_args(ingest)
    ingest.add_argument("--alpha-factory-report-path")
    ingest.add_argument("--alpha-campaign-manifest-path")
    ingest.add_argument("--experiment-id")
    ingest.add_argument("--shard-factor-store-dir", action="append", default=[])
    ingest.add_argument("--consolidate-shards", action="store_true")
    ingest.add_argument("--consolidated-factor-store-dir")
    ingest.add_argument("--write-leaderboard", action="store_true")
    ingest.add_argument("--validation-candidate-pool-dir")
    ingest.add_argument("--leaderboard-top-k", type=int, default=100)
    ingest.add_argument("--max-validation-candidates", type=int, default=50)
    ingest.add_argument("--pretty", action="store_true")

    consolidate = sub.add_parser("consolidate")
    _add_store_args(consolidate)
    consolidate.add_argument("--shard-factor-store-dir", action="append", required=True)
    consolidate.add_argument("--output-factor-store-dir", required=True)
    consolidate.add_argument("--experiment-id", default="")
    consolidate.add_argument("--campaign-id", default="")
    consolidate.add_argument("--write-leaderboard", action="store_true")
    consolidate.add_argument("--validation-candidate-pool-dir")
    consolidate.add_argument("--leaderboard-top-k", type=int, default=100)
    consolidate.add_argument("--max-validation-candidates", type=int, default=50)
    consolidate.add_argument("--pretty", action="store_true")

    leaderboard = sub.add_parser("leaderboard")
    _add_store_args(leaderboard)
    leaderboard.add_argument("--factor-store-dir", required=True)
    leaderboard.add_argument("--top-k", type=int, default=100)
    leaderboard.add_argument("--validation-candidate-pool-dir")
    leaderboard.add_argument("--max-validation-candidates", type=int, default=50)
    leaderboard.add_argument("--pretty", action="store_true")

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--output-dir", required=True)
    smoke.add_argument("--pretty", action="store_true")
    return parser


def _add_store_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--store-dir", required=True)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "register":
        store = LocalAlphaExperimentStore(args.store_dir)
        store.register_experiment(
            AlphaExperimentRecord(
                experiment_id=args.experiment_id,
                campaign_id=args.campaign_id,
                campaign_name=args.campaign_name,
                status=args.status,
            )
        )
        json_path, _md_path = write_store_report(store)
        payload = {"status": "success", "alpha_experiment_store_report_path": str(json_path)}
    elif args.command == "ingest":
        payload = ingest_alpha_factory_run(
            args.store_dir,
            campaign_report_path=args.alpha_factory_report_path,
            campaign_manifest_path=args.alpha_campaign_manifest_path,
            shard_factor_store_dirs=args.shard_factor_store_dir,
            experiment_id=args.experiment_id,
            consolidate_shards=args.consolidate_shards,
            consolidated_factor_store_dir=args.consolidated_factor_store_dir,
            write_leaderboard_flag=args.write_leaderboard,
            validation_candidate_pool_dir=args.validation_candidate_pool_dir,
            leaderboard_top_k=args.leaderboard_top_k,
            max_validation_candidates=args.max_validation_candidates,
        )
    elif args.command == "consolidate":
        payload = _run_consolidate(args)
    elif args.command == "leaderboard":
        payload = _run_leaderboard(args)
    elif args.command == "smoke":
        payload = _run_smoke(args.output_dir)
    else:
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None, sort_keys=getattr(args, "pretty", False)))
    return 0 if payload.get("status") in {"success", "warning"} else 1


def _run_consolidate(args: argparse.Namespace) -> dict:
    report = consolidate_factor_stores(
        args.shard_factor_store_dir,
        args.output_factor_store_dir,
        experiment_id=args.experiment_id,
        campaign_id=args.campaign_id,
        report_dir=args.store_dir,
    )
    store = LocalAlphaExperimentStore(args.store_dir)
    store.write_consolidated_factors(report.get("consolidated_factors", []))
    paths = {"alpha_factor_dedup_report_path": report.get("alpha_factor_dedup_report_path", "")}
    if args.write_leaderboard:
        leaderboard = build_leaderboard_from_factor_store(args.output_factor_store_dir, top_k=args.leaderboard_top_k, campaign_id=args.campaign_id)
        store.write_leaderboard(leaderboard)
        pool_dir = args.validation_candidate_pool_dir or args.store_dir
        pool_path, pool_rows = write_validation_candidate_pool(
            leaderboard,
            pool_dir,
            max_candidates=args.max_validation_candidates,
            factor_store_dir=args.output_factor_store_dir,
        )
        store.write_validation_candidate_pool(pool_rows)
        paths.update({"alpha_leaderboard_path": str(store.leaderboard_path), "alpha_validation_candidate_pool_path": str(pool_path)})
    report_json, report_md = write_store_report(store, {"dedup_report": report})
    return {
        "status": report["status"],
        "merged_factor_count": report["merged_factor_count"],
        "unique_formula_count": report["unique_formula_count"],
        "duplicate_count": report["duplicate_count"],
        "paths": paths | {
            "alpha_experiment_store_report_path": str(report_json),
            "alpha_experiment_store_report_md_path": str(report_md),
        },
    }


def _run_leaderboard(args: argparse.Namespace) -> dict:
    store = LocalAlphaExperimentStore(args.store_dir)
    leaderboard = build_leaderboard_from_factor_store(args.factor_store_dir, top_k=args.top_k)
    store.write_leaderboard(leaderboard)
    pool_path = None
    if args.validation_candidate_pool_dir:
        pool_path, pool_rows = write_validation_candidate_pool(
            leaderboard,
            args.validation_candidate_pool_dir,
            max_candidates=args.max_validation_candidates,
            factor_store_dir=args.factor_store_dir,
        )
        store.write_validation_candidate_pool(pool_rows)
    report_json, _ = write_store_report(store)
    return {
        "status": "success",
        "leaderboard_count": len(leaderboard),
        "validation_candidate_pool_path": str(pool_path) if pool_path else "",
        "alpha_experiment_store_report_path": str(report_json),
    }


def _run_smoke(output_dir: str | Path) -> dict:
    root = Path(output_dir)
    shard_dirs = [root / "shards" / "shard_0000" / "factor_store", root / "shards" / "shard_0001" / "factor_store"]
    for idx, store_dir in enumerate(shard_dirs):
        _write_fake_factor_store(store_dir, idx)
    store_dir = root / "store"
    merged_store = root / "merged_factor_store"
    store = LocalAlphaExperimentStore(store_dir)
    store.register_experiment(
        AlphaExperimentRecord(
            experiment_id="alpha_store_smoke",
            campaign_id="alpha_store_smoke",
            campaign_name="alpha_store_smoke",
            candidate_budget=4,
            shard_count=2,
            status="success",
        )
    )
    for idx, shard_dir in enumerate(shard_dirs):
        store.register_shard(
            AlphaShardRecord(
                shard_id=f"alpha_store_smoke_shard_{idx:04d}",
                experiment_id="alpha_store_smoke",
                shard_index=idx,
                shard_count=2,
                formula_count=2,
                evaluated_count=2,
                approved_count=1,
                factor_store_dir=str(shard_dir),
                status="success",
            )
        )
    report = consolidate_factor_stores(shard_dirs, merged_store, experiment_id="alpha_store_smoke", campaign_id="alpha_store_smoke", report_dir=store_dir)
    store.write_consolidated_factors(report.get("consolidated_factors", []))
    leaderboard = build_leaderboard_from_factor_store(merged_store, top_k=10, campaign_id="alpha_store_smoke")
    store.write_leaderboard(leaderboard)
    pool_path, pool_rows = write_validation_candidate_pool(leaderboard, store_dir, max_candidates=4, factor_store_dir=str(merged_store))
    store.write_validation_candidate_pool(pool_rows)
    report_json, report_md = write_store_report(store, {"dedup_report": report})
    return {
        "status": "success",
        "merged_factor_count": report["merged_factor_count"],
        "leaderboard_count": len(leaderboard),
        "validation_candidate_count": len(pool_rows),
        "paths": {
            "alpha_experiment_store_report_path": str(report_json),
            "alpha_experiment_store_report_md_path": str(report_md),
            "alpha_validation_candidate_pool_path": str(pool_path),
            "consolidated_factor_store_dir": str(merged_store),
        },
    }


def _write_fake_factor_store(store_dir: Path, idx: int) -> None:
    store = LocalFactorStore(store_dir)
    rows = [
        ("duplicate_hash", "factor_duplicate_a" if idx == 0 else "factor_duplicate_b", "validation_candidate" if idx == 1 else "research_evaluated", 0.7 + idx * 0.1),
        (f"unique_hash_{idx}", f"factor_unique_{idx}", "validation_candidate", 0.5 + idx * 0.1),
    ]
    for formula_hash, factor_id, status, score in rows:
        store.save_factor(
            FactorRecord(
                factor_id=factor_id,
                formula=["RET_1D"],
                formula_tokens=[0],
                formula_hash=formula_hash,
                feature_version="ashare_features_v1",
                operator_version="ashare_ops_v1",
                lookback_days=1,
                created_at="2026-07-03T00:00:00Z",
                status=status,
                metrics={"score": score, "coverage": 1.0, "turnover": 0.1},
                metadata={
                    "formula_complexity": 1,
                    "novelty_score": 0.2,
                    "alpha_family_tags": ["return"],
                    "gate_decision": {
                        "passed": status == "validation_candidate",
                        "checks": {
                            "oos_evidence_positive": status == "validation_candidate",
                            "test_evaluable_date_count": 2.0,
                            "test_valid_observation_count": 4.0,
                            "test_rank_ic_mean": 0.1,
                        },
                    },
                },
            )
        )
        store.save_factor_values(factor_id, ["000001.SZ", "000002.SZ"], ["20240102", "20240103"], [[1.0, 2.0], [2.0, 3.0]])


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "AlphaConsolidatedFactorRecord",
    "AlphaExperimentRecord",
    "AlphaLeaderboardRecord",
    "AlphaShardRecord",
    "LocalAlphaExperimentStore",
    "build_leaderboard",
    "build_leaderboard_from_factor_store",
    "consolidate_factor_stores",
    "discover_shard_factor_stores",
    "ingest_alpha_factory_run",
    "load_candidate_pool",
    "write_validation_candidate_pool",
]
