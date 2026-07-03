"""Factor store shard consolidation for Alpha Factory campaigns."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact
from factor_store import ExperimentRecord, FactorRecord, LocalFactorStore

from .models import AlphaConsolidatedFactorRecord


STATUS_PRIORITY = {
    "approved": 5,
    "production_candidate": 5,
    "candidate": 4,
    "rejected": 3,
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
