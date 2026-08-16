"""Bounded, non-admissible capability probes for external A-share providers.

This seam intentionally stops before governed ingestion.  It archives a finite
request plan and provider-observable responses, but it cannot create coverage
receipts, activate a Data Admission Profile, or authorize research/trading.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, Sequence
from urllib.parse import urlsplit

from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_prepared_generation,
    read_json,
    resolve_manifest,
    sha256_file,
)


SCHEMA_VERSION = "ashare_provider_capability_probe_v1"
POINTER_SCHEMA = "ashare_provider_capability_probe_pointer_v1"
MANIFEST_NAME = "provider_probe_evidence.json"
GENERATION_PREFIX = "provider_probe"
RAW_EVIDENCE_NAME = "raw_evidence.jsonl"
ATTEMPT_JOURNAL_NAME = "attempt_journal.jsonl"
REQUEST_PLAN_NAME = "request_plan.json"
CONTRACT_NAME = "probe_contract.json"

TERMINAL_STATES = frozenset({"positive", "empty", "error"})
HANDOFF_DISPOSITIONS = frozenset(
    {"local_repair", "bounded_backfill", "permission_missing", "provider_cannot_prove"}
)
SENSITIVE_HEADER_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "api-key",
    "apikey",
)
SAFETY_FLAGS = (
    "data_admission_eligible",
    "profile_activation_authorized",
    "bulk_backfill_authorized",
    "alpha_search_authorized",
    "holdout_activation_authorized",
    "paper_trading_authorized",
    "live_trading_authorized",
)


@dataclass(frozen=True)
class ProviderProbeContract:
    """Finite resource and network boundary for one capability probe."""

    probe_id: str
    output_root: str | Path
    allowed_hosts: tuple[str, ...]
    max_requests: int
    timeout_seconds: float
    max_response_bytes: int = 32 * 1024 * 1024
    max_total_response_bytes: int = 256 * 1024 * 1024
    max_wire_exchanges: int = 512
    adapter_identity: Mapping[str, str] = field(default_factory=dict)

    def semantic(self) -> dict[str, Any]:
        return {
            "schema_version": "provider_probe_contract_v1",
            "probe_id": self.probe_id,
            "allowed_hosts": list(self.allowed_hosts),
            "max_requests": self.max_requests,
            "timeout_seconds": self.timeout_seconds,
            "max_response_bytes": self.max_response_bytes,
            "max_total_response_bytes": self.max_total_response_bytes,
            "max_wire_exchanges": self.max_wire_exchanges,
            "adapter_identity": dict(sorted(self.adapter_identity.items())),
            "mode": "bounded_provider_probe",
            "data_admission_eligible": False,
        }


@dataclass(frozen=True)
class ProviderProbeRequest:
    """One public, credential-free request in a sealed probe plan."""

    request_id: str
    provider: str
    method: str
    url: str
    body: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    endpoint: str = ""
    disposition: str = "provider_cannot_prove"
    evidence_semantics: str = "raw_transport_payload"
    expected_terminal_states: tuple[str, ...] = ("positive", "empty")
    required_checks: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def semantic(self) -> dict[str, Any]:
        body = self.body or b""
        return {
            "request_id": self.request_id,
            "provider": self.provider,
            "endpoint": self.endpoint or self.provider,
            "method": self.method.upper(),
            "url": self.url,
            "headers": dict(sorted(self.headers.items())),
            "body_base64": base64.b64encode(body).decode("ascii"),
            "body_sha256": _sha256_bytes(body),
            "disposition": self.disposition,
            "evidence_semantics": self.evidence_semantics,
            "expected_terminal_states": list(self.expected_terminal_states),
            "required_checks": list(self.required_checks),
            "metadata": _json_value(self.metadata),
        }


@dataclass(frozen=True)
class ProviderProbeObservation:
    """Terminal provider-observable result returned by a probe adapter."""

    terminal_state: Literal["positive", "empty", "error"]
    raw_payload: bytes
    row_count: int | None
    status_code: int | None = None
    error_code: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    checks: Mapping[str, bool] = field(default_factory=dict)
    transport_exchange_count: int = 1


ProbeTransport = Callable[
    [ProviderProbeRequest, float],
    ProviderProbeObservation,
]


def run_provider_capability_probe(
    contract: ProviderProbeContract,
    requests: Sequence[ProviderProbeRequest],
    *,
    transport: ProbeTransport,
) -> dict[str, Any]:
    """Execute and publish a finite capability probe.

    All plan validation happens before the output root is created or transport
    is called.  Terminal observations are fsync'd after every request, allowing
    the same request-plan identity to resume after interruption.
    """

    request_rows = _validate_plan(contract, requests)
    contract_semantic = contract.semantic()
    contract_id = canonical_hash(contract_semantic)
    request_plan_hash = canonical_hash(request_rows)
    lexical_output = Path(contract.output_root)
    if _has_lexical_symlink_component(lexical_output):
        raise ValueError("provider_probe_output_root_symlink_forbidden")
    output = lexical_output.resolve()
    run_id = canonical_hash(
        {"contract_id": contract_id, "request_plan_hash": request_plan_hash}
    )
    with _probe_execution_lock(output, run_id=run_id):
        return _run_provider_capability_probe_locked(
            contract=contract,
            requests=requests,
            transport=transport,
            request_rows=request_rows,
            contract_semantic=contract_semantic,
            contract_id=contract_id,
            request_plan_hash=request_plan_hash,
            output=output,
            run_id=run_id,
        )


def _run_provider_capability_probe_locked(
    *,
    contract: ProviderProbeContract,
    requests: Sequence[ProviderProbeRequest],
    transport: ProbeTransport,
    request_rows: list[dict[str, Any]],
    contract_semantic: Mapping[str, Any],
    contract_id: str,
    request_plan_hash: str,
    output: Path,
    run_id: str,
) -> dict[str, Any]:

    cached = _matching_current_generation(
        output,
        contract_id=contract_id,
        request_plan_hash=request_plan_hash,
    )
    if cached is not None:
        return cached | {"cache_hit": True}

    run_root = output / ".runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _write_or_verify_json(run_root / CONTRACT_NAME, contract_semantic)
    _write_or_verify_json(
        run_root / REQUEST_PLAN_NAME,
        {
            "schema_version": "provider_probe_request_plan_v1",
            "request_plan_hash": request_plan_hash,
            "requests": request_rows,
        },
    )
    raw_path = run_root / RAW_EVIDENCE_NAME
    journal_path = run_root / ATTEMPT_JOURNAL_NAME
    recovered = _read_raw_records(raw_path, request_rows=request_rows)
    _assert_activity_budget(contract, _resource_usage(recovered.values()))
    if recovered:
        journal_rows = _journal_rows(journal_path)
        started = {
            str(row["request_id"])
            for row in journal_rows
            if row.get("event") == "attempt_started"
        }
        terminal = {
            str(row["request_id"])
            for row in journal_rows
            if row.get("event") == "attempt_terminal"
        }
        if not set(recovered) <= started:
            raise ValueError("provider_probe_recovery_intent_missing")
        for request_id, record in recovered.items():
            if request_id in terminal:
                continue
            _append_journal(
                journal_path,
                {
                    "event": "attempt_terminal",
                    "event_id": (
                        f"{request_id}:terminal_recovered:"
                        f"{_journal_event_count(journal_path) + 1}"
                    ),
                    "request_id": request_id,
                    "terminal_state": record["terminal_state"],
                    "raw_payload_sha256": record["raw_payload_sha256"],
                    "recorded_at": _utc_now(),
                    "recovered_after_interruption": True,
                },
            )
        restore = getattr(transport, "restore", None)
        if restore is not None:
            for request in requests:
                record = recovered.get(request.request_id)
                if record is not None:
                    restore(request, record)

    for request, request_row in zip(requests, request_rows, strict=True):
        if request.request_id in recovered:
            continue
        _append_journal(
            journal_path,
            {
                "event": "attempt_started",
                "event_id": f"{request.request_id}:attempt:{_journal_event_count(journal_path) + 1}",
                "request_id": request.request_id,
                "request_semantic_hash": canonical_hash(request_row),
                "recorded_at": _utc_now(),
            },
        )
        try:
            observation = transport(request, contract.timeout_seconds)
        except Exception as exc:  # A thrown adapter error is still terminal probe evidence.
            observation = ProviderProbeObservation(
                terminal_state="error",
                raw_payload=json.dumps(
                    {
                        "adapter_exception_type": type(exc).__name__,
                        "message": _scrub_text(str(exc)),
                    },
                    sort_keys=True,
                ).encode(),
                row_count=None,
                error_code=f"adapter_exception:{type(exc).__name__}",
                diagnostics={"exception_captured": True},
                checks={"transport_completed": False},
            )
        record = _observation_record(
            request=request,
            request_semantic_hash=canonical_hash(request_row),
            observation=observation,
        )
        if len(observation.raw_payload) > contract.max_response_bytes:
            raise ValueError(f"probe_response_budget_exceeded:{request.request_id}")
        _append_jsonl_fsync(raw_path, record)
        _append_journal(
            journal_path,
            {
                "event": "attempt_terminal",
                "event_id": f"{request.request_id}:terminal:{_journal_event_count(journal_path) + 1}",
                "request_id": request.request_id,
                "terminal_state": observation.terminal_state,
                "raw_payload_sha256": record["raw_payload_sha256"],
                "recorded_at": _utc_now(),
            },
        )
        recovered[request.request_id] = record
        _assert_activity_budget(contract, _resource_usage(recovered.values()))

    ordered_records = [recovered[row["request_id"]] for row in request_rows]
    _rewrite_jsonl_fsync(raw_path, ordered_records)
    return _publish_probe_generation(
        output=output,
        run_root=run_root,
        contract_semantic=contract_semantic,
        contract_id=contract_id,
        request_rows=request_rows,
        request_plan_hash=request_plan_hash,
        records=ordered_records,
    )


def validate_provider_capability_probe(path: str | Path) -> dict[str, Any]:
    """Recompute a probe generation's identity, file closure and raw evidence."""

    manifest_path = resolve_manifest(path, MANIFEST_NAME)
    payload = read_json(manifest_path)
    safety = payload.get("safety")
    if not isinstance(safety, dict) or set(safety) != set(SAFETY_FLAGS):
        raise ValueError("provider_probe_safety_contract_invalid")
    if any(safety[flag] is not False for flag in SAFETY_FLAGS):
        raise ValueError("provider_probe_non_admissible_safety_violation")
    if payload.get("mode") != "bounded_provider_probe":
        raise ValueError("provider_probe_mode_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("provider_probe_schema_invalid")

    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"content_hash", "generation_id"}
    }
    expected_hash = canonical_hash(semantic)
    generation_id = str(payload.get("generation_id") or "")
    if (
        payload.get("content_hash") != expected_hash
        or generation_id != f"{GENERATION_PREFIX}_{expected_hash[:24]}"
        or manifest_path.parent.name != generation_id
    ):
        raise ValueError("provider_probe_content_or_generation_hash_invalid")

    root = manifest_path.parent
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("provider_probe_file_catalog_invalid")
    expected_paths: set[str] = {MANIFEST_NAME}
    for row in files:
        if not isinstance(row, dict):
            raise ValueError("provider_probe_file_catalog_invalid")
        relative = _safe_relative_path(row.get("path"))
        expected_paths.add(relative.as_posix())
        artifact = root / relative
        if (
            not artifact.is_file()
            or artifact.is_symlink()
            or artifact.stat().st_size != row.get("size_bytes")
            or sha256_file(artifact) != row.get("sha256")
        ):
            raise ValueError(f"provider_probe_evidence_file_hash_invalid:{relative}")
    actual_paths = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file()
    }
    if actual_paths != expected_paths:
        raise ValueError("provider_probe_file_closure_invalid")

    raw = payload.get("raw_evidence")
    if not isinstance(raw, dict):
        raise ValueError("provider_probe_raw_evidence_invalid")
    raw_relative = _safe_relative_path(raw.get("path"))
    raw_path = root / raw_relative
    if sha256_file(raw_path) != raw.get("sha256"):
        raise ValueError("provider_probe_raw_evidence_hash_invalid")
    request_plan = read_json(root / REQUEST_PLAN_NAME)
    request_rows = request_plan.get("requests")
    if not isinstance(request_rows, list):
        raise ValueError("provider_probe_request_plan_invalid")
    if canonical_hash(request_rows) != payload.get("request_plan_hash"):
        raise ValueError("provider_probe_request_plan_hash_invalid")
    records = _read_raw_records(raw_path, request_rows=request_rows)
    if len(records) != raw.get("record_count") or len(records) != payload.get(
        "request_count"
    ):
        raise ValueError("provider_probe_raw_evidence_record_count_invalid")
    terminal_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for record in records.values():
        terminal_counts[record["terminal_state"]] += 1
    if terminal_counts != payload.get("terminal_counts"):
        raise ValueError("provider_probe_terminal_counts_invalid")

    contract = read_json(root / CONTRACT_NAME)
    if canonical_hash(contract) != payload.get("contract_id"):
        raise ValueError("provider_probe_contract_hash_invalid")
    resource_usage = _resource_usage(records.values())
    if resource_usage != payload.get("resource_usage"):
        raise ValueError("provider_probe_resource_usage_invalid")
    _assert_activity_budget_semantic(contract, resource_usage)
    _validate_journal(
        root / ATTEMPT_JOURNAL_NAME,
        records=records,
        request_rows=request_rows,
    )
    _validate_dispositions(request_rows, payload.get("endpoint_dispositions"))
    expected_status = (
        "succeeded"
        if all(bool(row.get("expectation_met")) for row in records.values())
        else "blocked"
    )
    if payload.get("status") != expected_status:
        raise ValueError("provider_probe_status_invalid")
    return payload | {"manifest_path": str(manifest_path)}


def _validate_plan(
    contract: ProviderProbeContract,
    requests: Sequence[ProviderProbeRequest],
) -> list[dict[str, Any]]:
    if not contract.probe_id.strip():
        raise ValueError("provider_probe_id_missing")
    if contract.max_requests <= 0 or len(requests) > contract.max_requests:
        raise ValueError("provider_probe_request_budget_exceeded:max_requests")
    if not requests:
        raise ValueError("provider_probe_request_plan_empty")
    if not 0 < contract.timeout_seconds <= 120:
        raise ValueError("provider_probe_timeout_invalid")
    if contract.max_response_bytes <= 0:
        raise ValueError("provider_probe_response_budget_invalid")
    if (
        contract.max_total_response_bytes < contract.max_response_bytes
        or contract.max_wire_exchanges < len(requests)
    ):
        raise ValueError("provider_probe_activity_budget_invalid")
    allowed_hosts = {host.lower().rstrip(".") for host in contract.allowed_hosts}
    if not allowed_hosts or any(not host for host in allowed_hosts):
        raise ValueError("provider_probe_host_allowlist_invalid")

    rows: list[dict[str, Any]] = []
    request_ids: set[str] = set()
    endpoint_dispositions: dict[str, str] = {}
    for request in requests:
        if not request.request_id or request.request_id in request_ids:
            raise ValueError("provider_probe_request_id_invalid_or_duplicate")
        request_ids.add(request.request_id)
        parsed = urlsplit(request.url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"https", "baostock"} or host not in allowed_hosts:
            raise ValueError(f"provider_probe_host_not_allowed:{request.request_id}:{host}")
        if request.method.upper() not in {"GET", "POST", "BAOSTOCK"}:
            raise ValueError(f"provider_probe_method_invalid:{request.request_id}")
        if request.disposition not in HANDOFF_DISPOSITIONS:
            raise ValueError(f"provider_probe_disposition_invalid:{request.request_id}")
        expected = set(request.expected_terminal_states)
        if not expected or not expected <= TERMINAL_STATES:
            raise ValueError(f"provider_probe_expected_terminal_invalid:{request.request_id}")
        if (
            len(set(request.required_checks)) != len(request.required_checks)
            or any(not str(name).strip() for name in request.required_checks)
        ):
            raise ValueError(f"provider_probe_required_checks_invalid:{request.request_id}")
        for header in request.headers:
            normalized = header.lower().replace("_", "-")
            if any(fragment in normalized for fragment in SENSITIVE_HEADER_FRAGMENTS):
                raise ValueError(f"provider_probe_sensitive_header_forbidden:{request.request_id}")
        endpoint = request.endpoint or request.provider
        previous = endpoint_dispositions.setdefault(endpoint, request.disposition)
        if previous != request.disposition:
            raise ValueError(f"provider_probe_endpoint_disposition_conflict:{endpoint}")
        rows.append(request.semantic())
    return rows


def _observation_record(
    *,
    request: ProviderProbeRequest,
    request_semantic_hash: str,
    observation: ProviderProbeObservation,
) -> dict[str, Any]:
    if observation.terminal_state not in TERMINAL_STATES:
        raise ValueError(f"provider_probe_terminal_state_invalid:{request.request_id}")
    if observation.row_count is not None and observation.row_count < 0:
        raise ValueError(f"provider_probe_row_count_invalid:{request.request_id}")
    if observation.terminal_state == "empty" and observation.row_count != 0:
        raise ValueError(f"provider_probe_empty_row_count_invalid:{request.request_id}")
    if observation.terminal_state == "positive" and not observation.row_count:
        raise ValueError(f"provider_probe_positive_row_count_invalid:{request.request_id}")
    checks = {str(key): bool(value) for key, value in observation.checks.items()}
    if observation.transport_exchange_count < 0:
        raise ValueError(f"provider_probe_transport_exchange_count_invalid:{request.request_id}")
    expectation_met = (
        observation.terminal_state in request.expected_terminal_states
        and all(checks.values())
        and all(checks.get(name) is True for name in request.required_checks)
    )
    raw = bytes(observation.raw_payload)
    return {
        "schema_version": "provider_probe_terminal_observation_v1",
        "request_id": request.request_id,
        "request_semantic_hash": request_semantic_hash,
        "provider": request.provider,
        "endpoint": request.endpoint or request.provider,
        "disposition": request.disposition,
        "evidence_semantics": request.evidence_semantics,
        "terminal_state": observation.terminal_state,
        "row_count": observation.row_count,
        "status_code": observation.status_code,
        "error_code": observation.error_code,
        "diagnostics": _json_value(observation.diagnostics),
        "checks": checks,
        "expectation_met": expectation_met,
        "raw_payload_base64": base64.b64encode(raw).decode("ascii"),
        "raw_payload_sha256": _sha256_bytes(raw),
        "raw_payload_size_bytes": len(raw),
        "transport_exchange_count": observation.transport_exchange_count,
        "observed_at": _utc_now(),
    }


def _publish_probe_generation(
    *,
    output: Path,
    run_root: Path,
    contract_semantic: Mapping[str, Any],
    contract_id: str,
    request_rows: list[dict[str, Any]],
    request_plan_hash: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    prepare_parent = Path(
        tempfile.mkdtemp(prefix=".provider_probe_prepare.", dir=output.parent)
    )
    staging = prepare_parent / "staging"
    staging.mkdir()
    try:
        shutil.copy2(run_root / RAW_EVIDENCE_NAME, staging / RAW_EVIDENCE_NAME)
        shutil.copy2(run_root / ATTEMPT_JOURNAL_NAME, staging / ATTEMPT_JOURNAL_NAME)
        shutil.copy2(run_root / CONTRACT_NAME, staging / CONTRACT_NAME)
        shutil.copy2(run_root / REQUEST_PLAN_NAME, staging / REQUEST_PLAN_NAME)
        files = [
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(staging.iterdir())
            if path.is_file()
        ]
        terminal_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
        for record in records:
            terminal_counts[record["terminal_state"]] += 1
        endpoints: dict[str, str] = {}
        for row in request_rows:
            endpoints[str(row["endpoint"])] = str(row["disposition"])
        semantic = {
            "schema_version": SCHEMA_VERSION,
            "mode": "bounded_provider_probe",
            "probe_id": contract_semantic["probe_id"],
            "contract_id": contract_id,
            "request_plan_hash": request_plan_hash,
            "request_count": len(request_rows),
            "terminal_counts": terminal_counts,
            "endpoint_dispositions": dict(sorted(endpoints.items())),
            "status": (
                "succeeded"
                if all(record["expectation_met"] for record in records)
                else "blocked"
            ),
            "raw_evidence": {
                "path": RAW_EVIDENCE_NAME,
                "sha256": sha256_file(staging / RAW_EVIDENCE_NAME),
                "record_count": len(records),
            },
            "resource_usage": _resource_usage(records),
            "files": files,
            "safety": {flag: False for flag in SAFETY_FLAGS},
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"{GENERATION_PREFIX}_{content_hash[:24]}"
        _write_json(
            staging / MANIFEST_NAME,
            semantic | {"content_hash": content_hash, "generation_id": generation_id},
        )
        prepared = prepare_parent / generation_id
        os.replace(staging, prepared)
        result = publish_prepared_generation(
            output,
            prepared_directory=prepared,
            manifest_name=MANIFEST_NAME,
            validator=validate_provider_capability_probe,
            pointer_schema=POINTER_SCHEMA,
            pointer_fields={
                "mode": "bounded_provider_probe",
                "data_admission_eligible": False,
            },
        )
        shutil.rmtree(run_root)
        _remove_empty_parent(run_root.parent)
        return result
    finally:
        if prepare_parent.exists():
            shutil.rmtree(prepare_parent)


def _matching_current_generation(
    output: Path,
    *,
    contract_id: str,
    request_plan_hash: str,
) -> dict[str, Any] | None:
    if not (output / "current.json").is_file():
        return None
    try:
        current = validate_provider_capability_probe(output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("provider_probe_existing_evidence_invalid") from exc
    if (
        current.get("contract_id") == contract_id
        and current.get("request_plan_hash") == request_plan_hash
        and current.get("status") == "succeeded"
    ):
        return current
    return None


def _read_raw_records(
    path: Path,
    *,
    request_rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    expected = {str(row["request_id"]): canonical_hash(row) for row in request_rows}
    records: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"provider_probe_raw_evidence_json_invalid:{line_number}") from exc
        request_id = str(row.get("request_id") or "")
        if request_id not in expected or request_id in records:
            raise ValueError("provider_probe_raw_evidence_request_invalid_or_duplicate")
        if row.get("request_semantic_hash") != expected[request_id]:
            raise ValueError("provider_probe_raw_evidence_request_hash_invalid")
        try:
            raw = base64.b64decode(row.get("raw_payload_base64") or "", validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("provider_probe_raw_evidence_base64_invalid") from exc
        if _sha256_bytes(raw) != row.get("raw_payload_sha256"):
            raise ValueError("provider_probe_raw_payload_hash_invalid")
        if len(raw) != row.get("raw_payload_size_bytes"):
            raise ValueError("provider_probe_raw_payload_size_invalid")
        exchange_count = row.get("transport_exchange_count")
        if not isinstance(exchange_count, int) or exchange_count < 0:
            raise ValueError("provider_probe_transport_exchange_count_invalid")
        state = row.get("terminal_state")
        if state not in TERMINAL_STATES:
            raise ValueError("provider_probe_raw_evidence_terminal_invalid")
        request_row = next(
            item for item in request_rows if str(item["request_id"]) == request_id
        )
        for key in ("provider", "endpoint", "disposition", "evidence_semantics"):
            if row.get(key) != request_row.get(key):
                raise ValueError(f"provider_probe_raw_evidence_semantics_invalid:{key}")
        checks = row.get("checks")
        if not isinstance(checks, dict) or any(
            not isinstance(value, bool) for value in checks.values()
        ):
            raise ValueError("provider_probe_raw_evidence_checks_invalid")
        recomputed_expectation = (
            state in set(request_row.get("expected_terminal_states") or [])
            and all(checks.values())
            and all(
                checks.get(str(name)) is True
                for name in request_row.get("required_checks") or []
            )
        )
        if row.get("expectation_met") is not recomputed_expectation:
            raise ValueError("provider_probe_raw_evidence_expectation_invalid")
        records[request_id] = row
    return records


def _append_journal(path: Path, event: Mapping[str, Any]) -> None:
    rows = _journal_rows(path)
    row = dict(event) | {
        "sequence": len(rows) + 1,
        "previous_event_hash": rows[-1]["event_hash"] if rows else "",
    }
    row["event_hash"] = canonical_hash(row)
    _append_jsonl_fsync(path, row)


def _validate_journal(
    path: Path,
    *,
    records: Mapping[str, Mapping[str, Any]],
    request_rows: Sequence[Mapping[str, Any]],
) -> None:
    rows = _journal_rows(path)
    expected = {str(row["request_id"]): row for row in request_rows}
    started_counts = {request_id: 0 for request_id in expected}
    terminal_counts = {request_id: 0 for request_id in expected}
    for row in rows:
        event = row.get("event")
        request_id = str(row.get("request_id") or "")
        if event not in {"attempt_started", "attempt_terminal"} or request_id not in expected:
            raise ValueError("provider_probe_attempt_journal_event_invalid")
        if event == "attempt_started":
            if row.get("request_semantic_hash") != canonical_hash(expected[request_id]):
                raise ValueError("provider_probe_attempt_journal_request_hash_invalid")
            started_counts[request_id] += 1
        else:
            record = records.get(request_id)
            if (
                record is None
                or row.get("terminal_state") != record.get("terminal_state")
                or row.get("raw_payload_sha256")
                != record.get("raw_payload_sha256")
            ):
                raise ValueError("provider_probe_attempt_journal_raw_binding_invalid")
            terminal_counts[request_id] += 1
    if any(count < 1 for count in started_counts.values()) or any(
        count != 1 for count in terminal_counts.values()
    ):
        raise ValueError("provider_probe_attempt_journal_terminal_invalid")


def _journal_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    previous = ""
    event_ids: set[str] = set()
    for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        event_hash = row.get("event_hash")
        unsigned = {key: value for key, value in row.items() if key != "event_hash"}
        event_id = str(row.get("event_id") or "")
        if (
            not event_id
            or event_id in event_ids
            or row.get("sequence") != sequence
            or row.get("previous_event_hash") != previous
            or canonical_hash(unsigned) != event_hash
        ):
            raise ValueError("provider_probe_attempt_journal_chain_invalid")
        rows.append(row)
        event_ids.add(event_id)
        previous = str(event_hash)
    return rows


def _journal_event_count(path: Path) -> int:
    return len(_journal_rows(path))


def _validate_dispositions(
    request_rows: Sequence[Mapping[str, Any]],
    observed: Any,
) -> None:
    expected: dict[str, str] = {}
    for row in request_rows:
        disposition = str(row.get("disposition") or "")
        if disposition not in HANDOFF_DISPOSITIONS:
            raise ValueError("provider_probe_handoff_disposition_invalid")
        endpoint = str(row.get("endpoint") or "")
        previous = expected.setdefault(endpoint, disposition)
        if previous != disposition:
            raise ValueError("provider_probe_handoff_disposition_conflict")
    if observed != dict(sorted(expected.items())):
        raise ValueError("provider_probe_handoff_dispositions_mismatch")


def _append_jsonl_fsync(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _rewrite_jsonl_fsync(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    with path.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _write_or_verify_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != encoded:
            raise ValueError(f"provider_probe_recovery_plan_conflict:{path.name}")
        return
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _safe_relative_path(value: Any) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("provider_probe_evidence_path_invalid")
    return relative


def _has_lexical_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    return any(component.is_symlink() for component in (absolute, *absolute.parents))


@contextmanager
def _probe_execution_lock(output: Path, *, run_id: str) -> Iterator[None]:
    """Single-flight one request-plan identity through recovery and publication."""

    output.mkdir(parents=True, exist_ok=True)
    lock_root = output / ".execution_locks"
    lock_root.mkdir(exist_ok=True)
    lock_path = lock_root / f"{run_id}.lock"
    if lock_path.is_symlink():
        raise ValueError("provider_probe_execution_lock_symlink_forbidden")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _remove_empty_parent(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _resource_usage(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    rows = list(records)
    sizes = [int(row.get("raw_payload_size_bytes") or 0) for row in rows]
    return {
        "transport_exchange_count": sum(
            int(row.get("transport_exchange_count") or 0) for row in rows
        ),
        "raw_response_bytes": sum(sizes),
        "max_single_response_bytes": max(sizes, default=0),
    }


def _assert_activity_budget(
    contract: ProviderProbeContract,
    usage: Mapping[str, int],
) -> None:
    _assert_activity_budget_semantic(contract.semantic(), usage)


def _assert_activity_budget_semantic(
    contract: Mapping[str, Any],
    usage: Mapping[str, int],
) -> None:
    if int(usage["transport_exchange_count"]) > int(contract["max_wire_exchanges"]):
        raise ValueError("provider_probe_wire_exchange_budget_exceeded")
    if int(usage["raw_response_bytes"]) > int(contract["max_total_response_bytes"]):
        raise ValueError("provider_probe_total_response_budget_exceeded")
    if int(usage["max_single_response_bytes"]) > int(contract["max_response_bytes"]):
        raise ValueError("provider_probe_response_budget_exceeded")


def _scrub_text(value: str) -> str:
    lowered = value.lower()
    if any(fragment in lowered for fragment in ("token=", "password=", "secret=")):
        return "sensitive_adapter_error_redacted"
    return value[:1000]


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
