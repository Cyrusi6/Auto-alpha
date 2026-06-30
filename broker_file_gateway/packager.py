"""Outbox packaging for broker file dry-run batches."""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .mapping import file_record_to_row, map_broker_requests_to_file_records, map_child_orders_to_file_records, map_internal_orders_to_file_records
from .models import BrokerFileBatch, BrokerFileBatchStatus, BrokerFileProfile, BrokerFileRecord
from .profiles import profile_hash
from .state import LocalBrokerFileGatewayStore, utc_now


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
