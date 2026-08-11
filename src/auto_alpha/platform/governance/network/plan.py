"""Seal exact-date remediation plans without executing network requests."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from auto_alpha.portfolio.simulator.evidence_contracts import (
    DAILY_FIELDS,
    MAX_DATE,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
)

from auto_alpha.platform.artifacts.storage import canonical_hash
from .transport import evidence_use_identity, transport_identity


PLAN_SCHEMA = "task055g_dynamic_network_plan_v1"


class NetworkPlanError(RuntimeError):
    pass


def seal_round_one_l1_plan(
    *,
    frontier_keys: Sequence[Sequence[str]],
    lineage: Mapping[str, Any],
    round_id: int = 1,
) -> dict[str, Any]:
    frontier = sorted({(str(item[0]), str(item[1])) for item in frontier_keys})
    if len(frontier) > MAX_UNIQUE_SECURITY_DATES:
        raise NetworkPlanError("round_one_frontier_unique_key_budget_exceeded")
    if any(trade_date > MAX_DATE for _, trade_date in frontier):
        raise NetworkPlanError("round_one_frontier_future_date")
    frontier_root = canonical_hash(frontier)
    normalized_lineage = dict(lineage)
    normalized_lineage["frontier_root"] = frontier_root
    normalized_lineage.setdefault("key_root", frontier_root)
    parent_hash = canonical_hash(normalized_lineage)
    requests = [
        _request(
            stage="L1",
            round_id=round_id,
            api_name="daily",
            ts_code=code,
            trade_date=trade_date,
            fields=DAILY_FIELDS,
            parent_plan_hash=parent_hash,
            frontier_root=frontier_root,
        )
        for code, trade_date in frontier
    ]
    if len(requests) > MAX_LOGICAL_REQUESTS:
        raise NetworkPlanError("round_one_frontier_logical_budget_exceeded")
    return _make_plan(
        stage="L1",
        round_id=round_id,
        requests=requests,
        lineage=normalized_lineage,
        frontier_root=frontier_root,
        status="sealed_round_one_exact_daily_l1",
    )


def _make_plan(
    *,
    stage: str,
    round_id: int,
    requests: Sequence[Mapping[str, Any]],
    lineage: Mapping[str, Any],
    frontier_root: str,
    status: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": PLAN_SCHEMA,
        "status": status,
        "stage": stage,
        "round_id": round_id,
        "frontier_root": frontier_root,
        "parent_apply_hash": None,
        "lineage": dict(lineage),
        "requests": [dict(row) for row in requests],
        "limits": {
            "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
            "logical_requests": MAX_LOGICAL_REQUESTS,
            "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        },
        "network_executed": False,
        "token_read": False,
        "frontier_semantics": "round_1_first_terminal_held_mark_blocker_not_total_gap_count",
        "l2_requests": [],
        "l2_generation_gate": "only_after_l1_apply_and_truth_fee_aware_frontier_rebuild",
    }
    payload["plan_hash"] = canonical_hash(payload)
    return payload


def _request(
    *,
    stage: str,
    round_id: int,
    api_name: str,
    ts_code: str,
    trade_date: str,
    fields: Iterable[str],
    parent_plan_hash: str,
    frontier_root: str,
) -> dict[str, Any]:
    params = {"ts_code": ts_code, "trade_date": trade_date}
    field_list = list(fields)
    transport_hash = transport_identity(api_name, params, field_list)
    return {
        "stage": stage,
        "round_id": round_id,
        "api_name": api_name,
        "params": params,
        "fields": field_list,
        "ts_code": ts_code,
        "trade_date": trade_date,
        "transport_hash": transport_hash,
        "evidence_use_hash": evidence_use_identity(
            stage=f"task055g_{stage.lower()}_exact",
            parent_plan_hash=parent_plan_hash,
            frontier_root=frontier_root,
            transport_hash=transport_hash,
        ),
    }
