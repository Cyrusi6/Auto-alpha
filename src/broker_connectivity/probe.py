"""Connectivity probe orchestration."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from .credentials import collect_credential_refs, validate_credentials
from .models import (
    BrokerConnectionProfile,
    BrokerConnectivityBlockedError,
    BrokerConnectivityIssue,
    BrokerConnectivityProbeResult,
    BrokerConnectivityStatus,
    BrokerNetworkGuard,
)


def run_connectivity_probe(
    profile: BrokerConnectionProfile,
    guard: BrokerNetworkGuard,
    client,
    *,
    account_id: str = "",
    trade_date: str = "",
    as_of_date: str = "",
    require_credentials: bool = False,
) -> BrokerConnectivityProbeResult:
    started = _utc_now()
    t0 = time.perf_counter()
    issues: list[BrokerConnectivityIssue] = []
    refs = collect_credential_refs(profile)
    ok_credentials, credential_issues = validate_credentials(profile, require_all=require_credentials)
    for issue in credential_issues:
        issues.append(BrokerConnectivityIssue(issue["severity"], issue["code"], issue["message"], {"env_var": issue["env_var"]}))

    ping: dict[str, Any] = {}
    server_time: dict[str, Any] = {}
    account_snapshot: dict[str, Any] = {}
    positions: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    statements: list[dict[str, Any]] = []
    blocked_submit = False

    if guard.status == BrokerConnectivityStatus.blocked:
        issues.append(BrokerConnectivityIssue("warning", "network_guard_blocked", "network guard blocked probe", {"blocked_reason": guard.blocked_reason}))
        status = BrokerConnectivityStatus.blocked
    elif not ok_credentials:
        status = BrokerConnectivityStatus.failed
    else:
        try:
            ping = _safe_dict(client.ping())
            server_time = _safe_dict(client.get_server_time())
            account_snapshot = _safe_dict(client.get_account_snapshot())
            positions = _safe_list(client.list_positions(trade_date, as_of_date))
            orders = _safe_list(client.list_orders(trade_date, as_of_date))
            fills = _safe_list(client.list_fills(trade_date, as_of_date))
            statements = _safe_list(client.list_statements(trade_date, as_of_date))
            try:
                client.submit_orders([])
            except BrokerConnectivityBlockedError:
                blocked_submit = True
            except AttributeError:
                blocked_submit = True
            if not blocked_submit:
                issues.append(BrokerConnectivityIssue("blocker", "prohibited_submit_not_blocked", "submit_orders was not blocked"))
            status = BrokerConnectivityStatus.passed if not [item for item in issues if item.severity in {"error", "blocker"}] else BrokerConnectivityStatus.failed
        except Exception as exc:  # noqa: BLE001 - probe reports structured errors
            issues.append(BrokerConnectivityIssue("error", "probe_exception", str(exc)))
            status = BrokerConnectivityStatus.failed

    finished = _utc_now()
    duration = time.perf_counter() - t0
    return BrokerConnectivityProbeResult(
        probe_id=f"broker_probe_{_utc_id(started)}",
        profile_id=profile.profile_id,
        profile_name=profile.profile_name,
        broker_name=profile.broker_name,
        connectivity_mode=profile.connectivity_mode,
        status=status,
        started_at=started,
        finished_at=finished,
        duration_seconds=float(duration),
        account_id=account_id or str(profile.metadata.get("account_id") or ""),
        trade_date=trade_date,
        as_of_date=as_of_date or trade_date,
        ping=ping,
        server_time=server_time,
        account_snapshot=account_snapshot,
        record_counts={
            "cash": 1 if account_snapshot else 0,
            "positions": len(positions),
            "orders": len(orders),
            "fills": len(fills),
            "statements": len(statements),
        },
        network_guard=guard.to_dict(),
        credential_summary={
            "credential_ref_count": len(refs),
            "present_count": sum(1 for ref in refs if ref.present),
            "missing_required_count": sum(1 for ref in refs if ref.required and not ref.present),
            "secret_values_stored": False,
            "secret_blocker_count": 0,
        },
        readonly_enforcement={
            "readonly_only": True,
            "prohibited_submit_blocked": blocked_submit or guard.status == BrokerConnectivityStatus.blocked,
            "prohibited_methods": list(profile.prohibited_methods),
        },
        issues=issues,
        metadata={
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "statements": statements,
            "real_submit_supported": False,
        },
    )


def _safe_dict(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, dict) else {}


def _safe_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    return []


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace("Z", "")

