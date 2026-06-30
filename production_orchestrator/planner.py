"""Production phase plan builder."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .models import ProductionPhase, ProductionRunMode, ProductionRunPlan


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def make_production_run_id(trade_date: str, run_mode: str, environment: str = "paper") -> str:
    digest = hashlib.sha256(f"{trade_date}|{run_mode}|{environment}".encode()).hexdigest()[:12]
    return f"prod_{trade_date}_{run_mode}_{digest}"


def build_production_plan(
    trade_date: str,
    as_of_date: str,
    run_mode: str,
    environment: str = "paper",
    production_run_id: str | None = None,
) -> ProductionRunPlan:
    run_id = production_run_id or make_production_run_id(trade_date, run_mode, environment)
    phases = [
        ProductionPhase.plan_day,
        ProductionPhase.validate_data_freeze,
        ProductionPhase.validate_market_calendar,
        ProductionPhase.validate_active_model,
        ProductionPhase.validate_active_optimizer_policy,
        ProductionPhase.validate_certification,
        ProductionPhase.validate_account_state,
        ProductionPhase.validate_risk_state,
    ]
    if run_mode == ProductionRunMode.shadow_only:
        phases.extend(
            [
                ProductionPhase.generate_orders,
                ProductionPhase.pre_trade_risk_gate,
                ProductionPhase.create_order_approval,
                ProductionPhase.shadow_execute,
                ProductionPhase.monitoring,
                ProductionPhase.close_day,
                ProductionPhase.publish_report,
            ]
        )
    elif run_mode == ProductionRunMode.paper_simulated:
        phases.extend(
            [
                ProductionPhase.apply_corporate_actions,
                ProductionPhase.settle_before_trading,
                ProductionPhase.generate_orders,
                ProductionPhase.pre_trade_risk_gate,
                ProductionPhase.create_order_approval,
                ProductionPhase.wait_for_approval,
                ProductionPhase.execute_approved,
                ProductionPhase.settle_after_execution,
                ProductionPhase.import_broker_statement,
                ProductionPhase.eod_reconciliation,
                ProductionPhase.monitoring,
                ProductionPhase.close_day,
                ProductionPhase.publish_report,
            ]
        )
    elif run_mode == ProductionRunMode.file_outbox:
        phases.extend(
            [
                ProductionPhase.generate_orders,
                ProductionPhase.pre_trade_risk_gate,
                ProductionPhase.create_order_approval,
                ProductionPhase.wait_for_approval,
                ProductionPhase.mapping_certification_check,
                ProductionPhase.export_broker_files,
                ProductionPhase.create_operator_handoff,
                ProductionPhase.wait_handoff_approval,
                ProductionPhase.import_broker_file_inbox,
                ProductionPhase.broker_file_roundtrip_check,
                ProductionPhase.publish_report,
            ]
        )
    else:
        phases.extend([ProductionPhase.monitoring, ProductionPhase.publish_report])
    return ProductionRunPlan(run_id, trade_date, as_of_date, run_mode, environment, phases, utc_now(), metadata={"phase_count": len(phases)})
