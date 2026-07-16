"""Secure, budgeted request execution with canary and v3 cache publication."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import TushareHttpClient
from data_pipeline.ashare.rate_limit import RequestRateLimitConfig, SimpleRateLimiter
from data_pipeline.ashare.request_normalization import stable_json_hash
from data_pipeline.ashare.security import CredentialStatus, load_tushare_credential, tls_preflight

from .cache import SecureCacheError, find_endpoint_schema_proof, inventory_caches, publish_validated_response, split_capped_request
from .contracts import GLOBAL_BUDGET, MAX_DATE


class NetworkGateError(RuntimeError):
    pass


def execute_plan(
    *, plan: Mapping[str, Any], output_root: str | Path, cache_roots: list[str | Path],
    allow_network: bool, sealed_plan_hash: str | None, request_budget: int,
    trade_dates: list[str] | None = None,
    credential_loader: Callable[[], CredentialStatus] = load_tushare_credential,
    tls_checker: Callable[[], dict[str, object]] = tls_preflight,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    if allow_network and sealed_plan_hash != plan.get("content_hash"):
        raise NetworkGateError("sealed_request_plan_hash_mismatch")
    if request_budget < 0 or request_budget > GLOBAL_BUDGET:
        raise NetworkGateError("global_network_budget_invalid")
    requests = [dict(row) for row in plan.get("requests") or ()]
    request_ledger = [_request_ledger_row(row) for row in requests]
    if any(row["policy_decision"] != "allowed_research_repair" for row in request_ledger):
        raise NetworkGateError("request_date_exceeds_observation_boundary")

    inventory = inventory_caches(cache_roots, requests)
    hit_hashes = set(inventory["hits"])
    pending = [row for row in requests if row["transport_hash"] not in hit_hashes]
    credential = credential_loader()
    tls = tls_checker()
    base = {
        "schema_version": "task055d_network_execution_v2",
        "plan_hash": plan["content_hash"],
        "tls_preflight": tls,
        "credential": {"credential_present": credential.credential_present, "source_type": credential.source_type},
        "cache_inventory": inventory,
        "logical_request_count": len(requests),
        "request_ledger": request_ledger,
        "request_firewall_violations": sum(row["policy_decision"] != "allowed_research_repair" for row in request_ledger),
        "read_firewall_violations": 0,
        "prospective_holdout_accessed": any(row["max_date"] > MAX_DATE for row in request_ledger),
    }
    if not pending:
        return _publish(Path(output_root), base | {
            "status": "complete", "physical_attempt_count": 0, "network_spend": 0,
            "validated_cache_hit_count": len(hit_hashes), "remaining_count": 0,
            "responses": [], "quarantine_receipts": [], "split_child_requests": [],
        })
    if not allow_network or not credential.credential_present:
        return _publish(Path(output_root), base | {
            "status": "blocked",
            "blocker": "network_not_allowed" if not allow_network else "credential_unavailable",
            "physical_attempt_count": 0,
            "network_spend": 0,
            "validated_cache_hit_count": len(hit_hashes),
            "remaining_count": len(pending),
            "responses": [],
            "quarantine_receipts": [],
            "split_child_requests": [],
        })
    if client_factory is None:
        def client_factory() -> TushareHttpClient:
            config = AShareDataConfig.from_env()
            limiter = SimpleRateLimiter(RequestRateLimitConfig(requests_per_minute=120, enabled=True))
            return TushareHttpClient(config, rate_limiter=limiter)
    client = client_factory()
    schema_proofs = {
        (row["api_name"], tuple(row["fields"])): find_endpoint_schema_proof(cache_roots, row["api_name"], row["fields"])
        for row in requests
    }
    responses: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    split_children: list[dict[str, Any]] = []
    spend = 0
    attempted_hashes: set[str] = set()
    queue = list(pending)
    ordinal = 0
    while queue and spend < request_budget:
        request = queue.pop(0)
        if request["transport_hash"] in attempted_hashes:
            continue
        attempted_hashes.add(request["transport_hash"])
        try:
            envelope = client.post_with_metadata(request["api_name"], params=request["params"], fields=request["fields"])
            proof = schema_proofs.get((request["api_name"], tuple(request["fields"])))
            receipt = publish_validated_response(cache_root=Path(output_root) / "formal_cache", request=request, envelope=envelope, endpoint_schema_proof=proof)
            responses.append({"transport_hash": request["transport_hash"], "canary": ordinal == 0, "receipt": receipt})
            if envelope.records:
                schema_proofs[(request["api_name"], tuple(request["fields"]))] = find_endpoint_schema_proof([Path(output_root) / "formal_cache"], request["api_name"], request["fields"])
        except SecureCacheError as exc:
            if str(exc) == "endpoint_cap_reached_split_required" and trade_dates:
                children = split_capped_request(request, trade_dates)
                split_children.extend(children)
                queue = children + queue
            else:
                quarantined.append({"transport_hash": request["transport_hash"], "reason": str(exc)})
        except Exception as exc:
            quarantined.append({"transport_hash": request["transport_hash"], "reason": type(exc).__name__})
        spend += 1
        if ordinal == 0 and quarantined:
            break
        ordinal += 1
    completed_hashes = hit_hashes | {row["transport_hash"] for row in responses}
    remaining = sum(row["transport_hash"] not in completed_hashes for row in requests)
    status = "complete" if remaining == 0 and not quarantined and not queue else "blocked"
    blocker = None
    if quarantined and responses == []:
        blocker = "canary_failed"
    elif spend >= request_budget and (remaining or queue):
        blocker = "network_budget_exhausted"
    elif quarantined:
        blocker = "response_validation_failed"
    return _publish(Path(output_root), base | {
        "status": status,
        "blocker": blocker,
        "physical_attempt_count": spend,
        "network_spend": spend,
        "validated_cache_hit_count": len(hit_hashes),
        "remaining_count": remaining,
        "responses": responses,
        "quarantine_receipts": quarantined,
        "split_child_requests": split_children,
    })


def _request_ledger_row(request: Mapping[str, Any]) -> dict[str, Any]:
    params = request.get("params") or {}
    dates = [str(value) for key, value in params.items() if key in {"trade_date", "start_date", "end_date"} and value]
    minimum = min(dates) if dates else ""
    maximum = max(dates) if dates else ""
    return {
        "transport_hash": request["transport_hash"],
        "evidence_use_hash": request.get("evidence_use_hash"),
        "api_name": request["api_name"],
        "min_date": minimum,
        "max_date": maximum,
        "policy_decision": "allowed_research_repair" if maximum and maximum <= MAX_DATE else "blocked_out_of_bounds",
    }


def _publish(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content = stable_json_hash(payload)
    result = payload | {"content_hash": content}
    path = root / f"network_execution_{content[:24]}.json"
    temporary = root / f".{path.name}.tmp"
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return result | {"manifest_path": str(path)}
