"""Governed security-date evidence classification for Task 055-B."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


EVIDENCE_SCHEMA = "task055b_security_date_evidence_overlay_v2"
EVIDENCE_POINTER_SCHEMA = "task055b_security_date_evidence_pointer_v1"
EVIDENCE_MANIFEST_NAME = "security_date_evidence_manifest.json"
REQUIRED_BAR_FIELDS = ("open", "high", "low", "close", "vol", "amount")


class EvidenceError(RuntimeError):
    """Raised when evidence is ambiguous, conflicting, or tampered with."""


class SecurityDateState(str, Enum):
    TRADED_PRIMARY_BAR = "TRADED_PRIMARY_BAR"
    TRADED_CORROBORATED_BAR = "TRADED_CORROBORATED_BAR"
    TRADED_SOURCE_CONFLICT = "TRADED_SOURCE_CONFLICT"
    OFFICIAL_NON_TRADING = "OFFICIAL_NON_TRADING"
    VENDOR_DAILY_NON_TRADING_MODELED = "VENDOR_DAILY_NON_TRADING_MODELED"
    LIFECYCLE_TERMINATED = "LIFECYCLE_TERMINATED"
    CALENDAR_OR_MEMBERSHIP_ERROR = "CALENDAR_OR_MEMBERSHIP_ERROR"
    RAW_BAR_REQUIRED_FIELD_INVALID = "RAW_BAR_REQUIRED_FIELD_INVALID"
    SOURCE_NORMALIZATION_ZERO_FILL = "SOURCE_NORMALIZATION_ZERO_FILL"
    CORPORATE_ACTION_VALUATION_UNPROVEN = "CORPORATE_ACTION_VALUATION_UNPROVEN"
    DATA_SOURCE_GAP = "DATA_SOURCE_GAP"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class SecurityDateEvidence:
    ts_code: str
    trade_date: str
    state: SecurityDateState
    primary_bar: Mapping[str, Any] | None
    secondary_bar: Mapping[str, Any] | None
    suspension_rows: tuple[Mapping[str, Any], ...]
    official_no_trade_proof: Mapping[str, Any] | None
    vendor_daily_no_trade_proof: Mapping[str, Any] | None
    lifecycle_event: Mapping[str, Any] | None
    corporate_action: Mapping[str, Any] | None
    membership: bool
    membership_known: bool
    listed: bool
    active: bool
    valuation_required: bool
    signal_used: bool
    target_used: bool
    affected_runs: tuple[str, ...]
    source_hashes: Mapping[str, str]
    request_receipts: tuple[Mapping[str, Any], ...]
    supporting_evidence: tuple[str, ...]
    conflicting_evidence: tuple[str, ...]
    classification_rule: str
    review_version: str

    @property
    def key(self) -> tuple[str, str]:
        return self.ts_code, self.trade_date

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def complete_bar(bar: Mapping[str, Any] | None) -> bool:
    if not bar:
        return False
    try:
        values = [float(bar[field]) for field in REQUIRED_BAR_FIELDS]
    except (KeyError, TypeError, ValueError):
        return False
    return all(_finite(value) for value in values) and all(float(bar[field]) > 0 for field in ("open", "high", "low", "close"))


def classify_security_date(row: Mapping[str, Any], *, review_version: str) -> SecurityDateEvidence:
    """Classify one security-date into exactly one fail-closed state."""

    ts_code = str(row.get("ts_code") or "").strip()
    trade_date = str(row.get("trade_date") or "").strip()
    if not ts_code or len(trade_date) != 8 or not trade_date.isdigit():
        raise EvidenceError("security_date_key_invalid")
    primary = _mapping_or_none(row.get("primary_bar"))
    secondary = _mapping_or_none(row.get("secondary_bar"))
    suspensions = tuple(dict(item) for item in row.get("suspension_rows", ()) if isinstance(item, Mapping))
    official = _mapping_or_none(row.get("official_no_trade_proof"))
    vendor = _mapping_or_none(row.get("vendor_daily_no_trade_proof"))
    lifecycle = _mapping_or_none(row.get("lifecycle_event"))
    action = _mapping_or_none(row.get("corporate_action"))
    supporting: list[str] = []
    conflicts: list[str] = []

    primary_valid = complete_bar(primary)
    secondary_valid = complete_bar(secondary)
    primary_present = primary is not None
    source_zero_fill = bool(row.get("source_normalization_zero_fill"))
    calendar_valid = bool(row.get("trade_calendar_session", True))
    membership_known = bool(row.get("membership_known", True))

    if source_zero_fill:
        state = SecurityDateState.SOURCE_NORMALIZATION_ZERO_FILL
        supporting.append("raw_envelope_null_normalized_to_zero")
    elif not calendar_valid or bool(row.get("membership_axis_error")):
        state = SecurityDateState.CALENDAR_OR_MEMBERSHIP_ERROR
        supporting.append("calendar_or_membership_axis_invalid")
    elif lifecycle and _proof_valid(lifecycle) and lifecycle.get("event_type") in {
        "delisted", "absorbed", "merged", "converted", "terminated"
    }:
        state = SecurityDateState.LIFECYCLE_TERMINATED
        supporting.append("governed_lifecycle_termination")
    elif primary_valid and secondary_valid:
        if _bars_agree(primary, secondary, float(row.get("bar_tolerance", 1e-6))):
            state = SecurityDateState.TRADED_CORROBORATED_BAR
            supporting.extend(("primary_complete_bar", "secondary_complete_bar_agrees"))
        else:
            state = SecurityDateState.TRADED_SOURCE_CONFLICT
            conflicts.append("primary_secondary_bar_mismatch")
    elif primary_valid:
        if official or vendor:
            state = SecurityDateState.CONFLICT
            conflicts.append("traded_bar_conflicts_with_non_trading_proof")
        else:
            state = SecurityDateState.TRADED_PRIMARY_BAR
            supporting.append("primary_complete_bar")
    elif secondary_valid:
        state = SecurityDateState.CONFLICT
        conflicts.append("secondary_bar_without_valid_primary_bar")
    elif primary_present:
        state = SecurityDateState.RAW_BAR_REQUIRED_FIELD_INVALID
        supporting.append("primary_row_present_required_field_invalid")
    elif official and _proof_valid(official):
        state = SecurityDateState.OFFICIAL_NON_TRADING
        supporting.append("official_daily_non_trading_proof")
    elif vendor and _vendor_modeled_no_trade_valid(vendor, suspensions):
        state = SecurityDateState.VENDOR_DAILY_NON_TRADING_MODELED
        supporting.append("exact_daily_vendor_s_record_with_cross_geometry_no_bar")
    elif action and bool(row.get("corporate_action_requires_valuation_proof")):
        state = SecurityDateState.CORPORATE_ACTION_VALUATION_UNPROVEN
        supporting.append("corporate_action_transform_unproven")
    else:
        state = SecurityDateState.DATA_SOURCE_GAP
        if suspensions:
            supporting.append("suspension_rows_insufficient_for_no_trade_proof")
        else:
            supporting.append("no_complete_bar_or_governed_non_trading_proof")

    return SecurityDateEvidence(
        ts_code=ts_code,
        trade_date=trade_date,
        state=state,
        primary_bar=primary,
        secondary_bar=secondary,
        suspension_rows=suspensions,
        official_no_trade_proof=official,
        vendor_daily_no_trade_proof=vendor,
        lifecycle_event=lifecycle,
        corporate_action=action,
        membership=bool(row.get("membership", False)),
        membership_known=membership_known,
        listed=bool(row.get("listed", False)),
        active=bool(row.get("active", False)),
        valuation_required=bool(row.get("valuation_required", False)),
        signal_used=bool(row.get("signal_used", False)),
        target_used=bool(row.get("target_used", False)),
        affected_runs=tuple(sorted({str(item) for item in row.get("affected_runs", ())})),
        source_hashes={str(key): str(value) for key, value in sorted(dict(row.get("source_hashes", {})).items())},
        request_receipts=tuple(dict(item) for item in row.get("request_receipts", ()) if isinstance(item, Mapping)),
        supporting_evidence=tuple(supporting),
        conflicting_evidence=tuple(conflicts),
        classification_rule=f"security_date_state_model:{review_version}",
        review_version=review_version,
    )


def publish_evidence_overlay(
    output_root: str | Path,
    rows: Iterable[Mapping[str, Any] | SecurityDateEvidence],
    *,
    source_lineage: Mapping[str, Any],
    review_version: str,
) -> dict[str, Any]:
    records = [
        row if isinstance(row, SecurityDateEvidence) else classify_security_date(row, review_version=review_version)
        for row in rows
    ]
    keys = [record.key for record in records]
    if len(keys) != len(set(keys)):
        raise EvidenceError("duplicate_security_date_key")
    records.sort(key=lambda item: item.key)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055b_evidence.", dir=root))
    try:
        records_path = staging / "security_date_evidence.jsonl"
        _write_jsonl(records_path, [record.to_dict() for record in records])
        lineage_path = staging / "source_lineage.json"
        _write_json(lineage_path, dict(source_lineage))
        state_counts = {state.value: 0 for state in SecurityDateState}
        for record in records:
            state_counts[record.state.value] += 1
        partitions = {
            records_path.name: {"sha256": sha256_file(records_path), "bytes": records_path.stat().st_size},
            lineage_path.name: {"sha256": sha256_file(lineage_path), "bytes": lineage_path.stat().st_size},
        }
        semantic = {
            "schema_version": EVIDENCE_SCHEMA,
            "review_version": review_version,
            "record_count": len(records),
            "key_hash": canonical_hash(keys),
            "state_counts": state_counts,
            "source_lineage_hash": canonical_hash(dict(source_lineage)),
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"security_date_evidence_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id, "status": "published"}
        _write_json(staging / EVIDENCE_MANIFEST_NAME, manifest)
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_write_json(root / "current.json", {
            "schema_version": EVIDENCE_POINTER_SCHEMA,
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/{EVIDENCE_MANIFEST_NAME}",
        })
        return manifest | {"root": str(target), "manifest_path": str(target / EVIDENCE_MANIFEST_NAME)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_evidence_overlay(path: str | Path) -> dict[str, Any]:
    generation, manifest = _resolve_generation(path)
    if manifest.get("schema_version") != EVIDENCE_SCHEMA or manifest.get("status") != "published":
        raise EvidenceError("evidence_manifest_invalid")
    for name, entry in dict(manifest.get("partitions", {})).items():
        artifact = generation / name
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise EvidenceError(f"evidence_partition_mismatch:{name}")
    rows = _read_jsonl(generation / "security_date_evidence.jsonl")
    keys = [(str(row["ts_code"]), str(row["trade_date"])) for row in rows]
    if len(keys) != len(set(keys)) or keys != sorted(keys):
        raise EvidenceError("evidence_keys_invalid")
    for row in rows:
        if row.get("state") not in {state.value for state in SecurityDateState}:
            raise EvidenceError("evidence_state_invalid")
    state_counts = {state.value: 0 for state in SecurityDateState}
    for row in rows:
        state_counts[str(row["state"])] += 1
    semantic = {key: manifest[key] for key in (
        "schema_version", "review_version", "record_count", "key_hash", "state_counts",
        "source_lineage_hash", "partitions",
    )}
    if len(rows) != manifest.get("record_count") or canonical_hash(keys) != manifest.get("key_hash"):
        raise EvidenceError("evidence_record_inventory_mismatch")
    if state_counts != manifest.get("state_counts") or canonical_hash(semantic) != manifest.get("content_hash"):
        raise EvidenceError("evidence_content_hash_mismatch")
    return manifest | {"root": str(generation), "manifest_path": str(generation / EVIDENCE_MANIFEST_NAME), "records": rows}


def _vendor_modeled_no_trade_valid(proof: Mapping[str, Any], suspensions: Sequence[Mapping[str, Any]]) -> bool:
    if not _proof_valid(proof):
        return False
    if proof.get("query_geometry") != "exact_trade_date_and_security_window":
        return False
    if proof.get("bar_row_count") != 0 or not proof.get("cross_geometry_agrees"):
        return False
    return any(str(row.get("suspend_type")) == "S" for row in suspensions)


def _proof_valid(proof: Mapping[str, Any] | None) -> bool:
    return bool(proof and proof.get("source_sha256") and proof.get("request_or_document_hash"))


def _bars_agree(left: Mapping[str, Any], right: Mapping[str, Any], tolerance: float) -> bool:
    return all(abs(float(left[field]) - float(right[field])) <= tolerance for field in REQUIRED_BAR_FIELDS)


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolve_generation(path: str | Path) -> tuple[Path, dict[str, Any]]:
    candidate = Path(path)
    if candidate.is_file():
        return candidate.parent, _read_json(candidate)
    pointer = candidate / "current.json"
    if pointer.is_file():
        payload = _read_json(pointer)
        manifest_path = candidate / str(payload.get("manifest"))
        manifest = _read_json(manifest_path)
        if payload.get("content_hash") != manifest.get("content_hash"):
            raise EvidenceError("evidence_pointer_drift")
        return manifest_path.parent, manifest
    manifest_path = candidate / EVIDENCE_MANIFEST_NAME
    if manifest_path.is_file():
        return candidate, _read_json(manifest_path)
    raise EvidenceError("evidence_overlay_not_found")
