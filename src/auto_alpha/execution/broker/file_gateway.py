"""Broker file gateway profiles, mapping, storage, round-trip validation, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerFileGatewayMode:
    dry_run = "dry_run"
    manual_handoff = "manual_handoff"
    disabled = "disabled"


class BrokerFileSchemaName:
    generic_broker_csv = "generic_broker_csv"
    generic_broker_jsonl = "generic_broker_jsonl"
    qmt_skeleton_csv = "qmt_skeleton_csv"
    custom_csv_mapping = "custom_csv_mapping"


class BrokerFileBatchStatus:
    planned = "planned"
    exported = "exported"
    handed_off = "handed_off"
    acknowledged = "acknowledged"
    partially_acknowledged = "partially_acknowledged"
    filled = "filled"
    partially_filled = "partially_filled"
    reconciled = "reconciled"
    rejected = "rejected"
    cancelled = "cancelled"
    failed = "failed"


@dataclass(frozen=True)
class BrokerFileProfile:
    profile_id: str
    profile_name: str
    schema_name: str
    field_mapping: dict[str, str]
    date_format: str = "%Y%m%d"
    price_precision: int = 4
    value_precision: int = 2
    share_unit: int = 1
    amount_unit: float = 1.0
    encoding: str = "utf-8"
    delimiter: str = ","
    required_columns: list[str] = field(default_factory=list)
    optional_columns: list[str] = field(default_factory=list)
    side_mapping: dict[str, str] = field(default_factory=lambda: {"BUY": "BUY", "SELL": "SELL"})
    status_mapping: dict[str, str] = field(default_factory=lambda: {"ACK": "ACK", "FILLED": "FILLED", "REJECTED": "REJECTED", "PARTIAL": "PARTIAL"})
    notice: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileBatch:
    file_batch_id: str
    production_run_id: str
    approval_id: str
    broker_batch_id: str
    trade_date: str
    account_id: str
    profile_id: str
    status: str
    created_at: str
    exported_at: str | None = None
    handed_off_at: str | None = None
    imported_at: str | None = None
    order_count: int = 0
    total_order_value: float = 0.0
    source_order_paths: dict[str, str] = field(default_factory=dict)
    outbox_paths: dict[str, str] = field(default_factory=dict)
    inbox_paths: dict[str, str] = field(default_factory=dict)
    manifest_path: str = ""
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileRecord:
    client_order_id: str
    trade_date: str
    ts_code: str
    side: str
    shares: int
    price: float
    price_type: str
    order_value: float
    parent_order_id: str | None = None
    child_order_id: str | None = None
    bucket: str | None = None
    broker_batch_id: str = ""
    production_run_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileRoundTripIssue:
    severity: str
    code: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerFileRoundTripReport:
    file_batch_id: str
    broker_batch_id: str
    status: str
    order_count: int
    ack_count: int
    status_count: int
    fill_count: int
    reject_count: int
    missing_ack_count: int
    orphan_fill_count: int
    duplicate_fill_count: int
    unknown_status_count: int
    error_count: int
    warning_count: int
    issues: list[BrokerFileRoundTripIssue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class BrokerFileGatewayReport:
    file_batch_id: str
    status: str
    profile: dict[str, Any]
    batch: dict[str, Any]
    manifest: dict[str, Any]
    roundtrip: dict[str, Any]
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import hashlib
import json
from pathlib import Path
from typing import Any



INTERNAL_FIELDS = [
    "client_order_id",
    "trade_date",
    "ts_code",
    "side",
    "shares",
    "price",
    "price_type",
    "order_value",
    "parent_order_id",
    "child_order_id",
    "bucket",
    "broker_batch_id",
    "production_run_id",
]

QMT_SKELETON_NOTICE = (
    "qmt_skeleton_csv is a config-driven dry-run mapping skeleton only. "
    "It does not guarantee compatibility with any real QMT or broker counter. "
    "Real columns, encoding, side enums, price types, order attributes and file paths require manual verification."
)


def get_profile(profile_name: str = BrokerFileSchemaName.generic_broker_csv) -> BrokerFileProfile:
    if profile_name == BrokerFileSchemaName.generic_broker_jsonl:
        return _profile(profile_name, BrokerFileSchemaName.generic_broker_jsonl, {}, "generic JSONL dry-run instruction schema")
    if profile_name == BrokerFileSchemaName.qmt_skeleton_csv:
        mapping = {
            "client_order_id": "client_order_id",
            "trade_date": "trade_date",
            "ts_code": "security_code",
            "side": "side",
            "shares": "volume",
            "price": "price",
            "price_type": "price_type",
            "order_value": "order_value",
            "parent_order_id": "parent_order_id",
            "child_order_id": "child_order_id",
            "bucket": "bucket",
            "broker_batch_id": "broker_batch_id",
            "production_run_id": "production_run_id",
        }
        return _profile(profile_name, BrokerFileSchemaName.qmt_skeleton_csv, mapping, QMT_SKELETON_NOTICE)
    return _profile(
        BrokerFileSchemaName.generic_broker_csv,
        BrokerFileSchemaName.generic_broker_csv,
        {},
        "generic broker CSV dry-run instruction schema; no real broker compatibility is implied",
    )


def load_profile(profile_name: str = BrokerFileSchemaName.generic_broker_csv, profile_config: str | Path | None = None) -> BrokerFileProfile:
    profile = get_profile(profile_name)
    if profile_config is None:
        return profile
    payload = json.loads(Path(profile_config).read_text(encoding="utf-8"))
    base = profile.to_dict()
    for key, value in payload.items():
        if key in base and value is not None:
            base[key] = value
    if base.get("schema_name") == BrokerFileSchemaName.custom_csv_mapping:
        base.setdefault("notice", "custom dry-run mapping; manual field certification required")
    base["profile_id"] = _profile_id(base)
    return BrokerFileProfile(**base)


def profile_hash(profile: BrokerFileProfile) -> str:
    return _profile_id(profile.to_dict()).replace("profile_", "")


def _profile(profile_name: str, schema_name: str, field_mapping: dict[str, str], notice: str) -> BrokerFileProfile:
    mapping = {field: field_mapping.get(field, field) for field in INTERNAL_FIELDS}
    payload: dict[str, Any] = {
        "profile_id": "",
        "profile_name": profile_name,
        "schema_name": schema_name,
        "field_mapping": mapping,
        "required_columns": [mapping[field] for field in INTERNAL_FIELDS[:8]],
        "optional_columns": [mapping[field] for field in INTERNAL_FIELDS[8:]],
        "notice": notice,
        "metadata": {"no_real_submit": True},
    }
    payload["profile_id"] = _profile_id(payload)
    return BrokerFileProfile(**payload)


def _profile_id(payload: dict[str, Any]) -> str:
    stable = json.dumps({k: v for k, v in payload.items() if k != "profile_id"}, ensure_ascii=False, sort_keys=True)
    return "profile_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]

import hashlib
from typing import Any, Iterable

from auto_alpha.execution.broker.adapter import BrokerFillRecord
from auto_alpha.execution.broker.adapter import BrokerOrderRequest



def map_internal_orders_to_file_records(
    orders: Iterable[dict[str, Any]],
    profile: BrokerFileProfile,
    *,
    broker_batch_id: str = "",
    production_run_id: str = "",
    trade_date: str = "",
) -> tuple[list[BrokerFileRecord], list[BrokerFileRoundTripIssue]]:
    records: list[BrokerFileRecord] = []
    issues: list[BrokerFileRoundTripIssue] = []
    for order in orders:
        try:
            records.append(_record_from_payload(order, broker_batch_id=broker_batch_id, production_run_id=production_run_id, trade_date=trade_date))
        except Exception as exc:
            issues.append(BrokerFileRoundTripIssue("error", "mapping_error", str(exc), {"payload": dict(order)}))
    return records, issues


def map_child_orders_to_file_records(
    child_orders: Iterable[dict[str, Any]],
    profile: BrokerFileProfile,
    *,
    broker_batch_id: str = "",
    production_run_id: str = "",
    trade_date: str = "",
) -> tuple[list[BrokerFileRecord], list[BrokerFileRoundTripIssue]]:
    return map_internal_orders_to_file_records(child_orders, profile, broker_batch_id=broker_batch_id, production_run_id=production_run_id, trade_date=trade_date)


def map_broker_requests_to_file_records(
    requests: Iterable[BrokerOrderRequest | dict[str, Any]],
    profile: BrokerFileProfile,
) -> tuple[list[BrokerFileRecord], list[BrokerFileRoundTripIssue]]:
    rows = [request.to_dict() if hasattr(request, "to_dict") else dict(request) for request in requests]
    return map_internal_orders_to_file_records(rows, profile)


def file_record_to_row(record: BrokerFileRecord, profile: BrokerFileProfile) -> dict[str, Any]:
    payload = record.to_dict()
    row = {}
    for field in INTERNAL_FIELDS:
        value = payload.get(field)
        if field == "side":
            value = profile.side_mapping.get(str(value).upper(), value)
        if field == "price":
            value = round(float(value or 0.0), profile.price_precision)
        if field == "order_value":
            value = round(float(value or 0.0) / float(profile.amount_unit or 1.0), profile.value_precision)
        row[profile.field_mapping.get(field, field)] = value
    return row


def row_to_internal(row: dict[str, Any], profile: BrokerFileProfile) -> dict[str, Any]:
    reverse = {value: key for key, value in profile.field_mapping.items()}
    payload = {reverse.get(key, key): value for key, value in row.items()}
    side_reverse = {value: key for key, value in profile.side_mapping.items()}
    if "side" in payload:
        payload["side"] = side_reverse.get(str(payload["side"]), str(payload["side"]).upper())
    return payload


def map_file_fills_to_broker_fills(rows: Iterable[dict[str, Any]], profile: BrokerFileProfile, batch_id: str) -> list[BrokerFillRecord]:
    fills: list[BrokerFillRecord] = []
    for row in rows:
        payload = row_to_internal(row, profile)
        client_order_id = str(payload.get("client_order_id") or "")
        shares = int(float(payload.get("shares") or 0))
        price = float(payload.get("price") or 0.0)
        value = float(payload.get("value") or payload.get("order_value") or shares * price)
        status = str(payload.get("status") or "FILLED").upper()
        fills.append(
            BrokerFillRecord(
                broker_fill_id=str(payload.get("broker_fill_id") or "bff_" + _hash(batch_id, client_order_id, shares, value)),
                broker_order_id=str(payload.get("broker_order_id") or client_order_id),
                client_order_id=client_order_id,
                batch_id=batch_id,
                trade_date=str(payload.get("trade_date") or ""),
                ts_code=str(payload.get("ts_code") or ""),
                side=str(payload.get("side") or ""),
                price=price,
                shares=shares,
                value=value,
                cost=float(payload.get("cost") or 0.0),
                status=status,
                reason=str(payload.get("reason") or ""),
                parent_order_id=payload.get("parent_order_id"),
                child_order_id=payload.get("child_order_id"),
                bucket=payload.get("bucket"),
                broker_adapter="file",
            )
        )
    return fills


def _record_from_payload(payload: dict[str, Any], *, broker_batch_id: str, production_run_id: str, trade_date: str) -> BrokerFileRecord:
    ts_code = str(payload.get("ts_code") or "")
    side = str(payload.get("side") or "").upper()
    order_value = float(payload.get("order_value") or payload.get("value") or 0.0)
    price = float(payload.get("price") or 0.0)
    shares = int(float(payload.get("shares") or (order_value / price if price > 0 else 0)))
    effective_trade_date = str(payload.get("trade_date") or trade_date)
    child_order_id = payload.get("child_order_id")
    client_order_id = str(payload.get("client_order_id") or child_order_id or "co_" + _hash(broker_batch_id, ts_code, side, order_value))
    if not ts_code or not side:
        raise ValueError("ts_code and side are required")
    return BrokerFileRecord(
        client_order_id=client_order_id,
        trade_date=effective_trade_date,
        ts_code=ts_code,
        side=side,
        shares=max(shares, 0),
        price=price,
        price_type=str(payload.get("price_type") or "MARKET"),
        order_value=order_value,
        parent_order_id=payload.get("parent_order_id"),
        child_order_id=child_order_id,
        bucket=payload.get("bucket"),
        broker_batch_id=str(payload.get("broker_batch_id") or broker_batch_id),
        production_run_id=str(payload.get("production_run_id") or production_run_id),
        metadata={"source_reason": payload.get("reason", "")},
    )


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20]

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



class LocalBrokerFileGatewayStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.batches_path = self.root_dir / "broker_file_batches.jsonl"
        self.state_path = self.root_dir / "broker_file_batch_state.json"
        self.events_path = self.root_dir / "broker_file_events.jsonl"
        self.issues_path = self.root_dir / "broker_file_roundtrip_issues.jsonl"

    def save_batch(self, batch: BrokerFileBatch) -> BrokerFileBatch:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        state.setdefault("batches", {})[batch.file_batch_id] = batch.to_dict()
        state.setdefault("idempotency", {})[_idempotency_key(batch)] = batch.file_batch_id
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        self._rewrite_batches()
        write_json_artifact(self.root_dir / "broker_file_batch.json", batch.to_dict(), artifact_type="broker_file_batch", producer="broker_file_gateway")
        self.append_event("save_batch", batch.file_batch_id, batch.status, {"order_count": batch.order_count})
        return batch

    def update_batch(self, batch: BrokerFileBatch, status: str | None = None, **changes: Any) -> BrokerFileBatch:
        payload = batch.to_dict()
        payload.update(changes)
        if status is not None:
            payload["status"] = status
        updated = BrokerFileBatch(**payload)
        return self.save_batch(updated)

    def find_existing(self, production_run_id: str, approval_id: str, profile_id: str) -> BrokerFileBatch | None:
        state = self._load_state()
        batch_id = state.get("idempotency", {}).get(f"{production_run_id}|{approval_id}|{profile_id}")
        return self.load_batch(batch_id) if batch_id else None

    def load_batch(self, file_batch_id: str | None = None) -> BrokerFileBatch | None:
        state = self._load_state().get("batches", {})
        if file_batch_id is None:
            if not state:
                return None
            payload = list(state.values())[-1]
        else:
            payload = state.get(file_batch_id)
        return BrokerFileBatch(**payload) if payload else None

    def list_batches(self) -> list[BrokerFileBatch]:
        return [BrokerFileBatch(**payload) for payload in self._load_state().get("batches", {}).values()]

    def append_event(self, event_type: str, file_batch_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "event_type": event_type,
            "file_batch_id": file_batch_id,
            "status": status,
            "created_at": utc_now(),
            "metadata": metadata or {},
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def _rewrite_batches(self) -> None:
        records = list(self._load_state().get("batches", {}).values())
        with self.batches_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"batches": {}, "idempotency": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"batches": {}, "idempotency": {}}


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _idempotency_key(batch: BrokerFileBatch) -> str:
    return f"{batch.production_run_id}|{batch.approval_id}|{batch.profile_id}"

import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def export_file_batch(
    *,
    store_dir: str | Path,
    outbox_dir: str | Path,
    profile: BrokerFileProfile,
    orders: Iterable[dict[str, Any]] | None = None,
    child_orders: Iterable[dict[str, Any]] | None = None,
    broker_requests: Iterable[Any] | None = None,
    production_run_id: str = "",
    approval_id: str = "",
    broker_batch_id: str = "",
    trade_date: str = "",
    account_id: str = "paper_ashare",
    source_order_paths: dict[str, str] | None = None,
    refresh: bool = False,
    zip_package: bool = False,
    handoff_dir: str | Path | None = None,
) -> dict[str, Any]:
    store = LocalBrokerFileGatewayStore(store_dir)
    broker_batch_id = broker_batch_id or approval_id
    existing = store.find_existing(production_run_id, approval_id, profile.profile_id)
    if existing is not None and not refresh:
        return {"status": "exported", "idempotent": True, "file_batch_id": existing.file_batch_id, "batch": existing.to_dict(), "paths": existing.outbox_paths}
    records = _records_from_inputs(profile, orders, child_orders, broker_requests, broker_batch_id, production_run_id, trade_date)
    file_batch_id = _file_batch_id(production_run_id, approval_id, profile.profile_id, trade_date)
    outbox = Path(outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    csv_path = outbox / "broker_orders.csv"
    jsonl_path = outbox / "broker_orders.jsonl"
    manifest_path = outbox / "broker_file_manifest.json"
    legacy_manifest_path = outbox / "broker_order_manifest.json"
    checksum_path = outbox / "broker_file_checksum_manifest.json"
    batch_path = outbox / "broker_file_batch.json"
    readme_path = outbox / "broker_file_operator_readme.md"
    rows = [file_record_to_row(record, profile) for record in records]
    with csv_path.open("w", encoding=profile.encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[profile.field_mapping[field] for field in profile.field_mapping])
        writer.writeheader()
        writer.writerows(rows)
    write_jsonl_artifact(jsonl_path, [record.to_dict() for record in records], artifact_type="broker_orders", producer="broker_file_gateway")
    manifest = {
        "file_batch_id": file_batch_id,
        "production_run_id": production_run_id,
        "approval_id": approval_id,
        "broker_batch_id": broker_batch_id,
        "trade_date": trade_date,
        "account_id": account_id,
        "profile": profile.to_dict(),
        "order_count": len(records),
        "total_order_value": float(sum(record.order_value for record in records)),
        "outbox_files": {"csv": str(csv_path), "jsonl": str(jsonl_path)},
        "notice": profile.notice,
        "no_real_submit": True,
        "created_at": utc_now(),
    }
    write_json_artifact(manifest_path, manifest, artifact_type="broker_file_manifest", producer="broker_file_gateway")
    write_json_artifact(legacy_manifest_path, manifest, artifact_type="broker_file_manifest", producer="broker_file_gateway")
    checksums = [_checksum_record(path) for path in [csv_path, jsonl_path, manifest_path, legacy_manifest_path]]
    checksum_manifest = {"file_batch_id": file_batch_id, "created_at": utc_now(), "files": checksums, "sha256": _combined_sha(checksums)}
    write_json_artifact(checksum_path, checksum_manifest, artifact_type="broker_file_checksum_manifest", producer="broker_file_gateway")
    readme_path.write_text(_readme(profile, file_batch_id, checksum_manifest), encoding="utf-8")
    outbox_paths = {
        "broker_orders_csv_path": str(csv_path),
        "broker_orders_jsonl_path": str(jsonl_path),
        "broker_file_manifest_path": str(manifest_path),
        "broker_order_manifest_path": str(legacy_manifest_path),
        "broker_file_checksum_manifest_path": str(checksum_path),
        "broker_file_operator_readme_path": str(readme_path),
    }
    if zip_package:
        zip_path = outbox / "broker_file_outbox_package.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in [csv_path, jsonl_path, manifest_path, checksum_path, readme_path]:
                archive.write(path, arcname=path.name)
        outbox_paths["broker_file_outbox_package_path"] = str(zip_path)
    if handoff_dir:
        handoff = Path(handoff_dir)
        handoff.mkdir(parents=True, exist_ok=True)
        for path in [csv_path, jsonl_path, manifest_path, checksum_path, readme_path]:
            (handoff / path.name).write_bytes(path.read_bytes())
        outbox_paths["broker_file_handoff_dir"] = str(handoff)
    batch = BrokerFileBatch(
        file_batch_id=file_batch_id,
        production_run_id=production_run_id,
        approval_id=approval_id,
        broker_batch_id=broker_batch_id,
        trade_date=trade_date,
        account_id=account_id,
        profile_id=profile.profile_id,
        status=BrokerFileBatchStatus.exported,
        created_at=utc_now(),
        exported_at=utc_now(),
        order_count=len(records),
        total_order_value=float(sum(record.order_value for record in records)),
        source_order_paths=source_order_paths or {},
        outbox_paths=outbox_paths,
        manifest_path=str(manifest_path),
        checksum=checksum_manifest["sha256"],
        metadata={"profile_name": profile.profile_name, "schema_name": profile.schema_name, "no_real_submit": True},
    )
    write_json_artifact(batch_path, batch.to_dict(), artifact_type="broker_file_batch", producer="broker_file_gateway")
    saved = store.save_batch(batch)
    return {"status": "exported", "idempotent": False, "file_batch_id": file_batch_id, "batch": saved.to_dict(), "manifest": manifest, "checksum_manifest": checksum_manifest, "paths": outbox_paths}


def _records_from_inputs(profile: BrokerFileProfile, orders, child_orders, broker_requests, broker_batch_id: str, production_run_id: str, trade_date: str) -> list[BrokerFileRecord]:
    if broker_requests is not None:
        records, issues = map_broker_requests_to_file_records(broker_requests, profile)
    elif child_orders is not None:
        records, issues = map_child_orders_to_file_records(child_orders, profile, broker_batch_id=broker_batch_id, production_run_id=production_run_id, trade_date=trade_date)
    else:
        records, issues = map_internal_orders_to_file_records(orders or [], profile, broker_batch_id=broker_batch_id, production_run_id=production_run_id, trade_date=trade_date)
    if any(issue.severity == "error" for issue in issues):
        raise ValueError("; ".join(issue.message for issue in issues if issue.severity == "error"))
    return records


def _file_batch_id(production_run_id: str, approval_id: str, profile_id: str, trade_date: str) -> str:
    digest = hashlib.sha256(f"{production_run_id}|{approval_id}|{profile_id}|{trade_date}".encode("utf-8")).hexdigest()[:16]
    return f"bfg_{trade_date}_{digest}"


def _checksum_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size, "record_count": _record_count(path), "created_at": utc_now()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _combined_sha(records: list[dict[str, Any]]) -> str:
    stable = json.dumps(records, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _record_count(path: Path) -> int:
    if path.suffix == ".jsonl":
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if path.suffix == ".csv":
        return max(sum(1 for _ in path.open("r", encoding="utf-8")) - 1, 0)
    return 1


def _readme(profile: BrokerFileProfile, file_batch_id: str, checksum_manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Broker File Dry-Run Handoff",
            "",
            f"- file_batch_id: `{file_batch_id}`",
            f"- profile: `{profile.profile_name}`",
            f"- schema: `{profile.schema_name}`",
            "- no_real_submit: `true`",
            "",
            profile.notice,
            "",
            "This package is for local dry-run/manual handoff rehearsal only. It must not be treated as live broker connectivity.",
            "",
            f"checksum_manifest_sha256: `{checksum_manifest.get('sha256')}`",
            "",
        ]
    )

import csv
import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_jsonl_artifact



def synthesize_inbox_files(
    *,
    outbox_dir: str | Path,
    inbox_dir: str | Path,
    profile: BrokerFileProfile,
    file_batch_id: str = "",
    reject_every: int = 0,
) -> dict[str, str]:
    outbox = Path(outbox_dir)
    inbox = Path(inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    orders = _read_orders(outbox / "broker_orders.jsonl")
    ack: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for index, order in enumerate(orders):
        rejected = reject_every > 0 and (index + 1) % reject_every == 0
        ack.append({"client_order_id": order["client_order_id"], "broker_batch_id": order.get("broker_batch_id", ""), "ack_status": "ACK", "trade_date": order.get("trade_date", ""), "ts_code": order.get("ts_code", "")})
        status = "REJECTED" if rejected else "FILLED"
        status_rows.append({"client_order_id": order["client_order_id"], "status": status, "reason": "synthetic_reject" if rejected else "", "trade_date": order.get("trade_date", ""), "ts_code": order.get("ts_code", "")})
        if rejected:
            rejects.append({**order, "status": "REJECTED", "reason": "synthetic_reject"})
        else:
            fills.append({**order, "broker_fill_id": f"bff_{order['client_order_id']}", "status": "FILLED", "value": order.get("order_value", 0.0), "cost": 0.0})
    paths = {
        "broker_ack_path": str(inbox / "broker_ack.jsonl"),
        "broker_status_path": str(inbox / "broker_status.jsonl"),
        "broker_fills_path": str(inbox / "broker_fills.jsonl"),
        "broker_rejects_path": str(inbox / "broker_rejects.jsonl"),
    }
    write_jsonl_artifact(paths["broker_ack_path"], ack, artifact_type="normalized_broker_file_ack", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_status_path"], status_rows, artifact_type="normalized_broker_file_status", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_fills_path"], fills, artifact_type="normalized_broker_file_fills", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_rejects_path"], rejects, artifact_type="normalized_broker_file_rejects", producer="broker_file_gateway")
    return paths


def import_inbox_files(
    *,
    store_dir: str | Path,
    inbox_dir: str | Path,
    output_dir: str | Path,
    profile: BrokerFileProfile,
    file_batch_id: str | None = None,
) -> dict[str, Any]:
    inbox = Path(inbox_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ack = _read_any(inbox, "broker_ack", profile)
    status = _read_any(inbox, "broker_status", profile)
    fills = _read_any(inbox, "broker_fills", profile)
    rejects = _read_any(inbox, "broker_rejects", profile)
    paths = {
        "normalized_broker_file_ack_path": str(output / "normalized_broker_file_ack.jsonl"),
        "normalized_broker_file_status_path": str(output / "normalized_broker_file_status.jsonl"),
        "normalized_broker_file_fills_path": str(output / "normalized_broker_file_fills.jsonl"),
        "normalized_broker_file_rejects_path": str(output / "normalized_broker_file_rejects.jsonl"),
    }
    write_jsonl_artifact(paths["normalized_broker_file_ack_path"], ack, artifact_type="normalized_broker_file_ack", producer="broker_file_gateway")
    write_jsonl_artifact(paths["normalized_broker_file_status_path"], status, artifact_type="normalized_broker_file_status", producer="broker_file_gateway")
    write_jsonl_artifact(paths["normalized_broker_file_fills_path"], fills, artifact_type="normalized_broker_file_fills", producer="broker_file_gateway")
    write_jsonl_artifact(paths["normalized_broker_file_rejects_path"], rejects, artifact_type="normalized_broker_file_rejects", producer="broker_file_gateway")
    store = LocalBrokerFileGatewayStore(store_dir)
    batch = store.load_batch(file_batch_id)
    if batch:
        status_value = BrokerFileBatchStatus.acknowledged if ack else batch.status
        if fills:
            status_value = BrokerFileBatchStatus.filled
        store.update_batch(batch, status=status_value, imported_at=utc_now(), inbox_paths=paths)
    return {"status": "imported" if (ack or status or fills or rejects) else "waiting_inbox", "file_batch_id": file_batch_id or (batch.file_batch_id if batch else ""), "ack_count": len(ack), "status_count": len(status), "fill_count": len(fills), "reject_count": len(rejects), "paths": paths}


def _read_orders(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_any(root: Path, stem: str, profile: BrokerFileProfile) -> list[dict[str, Any]]:
    jsonl = root / f"{stem}.jsonl"
    csv_path = root / f"{stem}.csv"
    if jsonl.exists():
        return [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if csv_path.exists():
        with csv_path.open("r", encoding=profile.encoding, newline="") as handle:
            return [row_to_internal(dict(row), profile) for row in csv.DictReader(handle)]
    return []

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def run_file_roundtrip_check(
    *,
    store_dir: str | Path,
    outbox_dir: str | Path,
    normalized_dir: str | Path,
    output_dir: str | Path,
    file_batch_id: str | None = None,
    broker_batch_id: str = "",
) -> dict[str, Any]:
    outbox = Path(outbox_dir)
    norm = Path(normalized_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    orders = _file_gateway_roundtrip_read_jsonl(outbox / "broker_orders.jsonl")
    ack = _file_gateway_roundtrip_read_jsonl(norm / "normalized_broker_file_ack.jsonl")
    status_rows = _file_gateway_roundtrip_read_jsonl(norm / "normalized_broker_file_status.jsonl")
    fills = _file_gateway_roundtrip_read_jsonl(norm / "normalized_broker_file_fills.jsonl")
    rejects = _file_gateway_roundtrip_read_jsonl(norm / "normalized_broker_file_rejects.jsonl")
    order_ids = {str(row.get("client_order_id") or ""): row for row in orders}
    ack_ids = [str(row.get("client_order_id") or "") for row in ack]
    fill_ids = [str(row.get("broker_fill_id") or row.get("client_order_id") or "") for row in fills]
    issues: list[BrokerFileRoundTripIssue] = []
    for client_order_id in order_ids:
        if client_order_id not in ack_ids:
            issues.append(BrokerFileRoundTripIssue("error", "missing_ack", "order is missing ack", {"client_order_id": client_order_id}))
    for fill in fills:
        client_order_id = str(fill.get("client_order_id") or "")
        if client_order_id not in order_ids:
            issues.append(BrokerFileRoundTripIssue("error", "orphan_fill", "fill does not match any order", {"client_order_id": client_order_id}))
            continue
        order_value = float(order_ids[client_order_id].get("order_value") or 0.0)
        fill_value = float(fill.get("value") or fill.get("order_value") or 0.0)
        if fill_value - order_value > 1e-6:
            issues.append(BrokerFileRoundTripIssue("error", "fill_value_exceeds_order", "fill notional exceeds order notional", {"client_order_id": client_order_id, "fill_value": fill_value, "order_value": order_value}))
    duplicates = len(fill_ids) - len(set(fill_ids))
    for _ in range(max(duplicates, 0)):
        issues.append(BrokerFileRoundTripIssue("error", "duplicate_fill", "duplicate fill id detected"))
    unknown_status = [row for row in status_rows if str(row.get("status") or "").upper() not in {"ACK", "ACCEPTED", "FILLED", "PARTIAL", "PARTIAL_FILLED", "REJECTED", "CANCELLED"}]
    for row in unknown_status:
        issues.append(BrokerFileRoundTripIssue("error", "unknown_status", "unknown broker status", {"status": row.get("status")}))
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    report = BrokerFileRoundTripReport(
        file_batch_id=file_batch_id or "",
        broker_batch_id=broker_batch_id,
        status="passed" if errors == 0 else "failed",
        order_count=len(orders),
        ack_count=len(ack),
        status_count=len(status_rows),
        fill_count=len(fills),
        reject_count=len(rejects),
        missing_ack_count=sum(1 for issue in issues if issue.code == "missing_ack"),
        orphan_fill_count=sum(1 for issue in issues if issue.code == "orphan_fill"),
        duplicate_fill_count=sum(1 for issue in issues if issue.code == "duplicate_fill"),
        unknown_status_count=len(unknown_status),
        error_count=errors,
        warning_count=warnings,
        issues=issues,
        summary={"no_real_submit": True, "roundtrip_checked": True},
    )
    paths = {
        "broker_file_roundtrip_report_path": str(output / "broker_file_roundtrip_report.json"),
        "broker_file_roundtrip_report_md_path": str(output / "broker_file_roundtrip_report.md"),
        "broker_file_roundtrip_issues_path": str(output / "broker_file_roundtrip_issues.jsonl"),
    }
    write_json_artifact(paths["broker_file_roundtrip_report_path"], report.to_dict(), artifact_type="broker_file_roundtrip_report", producer="broker_file_gateway")
    write_jsonl_artifact(paths["broker_file_roundtrip_issues_path"], [issue.to_dict() for issue in issues], artifact_type="broker_file_roundtrip_issues", producer="broker_file_gateway")
    Path(paths["broker_file_roundtrip_report_md_path"]).write_text(_file_gateway_roundtrip_markdown(report), encoding="utf-8")
    store = LocalBrokerFileGatewayStore(store_dir)
    batch = store.load_batch(file_batch_id)
    if batch:
        store.update_batch(batch, status=BrokerFileBatchStatus.reconciled if errors == 0 else BrokerFileBatchStatus.failed, metadata={**batch.metadata, "roundtrip": report.to_dict()})
    return {"status": report.status, "file_batch_id": file_batch_id or "", "roundtrip": report.to_dict(), "paths": paths}


def _file_gateway_roundtrip_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _file_gateway_roundtrip_markdown(report: BrokerFileRoundTripReport) -> str:
    lines = [
        "# Broker File Round-Trip Report",
        "",
        f"- file_batch_id: `{report.file_batch_id}`",
        f"- status: `{report.status}`",
        f"- order_count: `{report.order_count}`",
        f"- ack_count: `{report.ack_count}`",
        f"- fill_count: `{report.fill_count}`",
        f"- error_count: `{report.error_count}`",
        "",
        "| severity | code | message |",
        "| --- | --- | --- |",
    ]
    for issue in report.issues:
        lines.append(f"| {issue.severity} | {issue.code} | {issue.message} |")
    return "\n".join(lines) + "\n"

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_gateway_report(
    *,
    store_dir: str | Path,
    output_dir: str | Path | None = None,
    profile: BrokerFileProfile | None = None,
    file_batch_id: str | None = None,
    manifest: dict[str, Any] | None = None,
    roundtrip: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = LocalBrokerFileGatewayStore(store_dir)
    batch = store.load_batch(file_batch_id)
    root = Path(output_dir) if output_dir is not None else Path(store_dir)
    root.mkdir(parents=True, exist_ok=True)
    profile_payload = profile.to_dict() if profile else {}
    manifest_payload = manifest or _read_json(Path(batch.manifest_path) if batch and batch.manifest_path else root / "broker_file_manifest.json")
    roundtrip_payload = roundtrip or _read_json(root / "broker_file_roundtrip_report.json")
    summary = {
        "broker_file_batch_id": batch.file_batch_id if batch else "",
        "broker_file_gateway_status": batch.status if batch else "missing",
        "broker_file_order_count": batch.order_count if batch else 0,
        "broker_file_total_order_value": batch.total_order_value if batch else 0.0,
        "broker_file_roundtrip_error_count": int(roundtrip_payload.get("error_count", 0) or 0),
        "broker_file_missing_ack_count": int(roundtrip_payload.get("missing_ack_count", 0) or 0),
        "broker_file_orphan_fill_count": int(roundtrip_payload.get("orphan_fill_count", 0) or 0),
        "file_outbox_real_submit_detected": False,
        "no_real_submit": True,
    }
    paths = {
        "broker_file_gateway_report_path": str(root / "broker_file_gateway_report.json"),
        "broker_file_gateway_report_md_path": str(root / "broker_file_gateway_report.md"),
        "broker_file_events_path": str(root / "broker_file_events.jsonl"),
    }
    report = BrokerFileGatewayReport(
        file_batch_id=batch.file_batch_id if batch else "",
        status=batch.status if batch else "missing",
        profile=profile_payload,
        batch=batch.to_dict() if batch else {},
        manifest=manifest_payload,
        roundtrip=roundtrip_payload,
        paths=paths,
        summary=summary,
    )
    write_json_artifact(paths["broker_file_gateway_report_path"], report.to_dict(), artifact_type="broker_file_gateway_report", producer="broker_file_gateway")
    events = _file_gateway_report_read_jsonl(Path(store_dir) / "broker_file_events.jsonl")
    write_jsonl_artifact(paths["broker_file_events_path"], events, artifact_type="broker_file_events", producer="broker_file_gateway")
    Path(paths["broker_file_gateway_report_md_path"]).write_text(_file_gateway_report_markdown(report.to_dict()), encoding="utf-8")
    return {**report.to_dict(), "paths": paths}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _file_gateway_report_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _file_gateway_report_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    return "\n".join(
        [
            "# Broker File Gateway Report",
            "",
            f"- file_batch_id: `{payload.get('file_batch_id', '')}`",
            f"- status: `{payload.get('status', '')}`",
            f"- order_count: `{summary.get('broker_file_order_count', 0)}`",
            f"- roundtrip_error_count: `{summary.get('broker_file_roundtrip_error_count', 0)}`",
            f"- no_real_submit: `{summary.get('no_real_submit', True)}`",
            "",
        ]
    )

import argparse
import json
from pathlib import Path
from typing import Any



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Broker file dry-run gateway.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-profile", "validate-profile", "export-outbox", "import-inbox", "synthesize-inbox", "roundtrip-check", "show-batch", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gateway-store-dir", required=True)
    parser.add_argument("--profile-name", default="generic_broker_csv")
    parser.add_argument("--profile-config")
    parser.add_argument("--output-dir")
    parser.add_argument("--outbox-dir")
    parser.add_argument("--inbox-dir")
    parser.add_argument("--handoff-dir")
    parser.add_argument("--orders-path")
    parser.add_argument("--child-orders-path")
    parser.add_argument("--broker-requests-path")
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--broker-batch-id")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--approval-id", default="")
    parser.add_argument("--production-run-id", default="")
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--account-id", default="paper_ashare")
    parser.add_argument("--broker-name", default="local_file_dry_run")
    parser.add_argument("--file-batch-id")
    parser.add_argument("--schema-name")
    parser.add_argument("--zip-package", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    profile = load_profile(args.profile_name, args.profile_config)
    output_dir = Path(args.output_dir or args.gateway_store_dir)
    outbox_dir = Path(args.outbox_dir or output_dir / "outbox")
    inbox_dir = Path(args.inbox_dir or output_dir / "inbox")
    if args.command in {"init-profile", "validate-profile"}:
        payload = {"status": "valid", "profile": profile.to_dict(), "profile_notice": profile.notice}
    elif args.command in {"export-outbox", "smoke"}:
        orders = _load_records(args.child_orders_path or args.orders_path or args.broker_requests_path) or _sample_orders(args.trade_date)
        payload = export_file_batch(
            store_dir=args.gateway_store_dir,
            outbox_dir=outbox_dir,
            profile=profile,
            child_orders=orders,
            production_run_id=args.production_run_id or f"prod_{args.trade_date}_file_outbox",
            approval_id=args.approval_id or f"approval_{args.trade_date}_file",
            broker_batch_id=args.broker_batch_id or args.approval_id or f"approval_{args.trade_date}_file",
            trade_date=args.trade_date,
            account_id=args.account_id,
            source_order_paths={"orders_path": str(args.child_orders_path or args.orders_path or "")},
            refresh=args.refresh,
            zip_package=args.zip_package,
            handoff_dir=args.handoff_dir,
        )
        if args.command == "smoke":
            synth = synthesize_inbox_files(outbox_dir=outbox_dir, inbox_dir=inbox_dir, profile=profile, file_batch_id=payload["file_batch_id"])
            imported = import_inbox_files(store_dir=args.gateway_store_dir, inbox_dir=inbox_dir, output_dir=output_dir, profile=profile, file_batch_id=payload["file_batch_id"])
            roundtrip = run_file_roundtrip_check(store_dir=args.gateway_store_dir, outbox_dir=outbox_dir, normalized_dir=output_dir, output_dir=output_dir, file_batch_id=payload["file_batch_id"], broker_batch_id=args.broker_batch_id or args.approval_id or "")
            report = write_gateway_report(store_dir=args.gateway_store_dir, output_dir=output_dir, profile=profile, roundtrip=roundtrip["roundtrip"])
            payload = {"status": "success", "export": payload, "synthesized": synth, "imported": imported, "roundtrip": roundtrip, "report": report}
    elif args.command == "synthesize-inbox":
        payload = {"status": "success", "paths": synthesize_inbox_files(outbox_dir=outbox_dir, inbox_dir=inbox_dir, profile=profile, file_batch_id=args.file_batch_id or "")}
    elif args.command == "import-inbox":
        payload = import_inbox_files(store_dir=args.gateway_store_dir, inbox_dir=inbox_dir, output_dir=output_dir, profile=profile, file_batch_id=args.file_batch_id)
    elif args.command == "roundtrip-check":
        payload = run_file_roundtrip_check(store_dir=args.gateway_store_dir, outbox_dir=outbox_dir, normalized_dir=output_dir, output_dir=output_dir, file_batch_id=args.file_batch_id, broker_batch_id=args.broker_batch_id or "")
    elif args.command == "show-batch":
        store_dir = _resolve_gateway_store_dir(args.gateway_store_dir, args.file_batch_id)
        batch = LocalBrokerFileGatewayStore(store_dir).load_batch(args.file_batch_id)
        payload = {"status": "found" if batch else "missing", "batch": batch.to_dict() if batch else {}}
    elif args.command == "report":
        store_dir = _resolve_gateway_store_dir(args.gateway_store_dir, args.file_batch_id)
        payload = write_gateway_report(store_dir=store_dir, output_dir=output_dir, profile=profile, file_batch_id=args.file_batch_id)
    else:  # pragma: no cover
        payload = {"status": "failed", "error": f"unsupported command: {args.command}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if payload.get("status") in {"failed"} else 0


def _load_records(path: str | None) -> list[dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    target = Path(path)
    if target.suffix == ".json":
        payload = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(item) for item in payload]
        if isinstance(payload, dict):
            for key in ["child_orders", "orders", "requests"]:
                if isinstance(payload.get(key), list):
                    return [dict(item) for item in payload[key]]
            schedule = payload.get("schedule") if isinstance(payload.get("schedule"), dict) else {}
            if isinstance(schedule.get("child_orders"), list):
                return [dict(item) for item in schedule["child_orders"]]
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sample_orders(trade_date: str) -> list[dict[str, Any]]:
    return [
        {"child_order_id": f"child_{trade_date}_buy", "parent_order_id": f"parent_{trade_date}_buy", "trade_date": trade_date, "ts_code": "000001.SZ", "side": "BUY", "bucket": "open", "order_value": 10000.0, "price": 10.0},
        {"child_order_id": f"child_{trade_date}_sell", "parent_order_id": f"parent_{trade_date}_sell", "trade_date": trade_date, "ts_code": "600000.SH", "side": "SELL", "bucket": "close", "order_value": 5000.0, "price": 12.5},
    ]


def _resolve_gateway_store_dir(root_dir: str | Path, file_batch_id: str | None = None) -> Path:
    root = Path(root_dir)
    store = LocalBrokerFileGatewayStore(root)
    if store.load_batch(file_batch_id):
        return root
    candidates = sorted(root.rglob("broker_file_batch_state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for state_path in candidates:
        candidate = state_path.parent
        if LocalBrokerFileGatewayStore(candidate).load_batch(file_batch_id):
            return candidate
    return root


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "BrokerFileBatch",
    "BrokerFileBatchStatus",
    "BrokerFileGatewayMode",
    "BrokerFileGatewayReport",
    "BrokerFileProfile",
    "BrokerFileRoundTripReport",
    "BrokerFileSchemaName",
    "get_profile",
    "load_profile",
    "export_file_batch",
    "import_inbox_files",
    "synthesize_inbox_files",
    "run_file_roundtrip_check",
]
