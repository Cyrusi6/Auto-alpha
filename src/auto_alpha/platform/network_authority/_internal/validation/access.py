"""Pre-open access plans and append-only attempted-access evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .contracts import (
    ACCESS_LEDGER_SCHEMA,
    ACCESS_PLAN_SCHEMA,
    BOOTSTRAP_INPUTS,
    MAX_DATE,
    TASK055A_BUNDLE_CONTENT_HASH,
    TASK055A_POLICY_SEAL_HASH,
    TASK055E_REPORT_CONTENT_HASH,
    TASK055F_READ_LEDGER_CONTENT_HASH,
    TASK055F_REPORT_CONTENT_HASH,
)


class AccessPlanError(RuntimeError):
    pass


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class AccessEntry:
    relative_path: str
    dataset_role: str
    parent_generation: str
    expected_sha256: str
    read_mode: str
    date_parser: str
    declared_min_date: str | None = None
    declared_max_date: str | None = None
    byte_range: tuple[int, int] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "dataset_role": self.dataset_role,
            "parent_generation": self.parent_generation,
            "expected_sha256": self.expected_sha256,
            "read_mode": self.read_mode,
            "date_parser": self.date_parser,
            "declared_min_date": self.declared_min_date,
            "declared_max_date": self.declared_max_date,
            "byte_range": list(self.byte_range) if self.byte_range else None,
        }


def publish_bootstrap_access_plan(output_root: str | Path) -> dict[str, Any]:
    entries = []
    for raw in BOOTSTRAP_INPUTS:
        relative = str(raw["relative_path"])
        entries.append(
            AccessEntry(
                relative_path=relative,
                dataset_role=str(raw["dataset_role"]),
                parent_generation=_generation_from_path(relative),
                expected_sha256=str(raw["expected_sha256"]),
                read_mode=str(raw["read_mode"]),
                date_parser=str(raw["date_parser"]),
                declared_min_date=raw.get("declared_min_date"),
                declared_max_date=raw.get("declared_max_date"),
            ).payload()
        )
    semantic = {
        "schema_version": ACCESS_PLAN_SCHEMA,
        "status": "sealed",
        "plan_scope": "bootstrap_trust_anchors",
        "max_allowed_date": MAX_DATE,
        "entry_count": len(entries),
        "entries_root": canonical_hash(entries),
        "entries": entries,
        "trust_anchors": {
            "task055e_report_content_hash": TASK055E_REPORT_CONTENT_HASH,
            "task055f_report_content_hash": TASK055F_REPORT_CONTENT_HASH,
            "task055f_read_ledger_content_hash": TASK055F_READ_LEDGER_CONTENT_HASH,
            "task055a_bundle_content_hash": TASK055A_BUNDLE_CONTENT_HASH,
            "task055a_policy_seal_hash": TASK055A_POLICY_SEAL_HASH,
        },
    }
    return _publish_plan(Path(output_root), "bootstrap_access_plan", semantic)


def build_production_access_plan(
    *, governed_root: str | Path, bootstrap_plan: str | Path, output_root: str | Path
) -> dict[str, Any]:
    bootstrap = validate_access_plan(bootstrap_plan)
    broker = AccessBroker(governed_root, bootstrap)
    ledger_manifest = broker.read_json(
        BOOTSTRAP_INPUTS[0]["relative_path"], principal="access_plan_builder"
    )
    if ledger_manifest.get("content_hash") != TASK055F_READ_LEDGER_CONTENT_HASH:
        raise AccessPlanError("bootstrap_read_ledger_content_hash_mismatch")
    rows = broker.read_jsonl(
        BOOTSTRAP_INPUTS[1]["relative_path"], principal="access_plan_builder"
    )
    if len(rows) != int(ledger_manifest.get("record_count") or -1):
        raise AccessPlanError("bootstrap_read_ledger_record_count_mismatch")
    if canonical_hash(rows) != ledger_manifest.get("rows_root"):
        raise AccessPlanError("bootstrap_read_ledger_rows_root_mismatch")
    f_report = broker.read_json(BOOTSTRAP_INPUTS[2]["relative_path"], principal="access_plan_builder")
    e_report = broker.read_json(BOOTSTRAP_INPUTS[3]["relative_path"], principal="access_plan_builder")
    broker.read_json(BOOTSTRAP_INPUTS[4]["relative_path"], principal="access_plan_builder")
    bundle = broker.read_json(BOOTSTRAP_INPUTS[5]["relative_path"], principal="access_plan_builder")
    policy = broker.read_json(BOOTSTRAP_INPUTS[6]["relative_path"], principal="access_plan_builder")
    if f_report.get("content_hash") != TASK055F_REPORT_CONTENT_HASH:
        raise AccessPlanError("task055f_parent_content_hash_mismatch")
    if e_report.get("content_hash") != TASK055E_REPORT_CONTENT_HASH:
        raise AccessPlanError("task055e_parent_content_hash_mismatch")
    if bundle.get("content_hash") != TASK055A_BUNDLE_CONTENT_HASH:
        raise AccessPlanError("task055a_bundle_content_hash_mismatch")
    if policy.get("content_hash") != TASK055A_POLICY_SEAL_HASH:
        raise AccessPlanError("task055a_policy_seal_hash_mismatch")

    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        relative = _relative(str(row.get("relative_path") or ""))
        declared_max = row.get("declared_end_date") or row.get("actual_max_date")
        declared_min = row.get("declared_start_date") or row.get("actual_min_date")
        candidate = AccessEntry(
            relative_path=relative,
            dataset_role=str(row.get("dataset") or "unknown"),
            parent_generation=_generation_from_path(relative),
            expected_sha256=str(row.get("sha256") or ""),
            read_mode=_read_mode(relative),
            date_parser=_parser_for_role(str(row.get("dataset") or ""), relative),
            declared_min_date=str(declared_min) if declared_min else None,
            declared_max_date=str(declared_max) if declared_max else None,
        ).payload()
        prior = unique.get(relative)
        if prior is not None and prior != candidate:
            raise AccessPlanError(f"access_catalog_path_conflict:{relative}")
        unique[relative] = candidate
    for raw in BOOTSTRAP_INPUTS:
        relative = str(raw["relative_path"])
        unique[relative] = AccessEntry(
            relative_path=relative,
            dataset_role=str(raw["dataset_role"]),
            parent_generation=_generation_from_path(relative),
            expected_sha256=str(raw["expected_sha256"]),
            read_mode=str(raw["read_mode"]),
            date_parser=str(raw["date_parser"]),
            declared_min_date=raw.get("declared_min_date"),
            declared_max_date=raw.get("declared_max_date"),
        ).payload()
    bundle_root = Path(BOOTSTRAP_INPUTS[5]["relative_path"]).parent
    for name, artifact in sorted((bundle.get("artifacts") or {}).items()):
        relative = _relative(str(bundle_root / str(artifact.get("path") or "")))
        unique[relative] = AccessEntry(
            relative_path=relative,
            dataset_role=f"simulation_bundle:{artifact.get('role') or name}",
            parent_generation=str(bundle.get("generation_id") or ""),
            expected_sha256=str(artifact.get("sha256") or ""),
            read_mode=_read_mode(relative),
            date_parser=_parser_for_role(str(artifact.get("role") or name), relative),
            declared_min_date=None,
            declared_max_date=str(artifact.get("max_date") or bundle.get("valuation_cutoff") or "") or None,
        ).payload()
    catalog_entries = sorted(unique.values(), key=lambda row: row["relative_path"])
    catalog_semantic = {
        "schema_version": ACCESS_PLAN_SCHEMA,
        "status": "sealed",
        "plan_scope": "task055g_catalog_expansion",
        "max_allowed_date": MAX_DATE,
        "entry_count": len(catalog_entries),
        "entries_root": canonical_hash(catalog_entries),
        "entries": catalog_entries,
        "bootstrap_plan_content_hash": bootstrap["content_hash"],
    }
    catalog_plan = _publish_plan(Path(output_root) / "catalog", "catalog_access_plan", catalog_semantic)
    catalog_broker = AccessBroker(governed_root, catalog_plan["manifest_path"])
    provenance_entries = [row for row in catalog_entries if row["dataset_role"] == "task055e_provenance_manifest"]
    if len(provenance_entries) != 1:
        raise AccessPlanError("task055e_provenance_catalog_cardinality_invalid")
    provenance = catalog_broker.read_json(
        provenance_entries[0]["relative_path"], principal="access_plan_builder"
    )
    provenance_root = Path(provenance_entries[0]["relative_path"]).parent
    for name, partition in sorted((provenance.get("partitions") or {}).items()):
        relative = _relative(str(provenance_root / str(partition.get("path") or "")))
        unique[relative] = AccessEntry(
            relative_path=relative,
            dataset_role=f"task055e_provenance_partition:{name}",
            parent_generation=str(provenance.get("generation_id") or _generation_from_path(relative)),
            expected_sha256=str(partition.get("sha256") or ""),
            read_mode=_read_mode(relative),
            date_parser="provenance",
            declared_max_date=MAX_DATE,
        ).payload()
    matrix_entries = [row for row in catalog_entries if row["dataset_role"] == "strict_matrix_manifest"]
    if len(matrix_entries) != 1:
        raise AccessPlanError("strict_matrix_catalog_cardinality_invalid")
    matrix_manifest = catalog_broker.read_json(
        matrix_entries[0]["relative_path"], principal="access_plan_builder"
    )
    matrix_root = Path(matrix_entries[0]["relative_path"]).parent
    for name, digest in sorted((matrix_manifest.get("partition_sha256") or {}).items()):
        relative = _relative(str(matrix_root / name))
        unique[relative] = AccessEntry(
            relative_path=relative,
            dataset_role=f"strict_matrix_partition:{name}",
            parent_generation=str(matrix_manifest.get("generation_id") or _generation_from_path(relative)),
            expected_sha256=str(digest or ""),
            read_mode=_read_mode(relative),
            date_parser="binary_declared_axis" if relative.endswith(".npy") else _parser_for_role(name, relative),
            declared_min_date=None,
            declared_max_date=MAX_DATE,
        ).payload()
    catalog_expansion_ledger = catalog_broker.publish_ledger(Path(output_root) / "catalog_expansion_read_ledger")
    entries = sorted(unique.values(), key=lambda row: row["relative_path"])
    for entry in entries:
        _validate_entry(entry, max_date=MAX_DATE)
    bootstrap_ledger = broker.publish_ledger(Path(output_root) / "bootstrap_builder_read_ledger")
    semantic = {
        "schema_version": ACCESS_PLAN_SCHEMA,
        "status": "sealed",
        "plan_scope": "task055g_production_input_closure",
        "max_allowed_date": MAX_DATE,
        "entry_count": len(entries),
        "entries_root": canonical_hash(entries),
        "entries": entries,
        "bootstrap_plan_content_hash": bootstrap["content_hash"],
        "bootstrap_builder_read_ledger_content_hash": bootstrap_ledger["content_hash"],
        "catalog_access_plan_content_hash": catalog_plan["content_hash"],
        "catalog_expansion_read_ledger_content_hash": catalog_expansion_ledger["content_hash"],
        "parent_content_hashes": {
            "task055e": e_report["content_hash"],
            "task055f": f_report["content_hash"],
            "task055a_bundle": bundle["content_hash"],
            "task055a_policy": policy["content_hash"],
        },
    }
    return _publish_plan(Path(output_root), "production_access_plan", semantic)


def validate_access_plan(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "access_plan.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != ACCESS_PLAN_SCHEMA or payload.get("status") != "sealed":
        raise AccessPlanError("access_plan_schema_or_status_invalid")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise AccessPlanError("access_plan_content_hash_mismatch")
    entries = list(payload.get("entries") or ())
    if len(entries) != payload.get("entry_count") or canonical_hash(entries) != payload.get("entries_root"):
        raise AccessPlanError("access_plan_entries_mismatch")
    if len({row.get("relative_path") for row in entries}) != len(entries):
        raise AccessPlanError("access_plan_duplicate_path")
    for entry in entries:
        _validate_entry(entry, max_date=str(payload.get("max_allowed_date") or MAX_DATE))
    return payload | {"manifest_path": str(manifest_path)}


class AccessBroker:
    def __init__(
        self,
        governed_root: str | Path,
        plan: str | Path | Mapping[str, Any],
        *,
        open_bytes: Callable[[Path], bytes] | None = None,
    ) -> None:
        self.governed_root = Path(governed_root).resolve()
        self.plan = validate_access_plan(plan) if not isinstance(plan, Mapping) else dict(plan)
        self.entries = {str(row["relative_path"]): dict(row) for row in self.plan["entries"]}
        self.rows: list[dict[str, Any]] = []
        self._sequence = 0
        self._open_bytes = open_bytes or (lambda source: source.read_bytes())

    @property
    def max_read_date(self) -> str | None:
        dates = [str(row["actual_max_date"]) for row in self.rows if row.get("actual_max_date")]
        return max(dates) if dates else None

    @property
    def prospective_holdout_accessed(self) -> bool:
        return any(row.get("decision") == "opened_policy_violation" for row in self.rows)

    def read_json(
        self,
        path: str | Path,
        *,
        principal: str | None = None,
        expected_role: str | None = None,
        component: str | None = None,
        dataset: str | None = None,
        **_: Any,
    ) -> Any:
        principal = principal or component or "unknown_reader"
        raw = self._open(path, principal=principal, expected_mode="json", expected_role=expected_role)
        return json.loads(raw)

    def read_jsonl(
        self,
        path: str | Path,
        *,
        principal: str | None = None,
        expected_role: str | None = None,
        component: str | None = None,
        dataset: str | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        principal = principal or component or "unknown_reader"
        raw = self._open(path, principal=principal, expected_mode="jsonl", expected_role=expected_role)
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def read_bytes(self, path: str | Path, *, principal: str, expected_role: str | None = None) -> bytes:
        return self._open(path, principal=principal, expected_mode=None, expected_role=expected_role)

    def record_binary(self, path: str | Path, *, component: str, dataset: str, **_: Any) -> Path:
        self.read_bytes(path, principal=component, expected_role=dataset)
        return self._source(path)

    def load_npy(self, path: str | Path, *, component: str, dataset: str, **_: Any) -> Any:
        import numpy as np

        relative = self._relative(path)
        entry = self.entries.get(relative)
        if entry is None:
            self._record_attempt(relative, component, None, "blocked_before_open", "path_not_in_access_plan")
            raise AccessPlanError(f"access_path_not_sealed:{relative}")
        try:
            _validate_entry(entry, max_date=str(self.plan["max_allowed_date"]))
            if entry["read_mode"] != "npy":
                raise AccessPlanError("access_read_mode_mismatch")
        except AccessPlanError as exc:
            self._record_attempt(relative, component, entry, "blocked_before_open", str(exc))
            raise
        source = self._source(relative)
        actual_sha = sha256_file(source)
        size = source.stat().st_size
        if actual_sha != entry["expected_sha256"]:
            self._record_attempt(relative, component, entry, "opened_policy_violation", "source_sha256_mismatch", actual_sha=actual_sha, size_bytes=size)
            raise AccessPlanError(f"access_source_sha_mismatch:{relative}")
        self._record_attempt(relative, component, entry, "opened_allowed", None, actual_sha=actual_sha, size_bytes=size)
        return np.load(source, mmap_mode="r", allow_pickle=False)

    def publish_ledger(self, output_root: str | Path) -> dict[str, Any]:
        rows = list(self.rows)
        payload = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
            for row in rows
        )
        semantic = {
            "schema_version": ACCESS_LEDGER_SCHEMA,
            "status": "published",
            "access_plan_content_hash": self.plan["content_hash"],
            "max_allowed_date": self.plan["max_allowed_date"],
            "record_count": len(rows),
            "rows_root": canonical_hash(rows),
            "max_read_date": self.max_read_date,
            "prospective_holdout_accessed": self.prospective_holdout_accessed,
            "decision_counts": {
                decision: sum(row["decision"] == decision for row in rows)
                for decision in ("blocked_before_open", "opened_allowed", "opened_policy_violation")
            },
            "partition": {"path": "attempted_access.jsonl", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)},
        }
        return _publish_generation(Path(output_root), "access_ledger", "access_ledger_manifest.json", semantic, {"attempted_access.jsonl": payload})

    def _open(
        self,
        path: str | Path,
        *,
        principal: str,
        expected_mode: str | None,
        expected_role: str | None,
    ) -> bytes:
        relative = self._relative(path)
        entry = self.entries.get(relative)
        if entry is None:
            self._record_attempt(relative, principal, None, "blocked_before_open", "path_not_in_access_plan")
            raise AccessPlanError(f"access_path_not_sealed:{relative}")
        try:
            _validate_entry(entry, max_date=str(self.plan["max_allowed_date"]))
            if expected_mode and entry["read_mode"] != expected_mode:
                raise AccessPlanError("access_read_mode_mismatch")
            if expected_role and not _role_matches(str(entry["dataset_role"]), expected_role):
                raise AccessPlanError("access_dataset_role_mismatch")
        except AccessPlanError as exc:
            self._record_attempt(relative, principal, entry, "blocked_before_open", str(exc))
            raise
        source = self._source(relative)
        raw = self._open_bytes(source)
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != entry["expected_sha256"]:
            self._record_attempt(relative, principal, entry, "opened_policy_violation", "source_sha256_mismatch", actual_sha=actual_sha)
            raise AccessPlanError(f"access_source_sha_mismatch:{relative}")
        actual_dates = _extract_dates(raw, entry["date_parser"], entry["read_mode"])
        actual_min = min(actual_dates) if actual_dates else None
        actual_max = max(actual_dates) if actual_dates else None
        violation = bool(actual_max and actual_max > self.plan["max_allowed_date"])
        self._record_attempt(
            relative,
            principal,
            entry,
            "opened_policy_violation" if violation else "opened_allowed",
            "actual_date_exceeds_boundary" if violation else None,
            actual_sha=actual_sha,
            actual_min=actual_min,
            actual_max=actual_max,
            size_bytes=len(raw),
        )
        if violation:
            raise AccessPlanError(f"actual_read_date_exceeds_boundary:{actual_max}")
        return raw

    def _record_attempt(
        self,
        relative: str,
        principal: str,
        entry: Mapping[str, Any] | None,
        decision: str,
        reason: str | None,
        *,
        actual_sha: str | None = None,
        actual_min: str | None = None,
        actual_max: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        self._sequence += 1
        row = {
            "sequence": self._sequence,
            "principal": principal,
            "relative_path": relative,
            "dataset_role": None if entry is None else entry.get("dataset_role"),
            "expected_sha256": None if entry is None else entry.get("expected_sha256"),
            "actual_sha256": actual_sha,
            "sha256": actual_sha,
            "declared_min_date": None if entry is None else entry.get("declared_min_date"),
            "declared_max_date": None if entry is None else entry.get("declared_max_date"),
            "actual_min_date": actual_min,
            "actual_max_date": actual_max,
            "size_bytes": size_bytes,
            "decision": decision,
            "reason": reason,
        }
        row["row_hash"] = canonical_hash(row)
        self.rows.append(row)

    def _relative(self, path: str | Path) -> str:
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if self.governed_root not in resolved.parents:
                return str(candidate)
            return str(resolved.relative_to(self.governed_root))
        return _relative(str(candidate))

    def _source(self, path: str | Path) -> Path:
        relative = self._relative(path)
        source = (self.governed_root / relative).resolve()
        if self.governed_root not in source.parents or not source.is_file() or Path(self.governed_root / relative).is_symlink():
            raise AccessPlanError("access_source_missing_escape_or_symlink")
        return source


def validate_access_ledger(path: str | Path, *, plan: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "access_ledger_manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != ACCESS_LEDGER_SCHEMA or payload.get("status") != "published":
        raise AccessPlanError("access_ledger_schema_or_status_invalid")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise AccessPlanError("access_ledger_content_hash_mismatch")
    validated_plan = validate_access_plan(plan)
    if validated_plan["content_hash"] != payload.get("access_plan_content_hash"):
        raise AccessPlanError("access_ledger_plan_hash_mismatch")
    part = payload.get("partition") or {}
    rows_path = manifest_path.parent / str(part.get("path") or "")
    if not rows_path.is_file() or sha256_file(rows_path) != part.get("sha256"):
        raise AccessPlanError("access_ledger_partition_mismatch")
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != payload.get("record_count") or canonical_hash(rows) != payload.get("rows_root"):
        raise AccessPlanError("access_ledger_rows_mismatch")
    for index, row in enumerate(rows, 1):
        if row.get("sequence") != index:
            raise AccessPlanError("access_ledger_sequence_invalid")
        unsigned = {key: value for key, value in row.items() if key != "row_hash"}
        if canonical_hash(unsigned) != row.get("row_hash"):
            raise AccessPlanError("access_ledger_row_hash_invalid")
    accessed = any(row.get("decision") == "opened_policy_violation" for row in rows)
    if accessed != bool(payload.get("prospective_holdout_accessed")):
        raise AccessPlanError("access_ledger_future_access_flag_mismatch")
    return payload | {"manifest_path": str(manifest_path), "rows": rows}


def _validate_entry(entry: Mapping[str, Any], *, max_date: str) -> None:
    relative = _relative(str(entry.get("relative_path") or ""))
    if relative != entry.get("relative_path"):
        raise AccessPlanError("access_entry_relative_path_not_normalized")
    if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("expected_sha256") or "")):
        raise AccessPlanError("access_entry_sha_missing")
    if entry.get("read_mode") not in {"json", "jsonl", "npy", "binary", "text"}:
        raise AccessPlanError("access_entry_read_mode_invalid")
    if not entry.get("dataset_role") or not entry.get("parent_generation") or not entry.get("date_parser"):
        raise AccessPlanError("access_entry_lineage_incomplete")
    maximum = entry.get("declared_max_date")
    if maximum and (not re.fullmatch(r"\d{8}", str(maximum)) or str(maximum) > max_date):
        raise AccessPlanError("declared_read_range_exceeds_boundary")
    minimum = entry.get("declared_min_date")
    if minimum and not re.fullmatch(r"\d{8}", str(minimum)):
        raise AccessPlanError("access_entry_min_date_invalid")
    if minimum and maximum and str(minimum) > str(maximum):
        raise AccessPlanError("access_entry_date_range_invalid")


def _extract_dates(data: bytes, parser: str, mode: str) -> list[str]:
    if mode == "npy" or parser in {"none", "binary_declared_axis"}:
        return []
    try:
        if mode == "jsonl":
            payload: Any = [json.loads(line) for line in data.splitlines() if line.strip()]
        elif mode == "json":
            payload = json.loads(data)
        else:
            payload = data.decode("utf-8", errors="ignore")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccessPlanError("access_payload_parse_failed") from exc
    extractor = DATE_PARSERS.get(parser)
    if extractor is None:
        raise AccessPlanError(f"access_date_parser_unknown:{parser}")
    return sorted({date for date in extractor(payload) if _date_value(date)})


def _walk_fields(value: Any, fields: set[str]) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in fields:
                date = _date_value(item)
                if date:
                    yield date
            yield from _walk_fields(item, fields)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_fields(item, fields)


def _axis_dates(value: Any) -> Iterable[str]:
    if isinstance(value, list):
        for item in value:
            date = _date_value(item)
            if date:
                yield date


DATE_PARSERS: dict[str, Callable[[Any], Iterable[str]]] = {
    "none": lambda value: (),
    "manifest_metadata": lambda value: _walk_fields(value, {"min_date", "max_date", "start_date", "end_date", "max_allowed_date"}),
    "read_ledger_rows": lambda value: _walk_fields(value, {"declared_start_date", "declared_end_date", "actual_min_date", "actual_max_date"}),
    "task055f_report": lambda value: _walk_fields(value, {"max_read_date", "max_observed_signal_date", "max_observed_source_date", "max_observed_target_endpoint"}),
    "task055e_report": lambda value: _walk_fields(value, {"max_read_or_request_date", "max_project_observed_signal_date", "max_project_observed_source_date", "max_project_observed_target_endpoint", "start_date", "end_date", "trade_date"}),
    "config_dates": lambda value: _walk_fields(value, {"research_end_date", "cutoff", "start_date", "end_date", "max_date"}),
    "simulation_bundle_manifest": lambda value: _walk_fields(value, {"signal_cutoff", "execution_cutoff", "valuation_cutoff", "max_date", "coverage_end"}),
    "policy_seal": lambda value: _walk_fields(value, {"simulation_start", "simulation_end", "research_end_date", "signal_cutoff", "execution_cutoff"}),
    "date_axis": _axis_dates,
    "daily_envelope": lambda value: _walk_fields(value, {"trade_date", "start_date", "end_date"}),
    "suspension_envelope": lambda value: _walk_fields(value, {"trade_date", "start_date", "end_date"}),
    "inventory": lambda value: _walk_fields(value, {"trade_date", "source_date", "ex_date", "list_date", "delist_date", "start_date", "end_date"}),
    "provenance": lambda value: _walk_fields(value, {"trade_date", "request_start", "request_end", "start_date", "end_date"}),
    "bundle_artifact": lambda value: _walk_fields(value, {"trade_date", "source_date", "ex_date", "list_date", "delist_date", "start_date", "end_date"}),
    "binary_declared_axis": lambda value: (),
}


def _parser_for_role(role: str, relative: str) -> str:
    lowered = role.lower()
    if relative.endswith("trade_dates.json") or "date_axis" in lowered or "execution_axis" in lowered or "signal_axis" in lowered:
        return "date_axis"
    if "daily_cache" in lowered or ("daily" in lowered and "cache" in lowered):
        return "daily_envelope"
    if "suspend" in lowered and "cache" in lowered:
        return "suspension_envelope"
    if "inventory" in lowered:
        return "inventory"
    if "provenance" in lowered or "coverage_ledger" in lowered:
        return "provenance"
    if relative.endswith(".npy"):
        return "binary_declared_axis"
    if "simulation_bundle" in lowered:
        return "bundle_artifact"
    return "manifest_metadata"


def _role_matches(sealed: str, requested: str) -> bool:
    return sealed == requested or sealed.endswith(requested) or requested.endswith(sealed.split(":")[-1])


def _read_mode(relative: str) -> str:
    if relative.endswith(".jsonl"):
        return "jsonl"
    if relative.endswith(".json"):
        return "json"
    if relative.endswith(".npy"):
        return "npy"
    if relative.endswith((".html", ".htm", ".txt", ".md")):
        return "text"
    return "binary"


def _date_value(value: Any) -> str | None:
    text = str(value or "").replace("-", "").replace("/", "")
    return text if re.fullmatch(r"\d{8}", text) else None


def _relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise AccessPlanError("access_relative_path_invalid")
    return str(path)


def _generation_from_path(relative: str) -> str:
    for part in Path(relative).parts:
        if part.startswith(("generation", "offline_", "truth_", "simulation_", "policy_", "read_ledger_", "task055")):
            return part
    return Path(relative).parts[0]


def _publish_plan(root: Path, prefix: str, semantic: Mapping[str, Any]) -> dict[str, Any]:
    return _publish_generation(root, prefix, "access_plan.json", semantic, {})


def _publish_generation(
    root: Path,
    prefix: str,
    manifest_name: str,
    semantic: Mapping[str, Any],
    files: Mapping[str, bytes],
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(semantic)
    generation_id = f"{prefix}_{content_hash[:24]}"
    manifest = dict(semantic) | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=f".task055g.{prefix}.", dir=root))
    try:
        for relative, data in files.items():
            target = staging / _relative(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (staging / manifest_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(root / "current.json", {"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/{manifest_name}"})
        return manifest | {"manifest_path": str(target / manifest_name)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _resolve_manifest(path: str | Path, name: str) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / name
    if candidate.is_file():
        return candidate
    raise AccessPlanError("access_manifest_missing")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
