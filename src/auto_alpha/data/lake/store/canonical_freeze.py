"""Canonical immutable A-share research freeze with physically bounded views."""

from __future__ import annotations

import hashlib
import heapq
import inspect
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np

from auto_alpha.data.ingestion.pipeline.ashare.dataset_registry import (
    DATASET_DEFINITIONS,
    DATASET_PRIMARY_KEYS,
    FULL_RESEARCH_DATASETS,
)


SCHEMA_VERSION = "canonical_ashare_research_freeze_v1"
SOURCE_CUTOFF = "20260630"
REQUIRED_DATASETS = tuple(dict.fromkeys(FULL_RESEARCH_DATASETS))
CORE_GATE_DATASETS = {
    "securities",
    "trade_calendar",
    "st_status_daily",
    "name_changes",
    "suspensions",
    "daily_bars",
    "daily_limits",
    "adjustment_factors",
    "daily_basic",
    "income_statements",
    "balance_sheets",
    "cashflow_statements",
    "industry_members",
    "index_members",
    "corporate_actions",
}
PERIODS = (
    ("bootstrap", "00000000", "20111231", "search"),
    ("research", "20120101", "20191231", "search"),
    ("validation", "20200101", "20221231", "controlled"),
    ("retrospective_test", "20230101", "20241231", "controlled"),
    ("sealed_holdout", "20250101", SOURCE_CUTOFF, "sealed"),
)
FULL_ENVELOPE_SCHEMA = pa.schema(
    [
        ("dataset", pa.string()),
        ("primary_key", pa.string()),
        ("ts_code", pa.string()),
        ("index_code", pa.string()),
        ("trade_date", pa.string()),
        ("ann_date", pa.string()),
        ("f_ann_date", pa.string()),
        ("end_date", pa.string()),
        ("list_date", pa.string()),
        ("delist_date", pa.string()),
        ("update_flag", pa.string()),
        ("availability_date", pa.string()),
        ("availability_known", pa.bool_()),
        ("availability_basis", pa.string()),
        ("effective_date", pa.string()),
        ("effective_known", pa.bool_()),
        ("effective_basis", pa.string()),
        ("field_availability_json", pa.binary()),
        ("field_effective_json", pa.binary()),
        ("source_row_number", pa.int64()),
        ("raw_json", pa.binary()),
        ("observable_json", pa.binary()),
    ]
)
RESEARCH_ENVELOPE_SCHEMA = pa.schema(
    [field for field in FULL_ENVELOPE_SCHEMA if field.name != "raw_json"]
)


class CanonicalFreezeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalFreezeConfig:
    governed_root: str
    output_root: str
    source_cutoff: str = SOURCE_CUTOFF
    batch_rows: int = 50_000
    sample_size: int = 1_000
    workers: int = 1


def audit_canonical_freeze_sources(config: CanonicalFreezeConfig) -> dict[str, Any]:
    governed_root = Path(config.governed_root).resolve()
    output_root = Path(config.output_root).resolve()
    raw_index_path = _resolve_reviewed_raw_index(governed_root)
    raw_index = _read_json(raw_index_path)
    raw_dataset_rows = list(raw_index.get("datasets") or [])
    dataset_names = [str(row.get("dataset") or "") for row in raw_dataset_rows]
    datasets = {str(row["dataset"]): row for row in raw_dataset_rows if row.get("dataset")}
    source_rows = []
    blockers: list[str] = []
    warnings: list[str] = []
    if len(dataset_names) != len(set(dataset_names)):
        blockers.append("reviewed_raw_index_duplicate_dataset_rows")
    if int(raw_index.get("dataset_count", -1)) != len(raw_dataset_rows):
        blockers.append("reviewed_raw_index_dataset_count_mismatch")
    for dataset in REQUIRED_DATASETS:
        index_row = datasets.get(dataset)
        contract = _dataset_contract(dataset)
        row = {
            "dataset": dataset,
            "required": True,
            "core_gate": dataset in CORE_GATE_DATASETS,
            "contract": contract,
            "status": "missing",
            "records_relative_path": None,
            "record_count": 0,
            "records_sha256": None,
            "first_date": None,
            "last_date": None,
            "fields": [],
            "source_eligible": False,
            "blockers": [],
            "warnings": [],
        }
        if index_row is None:
            row["blockers"].append("dataset_missing_from_reviewed_raw_index")
        else:
            source = Path(str(index_row.get("records_path") or ""))
            row.update(
                {
                    "status": str(index_row.get("status") or "unknown"),
                    "record_count": int(index_row.get("record_count", 0) or 0),
                    "records_sha256": index_row.get("records_sha256"),
                    "first_date": index_row.get("first_date"),
                    "last_date": index_row.get("last_date"),
                    "primary_key_fields": list(index_row.get("primary_key_fields") or contract["primary_key"]),
                    "raw_index_evidence": {
                        key: index_row.get(key)
                        for key in (
                            "dataset",
                            "record_count",
                            "file_size_bytes",
                            "records_sha256",
                            "first_date",
                            "last_date",
                            "ann_date_first",
                            "ann_date_last",
                            "end_date_first",
                            "end_date_last",
                            "status",
                            "primary_key_fields",
                        )
                    },
                }
            )
            try:
                row["records_relative_path"] = source.resolve().relative_to(governed_root).as_posix()
            except (OSError, ValueError):
                row["blockers"].append("records_path_outside_governed_root")
            declared_maxima = {
                field: str(index_row.get(field) or "")
                for field in ("last_date", "ann_date_last", "end_date_last")
                if index_row.get(field)
            }
            last_date = max(declared_maxima.values(), default="")
            row["declared_date_maxima"] = declared_maxima
            if last_date and last_date > config.source_cutoff:
                row["warnings"].append("source_requires_post_cutoff_filter")
            if not source.is_file():
                row["blockers"].append("records_file_missing")
            elif source.is_symlink():
                row["blockers"].append("records_file_symlink_forbidden")
            elif source.stat().st_size != int(index_row.get("file_size_bytes", -1)):
                row["blockers"].append("records_file_size_drift")
            else:
                fields = _sample_fields(source)
                row["fields"] = fields
                missing_fields = sorted(set(contract["required_fields"]) - set(fields))
                if missing_fields:
                    row["blockers"].append(f"contract_fields_missing:{','.join(missing_fields)}")
            if int(row["record_count"]) <= 0:
                row["blockers"].append("required_dataset_empty")
            if len(str(row.get("records_sha256") or "")) != 64:
                row["blockers"].append("records_sha256_missing_or_invalid")
            if tuple(row.get("primary_key_fields") or ()) != tuple(contract["primary_key"]):
                row["blockers"].append("raw_index_primary_key_contract_mismatch")
            if str(index_row.get("status")) != "fresh":
                row["blockers"].append("raw_index_dataset_not_fresh")
            if dataset == "suspensions":
                if {"suspend_date", "resume_date"} & set(row["fields"]) or "trade_date" not in set(row["fields"]):
                    row["blockers"].append("legacy_unusable_suspension_contract")
            if dataset == "name_changes" and str(row.get("first_date") or "99999999") > "20120101":
                row["blockers"].append("historical_name_change_coverage_incomplete")
            if dataset == "industry_members" and int(index_row.get("record_count", 0) or 0) <= int(index_row.get("ts_code_count", 0) or 0):
                row["blockers"].append("historical_industry_transition_proof_missing")
            row["source_eligible"] = not any(_is_materialization_blocker(reason) for reason in row["blockers"])
        source_rows.append(row)
        warnings.extend(f"{dataset}:{reason}" for reason in row["warnings"])
        if row["core_gate"]:
            blockers.extend(f"{dataset}:{reason}" for reason in row["blockers"])
        else:
            warnings.extend(f"{dataset}:{reason}" for reason in row["blockers"])
    derived = _discover_derived_bundle(governed_root)
    source_coverage = _discover_source_coverage_proof(governed_root)
    securities_count = int((datasets.get("securities") or {}).get("record_count", 0) or 0)
    for dataset in ("st_status_daily", "suspensions", "name_changes"):
        proof = (source_coverage.get("datasets") or {}).get(dataset) or {}
        if not proof.get("complete"):
            blockers.append(f"{dataset}:full_market_request_coverage_proof_missing")
            continue
        if int(proof.get("security_count", 0) or 0) != securities_count:
            blockers.append(f"{dataset}:full_market_security_coverage_mismatch")
        if str(proof.get("start_date") or "99999999") > "20120101":
            blockers.append(f"{dataset}:research_period_coverage_starts_late")
        if str(proof.get("end_date") or "") < config.source_cutoff:
            blockers.append(f"{dataset}:coverage_ends_before_source_cutoff")
    if not derived["strict_matrix_present"]:
        blockers.append("strict_matrix_missing_from_canonical_lineage")
    if not derived["feature_validity_present"]:
        blockers.append("feature_validity_missing_from_canonical_lineage")
    if not derived["feature_values_present"]:
        blockers.append("feature_values_missing_from_canonical_lineage")
    if not derived["target_availability_present"]:
        blockers.append("target_availability_missing_from_canonical_lineage")
    if not derived["axes_present"]:
        blockers.append("stock_date_feature_axes_missing_from_canonical_lineage")
    period_policy = _period_policy_payload()
    source_catalog = {
        "schema_version": "canonical_ashare_source_catalog_v1",
        "governed_root_identity": _root_identity(governed_root),
        "raw_index_relative_path": raw_index_path.relative_to(governed_root).as_posix(),
        "raw_index_sha256": _sha256(raw_index_path),
        "raw_index_content_hash": raw_index.get("index_hash"),
        "raw_index_built_at": raw_index.get("built_at"),
        "raw_index_profile_name": raw_index.get("profile_name"),
        "source_cutoff": config.source_cutoff,
        "datasets": source_rows,
        "period_policy": period_policy,
        "derived_bundle": derived,
        "source_coverage_proof": source_coverage,
    }
    source_catalog_hash = _canonical_hash(source_catalog)
    status = "ready_to_build" if not blockers else "source_freeze_buildable_research_gate_blocked"
    report = {
        "schema_version": "canonical_ashare_freeze_preflight_v1",
        "status": status,
        "source_catalog_hash": source_catalog_hash,
        "source_catalog": source_catalog,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "alpha_search_authorized": False,
        "sealed_holdout_historically_observed": True,
        "sealed_holdout_untouched": False,
        "certification_ready": False,
    }
    generation_id = f"preflight_{_canonical_hash(report)[:24]}"
    target = output_root / "preflight" / "generations" / generation_id
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_bytes = _canonical_json_bytes(report, pretty=True)
    if target.exists():
        existing = target / "canonical_freeze_preflight.json"
        if not existing.is_file() or existing.read_bytes() != expected_bytes:
            raise CanonicalFreezeError("content-addressed preflight generation drift")
    else:
        temporary = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=target.parent))
        try:
            _atomic_bytes(temporary / "canonical_freeze_preflight.json", expected_bytes)
            os.replace(temporary, target)
            _make_immutable(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
    _atomic_json(
        output_root / "preflight" / "current.json",
        {
            "generation_id": generation_id,
            "manifest": f"generations/{generation_id}/canonical_freeze_preflight.json",
            "source_catalog_hash": source_catalog_hash,
        },
    )
    return report | {"report_path": str(target / "canonical_freeze_preflight.json"), "generation_dir": str(target)}


def build_canonical_research_freeze(config: CanonicalFreezeConfig) -> dict[str, Any]:
    preflight = audit_canonical_freeze_sources(config)
    output_root = Path(config.output_root).resolve()
    staging_parent = output_root / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".canonical_freeze.", dir=staging_parent))
    source_catalog = preflight["source_catalog"]
    build_source_semantic = _source_semantic_hash()
    dataset_results: list[dict[str, Any]] = []
    partition_rows: list[dict[str, Any]] = []
    search_partition_rows: list[dict[str, Any]] = []
    samples: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        _atomic_json(staging / "source_catalog.json", source_catalog)
        materialized_by_dataset: dict[str, dict[str, Any]] = {}
        eligible_rows = [row for row in source_catalog["datasets"] if row.get("source_eligible")]
        if int(config.workers) > 1 and len(eligible_rows) > 1:
            with ProcessPoolExecutor(max_workers=min(int(config.workers), len(eligible_rows))) as executor:
                futures = {
                    executor.submit(
                        _materialize_dataset,
                        source_row,
                        staging,
                        governed_root=Path(config.governed_root).resolve(),
                        batch_rows=config.batch_rows,
                        sample_size=config.sample_size,
                        source_cutoff=config.source_cutoff,
                    ): str(source_row["dataset"])
                    for source_row in eligible_rows
                }
                for future in as_completed(futures):
                    materialized_by_dataset[futures[future]] = future.result()
        else:
            for source_row in eligible_rows:
                materialized_by_dataset[str(source_row["dataset"])] = _materialize_dataset(
                    source_row,
                    staging,
                    governed_root=Path(config.governed_root).resolve(),
                    batch_rows=config.batch_rows,
                    sample_size=config.sample_size,
                    source_cutoff=config.source_cutoff,
                )
        for source_row in source_catalog["datasets"]:
            if not source_row.get("source_eligible"):
                dataset_results.append(
                    {
                        "dataset": source_row["dataset"],
                        "status": "blocked_not_materialized",
                        "blockers": list(source_row.get("blockers") or []),
                        "record_count": 0,
                        "partition_count": 0,
                    }
                )
                continue
            result = materialized_by_dataset[str(source_row["dataset"])]
            dataset_results.append(result["dataset"])
            partition_rows.extend(result["partitions"])
            search_partition_rows.extend(result["search_partitions"])
            samples[str(source_row["dataset"])] = result["samples"]
        reconciliation = _cross_source_reconciliation(samples)
        quality = {
            "schema_version": "canonical_ashare_freeze_quality_v1",
            "datasets": dataset_results,
            "cross_source_reconciliation": reconciliation,
            "source_dataset_count": len(source_catalog["datasets"]),
            "materialized_dataset_count": sum(str(row["status"]).startswith("materialized") for row in dataset_results),
            "blocked_dataset_count": sum(bool(row.get("blockers")) or not str(row["status"]).startswith("materialized") for row in dataset_results),
        }
        _atomic_json(staging / "quality_report.json", quality)
        partition_root = _canonical_hash(
            [{"path": row["relative_path"], "sha256": row["sha256"], "records": row["record_count"]} for row in partition_rows]
        )
        search_partition_root = _canonical_hash(
            [
                {"path": row["view_relative_path"], "sha256": row["sha256"], "records": row["record_count"]}
                for row in search_partition_rows
            ]
        )
        materialization_blockers = sorted(
            {
                f"{row['dataset']}:{reason}"
                for row in dataset_results
                if row["dataset"] in CORE_GATE_DATASETS
                for reason in row.get("blockers") or []
            }
        )
        reconciliation_blockers = sorted(
            {
                f"cross_source:{row['left']}:{row['right']}:{row['status']}"
                for row in reconciliation.get("pairs") or []
                if row.get("status") != "passed"
            }
        )
        source_semantic = _source_semantic_hash()
        if source_semantic != build_source_semantic:
            raise CanonicalFreezeError("freeze builder source semantics changed during materialization")
        period_coverage = _period_coverage(partition_rows)
        frozen_derived = _copy_derived_bundle_to_search_view(
            Path(config.governed_root).resolve(),
            source_catalog["derived_bundle"],
            staging,
        )
        core = {
            "schema_version": SCHEMA_VERSION,
            "source_catalog_hash": preflight["source_catalog_hash"],
            "period_policy": source_catalog["period_policy"],
            "partition_root": partition_root,
            "search_partition_root": search_partition_root,
            "period_coverage": period_coverage,
            "dataset_quality_root": _canonical_hash(dataset_results),
            "cross_source_reconciliation_hash": _canonical_hash(reconciliation),
            "source_semantic_hash": source_semantic,
            "strict_derived_bundle": frozen_derived,
            "blockers": sorted(set(preflight["blockers"] + materialization_blockers + reconciliation_blockers)),
            "warnings": list(preflight["warnings"]),
        }
        content_hash = _canonical_hash(core)
        generation_id = f"ashare_freeze_{content_hash[:24]}"
        search_manifest = _build_search_view_manifest(
            generation_id,
            content_hash,
            search_partition_rows,
            core,
        )
        _atomic_json(staging / "search_view" / "research_view_manifest.json", search_manifest)
        manifest = {
            **core,
            "generation_id": generation_id,
            "content_hash": content_hash,
            "status": "canonical_freeze_ready" if not core["blockers"] else "canonical_freeze_built_research_gate_blocked",
            "partitions": partition_rows,
            "partition_count": len(partition_rows),
            "dataset_count": len(dataset_results),
            "materialized_dataset_count": quality["materialized_dataset_count"],
            "source_catalog_sha256": _sha256(staging / "source_catalog.json"),
            "quality_report_sha256": _sha256(staging / "quality_report.json"),
            "search_view_manifest_sha256": _sha256(staging / "search_view" / "research_view_manifest.json"),
            "alpha_search_authorized": not core["blockers"],
            "sealed_holdout": {
                "period": "20250101-20260630",
                "physical_view": "sealed_holdout",
                "historically_observed": True,
                "untouched": False,
                "candidate_freeze_required_before_access": True,
                "content_root": _period_content_root(partition_rows, "sealed_holdout"),
            },
            "certification_ready": False,
        }
        _atomic_json(staging / "canonical_freeze_manifest.json", manifest)
        target = output_root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
            validated = validate_canonical_research_freeze(target / "canonical_freeze_manifest.json")
            if validated["content_hash"] != content_hash:
                raise CanonicalFreezeError("canonical freeze content-address collision")
            return validated | {"cache_hit": True}
        os.replace(staging, target)
        _make_immutable(target)
        _atomic_json(
            output_root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/canonical_freeze_manifest.json",
                "alpha_search_authorized": manifest["alpha_search_authorized"],
            },
        )
        validated = validate_canonical_research_freeze(target / "canonical_freeze_manifest.json")
        return validated | {"cache_hit": False}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def validate_canonical_research_freeze(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = _read_json(manifest_path)
    root = manifest_path.parent
    core_keys = (
        "schema_version",
        "source_catalog_hash",
        "period_policy",
        "partition_root",
        "search_partition_root",
        "period_coverage",
        "dataset_quality_root",
        "cross_source_reconciliation_hash",
        "source_semantic_hash",
        "strict_derived_bundle",
        "blockers",
        "warnings",
    )
    core = {key: manifest[key] for key in core_keys}
    if _canonical_hash(core) != manifest.get("content_hash"):
        raise CanonicalFreezeError("canonical freeze semantic content hash mismatch")
    actual_partitions = []
    for row in manifest.get("partitions") or []:
        relative = str(row.get("relative_path") or "")
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or not source.is_file() or source.is_symlink():
            raise CanonicalFreezeError(f"canonical freeze partition containment failure: {relative}")
        if _sha256(source) != row.get("sha256") or source.stat().st_size != int(row.get("size_bytes", -1)):
            raise CanonicalFreezeError(f"canonical freeze partition drift: {relative}")
        metadata = pq.read_metadata(source)
        if metadata.num_rows != int(row.get("record_count", -1)):
            raise CanonicalFreezeError(f"canonical freeze partition row-count drift: {relative}")
        if set(metadata.schema.to_arrow_schema().names) != set(FULL_ENVELOPE_SCHEMA.names):
            raise CanonicalFreezeError(f"canonical freeze partition schema drift: {relative}")
        _validate_partition_dates(source, row)
        actual_partitions.append(
            {"path": relative, "sha256": row["sha256"], "records": int(row["record_count"])}
        )
    if _canonical_hash(actual_partitions) != manifest.get("partition_root"):
        raise CanonicalFreezeError("canonical freeze partition root mismatch")
    source_catalog_path = root / "source_catalog.json"
    if _sha256(source_catalog_path) != manifest.get("source_catalog_sha256"):
        raise CanonicalFreezeError("canonical freeze source catalog drift")
    if _canonical_hash(_read_json(source_catalog_path)) != manifest.get("source_catalog_hash"):
        raise CanonicalFreezeError("canonical freeze source catalog semantic mismatch")
    quality_path = root / "quality_report.json"
    if _sha256(quality_path) != manifest.get("quality_report_sha256"):
        raise CanonicalFreezeError("canonical freeze quality report drift")
    quality = _read_json(quality_path)
    if _canonical_hash(quality.get("datasets") or []) != manifest.get("dataset_quality_root"):
        raise CanonicalFreezeError("canonical freeze dataset quality root mismatch")
    if _canonical_hash(quality.get("cross_source_reconciliation") or {}) != manifest.get(
        "cross_source_reconciliation_hash"
    ):
        raise CanonicalFreezeError("canonical freeze reconciliation root mismatch")
    search_path = root / "search_view" / "research_view_manifest.json"
    if _sha256(search_path) != manifest.get("search_view_manifest_sha256"):
        raise CanonicalFreezeError("canonical freeze search view drift")
    search = validate_physical_research_view(search_path)
    if search.get("freeze_content_hash") != manifest.get("content_hash"):
        raise CanonicalFreezeError("physical research view freeze lineage mismatch")
    if search.get("partition_root") != manifest.get("search_partition_root"):
        raise CanonicalFreezeError("physical research view partition lineage mismatch")
    if bool(search.get("alpha_search_authorized")) != bool(manifest.get("alpha_search_authorized")):
        raise CanonicalFreezeError("physical research view authorization mismatch")
    if bool(manifest.get("alpha_search_authorized")) != (not manifest.get("blockers")):
        raise CanonicalFreezeError("canonical freeze research authorization mismatch")
    return manifest | {
        "manifest_path": str(manifest_path),
        "generation_dir": str(root),
        "search_view_manifest_path": str(search_path),
        "search_view": search,
    }


def validate_physical_research_view(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = _read_json(manifest_path)
    root = manifest_path.parent
    semantic = {key: value for key, value in manifest.items() if key != "content_hash"}
    if _canonical_hash(semantic) != manifest.get("content_hash"):
        raise CanonicalFreezeError("physical research view content hash mismatch")
    if manifest.get("allowed_periods") != ["bootstrap", "research"]:
        raise CanonicalFreezeError("physical research view period contract mismatch")
    if str(manifest.get("max_availability_date")) != "20191231":
        raise CanonicalFreezeError("physical research view cutoff mismatch")
    actual_partitions = []
    for row in manifest.get("partitions") or []:
        if row.get("period") not in {"bootstrap", "research"}:
            raise CanonicalFreezeError("sealed or evaluation partition leaked into research view")
        source = (root / str(row["view_relative_path"])).resolve()
        if not source.is_relative_to(root) or not source.is_file() or source.is_symlink():
            raise CanonicalFreezeError("physical research partition containment failure")
        if _sha256(source) != row.get("sha256"):
            raise CanonicalFreezeError("physical research partition hash mismatch")
        metadata = pq.read_metadata(source)
        if metadata.num_rows != int(row.get("record_count", -1)):
            raise CanonicalFreezeError("physical research partition row-count mismatch")
        schema_names = set(metadata.schema.to_arrow_schema().names)
        if "raw_json" in schema_names or "observable_json" not in schema_names:
            raise CanonicalFreezeError("physical research partition exposes raw payload")
        if str(row.get("max_availability_date") or "") > "20191231":
            raise CanonicalFreezeError("post-research data leaked into physical research view")
        actual_partitions.append(
            {
                "path": row["view_relative_path"],
                "sha256": row["sha256"],
                "records": int(row["record_count"]),
            }
        )
    if _canonical_hash(actual_partitions) != manifest.get("partition_root"):
        raise CanonicalFreezeError("physical research partition root mismatch")
    derived = manifest.get("strict_derived_bundle") or {}
    frozen_artifacts = list(derived.get("frozen_artifacts") or [])
    for row in frozen_artifacts:
        source = (root / str(row.get("view_relative_path") or "")).resolve()
        if not source.is_relative_to(root) or not source.is_file() or source.is_symlink():
            raise CanonicalFreezeError("physical research derived artifact containment failure")
        if source.stat().st_size != int(row.get("size_bytes", -1)) or _sha256(source) != row.get("sha256"):
            raise CanonicalFreezeError("physical research derived artifact drift")
    if frozen_artifacts and _canonical_hash(frozen_artifacts) != derived.get("frozen_artifact_root"):
        raise CanonicalFreezeError("physical research derived artifact root mismatch")
    if any("sealed_holdout" in str(value) for value in _walk_strings(manifest)):
        raise CanonicalFreezeError("physical research view exposes sealed holdout locator")
    return manifest | {"manifest_path": str(manifest_path), "view_root": str(root)}


class PhysicalResearchDataView:
    def __init__(self, manifest_path: str | Path):
        self.manifest = validate_physical_research_view(manifest_path)
        self.root = Path(self.manifest["view_root"])
        self._datasets: dict[str, list[Path]] = {}
        for row in self.manifest.get("partitions") or []:
            path = (self.root / str(row["view_relative_path"])).resolve()
            self._datasets.setdefault(str(row["dataset"]), []).append(path)

    def dataset_partitions(self, dataset: str) -> tuple[Path, ...]:
        paths = tuple(sorted(self._datasets.get(str(dataset), [])))
        if not paths:
            raise CanonicalFreezeError(f"research dataset unavailable in physical view: {dataset}")
        return paths

    def iter_observable_records(self, dataset: str) -> Iterable[dict[str, Any]]:
        for path in self.dataset_partitions(dataset):
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(columns=["observable_json"]):
                for raw in batch.column(0).to_pylist():
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise CanonicalFreezeError("research observable payload is not an object")
                    yield payload


def _materialize_dataset(
    source_row: Mapping[str, Any],
    staging: Path,
    *,
    governed_root: Path,
    batch_rows: int,
    sample_size: int,
    source_cutoff: str,
) -> dict[str, Any]:
    dataset = str(source_row["dataset"])
    source = (governed_root / str(source_row["records_relative_path"])).resolve()
    if not source.is_relative_to(governed_root) or source.is_symlink():
        raise CanonicalFreezeError(f"source containment failure during freeze build: {dataset}")
    contract = dict(source_row["contract"])
    source_stat = source.stat()
    source_digest = hashlib.sha256()
    key_hashes: set[bytes] = set()
    duplicate_count = 0
    null_primary_key_count = 0
    present_counts: dict[str, int] = {}
    null_counts: dict[str, int] = {}
    fields: set[str] = set()
    anomaly_counts: dict[str, int] = {}
    excluded_post_cutoff_count = 0
    excluded_post_cutoff_digest = hashlib.sha256()
    period_buffers: dict[str, list[dict[str, Any]]] = {name: [] for name, *_ in PERIODS}
    period_buffers["unknown_availability"] = []
    partitions: list[dict[str, Any]] = []
    search_partitions: list[dict[str, Any]] = []
    part_indices: dict[str, int] = {key: 0 for key in period_buffers}
    period_counts: dict[str, int] = {key: 0 for key in period_buffers}
    period_min: dict[str, str | None] = {key: None for key in period_buffers}
    period_max: dict[str, str | None] = {key: None for key in period_buffers}
    samples = _DeterministicSamples(sample_size)
    distinct_ts_codes: set[str] = set()
    distinct_dates: set[str] = set()
    semantic_counts: dict[str, dict[str, int]] = {}
    index_snapshot_members: dict[tuple[str, str], set[str]] = {}
    index_snapshot_weights: dict[tuple[str, str], float] = {}
    index_snapshot_duplicate_members = 0
    record_count = 0
    with source.open("rb") as handle:
        for row_number, raw_line in enumerate(handle, start=1):
            source_digest.update(raw_line)
            stripped = raw_line.rstrip(b"\r\n")
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CanonicalFreezeError(f"source JSON parse error: {dataset}:{row_number}:{exc}") from exc
            if not isinstance(payload, dict):
                raise CanonicalFreezeError(f"source row is not an object: {dataset}:{row_number}")
            record_count += 1
            if payload.get("ts_code") not in {None, ""}:
                distinct_ts_codes.add(str(payload["ts_code"]))
            for date_field in ("trade_date", "ann_date", "end_date", "list_date", "in_date"):
                date_value = _valid_date(payload.get(date_field))
                if date_value:
                    distinct_dates.add(date_value)
                    break
            fields.update(str(key) for key in payload)
            for key, value in payload.items():
                present_counts[str(key)] = present_counts.get(str(key), 0) + 1
                if value is None:
                    null_counts[str(key)] = null_counts.get(str(key), 0) + 1
            primary_values = [payload.get(field) for field in contract["primary_key"]]
            if any(value in {None, ""} for value in primary_values):
                null_primary_key_count += 1
            primary_key = json.dumps(primary_values, ensure_ascii=False, separators=(",", ":"), default=str)
            key_digest = hashlib.sha256(primary_key.encode("utf-8")).digest()
            if key_digest in key_hashes:
                duplicate_count += 1
            else:
                key_hashes.add(key_digest)
            availability, availability_basis = _availability_date(dataset, payload, contract)
            effective, effective_basis = _effective_date(payload, contract)
            field_availability, field_effective = _field_temporal_contract(
                dataset,
                payload,
                availability=availability,
                availability_basis=availability_basis,
                effective=effective,
                effective_basis=effective_basis,
            )
            period = _period_for_availability(availability)
            if availability and availability > source_cutoff:
                excluded_post_cutoff_count += 1
                excluded_post_cutoff_digest.update(hashlib.sha256(stripped).digest())
                continue
            row = {
                "dataset": dataset,
                "primary_key": primary_key,
                "ts_code": _string_or_none(payload.get("ts_code")),
                "index_code": _string_or_none(payload.get("index_code")),
                "trade_date": _string_or_none(payload.get("trade_date")),
                "ann_date": _string_or_none(payload.get("ann_date")),
                "f_ann_date": _string_or_none(payload.get("f_ann_date")),
                "end_date": _string_or_none(payload.get("end_date")),
                "list_date": _string_or_none(payload.get("list_date")),
                "delist_date": _string_or_none(payload.get("delist_date")),
                "update_flag": _string_or_none(payload.get("update_flag")),
                "availability_date": availability,
                "availability_known": availability is not None,
                "availability_basis": availability_basis,
                "effective_date": effective,
                "effective_known": effective is not None,
                "effective_basis": effective_basis,
                "field_availability_json": _canonical_json_bytes(field_availability),
                "field_effective_json": _canonical_json_bytes(field_effective),
                "source_row_number": row_number,
                "raw_json": stripped,
                "observable_json": _canonical_json_bytes(
                    _observable_payload(payload, field_availability, cutoff="20191231")
                ),
            }
            period_buffers[period].append(row)
            period_counts[period] += 1
            if availability:
                period_min[period] = min(period_min[period] or availability, availability)
                period_max[period] = max(period_max[period] or availability, availability)
            samples.add(primary_key, _sample_payload(dataset, payload))
            _update_anomalies(dataset, payload, anomaly_counts)
            _update_semantic_counts(payload, semantic_counts)
            if dataset == "index_members" and payload.get("index_code") == "000300.SH":
                snapshot_key = (str(payload["index_code"]), str(payload.get("trade_date") or ""))
                member = str(payload.get("ts_code") or "")
                members = index_snapshot_members.setdefault(snapshot_key, set())
                if member in members:
                    index_snapshot_duplicate_members += 1
                members.add(member)
                weight = _finite_float(payload.get("weight"))
                if weight is not None:
                    index_snapshot_weights[snapshot_key] = index_snapshot_weights.get(snapshot_key, 0.0) + weight
            if len(period_buffers[period]) >= batch_rows:
                full_partition, research_partition = _flush_partition(
                    staging, dataset, period, part_indices[period], period_buffers[period]
                )
                partitions.append(full_partition)
                if research_partition is not None:
                    search_partitions.append(research_partition)
                part_indices[period] += 1
                period_buffers[period] = []
    for period, rows in period_buffers.items():
        if rows:
            full_partition, research_partition = _flush_partition(staging, dataset, period, part_indices[period], rows)
            partitions.append(full_partition)
            if research_partition is not None:
                search_partitions.append(research_partition)
    actual_sha = source_digest.hexdigest()
    if actual_sha != source_row.get("records_sha256"):
        raise CanonicalFreezeError(f"source SHA drift during freeze build: {dataset}")
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
        raise CanonicalFreezeError(f"source mutated during freeze build: {dataset}")
    if record_count != int(source_row.get("record_count", -1)):
        raise CanonicalFreezeError(f"source record count drift during freeze build: {dataset}")
    missing_counts = {
        field: int(null_counts.get(field, 0) + record_count - present_counts.get(field, 0))
        for field in sorted(fields | set(contract["required_fields"]))
    }
    blockers = []
    if duplicate_count:
        blockers.append(f"duplicate_primary_keys:{duplicate_count}")
    if null_primary_key_count:
        blockers.append(f"null_primary_keys:{null_primary_key_count}")
    if period_counts["unknown_availability"]:
        blockers.append(f"unknown_availability_rows:{period_counts['unknown_availability']}")
    frozen_record_count = record_count - excluded_post_cutoff_count
    if frozen_record_count <= 0:
        blockers.append("no_in_scope_records")
    index_snapshot_proof = None
    if dataset == "index_members":
        index_snapshot_proof = _index_snapshot_proof(
            index_snapshot_members,
            index_snapshot_weights,
            duplicate_member_count=index_snapshot_duplicate_members,
        )
        if not index_snapshot_proof["historical_constituent_proof"]:
            blockers.extend(index_snapshot_proof["blockers"])
    return {
        "dataset": {
            "dataset": dataset,
            "status": "materialized_with_blockers" if blockers else "materialized",
            "source_sha256": actual_sha,
            "record_count": frozen_record_count,
            "source_record_count": record_count,
            "excluded_post_cutoff_count": excluded_post_cutoff_count,
            "excluded_post_cutoff_row_hash_root": (
                excluded_post_cutoff_digest.hexdigest() if excluded_post_cutoff_count else None
            ),
            "partition_count": len(partitions),
            "fields": sorted(fields),
            "missing_counts": missing_counts,
            "missing_rates": {field: count / record_count if record_count else 0.0 for field, count in missing_counts.items()},
            "duplicate_primary_key_count": duplicate_count,
            "null_primary_key_count": null_primary_key_count,
            "anomaly_counts": anomaly_counts,
            "period_record_counts": period_counts,
            "period_min_availability": period_min,
            "period_max_availability": period_max,
            "distinct_ts_code_count": len(distinct_ts_codes),
            "distinct_date_count": len(distinct_dates),
            "semantic_counts": semantic_counts,
            "index_snapshot_proof": index_snapshot_proof,
            "availability_policy": contract["availability_policy"],
            "effective_policy": contract["effective_policy"],
            "blockers": blockers,
        },
        "partitions": partitions,
        "search_partitions": search_partitions,
        "samples": samples.to_dict(),
    }


def _flush_partition(
    staging: Path,
    dataset: str,
    period: str,
    index: int,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    view = _period_view(period)
    path = staging / "full_view" / "data" / dataset / f"period={period}" / f"part-{index:05d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=FULL_ENVELOPE_SCHEMA)
    pq.write_table(table, path, compression="zstd", compression_level=3, use_dictionary=True, write_statistics=True)
    availability = [value for value in table.column("availability_date").to_pylist() if value]
    full_row = {
        "dataset": dataset,
        "period": period,
        "view": view,
        "relative_path": path.relative_to(staging).as_posix(),
        "record_count": table.num_rows,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "min_availability_date": min(availability) if availability else None,
        "max_availability_date": max(availability) if availability else None,
        "format": "parquet_raw_json_envelope_v1",
    }
    research_row = None
    if period in {"bootstrap", "research"}:
        research_path = staging / "search_view" / "data" / dataset / f"period={period}" / path.name
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_table = table.select(RESEARCH_ENVELOPE_SCHEMA.names)
        pq.write_table(
            research_table,
            research_path,
            compression="zstd",
            compression_level=3,
            use_dictionary=True,
            write_statistics=True,
        )
        research_row = {
            **full_row,
            "view": "search",
            "view_relative_path": research_path.relative_to(staging / "search_view").as_posix(),
            "size_bytes": research_path.stat().st_size,
            "sha256": _sha256(research_path),
            "format": "parquet_observable_json_envelope_v1",
        }
        research_row.pop("relative_path", None)
    return full_row, research_row


def _build_search_view_manifest(
    generation_id: str,
    freeze_hash: str,
    partitions: list[dict[str, Any]],
    core: Mapping[str, Any],
) -> dict[str, Any]:
    rows = list(partitions)
    semantic = {
        "schema_version": "physical_ashare_research_view_v1",
        "generation_id": generation_id,
        "freeze_content_hash": freeze_hash,
        "allowed_periods": ["bootstrap", "research"],
        "max_availability_date": "20191231",
        "partition_root": _canonical_hash(
            [
                {"path": row["view_relative_path"], "sha256": row["sha256"], "records": row["record_count"]}
                for row in rows
            ]
        ),
        "partitions": rows,
        "source_semantic_hash": core["source_semantic_hash"],
        "strict_derived_bundle": core["strict_derived_bundle"],
        "alpha_search_authorized": not core["blockers"],
        "candidate_identity_freeze_required_before_evaluation_views": True,
        "holdout_locator_exposed": False,
    }
    return semantic | {"content_hash": _canonical_hash(semantic)}


def _dataset_contract(dataset: str) -> dict[str, Any]:
    base = {
        "securities": ("list_date", "list_date", ("ts_code",), ("ts_code", "list_date", "delist_date", "list_status")),
        "trade_calendar": ("trade_date", "trade_date", ("trade_date",), ("trade_date", "is_open")),
        "daily_bars": ("trade_date", "trade_date", ("ts_code", "trade_date"), ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "volume", "amount")),
        "daily_basic": ("trade_date", "trade_date", ("ts_code", "trade_date"), ("ts_code", "trade_date")),
        "daily_limits": ("trade_date", "trade_date", ("ts_code", "trade_date"), ("ts_code", "trade_date", "up_limit", "down_limit")),
        "adjustment_factors": ("trade_date", "trade_date", ("ts_code", "trade_date"), ("ts_code", "trade_date", "adj_factor")),
        "financial_features": (
            "announce_date",
            "report_period",
            ("ts_code", "report_period", "announce_date"),
            ("ts_code", "report_period", "announce_date"),
        ),
        "index_members": ("trade_date", "trade_date", ("index_code", "ts_code", "trade_date"), ("index_code", "ts_code", "trade_date", "weight")),
        "corporate_actions": ("ann_date", "ex_date", DATASET_PRIMARY_KEYS["corporate_actions"], ("ts_code", "ann_date", "ex_date", "div_proc")),
    }
    if dataset in base:
        availability, effective, primary, fields = base[dataset]
        return {
            "dataset": dataset,
            "primary_key": list(primary),
            "required_fields": list(fields),
            "availability_field": availability,
            "effective_field": effective,
            "availability_policy": f"field:{availability}",
            "effective_policy": f"field:{effective}",
        }
    definition = DATASET_DEFINITIONS.get(dataset)
    if definition is None:
        raise CanonicalFreezeError(f"missing governed dataset contract: {dataset}")
    required_fields = set(definition.primary_key)
    required_fields.update(field for field in (definition.availability_date_field, definition.effective_date_field) if field)
    if dataset in {"income_statements", "balance_sheets", "cashflow_statements"}:
        required_fields.update(("ann_date", "f_ann_date", "update_flag"))
    return {
        "dataset": dataset,
        "primary_key": list(definition.primary_key),
        "required_fields": sorted(required_fields),
        "availability_field": definition.availability_date_field,
        "effective_field": definition.effective_date_field,
        "availability_policy": (
            "conservative_max_ann_date_f_ann_date"
            if dataset in {"income_statements", "balance_sheets", "cashflow_statements"}
            else f"field:{definition.availability_date_field}"
        ),
        "effective_policy": f"field:{definition.effective_date_field}",
    }


def _is_materialization_blocker(reason: str) -> bool:
    return reason not in {
        "historical_name_change_coverage_incomplete",
        "historical_industry_transition_proof_missing",
    }


def _availability_date(dataset: str, payload: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[str | None, str]:
    if dataset in {"income_statements", "balance_sheets", "cashflow_statements"}:
        dates = [_valid_date(payload.get(field)) for field in ("ann_date", "f_ann_date")]
        known = [value for value in dates if value]
        return (max(known), "max(ann_date,f_ann_date)") if known else (None, "max(ann_date,f_ann_date)")
    field = contract.get("availability_field")
    return _valid_date(payload.get(str(field))) if field else None, f"field:{field}"


def _effective_date(payload: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[str | None, str]:
    field = contract.get("effective_field")
    return _valid_date(payload.get(str(field))) if field else None, f"field:{field}"


def _field_temporal_contract(
    dataset: str,
    payload: Mapping[str, Any],
    *,
    availability: str | None,
    availability_basis: str,
    effective: str | None,
    effective_basis: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    field_availability: dict[str, Any] = {
        "default": {"date": availability, "known": availability is not None, "basis": availability_basis},
        "overrides": {},
    }
    field_effective: dict[str, Any] = {
        "default": {"date": effective, "known": effective is not None, "basis": effective_basis},
        "overrides": {},
    }
    if dataset == "securities":
        list_date = _valid_date(payload.get("list_date"))
        delist_date = _valid_date(payload.get("delist_date"))
        stable_at_listing = {"ts_code", "symbol", "exchange", "market", "board", "list_date"}
        current_snapshot_only = {"name", "raw_name", "area", "industry", "is_st", "list_status"}
        for field in stable_at_listing & set(payload):
            field_availability["overrides"][field] = {
                "date": list_date,
                "known": list_date is not None,
                "basis": "security_listing_date",
            }
            field_effective["overrides"][field] = dict(field_availability["overrides"][field])
        if "delist_date" in payload:
            field_availability["overrides"]["delist_date"] = {
                "date": delist_date,
                "known": delist_date is not None,
                "basis": "conservative_delist_effective_date_proxy",
            }
            field_effective["overrides"]["delist_date"] = dict(
                field_availability["overrides"]["delist_date"]
            )
        for field in current_snapshot_only & set(payload):
            field_availability["overrides"][field] = {
                "date": None,
                "known": False,
                "basis": "current_snapshot_not_historical_pit",
            }
            field_effective["overrides"][field] = {
                "date": None,
                "known": False,
                "basis": "current_snapshot_not_historical_pit",
            }
    elif dataset == "corporate_actions":
        ex_date = _valid_date(payload.get("ex_date"))
        for field in ("cash_div", "cash_div_tax", "stk_bo_rate", "stk_co_rate", "stk_div"):
            if field in payload:
                field_effective["overrides"][field] = {
                    "date": ex_date,
                    "known": ex_date is not None,
                    "basis": "corporate_action_ex_date",
                }
        for field in ("record_date", "ex_date", "pay_date", "div_listdate"):
            if field in payload:
                value = _valid_date(payload.get(field))
                field_effective["overrides"][field] = {
                    "date": value,
                    "known": value is not None,
                    "basis": f"field:{field}",
                }
    elif dataset == "industry_members":
        out_date = _valid_date(payload.get("out_date"))
        if "out_date" in payload:
            field_availability["overrides"]["out_date"] = {
                "date": out_date,
                "known": out_date is not None,
                "basis": "industry_out_date_publication_proxy",
            }
            field_effective["overrides"]["out_date"] = dict(field_availability["overrides"]["out_date"])
    return field_availability, field_effective


def _observable_payload(
    payload: Mapping[str, Any],
    field_availability: Mapping[str, Any],
    *,
    cutoff: str,
) -> dict[str, Any]:
    default = dict(field_availability.get("default") or {})
    overrides = dict(field_availability.get("overrides") or {})
    observable = {}
    for field, value in payload.items():
        state = dict(overrides.get(str(field)) or default)
        if bool(state.get("known")) and str(state.get("date") or "") <= cutoff:
            observable[str(field)] = value
    return observable


def _period_for_availability(value: str | None) -> str:
    if value is None:
        return "unknown_availability"
    for name, start, end, _view in PERIODS:
        if start <= value <= end:
            return name
    return "unknown_availability"


def _period_view(period: str) -> str:
    if period in {"bootstrap", "research"}:
        return "search"
    if period in {"validation", "retrospective_test"}:
        return "controlled"
    if period == "sealed_holdout":
        return "sealed_holdout"
    return "quarantine"


def _period_policy_payload() -> dict[str, Any]:
    return {
        "version": "ashare_physical_period_policy_v1",
        "partition_key": "availability_date",
        "periods": [
            {"name": name, "start": start, "end": end, "access_class": view}
            for name, start, end, view in PERIODS
        ],
        "search_view_periods": ["bootstrap", "research"],
        "search_max_availability_date": "20191231",
        "candidate_identity_freeze_required_before_validation_or_later_access": True,
        "sealed_holdout_historically_observed": True,
        "sealed_holdout_untouched": False,
    }


def _resolve_reviewed_raw_index(governed_root: Path) -> Path:
    candidates = []
    reports = governed_root / "reports"
    for path in reports.glob("raw_index_*/raw_data_index_manifest.reviewed_fresh.json"):
        try:
            payload = _read_json(path)
        except Exception:
            continue
        try:
            data_dir = Path(str(payload.get("data_dir") or "")).resolve()
        except OSError:
            continue
        if data_dir != (governed_root / "data").resolve() or payload.get("status") != "fresh":
            continue
        candidates.append((int(payload.get("dataset_count", 0) or 0), str(payload.get("built_at") or ""), path))
    if not candidates:
        raise CanonicalFreezeError("validated reviewed raw index not found under governed root")
    candidates.sort(reverse=True)
    best = candidates[0]
    if any(row[:2] == best[:2] and row[2] != best[2] for row in candidates[1:]):
        raise CanonicalFreezeError("ambiguous reviewed raw index candidates")
    return best[2]


def _discover_derived_bundle(governed_root: Path) -> dict[str, Any]:
    pointer = governed_root / "governance" / "canonical_derived" / "current.json"
    if not pointer.is_file():
        return {
            "status": "missing",
            "strict_matrix_present": False,
            "feature_validity_present": False,
            "feature_values_present": False,
            "target_availability_present": False,
            "axes_present": False,
            "content_hash": None,
        }
    payload = _read_json(pointer)
    if payload.get("schema_version") != "canonical_derived_pointer_v1":
        raise CanonicalFreezeError("canonical derived bundle pointer schema mismatch")
    manifest = (pointer.parent / str(payload.get("manifest") or "")).resolve()
    if not manifest.is_relative_to(pointer.parent.resolve()) or not manifest.is_file():
        raise CanonicalFreezeError("canonical derived bundle pointer containment failure")
    bundle = _read_json(manifest)
    semantic = {key: value for key, value in bundle.items() if key != "content_hash"}
    if _canonical_hash(semantic) != bundle.get("content_hash"):
        raise CanonicalFreezeError("canonical derived bundle semantic hash mismatch")
    if payload.get("content_hash") != bundle.get("content_hash"):
        raise CanonicalFreezeError("canonical derived pointer content hash mismatch")
    artifacts: dict[str, dict[str, Any]] = {}
    for row in bundle.get("artifacts") or []:
        role = str(row.get("role") or "")
        relative = str(row.get("relative_path") or "")
        source = (manifest.parent / relative).resolve()
        if not role or role in artifacts:
            raise CanonicalFreezeError("canonical derived bundle artifact role invalid")
        if not source.is_relative_to(manifest.parent) or not source.is_file() or source.is_symlink():
            raise CanonicalFreezeError(f"canonical derived bundle artifact containment failure: {role}")
        if source.stat().st_size != int(row.get("size_bytes", -1)) or _sha256(source) != row.get("sha256"):
            raise CanonicalFreezeError(f"canonical derived bundle artifact drift: {role}")
        artifacts[role] = dict(row)
    strict_matrix_present = "strict_matrix_manifest" in artifacts
    feature_values_present = "feature_values" in artifacts
    feature_validity_present = "feature_validity" in artifacts
    target_availability_present = "target_availability" in artifacts
    axes_present = all(role in artifacts for role in ("stock_axis", "date_axis", "feature_axis"))
    derived_validation = _validate_derived_artifact_contract(manifest.parent, artifacts)
    return {
        "status": "present",
        "manifest_relative_path": manifest.relative_to(governed_root).as_posix(),
        "manifest_sha256": _sha256(manifest),
        "content_hash": bundle.get("content_hash"),
        "artifact_root": _canonical_hash(
            [
                {
                    "role": role,
                    "relative_path": row["relative_path"],
                    "sha256": row["sha256"],
                    "size_bytes": int(row["size_bytes"]),
                }
                for role, row in sorted(artifacts.items())
            ]
        ),
        "strict_matrix_present": strict_matrix_present,
        "feature_values_present": feature_values_present,
        "feature_validity_present": feature_validity_present,
        "target_availability_present": target_availability_present,
        "axes_present": axes_present,
        "validated_shape": derived_validation.get("shape"),
        "validated_feature_shape": derived_validation.get("feature_shape"),
        "date_axis_min": derived_validation.get("date_axis_min"),
        "date_axis_max": derived_validation.get("date_axis_max"),
        "axis_hashes": derived_validation.get("axis_hashes"),
        "artifacts": [
            {
                **row,
                "source_relative_path": (
                    manifest.parent / str(row["relative_path"])
                ).resolve().relative_to(governed_root).as_posix(),
            }
            for row in artifacts.values()
        ],
    }


def _discover_source_coverage_proof(governed_root: Path) -> dict[str, Any]:
    pointer = governed_root / "governance" / "canonical_source_coverage" / "current.json"
    if not pointer.is_file():
        return {"status": "missing", "content_hash": None, "datasets": {}}
    payload = _read_json(pointer)
    if payload.get("schema_version") != "canonical_source_coverage_pointer_v1":
        raise CanonicalFreezeError("canonical source coverage pointer schema mismatch")
    manifest = (pointer.parent / str(payload.get("manifest") or "")).resolve()
    if not manifest.is_relative_to(pointer.parent.resolve()) or not manifest.is_file():
        raise CanonicalFreezeError("canonical source coverage pointer containment failure")
    proof = _read_json(manifest)
    semantic = {key: value for key, value in proof.items() if key != "content_hash"}
    if _canonical_hash(semantic) != proof.get("content_hash") or payload.get("content_hash") != proof.get("content_hash"):
        raise CanonicalFreezeError("canonical source coverage proof hash mismatch")
    datasets: dict[str, dict[str, Any]] = {}
    for row in proof.get("datasets") or []:
        dataset = str(row.get("dataset") or "")
        if not dataset or dataset in datasets:
            raise CanonicalFreezeError("canonical source coverage dataset identity invalid")
        complete = bool(row.get("complete"))
        security_count = int(row.get("security_count", 0) or 0)
        start_date = _valid_date(row.get("start_date"))
        end_date = _valid_date(row.get("end_date"))
        if complete and (security_count <= 0 or start_date is None or end_date is None or end_date > SOURCE_CUTOFF):
            raise CanonicalFreezeError(f"canonical source coverage range invalid: {dataset}")
        datasets[dataset] = {
            "complete": complete,
            "security_count": security_count,
            "start_date": start_date,
            "end_date": end_date,
            "coverage_root": row.get("coverage_root"),
            "negative_attestation_count": int(row.get("negative_attestation_count", 0) or 0),
        }
    return {
        "status": "present",
        "manifest_relative_path": manifest.relative_to(governed_root).as_posix(),
        "manifest_sha256": _sha256(manifest),
        "content_hash": proof.get("content_hash"),
        "datasets": datasets,
    }


def _validate_derived_artifact_contract(
    root: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    required = {
        "strict_matrix_manifest",
        "stock_axis",
        "date_axis",
        "feature_axis",
        "feature_manifest",
        "feature_values",
        "feature_validity",
        "target_availability",
    }
    if not required.issubset(artifacts):
        return {}

    def path_for(role: str) -> Path:
        return (root / str(artifacts[role]["relative_path"])).resolve()

    matrix = _read_json(path_for("strict_matrix_manifest"))
    shape = tuple(int(value) for value in matrix.get("shape") or ())
    if len(shape) != 2 or min(shape, default=0) <= 0:
        raise CanonicalFreezeError("canonical derived strict matrix shape invalid")
    stock_axis = _read_json_array(path_for("stock_axis"))
    date_axis = _read_json_array(path_for("date_axis"))
    feature_axis = _read_json_array(path_for("feature_axis"))
    axis_hashes = {
        "stock_axis": _canonical_hash(stock_axis),
        "date_axis": _canonical_hash(date_axis),
        "feature_axis": _canonical_hash(feature_axis),
    }
    for role, axis_hash in axis_hashes.items():
        if artifacts[role].get("axis_hash") != axis_hash:
            raise CanonicalFreezeError(f"canonical derived axis hash mismatch: {role}")
    if len(stock_axis) != shape[0] or len(date_axis) != shape[1] or not feature_axis:
        raise CanonicalFreezeError("canonical derived axis shape mismatch")
    if any(_valid_date(value) is None for value in date_axis):
        raise CanonicalFreezeError("canonical derived date axis invalid")
    if list(date_axis) != sorted(date_axis) or len(date_axis) != len(set(date_axis)):
        raise CanonicalFreezeError("canonical derived date axis order invalid")
    if str(max(date_axis)) > "20191231":
        raise CanonicalFreezeError("canonical derived artifacts expose post-research dates")
    if matrix.get("stock_axis_hash") != axis_hashes["stock_axis"] or matrix.get("date_axis_hash") != axis_hashes[
        "date_axis"
    ]:
        raise CanonicalFreezeError("canonical derived matrix axis lineage mismatch")
    values = np.load(path_for("feature_values"), mmap_mode="r", allow_pickle=False)
    validity = np.load(path_for("feature_validity"), mmap_mode="r", allow_pickle=False)
    target = np.load(path_for("target_availability"), mmap_mode="r", allow_pickle=False)
    expected_feature_shape = (shape[0], len(feature_axis), shape[1])
    if tuple(values.shape) != expected_feature_shape or tuple(validity.shape) != expected_feature_shape:
        raise CanonicalFreezeError("canonical derived feature tensor shape mismatch")
    if tuple(target.shape) != shape:
        raise CanonicalFreezeError("canonical derived target mask shape mismatch")
    if values.dtype != np.float32 or validity.dtype != np.bool_ or target.dtype != np.bool_:
        raise CanonicalFreezeError("canonical derived dtype contract mismatch")
    partition_sha = matrix.get("partition_sha256") or {}
    for role in ("target_availability",):
        row = artifacts[role]
        basename = Path(str(row["relative_path"])).name
        declared = partition_sha.get(basename)
        if declared is not None and declared != row["sha256"]:
            raise CanonicalFreezeError(f"canonical derived matrix partition lineage mismatch: {role}")
    return {
        "shape": list(shape),
        "feature_shape": list(expected_feature_shape),
        "date_axis_min": min(date_axis),
        "date_axis_max": max(date_axis),
        "axis_hashes": axis_hashes,
    }


def _copy_derived_bundle_to_search_view(
    governed_root: Path,
    derived: Mapping[str, Any],
    staging: Path,
) -> dict[str, Any]:
    if derived.get("status") != "present":
        return dict(derived)
    frozen = []
    for row in derived.get("artifacts") or []:
        role = str(row["role"])
        source = (governed_root / str(row["source_relative_path"])).resolve()
        if not source.is_relative_to(governed_root) or not source.is_file() or source.is_symlink():
            raise CanonicalFreezeError(f"derived source containment failure during freeze build: {role}")
        target = staging / "search_view" / "derived" / role / Path(str(row["relative_path"])).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if _sha256(target) != row["sha256"]:
            raise CanonicalFreezeError(f"derived artifact copy drift: {role}")
        frozen.append(
            {
                "role": role,
                "view_relative_path": target.relative_to(staging / "search_view").as_posix(),
                "sha256": row["sha256"],
                "size_bytes": int(row["size_bytes"]),
            }
        )
    return {
        key: value
        for key, value in derived.items()
        if key not in {"artifacts", "manifest_relative_path"}
    } | {
        "frozen_artifacts": frozen,
        "frozen_artifact_root": _canonical_hash(frozen),
    }


def _cross_source_reconciliation(samples: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    daily = samples.get("daily_bars", {})
    rows = []
    for dataset in ("adjustment_factors", "daily_limits", "daily_basic"):
        other = samples.get(dataset, {})
        shared = sorted(set(daily) & set(other))
        rows.append(
            {
                "left": "daily_bars",
                "right": dataset,
                "shared_sample_keys": len(shared),
                "daily_sample_keys": len(daily),
                "other_sample_keys": len(other),
                "status": "passed" if shared else "blocked_no_shared_sample",
                "sample_root": _canonical_hash([{"key": key, "left": daily[key], "right": other[key]} for key in shared]),
            }
        )
    return {"method": "deterministic_smallest_primary_key_hash", "sample_size": 1_000, "pairs": rows}


def _sample_payload(dataset: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "daily_bars": ("ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"),
        "adjustment_factors": ("ts_code", "trade_date", "adj_factor"),
        "daily_limits": ("ts_code", "trade_date", "up_limit", "down_limit"),
        "daily_basic": ("ts_code", "trade_date", "total_mv", "turnover_rate"),
    }.get(dataset, ())
    return {field: payload.get(field) for field in fields}


def _update_anomalies(dataset: str, payload: Mapping[str, Any], counts: dict[str, int]) -> None:
    if dataset == "daily_bars":
        prices = [_finite_float(payload.get(field)) for field in ("open", "high", "low", "close", "pre_close")]
        if any(value is None or value <= 0 for value in prices):
            counts["invalid_price"] = counts.get("invalid_price", 0) + 1
        elif prices[1] < max(prices[0], prices[3]) or prices[2] > min(prices[0], prices[3]) or prices[1] < prices[2]:
            counts["ohlc_relation_violation"] = counts.get("ohlc_relation_violation", 0) + 1
        if any((_finite_float(payload.get(field)) or 0.0) < 0 for field in ("volume", "amount")):
            counts["negative_volume_or_amount"] = counts.get("negative_volume_or_amount", 0) + 1
    elif dataset == "adjustment_factors":
        value = _finite_float(payload.get("adj_factor"))
        if value is None or value <= 0:
            counts["invalid_adjustment_factor"] = counts.get("invalid_adjustment_factor", 0) + 1
    elif dataset == "daily_limits":
        up = _finite_float(payload.get("up_limit"))
        down = _finite_float(payload.get("down_limit"))
        if up is None or down is None or up <= 0 or down <= 0 or up < down:
            counts["invalid_daily_limit"] = counts.get("invalid_daily_limit", 0) + 1


def _update_semantic_counts(payload: Mapping[str, Any], counts: dict[str, dict[str, int]]) -> None:
    for field in ("list_status", "suspend_type", "type", "update_flag", "index_code", "exchange"):
        if field not in payload:
            continue
        value = "<NULL>" if payload.get(field) in {None, ""} else str(payload[field])
        bucket = counts.setdefault(field, {})
        bucket[value] = bucket.get(value, 0) + 1
    if "delist_date" in payload:
        bucket = counts.setdefault("lifecycle", {})
        key = "delisted" if _valid_date(payload.get("delist_date")) else "not_delisted_in_source"
        bucket[key] = bucket.get(key, 0) + 1


def _index_snapshot_proof(
    members: Mapping[tuple[str, str], set[str]],
    weights: Mapping[tuple[str, str], float],
    *,
    duplicate_member_count: int,
) -> dict[str, Any]:
    snapshots = []
    rejected = 0
    for (index_code, trade_date), codes in sorted(members.items()):
        weight_sum = float(weights.get((index_code, trade_date), 0.0))
        accepted = len(codes) == 300 and 99.5 <= weight_sum <= 100.5
        rejected += int(not accepted)
        snapshots.append(
            {
                "index_code": index_code,
                "snapshot_date": trade_date,
                "member_count": len(codes),
                "weight_sum": weight_sum,
                "accepted": accepted,
            }
        )
    blockers = []
    if not snapshots:
        blockers.append("csi300_snapshot_sequence_missing")
    if rejected:
        blockers.append(f"csi300_rejected_snapshots:{rejected}")
    if duplicate_member_count:
        blockers.append(f"csi300_duplicate_snapshot_members:{duplicate_member_count}")
    months = sorted({row["snapshot_date"][:6] for row in snapshots if _valid_date(row["snapshot_date"])})
    return {
        "canonical_index_code": "000300.SH",
        "expected_member_count": 300,
        "weight_sum_range": [99.5, 100.5],
        "snapshot_count": len(snapshots),
        "accepted_snapshot_count": len(snapshots) - rejected,
        "rejected_snapshot_count": rejected,
        "duplicate_member_count": duplicate_member_count,
        "month_count": len(months),
        "first_snapshot_date": snapshots[0]["snapshot_date"] if snapshots else None,
        "last_snapshot_date": snapshots[-1]["snapshot_date"] if snapshots else None,
        "member_count_min": min((row["member_count"] for row in snapshots), default=0),
        "member_count_max": max((row["member_count"] for row in snapshots), default=0),
        "weight_sum_min": min((row["weight_sum"] for row in snapshots), default=0.0),
        "weight_sum_max": max((row["weight_sum"] for row in snapshots), default=0.0),
        "historical_constituent_proof": not blockers,
        "blockers": blockers,
        "snapshot_root": _canonical_hash(snapshots),
    }


class _DeterministicSamples:
    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self.heap: list[tuple[int, str, dict[str, Any]]] = []
        self.seen_keys: set[str] = set()

    def add(self, key: str, payload: dict[str, Any]) -> None:
        if not payload or self.limit <= 0 or key in self.seen_keys:
            return
        self.seen_keys.add(key)
        score = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
        item = (-score, key, payload)
        if len(self.heap) < self.limit:
            heapq.heappush(self.heap, item)
        elif item > self.heap[0]:
            heapq.heapreplace(self.heap, item)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {key: payload for _score, key, payload in sorted(self.heap, reverse=True)}


def _validate_partition_dates(path: Path, row: Mapping[str, Any]) -> None:
    table = pq.read_table(path, columns=["availability_date"])
    values = [value for value in table.column("availability_date").to_pylist() if value]
    if values:
        if min(values) != row.get("min_availability_date") or max(values) != row.get("max_availability_date"):
            raise CanonicalFreezeError(f"partition availability range drift: {path.name}")
        period = str(row["period"])
        bounds = {name: (start, end) for name, start, end, _view in PERIODS}
        if period in bounds and not all(bounds[period][0] <= value <= bounds[period][1] for value in values):
            raise CanonicalFreezeError(f"partition availability boundary violation: {path.name}")
    elif row.get("period") != "unknown_availability":
        raise CanonicalFreezeError(f"known period partition has no availability dates: {path.name}")


def _period_content_root(partitions: list[dict[str, Any]], period: str) -> str:
    return _canonical_hash(
        [
            {"dataset": row["dataset"], "sha256": row["sha256"], "records": row["record_count"]}
            for row in partitions
            if row["period"] == period
        ]
    )


def _period_coverage(partitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name, start, end, access_class in PERIODS:
        selected = [row for row in partitions if row["period"] == name]
        dates = [
            str(value)
            for row in selected
            for value in (row.get("min_availability_date"), row.get("max_availability_date"))
            if value
        ]
        rows.append(
            {
                "period": name,
                "configured_start": start,
                "configured_end": end,
                "access_class": access_class,
                "dataset_count": len({row["dataset"] for row in selected}),
                "partition_count": len(selected),
                "record_count": sum(int(row["record_count"]) for row in selected),
                "actual_min_availability_date": min(dates) if dates else None,
                "actual_max_availability_date": max(dates) if dates else None,
                "content_root": _period_content_root(partitions, name),
            }
        )
    return rows


def _sample_fields(path: Path, limit: int = 256) -> list[str]:
    fields: set[str] = set()
    with path.open("rb") as handle:
        for index, line in enumerate(handle):
            if index >= limit:
                break
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                fields.update(str(key) for key in payload)
    return sorted(fields)


def _valid_date(value: Any) -> str | None:
    text = str(value or "")
    return _validated_date_text(text)


@lru_cache(maxsize=131_072)
def _validated_date_text(text: str) -> str | None:
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None
    return text


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _string_or_none(value: Any) -> str | None:
    return None if value in {None, ""} else str(value)


def _source_semantic_hash() -> str:
    from auto_alpha.data.ingestion.pipeline.ashare import dataset_registry

    files = [Path(__file__).resolve(), Path(inspect.getsourcefile(dataset_registry) or "").resolve()]
    return _canonical_hash(
        {
            "sources": [{"path": path.name, "sha256": _sha256(path)} for path in files],
            "runtime": {
                "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "numpy": np.__version__,
                "pyarrow": pa.__version__,
                "parquet_contract": "zstd_level_3_dictionary_statistics",
            },
        }
    )


def _root_identity(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise CanonicalFreezeError("governed root is missing or symlinked")
    return {
        "kind": "governed_ashare_lake",
        "layout_version": "canonical_source_catalog_v1",
        "resolved_name": root.name,
    }


def _make_immutable(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o550 if path.is_dir() else 0o440)
    root.chmod(0o550)


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


def _read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise CanonicalFreezeError(f"required immutable JSON missing or symlinked: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CanonicalFreezeError(f"expected JSON object: {target}")
    return payload


def _read_json_array(path: str | Path) -> list[Any]:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise CanonicalFreezeError(f"required immutable JSON array missing or symlinked: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise CanonicalFreezeError(f"expected JSON array: {target}")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_bytes(path, _canonical_json_bytes(payload, pretty=True))


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
