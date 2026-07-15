"""Immutable, bounded request planning for Task 055-B security-date evidence.

This module never creates a network client.  Callers must explicitly supply a
requester and a request budget to execute a previously published plan.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from data_pipeline.ashare.request_normalization import normalize_tushare_request, stable_json_hash
from data_pipeline.ashare.validators import is_valid_ts_code, is_valid_yyyymmdd


REQUEST_PLAN_SCHEMA = "task_055b_bounded_request_plan.v1"
EVIDENCE_RUN_SCHEMA = "task_055b_dual_geometry_evidence_run.v1"
MAX_OBSERVED_END_DATE = "20260630"
DAILY_FIELDS = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount")
SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")


class RequestPlanError(RuntimeError):
    """Raised when a request plan or response evidence is not trustworthy."""


@dataclass(frozen=True)
class RequestPlanConfig:
    output_root: Path
    max_network_requests: int
    observed_end_date: str = MAX_OBSERVED_END_DATE
    endpoint: str = "http://api.tushare.pro"
    provider_api_version: str = "tushare_pro_http.v1"
    request_timeout_seconds: int = 30


@dataclass(frozen=True)
class ResponseEnvelope:
    records: tuple[Mapping[str, Any], ...]
    response_fields: tuple[str, ...]
    response_code: int = 0
    response_message: str = ""
    provider_api_version: str = "tushare_pro_http.v1"


Requester = Callable[[str, Mapping[str, str], Sequence[str]], ResponseEnvelope]


def build_request_plan(
    gap_cells: Iterable[Mapping[str, Any]],
    trade_calendar: Iterable[str],
    config: RequestPlanConfig,
) -> dict[str, Any]:
    """Publish an immutable exact-date plus stock-window request plan."""

    if config.max_network_requests <= 0:
        raise RequestPlanError("max_network_requests must be positive")
    if not is_valid_yyyymmdd(config.observed_end_date) or config.observed_end_date > MAX_OBSERVED_END_DATE:
        raise RequestPlanError(f"observed_end_date must not exceed {MAX_OBSERVED_END_DATE}")
    dates = _validated_calendar(trade_calendar, config.observed_end_date)
    date_index = {trade_date: index for index, trade_date in enumerate(dates)}
    cells = _validated_cells(gap_cells, date_index, config.observed_end_date)
    episodes = _build_episodes(cells, dates, date_index)
    unique_dates = sorted({cell["trade_date"] for cell in cells})
    requests: list[dict[str, Any]] = []
    for trade_date in unique_dates:
        requests.extend(
            (
                _request("daily", "exact_trade_date", {"trade_date": trade_date}, DAILY_FIELDS),
                _request("suspend_d", "exact_trade_date", {"trade_date": trade_date}, SUSPEND_FIELDS),
            )
        )
    for episode in episodes:
        params = {
            "ts_code": episode["ts_code"],
            "start_date": episode["window_start_date"],
            "end_date": episode["window_end_date"],
        }
        requests.extend(
            (
                _request("daily", "security_window", params, DAILY_FIELDS, episode_id=episode["episode_id"]),
                _request("suspend_d", "security_window", params, SUSPEND_FIELDS, episode_id=episode["episode_id"]),
            )
        )
    requests.sort(key=lambda item: (item["geometry"], item["api_name"], stable_json_hash(item["normalized_params"])))
    if len(requests) > config.max_network_requests:
        raise RequestPlanError(
            f"planned request count {len(requests)} exceeds immutable budget {config.max_network_requests}"
        )
    semantic = {
        "schema_version": REQUEST_PLAN_SCHEMA,
        "observed_end_date": config.observed_end_date,
        "endpoint": config.endpoint,
        "provider_api_version": config.provider_api_version,
        "request_timeout_seconds": config.request_timeout_seconds,
        "max_network_requests": config.max_network_requests,
        "gap_cell_count": len(cells),
        "gap_cell_hash": stable_json_hash(cells),
        "unique_gap_dates": unique_dates,
        "affected_ts_codes": sorted({cell["ts_code"] for cell in cells}),
        "episodes": episodes,
        "requests": requests,
        "request_count": len(requests),
        "query_geometries": ["exact_trade_date", "security_window"],
        "pagination_policy": "fail_closed_no_implicit_pagination",
        "split_policy": "preplanned_exact_date_and_episode_window_only",
        "cache_policy": "content_addressed_response_envelope_v1",
        "resume_policy": "request_hash_response_hash_and_envelope_validation_v1",
        "stop_conditions": [
            "network_budget_exhausted",
            "response_schema_invalid",
            "response_outside_request_geometry",
            "response_primary_key_conflict",
            "cache_corrupt_or_lineage_mismatch",
        ],
        "source_semantic_hash": _source_hash(),
    }
    plan_hash = stable_json_hash(semantic)
    manifest = {
        **semantic,
        "artifact_type": "task_055b_request_plan",
        "content_hash": plan_hash,
        "created_at": _utc_now(),
        "request_plan_is_immutable": True,
        "prospective_holdout_access_allowed": False,
    }
    generation = config.output_root / "generations" / f"request_plan_{plan_hash}"
    path = generation / "request_plan.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if stable_json_hash(_without_runtime(existing)) != plan_hash:
            raise RequestPlanError("existing request plan generation content drift")
    else:
        generation.mkdir(parents=True, exist_ok=False)
        _atomic_json(path, manifest)
    return {**manifest, "manifest_path": str(path)}


def validate_request_plan(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != REQUEST_PLAN_SCHEMA:
        raise RequestPlanError("request plan schema mismatch")
    expected = stable_json_hash(_without_runtime(manifest))
    if manifest.get("content_hash") != expected:
        raise RequestPlanError("request plan content hash mismatch")
    if manifest.get("request_count") != len(manifest.get("requests", [])):
        raise RequestPlanError("request plan request count mismatch")
    if int(manifest["request_count"]) > int(manifest["max_network_requests"]):
        raise RequestPlanError("request plan exceeds network budget")
    if manifest.get("observed_end_date", "") > MAX_OBSERVED_END_DATE:
        raise RequestPlanError("request plan crosses observed research boundary")
    for request in manifest["requests"]:
        expected_request = _request(
            request["api_name"],
            request["geometry"],
            request["normalized_params"],
            tuple(request["fields"]),
            episode_id=request.get("episode_id"),
        )
        if request != expected_request:
            raise RequestPlanError("request specification fingerprint mismatch")
    return manifest


def execute_request_plan(
    plan_path: str | Path,
    evidence_root: str | Path,
    requester: Requester,
    *,
    request_budget: int,
    resume: bool = True,
) -> dict[str, Any]:
    """Execute a plan with explicit budget; cached validated responses cost zero."""

    plan = validate_request_plan(plan_path)
    if request_budget < 0 or request_budget > int(plan["max_network_requests"]):
        raise RequestPlanError("execution request_budget exceeds immutable plan budget")
    root = Path(evidence_root)
    response_dir = root / "responses"
    response_dir.mkdir(parents=True, exist_ok=True)
    executions: list[dict[str, Any]] = []
    network_requests = 0
    stopped = False
    for request in plan["requests"]:
        cache_path = response_dir / f"{request['request_hash']}.json"
        if resume and cache_path.exists():
            envelope = _load_cached_envelope(cache_path, request, plan)
            cache_hit = True
        else:
            if network_requests >= request_budget:
                stopped = True
                break
            raw_envelope = requester(request["api_name"], request["normalized_params"], tuple(request["fields"]))
            envelope = _validate_response_envelope(raw_envelope, request, plan)
            _atomic_json(cache_path, envelope)
            network_requests += 1
            cache_hit = False
        executions.append(
            {
                "request_hash": request["request_hash"],
                "api_name": request["api_name"],
                "geometry": request["geometry"],
                "episode_id": request.get("episode_id"),
                "cache_hit": cache_hit,
                "response_hash": envelope["response_hash"],
                "response_path": str(cache_path),
                "item_count": envelope["item_count"],
                "negative_vendor_response": envelope["item_count"] == 0,
                "negative_response_proves_trading_state": False,
            }
        )
    status = "budget_exhausted" if stopped else "complete"
    reconciliation = _reconcile(plan, executions)
    semantic = {
        "schema_version": EVIDENCE_RUN_SCHEMA,
        "request_plan_hash": plan["content_hash"],
        "status": status,
        "planned_request_count": plan["request_count"],
        "completed_request_count": len(executions),
        "network_request_count": network_requests,
        "cache_hit_count": sum(int(item["cache_hit"]) for item in executions),
        "request_budget": request_budget,
        "executions": executions,
        "reconciliation": reconciliation,
        "prospective_holdout_accessed": False,
    }
    run_hash = stable_json_hash(semantic)
    manifest = {
        **semantic,
        "artifact_type": "task_055b_dual_geometry_evidence_run",
        "content_hash": run_hash,
        "created_at": _utc_now(),
    }
    run_path = root / "runs" / f"evidence_run_{run_hash}" / "evidence_run.json"
    if not run_path.exists():
        run_path.parent.mkdir(parents=True, exist_ok=False)
        _atomic_json(run_path, manifest)
    return {**manifest, "manifest_path": str(run_path)}


def validate_evidence_run(path: str | Path, *, request_plan: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != EVIDENCE_RUN_SCHEMA:
        raise RequestPlanError("evidence run schema mismatch")
    semantic = {
        key: value for key, value in manifest.items()
        if key not in {"artifact_type", "content_hash", "created_at", "manifest_path"}
    }
    if stable_json_hash(semantic) != manifest.get("content_hash"):
        raise RequestPlanError("evidence run content hash mismatch")
    executions = manifest.get("executions") or []
    if int(manifest.get("completed_request_count", -1)) != len(executions):
        raise RequestPlanError("evidence run completed count mismatch")
    if int(manifest.get("network_request_count", -1)) + int(manifest.get("cache_hit_count", -1)) != len(executions):
        raise RequestPlanError("evidence run execution accounting mismatch")
    if request_plan is not None:
        plan = validate_request_plan(request_plan)
        if manifest.get("request_plan_hash") != plan.get("content_hash"):
            raise RequestPlanError("evidence run request plan lineage mismatch")
        if int(manifest.get("planned_request_count", -1)) != int(plan.get("request_count", -2)):
            raise RequestPlanError("evidence run planned request count mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def _request(
    api_name: str,
    geometry: str,
    params: Mapping[str, Any],
    fields: Sequence[str],
    *,
    episode_id: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_tushare_request(api_name, params=dict(params), fields=fields)
    item = {
        "api_name": api_name,
        "geometry": geometry,
        "normalized_params": normalized["params"],
        "fields": normalized["fields"],
        "request_hash": stable_json_hash(
            {"api_name": api_name, "geometry": geometry, "request": normalized, "episode_id": episode_id}
        ),
    }
    if episode_id is not None:
        item["episode_id"] = episode_id
    return item


def _validated_calendar(calendar: Iterable[str], observed_end_date: str) -> list[str]:
    dates = sorted({str(value) for value in calendar if str(value) <= observed_end_date})
    if not dates or any(not is_valid_yyyymmdd(value) for value in dates):
        raise RequestPlanError("trade calendar is empty or invalid")
    return dates


def _validated_cells(
    gap_cells: Iterable[Mapping[str, Any]],
    date_index: Mapping[str, int],
    observed_end_date: str,
) -> list[dict[str, str]]:
    cells: set[tuple[str, str]] = set()
    for row in gap_cells:
        ts_code = str(row.get("ts_code") or "")
        trade_date = str(row.get("trade_date") or "")
        if not is_valid_ts_code(ts_code):
            raise RequestPlanError(f"invalid gap ts_code: {ts_code!r}")
        if not is_valid_yyyymmdd(trade_date) or trade_date not in date_index:
            raise RequestPlanError(f"gap date is not a verified trade session: {trade_date!r}")
        if trade_date > observed_end_date:
            raise RequestPlanError("gap cell crosses observed research boundary")
        cells.add((ts_code, trade_date))
    if not cells:
        raise RequestPlanError("gap inventory is empty")
    return [{"ts_code": code, "trade_date": date} for code, date in sorted(cells)]


def _build_episodes(
    cells: list[dict[str, str]], dates: list[str], date_index: Mapping[str, int]
) -> list[dict[str, Any]]:
    by_code: dict[str, list[str]] = {}
    for cell in cells:
        by_code.setdefault(cell["ts_code"], []).append(cell["trade_date"])
    episodes: list[dict[str, Any]] = []
    for ts_code, code_dates in sorted(by_code.items()):
        ordered = sorted(code_dates, key=date_index.__getitem__)
        groups: list[list[str]] = []
        for trade_date in ordered:
            if not groups or date_index[trade_date] != date_index[groups[-1][-1]] + 1:
                groups.append([trade_date])
            else:
                groups[-1].append(trade_date)
        for group in groups:
            start_index = date_index[group[0]]
            end_index = date_index[group[-1]]
            window_start = dates[max(0, start_index - 1)]
            window_end = dates[min(len(dates) - 1, end_index + 1)]
            episode_semantic = {
                "ts_code": ts_code,
                "start_date": group[0],
                "end_date": group[-1],
                "window_start_date": window_start,
                "window_end_date": window_end,
                "cell_count": len(group),
            }
            episodes.append({**episode_semantic, "episode_id": stable_json_hash(episode_semantic)[:20]})
    return episodes


def _validate_response_envelope(
    raw: ResponseEnvelope, request: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(raw, ResponseEnvelope):
        raise RequestPlanError("requester must return a raw ResponseEnvelope")
    if raw.response_code != 0:
        raise RequestPlanError(f"provider response code is nonzero: {raw.response_code}")
    records = [dict(record) for record in raw.records]
    response_fields = list(raw.response_fields)
    if records and not set(request["fields"]).issubset(response_fields):
        raise RequestPlanError("provider response omitted requested fields")
    _validate_geometry(records, request, plan)
    _validate_primary_keys(records, request["api_name"])
    semantic = {
        "schema_version": "task_055b_raw_response_envelope.v1",
        "request_hash": request["request_hash"],
        "normalized_params": request["normalized_params"],
        "requested_fields": request["fields"],
        "response_fields": response_fields,
        "response_fields_observed": True,
        "response_code": raw.response_code,
        "response_message": raw.response_message,
        "provider_api_version": raw.provider_api_version,
        "endpoint": plan["endpoint"],
        "item_count": len(records),
        "records": records,
        "records_hash": stable_json_hash(records),
    }
    return {**semantic, "response_hash": stable_json_hash(semantic), "acquired_at": _utc_now()}


def _load_cached_envelope(path: Path, request: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestPlanError(f"corrupt response cache: {path}") from exc
    response_hash = envelope.pop("response_hash", None)
    acquired_at = envelope.pop("acquired_at", None)
    if response_hash != stable_json_hash(envelope):
        raise RequestPlanError("cached response hash mismatch")
    if envelope.get("request_hash") != request["request_hash"]:
        raise RequestPlanError("cached response request lineage mismatch")
    if envelope.get("endpoint") != plan["endpoint"]:
        raise RequestPlanError("cached response endpoint mismatch")
    _validate_geometry(envelope.get("records", []), request, plan)
    _validate_primary_keys(envelope.get("records", []), request["api_name"])
    return {**envelope, "response_hash": response_hash, "acquired_at": acquired_at}


def _validate_geometry(records: Iterable[Mapping[str, Any]], request: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    params = request["normalized_params"]
    for row in records:
        ts_code = str(row.get("ts_code") or "")
        trade_date = str(row.get("trade_date") or "")
        if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
            raise RequestPlanError("response contains invalid primary key")
        if trade_date > plan["observed_end_date"]:
            raise RequestPlanError("response crosses observed research boundary")
        if request["geometry"] == "exact_trade_date" and trade_date != params["trade_date"]:
            raise RequestPlanError("response row is outside exact-date geometry")
        if request["geometry"] == "security_window":
            if ts_code != params["ts_code"] or not params["start_date"] <= trade_date <= params["end_date"]:
                raise RequestPlanError("response row is outside security-window geometry")


def _validate_primary_keys(records: Iterable[Mapping[str, Any]], api_name: str) -> None:
    keys: set[tuple[Any, ...]] = set()
    for row in records:
        if api_name == "suspend_d":
            key = (row.get("ts_code"), row.get("trade_date"), row.get("suspend_type"), row.get("suspend_timing"))
        else:
            key = (row.get("ts_code"), row.get("trade_date"))
        if key in keys:
            raise RequestPlanError("response contains duplicate primary key")
        keys.add(key)


def _reconcile(plan: Mapping[str, Any], executions: list[dict[str, Any]]) -> dict[str, Any]:
    by_request = {item["request_hash"]: item for item in executions}
    completed = set(by_request)
    missing = [item["request_hash"] for item in plan["requests"] if item["request_hash"] not in completed]
    geometry_counts = {
        geometry: sum(item["geometry"] == geometry for item in executions)
        for geometry in ("exact_trade_date", "security_window")
    }
    return {
        "geometry_execution_counts": geometry_counts,
        "missing_request_hashes": missing,
        "all_planned_requests_completed": not missing,
        "empty_responses_are_vendor_negative_only": True,
        "broad_range_is_not_security_date_attestation": True,
    }


def _without_runtime(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"artifact_type", "content_hash", "created_at", "manifest_path", "request_plan_is_immutable", "prospective_holdout_access_allowed"}
    }


def _source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
