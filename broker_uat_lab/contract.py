"""BrokerAdapter contract checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from broker_adapter import BrokerOrderRequest, BrokerOrderStatus

from .models import (
    BrokerAdapterCapabilityManifest,
    BrokerAdapterContractReport,
    BrokerUatResult,
    BrokerUatScenario,
    BrokerUatScenarioType,
    BrokerUatStatus,
)


def run_broker_adapter_contract_suite(adapter, scenarios: list[BrokerUatScenario], *, adapter_name: str = "mock") -> BrokerAdapterContractReport:
    results: list[BrokerUatResult] = []
    for scenario in scenarios:
        if not scenario.enabled:
            results.append(BrokerUatResult(scenario.scenario_id, scenario.scenario_type, BrokerUatStatus.skipped, "scenario disabled"))
            continue
        if hasattr(adapter, "set_scenario"):
            adapter.set_scenario(_adapter_scenario(scenario.scenario_type))
        result = _run_scenario(adapter, scenario)
        results.append(result)
    failed = sum(1 for item in results if item.status == BrokerUatStatus.failed)
    warnings = sum(1 for item in results if item.status == BrokerUatStatus.warning)
    skipped = sum(1 for item in results if item.status == BrokerUatStatus.skipped)
    passed = sum(1 for item in results if item.status == BrokerUatStatus.passed)
    manifest = BrokerAdapterCapabilityManifest(
        adapter_name=adapter_name,
        supports_submit=hasattr(adapter, "submit_orders"),
        supports_cancel=hasattr(adapter, "cancel_order"),
        supports_replace=hasattr(adapter, "replace_order"),
        supports_status_poll=hasattr(adapter, "get_order"),
        supports_fills=hasattr(adapter, "list_fills"),
        supports_file_outbox=adapter_name == "file",
        supports_statement_import=False,
        supports_idempotency=True,
        supports_kill_switch_block=True,
        supports_readonly_connectivity=any(result.scenario_type == BrokerUatScenarioType.readonly_connectivity and result.status == BrokerUatStatus.passed for result in results),
        supports_readonly_account_snapshot=any(result.scenario_type == BrokerUatScenarioType.readonly_connectivity and result.status == BrokerUatStatus.passed for result in results),
        supports_readonly_mirror=any(result.scenario_type == BrokerUatScenarioType.readonly_mirror_reconciliation and result.status == BrokerUatStatus.passed for result in results),
        prohibits_real_submit_in_uat=True,
        real_network_required=False,
        real_broker_credentials_required=False,
    )
    return BrokerAdapterContractReport(
        adapter_name=adapter_name,
        status=BrokerUatStatus.failed if failed else BrokerUatStatus.warning if warnings else BrokerUatStatus.passed,
        scenario_count=len(results),
        passed_count=passed,
        failed_count=failed,
        warning_count=warnings,
        skipped_count=skipped,
        capability_manifest=manifest.to_dict(),
        results=[result.to_dict() for result in results],
        issues=[issue for result in results for issue in result.issues],
    )


def _run_scenario(adapter, scenario: BrokerUatScenario) -> BrokerUatResult:
    try:
        if scenario.scenario_type in {
            BrokerUatScenarioType.readonly_connectivity,
            BrokerUatScenarioType.credential_redaction,
            BrokerUatScenarioType.network_guard,
            BrokerUatScenarioType.readonly_mirror_reconciliation,
        }:
            return _run_readonly_scenario(scenario)
        if not hasattr(adapter, "set_scenario") and scenario.scenario_type in {
            BrokerUatScenarioType.partial_fill,
            BrokerUatScenarioType.reject_order,
            BrokerUatScenarioType.duplicate_fill,
            BrokerUatScenarioType.out_of_order_fill,
            BrokerUatScenarioType.missing_ack,
            BrokerUatScenarioType.rate_limit,
            BrokerUatScenarioType.kill_switch_block,
            BrokerUatScenarioType.eod_reconciliation,
            BrokerUatScenarioType.settlement_reconciliation,
        }:
            return BrokerUatResult(
                scenario.scenario_id,
                scenario.scenario_type,
                BrokerUatStatus.warning,
                "scenario requires deterministic fault injection; skipped for this adapter instance",
            )
        request = _request(scenario.scenario_id)
        if scenario.scenario_type == BrokerUatScenarioType.submit_idempotency:
            first = adapter.submit_orders([request], batch_id=request.batch_id)
            second = adapter.submit_orders([request], batch_id=request.batch_id)
            ok = first.orders and second.orders and first.orders[0].broker_order_id == second.orders[0].broker_order_id
            return _result(scenario, ok, "idempotent submit replay", {"idempotent_replay_count": second.idempotent_replay_count})
        if scenario.scenario_type == BrokerUatScenarioType.cancel_order:
            result = adapter.submit_orders([request], batch_id=request.batch_id)
            order = result.orders[0]
            if order.status == BrokerOrderStatus.CANCELLED:
                return BrokerUatResult(scenario.scenario_id, scenario.scenario_type, BrokerUatStatus.passed, "cancel already processed", {"status": order.status})
            if order.status in {BrokerOrderStatus.FILLED, BrokerOrderStatus.REJECTED}:
                return BrokerUatResult(scenario.scenario_id, scenario.scenario_type, BrokerUatStatus.warning, "order terminal before cancel", {"status": order.status})
            cancelled = adapter.cancel_order(order.broker_order_id, "uat_cancel")
            return _result(scenario, cancelled.status == BrokerOrderStatus.CANCELLED, "cancel processed", {"status": cancelled.status})
        if scenario.scenario_type == BrokerUatScenarioType.replace_order:
            result = adapter.submit_orders([request], batch_id=request.batch_id)
            order = result.orders[0]
            if order.status == BrokerOrderStatus.REPLACED and order.replace_count >= 1:
                return BrokerUatResult(
                    scenario.scenario_id,
                    scenario.scenario_type,
                    BrokerUatStatus.passed,
                    "replace already processed",
                    {"status": order.status, "replace_count": order.replace_count},
                )
            if order.status in {BrokerOrderStatus.FILLED, BrokerOrderStatus.REJECTED}:
                return BrokerUatResult(scenario.scenario_id, scenario.scenario_type, BrokerUatStatus.warning, "order terminal before replace", {"status": order.status})
            replaced = adapter.replace_order(order.broker_order_id, shares=200, reason="uat_replace")
            return _result(scenario, replaced.replace_count >= 1, "replace processed", {"status": replaced.status, "replace_count": replaced.replace_count})
        if scenario.scenario_type == BrokerUatScenarioType.rate_limit:
            try:
                adapter.submit_orders([request], batch_id=request.batch_id)
            except Exception as exc:
                return BrokerUatResult(scenario.scenario_id, scenario.scenario_type, BrokerUatStatus.warning, f"structured exception observed: {exc}")
        result = adapter.submit_orders([request], batch_id=request.batch_id)
        observed = {
            "orders": len(result.orders),
            "fills": len(result.fills),
            "statuses": [order.status for order in result.orders],
            "fill_statuses": [fill.status for fill in result.fills],
        }
        if scenario.scenario_type in {BrokerUatScenarioType.reject_order, BrokerUatScenarioType.kill_switch_block}:
            return _result(scenario, any(order.status == BrokerOrderStatus.REJECTED for order in result.orders), "reject path observed", observed)
        if scenario.scenario_type == BrokerUatScenarioType.partial_fill:
            return _result(scenario, any(order.status == BrokerOrderStatus.PARTIAL_FILLED for order in result.orders), "partial path observed", observed)
        if scenario.scenario_type == BrokerUatScenarioType.missing_ack:
            return BrokerUatResult(scenario.scenario_id, scenario.scenario_type, BrokerUatStatus.warning, "missing ack scenario produced open order", observed)
        return _result(scenario, bool(result.orders), "scenario executed", observed)
    except Exception as exc:
        expected_warning = scenario.expected_status == BrokerUatStatus.warning
        return BrokerUatResult(
            scenario.scenario_id,
            scenario.scenario_type,
            BrokerUatStatus.warning if expected_warning else BrokerUatStatus.failed,
            str(exc),
            issues=[{"severity": "warning" if expected_warning else "error", "code": "scenario_exception", "message": str(exc)}],
        )


def _result(scenario: BrokerUatScenario, ok: bool, message: str, observed: dict[str, Any]) -> BrokerUatResult:
    status = BrokerUatStatus.passed if ok else BrokerUatStatus.failed
    return BrokerUatResult(scenario.scenario_id, scenario.scenario_type, status, message, observed, [] if ok else [{"severity": "error", "code": "unexpected_outcome", "message": message}])


def _run_readonly_scenario(scenario: BrokerUatScenario) -> BrokerUatResult:
    metadata = dict(scenario.metadata or {})
    if scenario.scenario_type == BrokerUatScenarioType.readonly_connectivity:
        report = _read_json(metadata.get("broker_connectivity_report_path"))
        summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
        ok = bool(report) and str(report.get("status") or "") in {"passed", "warning"} and bool(summary.get("readonly_only")) and not bool(summary.get("real_submit_supported"))
        return _result(scenario, ok, "read-only connectivity evidence checked", {"status": report.get("status"), "summary": summary})
    if scenario.scenario_type == BrokerUatScenarioType.credential_redaction:
        manifest = _read_json(metadata.get("broker_credential_ref_manifest_path"))
        summary = manifest.get("summary", {}) if isinstance(manifest.get("summary"), dict) else {}
        ok = bool(manifest) and not bool(manifest.get("secret_values_stored")) and int(summary.get("secret_blocker_count", 0) or 0) == 0
        return _result(scenario, ok, "credential references are redacted", {"summary": summary, "secret_values_stored": manifest.get("secret_values_stored")})
    if scenario.scenario_type == BrokerUatScenarioType.network_guard:
        guard = _read_json(metadata.get("broker_network_guard_report_path"))
        payload = guard.get("network_guard", guard) if isinstance(guard, dict) else {}
        ok = bool(guard) and bool(payload.get("readonly_only", False))
        return _result(scenario, ok, "network guard is read-only", {"network_guard": payload})
    mirror = _read_json(metadata.get("readonly_mirror_reconciliation_report_path"))
    real_submit_supported = bool(mirror.get("real_submit_supported", False))
    break_count = int(mirror.get("break_count", 0) or 0)
    if not mirror or real_submit_supported:
        return _result(
            scenario,
            False,
            "read-only mirror reconciliation checked",
            {"break_count": mirror.get("break_count"), "status": mirror.get("status"), "real_submit_supported": real_submit_supported},
        )
    if break_count:
        return BrokerUatResult(
            scenario.scenario_id,
            scenario.scenario_type,
            BrokerUatStatus.warning,
            "read-only mirror reconciliation has local mirror breaks",
            {"break_count": break_count, "status": mirror.get("status"), "real_submit_supported": real_submit_supported},
            [{"severity": "warning", "code": "readonly_mirror_breaks", "message": "read-only mirror breaks require review"}],
        )
    return _result(scenario, True, "read-only mirror reconciliation checked", {"break_count": break_count, "status": mirror.get("status")})


def _request(scenario_id: str) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=f"uat_client_{scenario_id}",
        batch_id=f"uat_batch_{scenario_id}",
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        shares=100,
        order_value=1000.0,
        price=10.0,
        parent_order_id=f"parent_{scenario_id}",
        child_order_id=f"child_{scenario_id}",
        bucket="open",
    )


def _adapter_scenario(scenario_type: str) -> str:
    mapping = {
        BrokerUatScenarioType.full_fill: "full_fill",
        BrokerUatScenarioType.submit_idempotency: "full_fill",
        BrokerUatScenarioType.cancel_order: "missing_ack",
        BrokerUatScenarioType.replace_order: "missing_ack",
        BrokerUatScenarioType.partial_fill: "partial_fill",
        BrokerUatScenarioType.reject_order: "reject_order",
        BrokerUatScenarioType.duplicate_fill: "duplicate_fill",
        BrokerUatScenarioType.out_of_order_fill: "out_of_order_fill",
        BrokerUatScenarioType.missing_ack: "missing_ack",
        BrokerUatScenarioType.reconnect_replay: "full_fill",
        BrokerUatScenarioType.rate_limit: "rate_limit",
        BrokerUatScenarioType.kill_switch_block: "kill_switch_block",
        BrokerUatScenarioType.file_outbox_roundtrip: "full_fill",
        BrokerUatScenarioType.eod_reconciliation: "full_fill",
        BrokerUatScenarioType.settlement_reconciliation: "full_fill",
    }
    return mapping.get(scenario_type, "full_fill")


def _read_json(path: Any) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(str(path))
    if not target.exists():
        return {}
    try:
        import json

        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
