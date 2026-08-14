from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auto_alpha.data.lake.store.admission import (
    ProviderAcquisitionContract,
    CoverageObligation,
    CoveragePlan,
    CoveragePopulation,
    DatasetAdmissionContract,
)
from auto_alpha.platform.artifacts.storage import canonical_hash, sha256_file
from auto_alpha.platform.governance.network.signing import EphemeralReceiptSigner


_SIGNER: EphemeralReceiptSigner | None = None


def controlled_acquisition_contract(dataset: str) -> dict[str, str]:
    public_key_hash = canonical_hash(_capture_signer().public_key_pem.decode("ascii"))
    return {
        "provider": "controlled",
        "provider_adapter": "controlled_adapter_v1",
        "endpoint": dataset,
        "provider_api_version": "controlled_v1",
        "adapter_schema_version": f"{dataset}_v1",
        "permission_context_id": "controlled-permission",
        "capture_public_key_sha256": public_key_hash,
        "pagination_mode": "deterministic_split",
        "row_cap": 5_000,
        "allowed_retry_failure_kinds": ["network_error", "rate_limited", "timeout"],
    }


def controlled_dataset_row(
    dataset: str,
    *,
    coverage_granularity: str = "security_day",
    approved_fields: tuple[str, ...] = ("ts_code", "trade_date", "type", "type_name"),
    empty_policy: str = "observed_empty_allowed",
    coverage_watermark: str = "as_of_market_date",
    record_subject_field: str = "ts_code",
    record_date_field: str = "trade_date",
    coverage_subjects: tuple[str, ...] = (),
    requires_pre_span_state: bool = False,
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "role": "base-required",
        "coverage_granularity": coverage_granularity,
        "approved_fields": sorted(approved_fields),
        "consumer_roles": ["controlled_test"],
        "evidence_grade": "governed_receipts",
        "empty_policy": empty_policy,
        "coverage_watermark": coverage_watermark,
        "requires_pre_span_state": requires_pre_span_state,
        "record_subject_field": record_subject_field,
        "record_date_field": record_date_field,
        "coverage_subjects": list(coverage_subjects),
        "read_only_required": True,
        "max_retries": 2,
        "max_split_leaves": 16,
        "acquisition_contracts": [controlled_acquisition_contract(dataset)],
        "not_applicable_authorities": {},
    }


def inactive_dataset_row(
    dataset: str,
    *,
    coverage_granularity: str = "security_span",
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "role": "inactive",
        "coverage_granularity": coverage_granularity,
        "approved_fields": [],
        "consumer_roles": [],
        "evidence_grade": "inactive",
        "empty_policy": "nonempty_required",
        "coverage_watermark": "inactive",
        "requires_pre_span_state": False,
        "record_subject_field": "",
        "record_date_field": "",
        "coverage_subjects": [],
        "read_only_required": False,
        "max_retries": 0,
        "max_split_leaves": 0,
        "acquisition_contracts": [],
        "not_applicable_authorities": {},
    }


def build_attempt_pair(
    root: Path,
    obligation: CoverageObligation,
    *,
    contract: DatasetAdmissionContract | None = None,
    population: CoveragePopulation | None = None,
    attempt_ordinal: int,
    sequence_start: int,
    previous_event_hash: str,
    empty: bool,
    disposition: str | None = None,
    retry_of_attempt_id: str | None = None,
    retry_ordinal: int = 0,
    leaf_ordinal: int = 1,
    leaf_count: int = 1,
    leaf_start: str | None = None,
    leaf_end: str | None = None,
    split_leaves: list[dict[str, Any]] | None = None,
    applicability_evidence: dict[str, Any] | None = None,
    failure_kind: str = "rate_limited",
    record_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = contract or DatasetAdmissionContract.from_mapping(
        controlled_dataset_row(obligation.dataset)
    )
    population = population or CoveragePopulation(
        securities=(
            # The default is sufficient for the one-security coverage tests.
            # Full verdict fixtures pass the compiled plan population explicitly.
        ),
        trading_dates=(obligation.date_start,),
    )
    attempt_id = f"attempt-{attempt_ordinal:04d}"
    fields = list(contract.approved_fields)
    leaf_start = leaf_start or obligation.date_start
    leaf_end = leaf_end or obligation.date_end
    split_leaves = split_leaves or [
        {
            "leaf_ordinal": 1,
            "leaf_start": obligation.date_start,
            "leaf_end": obligation.date_end,
        }
    ]
    items = (
        []
        if empty
        else _controlled_items(
            obligation,
            contract,
            population,
            leaf_start=leaf_start,
            leaf_end=leaf_end,
            record_overrides=record_overrides,
        )
    )
    raw_payload = {"code": 0, "msg": None, "data": {"fields": fields, "items": items}}
    raw_path = root / "raw_envelopes" / f"{attempt_id}.json"
    write_json(raw_path, raw_payload)
    acquisition = ProviderAcquisitionContract.from_mapping(
        controlled_acquisition_contract(obligation.dataset)
    )
    request = {
        "canonical_dataset": obligation.dataset,
        **acquisition.to_dict(),
        "normalized_params": {
            "subject": obligation.subject,
            "date_start": leaf_start,
            "date_end": leaf_end,
        },
        "fields": fields,
        "request_fingerprint": "",
        "evidence_use_identity": evidence_use_identity(contract, acquisition),
        "read_only": True,
        "max_retries": contract.max_retries,
        "retry_ordinal": retry_ordinal,
        "pagination_plan": {
            "obligation_id": obligation.obligation_id,
            "root_start": obligation.date_start,
            "root_end": obligation.date_end,
            "leaf_ordinal": leaf_ordinal,
            "leaf_count": leaf_count,
            "leaf_start": leaf_start,
            "leaf_end": leaf_end,
            "split_leaves": split_leaves,
            "split_plan_root": canonical_hash(split_leaves),
            "row_cap": 5_000,
        },
        "obligation_record_projection": {
            "subject_field": contract.record_subject_field,
            "date_field": contract.record_date_field,
        },
    }
    request["request_fingerprint"] = request_fingerprint(request)
    started = datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc) + timedelta(
        seconds=(attempt_ordinal - 1) * 2
    )
    completed = started + timedelta(seconds=1)
    started_at = started.isoformat().replace("+00:00", "Z")
    completed_at = completed.isoformat().replace("+00:00", "Z")
    terminal_disposition = disposition or (
        "satisfied_empty" if not items else "satisfied_nonempty"
    )
    signer = _capture_signer()
    public_key_text = signer.public_key_pem.decode("ascii")
    capture_identity = {
        "capture_public_key_pem_b64": base64.b64encode(signer.public_key_pem).decode(
            "ascii"
        ),
        "capture_public_key_sha256": canonical_hash(public_key_text),
        "capture_key_isolated": True,
    }
    start = {
        "schema_version": "data_coverage_attempt_started_v1",
        "event_type": "attempt_started",
        "event_id": f"attempt_started:{attempt_id}",
        "attempt_id": attempt_id,
        "sequence": sequence_start,
        "previous_event_hash": previous_event_hash,
        "dataset": obligation.dataset,
        "obligation_ids": [obligation.obligation_id],
        "request": request,
        "retry_of_attempt_id": retry_of_attempt_id,
        "capture_started_at": started_at,
        "occurred_at": started_at,
        **capture_identity,
        "attempt_start_signature": "",
    }
    reseal_start(start)
    receipt = {
        "schema_version": "data_coverage_receipt_v1",
        "event_type": "post_transport_receipt",
        "event_id": f"post_transport_receipt:{attempt_id}",
        "receipt_id": f"receipt-{attempt_ordinal:04d}",
        "attempt_id": attempt_id,
        "attempt_started_event_hash": start["event_hash"],
        "sequence": sequence_start + 1,
        "previous_event_hash": start["event_hash"],
        "dataset": obligation.dataset,
        "obligation_ids": [obligation.obligation_id],
        "request": request,
        "response": {
            "transport_status": (
                "failed" if terminal_disposition == "failed" else "completed"
            ),
            "failure_kind": (
                failure_kind if terminal_disposition == "failed" else None
            ),
            "provider_code": 429 if terminal_disposition == "failed" else 0,
            "item_count": len(items),
            "response_fields": fields,
            "response_payload_hash": canonical_hash(raw_payload),
            "records_hash": canonical_hash(items),
            "raw_envelope_relative_path": raw_path.relative_to(root).as_posix(),
            "raw_envelope_sha256": sha256_file(raw_path),
        },
        "pagination": {
            "row_cap": 5_000,
            "returned_count": len(items),
            "terminal": True,
            "end_marker": True,
            "cap_suspected": False,
            "cursor": None,
            "next_cursor": None,
            "root_start": obligation.date_start,
            "root_end": obligation.date_end,
            "leaf_ordinal": leaf_ordinal,
            "leaf_count": leaf_count,
            "leaf_start": leaf_start,
            "leaf_end": leaf_end,
        },
        "terminal_disposition": terminal_disposition,
        "failure_kind": failure_kind if terminal_disposition == "failed" else None,
        "applicability_evidence": (
            _sealed_applicability_evidence(applicability_evidence)
            if terminal_disposition == "not_applicable"
            else None
        ),
        "retry_of_attempt_id": retry_of_attempt_id,
        "capture_started_at": started_at,
        "capture_completed_at": completed_at,
        "occurred_at": completed_at,
        **capture_identity,
        "capture_signature": "",
    }
    reseal_receipt(receipt)
    return start, receipt


def reseal_pair(
    start: dict[str, Any],
    receipt: dict[str, Any],
    *,
    previous_event_hash: str | None = None,
) -> None:
    if previous_event_hash is not None:
        start["previous_event_hash"] = previous_event_hash
    reseal_start(start)
    receipt["attempt_started_event_hash"] = start["event_hash"]
    receipt["previous_event_hash"] = start["event_hash"]
    reseal_receipt(receipt)


def reseal_start(start: dict[str, Any]) -> None:
    semantic = {
        key: value
        for key, value in start.items()
        if key not in {"event_hash", "attempt_start_signature"}
    }
    start["attempt_start_signature"] = _capture_signer().sign(canonical_bytes(semantic))
    start["event_hash"] = canonical_hash(
        {key: value for key, value in start.items() if key != "event_hash"}
    )


def reseal_receipt(receipt: dict[str, Any]) -> None:
    semantic = {
        key: value
        for key, value in receipt.items()
        if key not in {"event_hash", "capture_signature"}
    }
    receipt["capture_signature"] = _capture_signer().sign(canonical_bytes(semantic))
    receipt["event_hash"] = canonical_hash(
        {key: value for key, value in receipt.items() if key != "event_hash"}
    )


def write_coverage_evidence(
    root: Path,
    *,
    plan: CoveragePlan,
    events: list[dict[str, Any]],
    producer_complete: bool = False,
    producer_coverage_root: str = "0" * 64,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    journal = root / "coverage_attempt_journal.jsonl"
    journal.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
            for event in events
        ),
        encoding="utf-8",
    )
    semantic = {
        "schema_version": "data_coverage_evidence_v1",
        "coverage_plan_content_hash": plan.content_hash,
        "producer_claim": {
            "complete": producer_complete,
            "coverage_root": producer_coverage_root,
        },
        "attempt_journal": {
            "relative_path": "coverage_attempt_journal.jsonl",
            "sha256": sha256_file(journal),
            "event_count": len(events),
        },
    }
    write_json(
        root / "coverage_evidence_manifest.json",
        semantic | {"content_hash": canonical_hash(semantic)},
    )
    return root


def request_fingerprint(request: dict[str, Any]) -> str:
    return canonical_hash(
        {
            key: request[key]
            for key in (
                "canonical_dataset",
                "provider",
                "provider_adapter",
                "endpoint",
                "provider_api_version",
                "adapter_schema_version",
                "permission_context_id",
                "capture_public_key_sha256",
                "pagination_mode",
                "row_cap",
                "allowed_retry_failure_kinds",
                "normalized_params",
                "fields",
                "read_only",
                "max_retries",
                "pagination_plan",
                "obligation_record_projection",
            )
        }
    )


def evidence_use_identity(
    contract: DatasetAdmissionContract,
    acquisition: ProviderAcquisitionContract,
) -> str:
    return canonical_hash(
        {
            "schema_version": "coverage_evidence_use_v1",
            "dataset": contract.dataset,
            "approved_fields": list(contract.approved_fields),
            "record_subject_field": contract.record_subject_field,
            "record_date_field": contract.record_date_field,
            "read_only_required": contract.read_only_required,
            "max_retries": contract.max_retries,
            "max_split_leaves": contract.max_split_leaves,
            "acquisition_contract": acquisition.to_dict(),
        }
    )


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sealed_applicability_evidence(
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    semantic = {
        "reason": str((value or {}).get("reason") or ""),
        "authority_obligation_ids": list(
            (value or {}).get("authority_obligation_ids") or ()
        ),
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def _capture_signer() -> EphemeralReceiptSigner:
    global _SIGNER
    if _SIGNER is None:
        _SIGNER = EphemeralReceiptSigner.generate()
    return _SIGNER


def _controlled_items(
    obligation: CoverageObligation,
    contract: DatasetAdmissionContract,
    population: CoveragePopulation,
    *,
    leaf_start: str,
    leaf_end: str,
    record_overrides: dict[str, Any] | None = None,
) -> list[list[Any]]:
    if obligation.dataset == "securities":
        records: list[dict[str, Any]] = []
        for security in population.securities:
            status = "D" if security.delist_date else "L"
            if obligation.subject != f"list_status:{status}":
                continue
            records.append(
                {
                    "ts_code": security.security_id,
                    "symbol": security.security_id.split(".", 1)[0],
                    "exchange": "SSE" if security.security_id.endswith(".SH") else "SZSE",
                    "board": "MAIN",
                    "list_date": security.list_date,
                    "delist_date": security.delist_date,
                    "list_status": status,
                }
            )
        for record in records:
            record.update(record_overrides or {})
        return [[record.get(field) for field in contract.approved_fields] for record in records]
    if obligation.dataset == "trade_calendar":
        open_dates = set(population.trading_dates)
        current = datetime.strptime(leaf_start, "%Y%m%d")
        end = datetime.strptime(leaf_end, "%Y%m%d")
        records = []
        previous_open = ""
        while current <= end:
            trade_date = current.strftime("%Y%m%d")
            is_open = 1 if trade_date in open_dates else 0
            records.append(
                {
                    "exchange": obligation.subject,
                    "trade_date": trade_date,
                    "is_open": is_open,
                    "prev_trade_date": previous_open,
                }
            )
            if is_open:
                previous_open = trade_date
            current += timedelta(days=1)
        return [[record.get(field) for field in contract.approved_fields] for record in records]
    record = {field: 0 for field in contract.approved_fields}
    record[contract.record_subject_field] = obligation.subject
    record[contract.record_date_field] = leaf_start
    if obligation.dataset == "suspensions":
        record["suspend_type"] = "S"
        record["suspend_timing"] = "before_open"
    record.update(record_overrides or {})
    return [[record[field] for field in contract.approved_fields]]


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path
