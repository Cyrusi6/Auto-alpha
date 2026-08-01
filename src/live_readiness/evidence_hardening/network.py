"""Task 055-F staged exact-date network state machine.

This module intentionally does not call the Task 055-D network runner.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import TUSHARE_PROVIDER_API_VERSION, TushareHttpClient
from data_pipeline.ashare.request_normalization import (
    tushare_code_semantic_hash,
)
from data_pipeline.ashare.security import CredentialStatus, tls_preflight

from .causal import validate_causal_frontier
from .contracts import (
    CANARY_ACCEPTANCE_SCHEMA,
    CANARY_SCHEMA,
    DAILY_FIELDS,
    MAX_DATE,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
    NETWORK_PLAN_SCHEMA,
    SUSPEND_FIELDS,
)
from .read_ledger import canonical_hash, sha256_file
from .truth_v2 import validate_truth_v2
from .transport import CANONICAL_ORIGIN, evidence_use_identity, transport_identity


class Task055FNetworkError(RuntimeError):
    pass


ENDPOINT_ROW_CAPS = {"daily": 6000, "suspend_d": 1000}


@dataclass(frozen=True)
class LoadedCredential:
    token: str
    source_type: str


def credential_presence(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    inline = bool(str(env.get("TUSHARE_TOKEN") or "").strip())
    file_name = str(env.get("TUSHARE_TOKEN_FILE") or "").strip()
    if inline and file_name:
        return {"credential_present": False, "source_type": "multiple", "blocker": "multiple_credential_sources"}
    if inline:
        return {"credential_present": True, "source_type": "environment"}
    if file_name:
        path = Path(file_name)
        return {
            "credential_present": path.is_file() and not path.is_symlink(),
            "source_type": "credential_file",
        }
    return {"credential_present": False, "source_type": "none"}


def load_credential_once(
    *,
    repo_root: str | Path,
    governed_root: str | Path,
    output_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> LoadedCredential:
    env = os.environ if environ is None else environ
    inline = str(env.get("TUSHARE_TOKEN") or "").strip()
    file_name = str(env.get("TUSHARE_TOKEN_FILE") or "").strip()
    if inline and file_name:
        raise Task055FNetworkError("multiple_credential_sources")
    if inline:
        return LoadedCredential(inline, "environment")
    if not file_name:
        raise Task055FNetworkError("credential_unavailable")
    path = Path(file_name)
    if not path.is_absolute() or path.is_symlink():
        raise Task055FNetworkError("credential_file_path_or_symlink_invalid")
    resolved = path.resolve(strict=True)
    forbidden = [Path(repo_root).resolve(), Path(governed_root).resolve(), Path(output_root).resolve()]
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise Task055FNetworkError("credential_file_inside_governed_or_repo_root")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        raise Task055FNetworkError("credential_file_owner_or_permissions_invalid")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise Task055FNetworkError("credential_file_empty")
    return LoadedCredential(token, "credential_file")


def execute_canary(
    *,
    causal_manifest: str | Path,
    output_root: str | Path,
    cache_data_root: str | Path,
    allow_network: bool,
    sealed_plan_hash: str,
    repo_root: str | Path,
    governed_root: str | Path,
    credential_loader: Callable[..., LoadedCredential] = load_credential_once,
    tls_checker: Callable[[], Mapping[str, Any]] = tls_preflight,
    client_factory: Callable[[LoadedCredential, Path], Any] | None = None,
) -> dict[str, Any]:
    raise Task055FNetworkError("superseded_by_task055k_transport_broker")
    causal = validate_causal_frontier(causal_manifest)
    plan = causal["network_plan"]
    _validate_plan(plan)
    if not allow_network or sealed_plan_hash != plan.get("plan_hash"):
        raise Task055FNetworkError("canary_cli_network_authorization_invalid")
    requests = list(plan.get("requests") or ())
    if not requests:
        raise Task055FNetworkError("canary_plan_has_no_request")
    tls = dict(tls_checker())
    if (
        tls.get("status") != "passed"
        or tls.get("origin") != CANONICAL_ORIGIN
        or tls.get("hostname_verified") is not True
        or tls.get("certificate_verified") is not True
    ):
        raise Task055FNetworkError("canary_tls_preflight_failed")
    credential = credential_loader(
        repo_root=repo_root,
        governed_root=governed_root,
        output_root=output_root,
    )
    request = dict(requests[0])
    spend_root = Path(output_root).resolve().parent / "network_spend"
    before = _load_spend_ledger(spend_root)
    if _physical_attempt_count(before) >= MAX_PHYSICAL_ATTEMPTS:
        raise Task055FNetworkError("global_physical_attempt_budget_exhausted")
    try:
        result = _execute_one(
            request=request,
            cache_data_root=Path(cache_data_root),
            credential=credential,
            client_factory=client_factory,
            spend_root=spend_root,
        )
    except Exception:
        raise
    spend = _load_spend_ledger(spend_root)
    physical_attempts = _physical_attempt_count(spend) - _physical_attempt_count(before)
    if physical_attempts != 1:
        raise Task055FNetworkError("canary_physical_attempt_count_invalid")
    payload = {
        "schema_version": CANARY_SCHEMA,
        "status": "completed",
        "parent_plan_hash": plan["plan_hash"],
        "frontier_root": plan["frontier_root"],
        "logical_request_count": 1,
        "physical_attempt_count": physical_attempts,
        "must_stop_after_canary": True,
        "batch_started": False,
        "tls_preflight": tls,
        "credential": {"credential_present": True, "source_type": credential.source_type},
        "result": result,
        "spend_ledger_content_hash": spend.get("content_hash"),
    }
    return _publish_json_generation(Path(output_root), "canary", payload, "canary_manifest.json")


def verify_canary(
    canary_manifest: str | Path,
    *,
    cache_data_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    path = Path(canary_manifest)
    payload = _validate_generation_manifest(path)
    if payload.get("schema_version") != CANARY_SCHEMA or payload.get("status") != "completed":
        raise Task055FNetworkError("canary_manifest_invalid")
    if payload.get("logical_request_count") != 1 or payload.get("physical_attempt_count") != 1:
        raise Task055FNetworkError("canary_request_count_invalid")
    if payload.get("must_stop_after_canary") is not True or payload.get("batch_started") is not False:
        raise Task055FNetworkError("canary_did_not_stop")
    spend_root = path.parents[3] / "network_spend"
    spend = _load_spend_ledger(spend_root)
    if (
        spend.get("content_hash") != payload.get("spend_ledger_content_hash")
        or _physical_attempt_count(spend) != 1
    ):
        raise Task055FNetworkError("canary_spend_ledger_invalid")
    result = payload.get("result") or {}
    request = result.get("request") or {}
    cache = TushareResponseCache(cache_data_root, enabled=True)
    read = cache.read(
        str(request.get("api_name")),
        params=dict(request.get("params") or {}),
        fields=list(request.get("fields") or ()),
        endpoint_schema_proof=result.get("endpoint_schema_proof"),
        allow_legacy_source_semantics=False,
    )
    if not read or not read.hit or sha256_file(read.path) != result.get("cache_sha256"):
        raise Task055FNetworkError("canary_cache_reread_failed")
    _validate_records(request, read.records)
    acceptance = {
        "schema_version": CANARY_ACCEPTANCE_SCHEMA,
        "status": "accepted",
        "canary_content_hash": payload.get("content_hash"),
        "parent_plan_hash": payload.get("parent_plan_hash"),
        "frontier_root": payload.get("frontier_root"),
        "transport_hash": request.get("transport_hash"),
        "cache_sha256": result.get("cache_sha256"),
        "item_count": len(read.records),
        "spend_ledger_content_hash": payload.get("spend_ledger_content_hash"),
        "resume_authorized": True,
    }
    return _publish_json_generation(Path(output_root), "canary_acceptance", acceptance, "canary_acceptance_manifest.json")


def execute_l1_resume(
    *,
    causal_manifest: str | Path,
    canary_acceptance_manifest: str | Path,
    output_root: str | Path,
    cache_data_root: str | Path,
    allow_network: bool,
    sealed_plan_hash: str,
    repo_root: str | Path,
    governed_root: str | Path,
    credential_loader: Callable[..., LoadedCredential] = load_credential_once,
    tls_checker: Callable[[], Mapping[str, Any]] = tls_preflight,
    client_factory: Callable[[LoadedCredential, Path], Any] | None = None,
) -> dict[str, Any]:
    raise Task055FNetworkError("superseded_by_task055k_transport_broker")
    causal = validate_causal_frontier(causal_manifest)
    plan = causal["network_plan"]
    _validate_plan(plan)
    acceptance = _validate_generation_manifest(Path(canary_acceptance_manifest))
    if (
        acceptance.get("schema_version") != CANARY_ACCEPTANCE_SCHEMA
        or acceptance.get("status") != "accepted"
        or acceptance.get("resume_authorized") is not True
        or acceptance.get("parent_plan_hash") != plan.get("plan_hash")
    ):
        raise Task055FNetworkError("l1_resume_canary_acceptance_invalid")
    if not allow_network or sealed_plan_hash != plan.get("plan_hash"):
        raise Task055FNetworkError("l1_resume_cli_network_authorization_invalid")
    spend_root = Path(output_root).resolve().parent / "network_spend"
    spend_state = _load_spend_ledger(spend_root)
    prior_spend = _physical_attempt_count(spend_state)
    if prior_spend != 1 or spend_state.get("content_hash") != acceptance.get("spend_ledger_content_hash", spend_state.get("content_hash")):
        raise Task055FNetworkError("l1_resume_spend_ledger_invalid")
    tls = dict(tls_checker())
    if (
        tls.get("status") != "passed"
        or tls.get("origin") != CANONICAL_ORIGIN
        or tls.get("hostname_verified") is not True
        or tls.get("certificate_verified") is not True
    ):
        raise Task055FNetworkError("l1_resume_tls_preflight_failed")
    credential = credential_loader(
        repo_root=repo_root,
        governed_root=governed_root,
        output_root=output_root,
    )
    results = []
    spend = int(prior_spend)
    cache = TushareResponseCache(cache_data_root, enabled=True)
    for request in list(plan.get("requests") or ())[1:]:
        existing = cache.read(
            request["api_name"], params=request["params"], fields=request["fields"], allow_legacy_source_semantics=False
        )
        if existing and existing.hit:
            _validate_records(request, existing.records)
            results.append(
                {
                    "request": request,
                    "outcome": "validated_cache_hit",
                    "cache_sha256": sha256_file(existing.path),
                    "item_count": len(existing.records),
                }
            )
            continue
        if spend >= MAX_PHYSICAL_ATTEMPTS:
            raise Task055FNetworkError("global_physical_attempt_budget_exhausted")
        results.append(
            _execute_one(
                request=request,
                cache_data_root=Path(cache_data_root),
                credential=credential,
                client_factory=client_factory,
                spend_root=spend_root,
            )
        )
        spend = _physical_attempt_count(_load_spend_ledger(spend_root))
    payload = {
        "schema_version": "task055f_l1_resume_v1",
        "status": "completed",
        "parent_plan_hash": plan["plan_hash"],
        "frontier_root": plan["frontier_root"],
        "prior_spend": prior_spend,
        "cumulative_physical_attempt_count": spend,
        "results": results,
        "spend_ledger_content_hash": _load_spend_ledger(spend_root).get("content_hash"),
        "l2_created": False,
        "next_stage": "apply_l1_then_rebuild_truth_v2_and_causal_frontier",
    }
    return _publish_json_generation(Path(output_root), "l1_resume", payload, "l1_resume_manifest.json")


def build_dynamic_l2_plan(
    *,
    parent_l1_apply_hash: str,
    causal_manifest: str | Path,
    truth_v2_manifest: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    causal = validate_causal_frontier(causal_manifest)
    truth = validate_truth_v2(truth_v2_manifest)
    truth_by_key = {(row["ts_code"], row["trade_date"]): row for row in truth["records"]}
    requests = []
    for code, date in sorted(
        (row["blocker"]["ts_code"], row["blocker"]["trade_date"])
        for row in causal["run_rows"]
        if (row.get("blocker") or {}).get("code") == "held_position_mark_unavailable"
    ):
        row = truth_by_key.get((code, date)) or {}
        if row.get("suspend_type") != "none":
            continue
        request = {
            "stage": "L2_suspend_exact",
            "api_name": "suspend_d",
            "params": {"ts_code": code, "trade_date": date},
            "fields": list(SUSPEND_FIELDS),
            "ts_code": code,
            "trade_date": date,
        }
        request["transport_hash"] = transport_identity(request["api_name"], request["params"], request["fields"])
        requests.append(request)
    requests = list({row["transport_hash"]: row for row in requests}.values())
    l1_request_count = len((causal.get("network_plan") or {}).get("requests") or ())
    if (
        len(requests) > MAX_UNIQUE_SECURITY_DATES
        or l1_request_count + len(requests) > MAX_LOGICAL_REQUESTS
    ):
        raise Task055FNetworkError("dynamic_l2_request_budget_exceeded")
    for request in requests:
        request["evidence_use_hash"] = evidence_use_identity(
            stage="L2_suspend_exact",
            parent_plan_hash=parent_l1_apply_hash,
            frontier_root=str(causal["missing_key_root"]),
            transport_hash=request["transport_hash"],
        )
    payload = {
        "schema_version": NETWORK_PLAN_SCHEMA,
        "status": "sealed_dynamic_l2_exact_only",
        "parent_l1_apply_hash": parent_l1_apply_hash,
        "truth_v2_content_hash": truth["content_hash"],
        "causal_content_hash": causal["content_hash"],
        "frontier_root": causal["missing_key_root"],
        "requests": requests,
        "empty_response_semantics": "vendor_absence_only_not_full_day_suspension_proof",
    }
    payload["plan_hash"] = canonical_hash(payload)
    return _publish_json_generation(Path(output_root), "dynamic_l2", payload, "dynamic_l2_plan.json")


def _execute_one(
    *,
    request: Mapping[str, Any],
    cache_data_root: Path,
    credential: LoadedCredential,
    client_factory: Callable[[LoadedCredential, Path], Any] | None,
    spend_root: Path,
) -> dict[str, Any]:
    raise Task055FNetworkError("superseded_by_task055k_transport_broker")
    if str(request.get("trade_date") or (request.get("params") or {}).get("trade_date") or "") > MAX_DATE:
        raise Task055FNetworkError("network_request_date_exceeds_boundary")
    if client_factory is None:
        config = AShareDataConfig(
            tushare_token=credential.token,
            tushare_api_url=CANONICAL_ORIGIN,
            tushare_retry_count=1,
            data_dir=cache_data_root,
        )
        client = TushareHttpClient(config)
    else:
        client = client_factory(credential, cache_data_root)
    if getattr(client, "retry_count", 1) != 1:
        raise Task055FNetworkError("unobservable_internal_retry_forbidden")
    started = _append_spend_event(
        spend_root,
        {
            "event": "physical_post_started",
            "api_name": request["api_name"],
            "transport_hash": request["transport_hash"],
            "evidence_use_hash": request.get("evidence_use_hash"),
            "ts_code": request.get("ts_code"),
            "trade_date": request.get("trade_date"),
        },
    )
    if _physical_attempt_count(started) > MAX_PHYSICAL_ATTEMPTS:
        raise Task055FNetworkError("global_physical_attempt_budget_exhausted")
    try:
        envelope = client.post_with_metadata(
            request["api_name"], params=dict(request["params"]), fields=list(request["fields"])
        )
    except Exception as exc:
        _append_spend_event(
            spend_root,
            {
                "event": "physical_post_failed",
                "transport_hash": request["transport_hash"],
                "error_class": type(exc).__name__,
            },
        )
        raise Task055FNetworkError(_scrub(str(exc), credential.token)) from exc
    if envelope.endpoint != CANONICAL_ORIGIN or envelope.provider_api_version != TUSHARE_PROVIDER_API_VERSION:
        raise Task055FNetworkError("response_provider_origin_or_version_invalid")
    if int(envelope.response_code) != 0 or int(envelope.item_count) != len(envelope.records):
        raise Task055FNetworkError("response_code_or_item_count_invalid")
    requested_fields = set(request["fields"])
    response_fields = set(envelope.response_fields or ())
    if envelope.records and not requested_fields.issubset(response_fields):
        raise Task055FNetworkError("response_fields_missing")
    records = [dict(row) for row in envelope.records]
    _validate_records(request, records)
    cap = ENDPOINT_ROW_CAPS.get(str(request["api_name"]))
    if cap is None or len(records) >= cap:
        raise Task055FNetworkError("response_row_cap_reached_or_unknown")
    cache = TushareResponseCache(cache_data_root, enabled=True)
    endpoint_schema_proof = None
    if not records and not envelope.response_fields:
        endpoint_schema_proof = cache.build_endpoint_schema_proof(
            request["api_name"], request["fields"], code_semantic_hash=tushare_code_semantic_hash()
        )
        if endpoint_schema_proof is None:
            raise Task055FNetworkError("empty_response_schema_proof_missing")
    path = cache.write(
        request["api_name"],
        params=dict(request["params"]),
        fields=list(request["fields"]),
        records=records,
        response_code=envelope.response_code,
        response_message=_scrub(envelope.response_message, credential.token),
        response_fields=envelope.response_fields,
        item_count=envelope.item_count,
        response_fields_observed=bool(envelope.response_fields),
        endpoint_schema_proof=endpoint_schema_proof,
        endpoint=envelope.endpoint,
        provider_api_version=envelope.provider_api_version,
    )
    reread = cache.read(
        request["api_name"],
        params=dict(request["params"]),
        fields=list(request["fields"]),
        endpoint_schema_proof=endpoint_schema_proof,
        allow_legacy_source_semantics=False,
    )
    if not reread or not reread.hit:
        raise Task055FNetworkError("published_cache_reread_failed")
    _append_spend_event(
        spend_root,
        {
            "event": "physical_post_completed",
            "transport_hash": request["transport_hash"],
            "cache_sha256": sha256_file(path),
            "item_count": len(records),
        },
    )
    return {
        "request": dict(request),
        "outcome": "positive_response" if records else "negative_vendor_response",
        "item_count": len(records),
        "cache_relative_path": str(path.relative_to(cache_data_root)),
        "cache_sha256": sha256_file(path),
        "endpoint_schema_proof": endpoint_schema_proof,
        "physical_attempt_count": 1,
    }


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != NETWORK_PLAN_SCHEMA or plan.get("status") != "sealed_round_one_daily_only":
        raise Task055FNetworkError("round_one_plan_schema_or_status_invalid")
    requests = list(plan.get("requests") or ())
    unsigned = {key: value for key, value in plan.items() if key != "plan_hash"}
    if plan.get("plan_hash") != canonical_hash(unsigned):
        raise Task055FNetworkError("round_one_plan_hash_invalid")
    keys = {(str(row.get("ts_code")), str(row.get("trade_date"))) for row in requests}
    if len(keys) != len(requests) or len(keys) > MAX_UNIQUE_SECURITY_DATES or len(requests) > MAX_LOGICAL_REQUESTS:
        raise Task055FNetworkError("round_one_plan_limits_or_duplicates_invalid")
    for request in requests:
        if request.get("api_name") != "daily" or set(request.get("params") or {}) != {"ts_code", "trade_date"}:
            raise Task055FNetworkError("round_one_plan_not_exact_daily")
        expected = transport_identity("daily", request["params"], request["fields"])
        expected_use = evidence_use_identity(
            stage="L1_daily_exact",
            parent_plan_hash=str(plan.get("frontier_root") or ""),
            frontier_root=str(plan.get("frontier_root") or ""),
            transport_hash=expected,
        )
        if (
            request.get("transport_hash") != expected
            or request.get("evidence_use_hash") != expected_use
            or str(request["trade_date"]) > MAX_DATE
        ):
            raise Task055FNetworkError("round_one_transport_identity_or_date_invalid")


def _validate_records(request: Mapping[str, Any], records: list[Mapping[str, Any]]) -> None:
    code = str((request.get("params") or {}).get("ts_code") or "")
    date = str((request.get("params") or {}).get("trade_date") or "")
    seen = set()
    for row in records:
        if str(row.get("ts_code")) != code or str(row.get("trade_date")) != date or date > MAX_DATE:
            raise Task055FNetworkError("response_geometry_invalid")
        if request["api_name"] == "daily":
            key = (code, date)
            values = {}
            for field in DAILY_FIELDS[2:]:
                try:
                    values[field] = float(row.get(field))
                except (TypeError, ValueError) as exc:
                    raise Task055FNetworkError(f"daily_field_invalid:{field}") from exc
            if any(not (value == value and abs(value) != float("inf")) for value in values.values()):
                raise Task055FNetworkError("daily_non_finite_value")
            if any(values[field] <= 0 for field in ("open", "high", "low", "close", "pre_close")):
                raise Task055FNetworkError("daily_nonpositive_price")
            if values["high"] < max(values["open"], values["low"], values["close"]):
                raise Task055FNetworkError("daily_high_relation_invalid")
            if values["low"] > min(values["open"], values["high"], values["close"]):
                raise Task055FNetworkError("daily_low_relation_invalid")
            if values["vol"] < 0 or values["amount"] < 0:
                raise Task055FNetworkError("daily_volume_or_amount_invalid")
        else:
            kind = str(row.get("suspend_type") or "")
            timing = row.get("suspend_timing")
            if kind not in {"S", "R"}:
                raise Task055FNetworkError("suspend_type_invalid")
            key = (code, date, kind, timing)
        if key in seen:
            raise Task055FNetworkError("response_primary_key_duplicate")
        seen.add(key)


def _publish_json_generation(root: Path, prefix: str, payload: Mapping[str, Any], file_name: str) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    semantic = dict(payload)
    content_hash = canonical_hash(semantic)
    generation_id = f"{prefix}_{content_hash[:24]}"
    manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=f".task055f.{prefix}.", dir=root))
    try:
        (staging / file_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            import shutil

            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            root / "current.json",
            {"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/{file_name}"},
        )
        return manifest | {"manifest_path": str(target / file_name)}
    except Exception:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_generation_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    content_hash = canonical_hash(semantic)
    if payload.get("content_hash") != content_hash:
        raise Task055FNetworkError("network_generation_content_hash_invalid")
    generation_id = str(payload.get("generation_id") or "")
    if not generation_id.endswith(content_hash[:24]):
        raise Task055FNetworkError("network_generation_identity_invalid")
    return payload


def _load_spend_ledger(root: Path) -> dict[str, Any]:
    pointer = root / "current.json"
    if not pointer.is_file():
        return {
            "schema_version": "task055f_append_only_network_spend_v1",
            "events": [],
            "content_hash": canonical_hash([]),
        }
    current = json.loads(pointer.read_text(encoding="utf-8"))
    manifest = root / str(current.get("manifest") or "")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise Task055FNetworkError("network_spend_ledger_content_hash_invalid")
    events = list(payload.get("events") or ())
    previous = ""
    for sequence, row in enumerate(events, 1):
        unsigned = {key: value for key, value in row.items() if key != "row_hash"}
        if (
            int(row.get("sequence") or 0) != sequence
            or row.get("previous_row_hash") != previous
            or canonical_hash(unsigned) != row.get("row_hash")
        ):
            raise Task055FNetworkError("network_spend_ledger_chain_invalid")
        previous = str(row["row_hash"])
    return payload


def _append_spend_event(root: Path, event: Mapping[str, Any]) -> dict[str, Any]:
    current = _load_spend_ledger(root)
    events = [dict(row) for row in current.get("events") or ()]
    row = dict(event) | {
        "sequence": len(events) + 1,
        "previous_row_hash": events[-1]["row_hash"] if events else "",
    }
    row["row_hash"] = canonical_hash(row)
    events.append(row)
    semantic = {
        "schema_version": "task055f_append_only_network_spend_v1",
        "events": events,
        "physical_attempt_count": sum(item.get("event") == "physical_post_started" for item in events),
        "logical_transport_count": len(
            {item.get("transport_hash") for item in events if item.get("event") == "physical_post_started"}
        ),
    }
    return _publish_json_generation(root, "network_spend", semantic, "network_spend_ledger.json")


def _physical_attempt_count(payload: Mapping[str, Any]) -> int:
    if "physical_attempt_count" in payload:
        return int(payload["physical_attempt_count"])
    return sum(row.get("event") == "physical_post_started" for row in payload.get("events") or ())


def _scrub(value: str, token: str) -> str:
    return str(value).replace(token, "[REDACTED]") if token else str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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
