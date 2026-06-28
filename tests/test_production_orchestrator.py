from __future__ import annotations

import json

from production_orchestrator import ProductionOrchestratorConfig, ProductionOrchestratorRunner


def _config(tmp_path, **overrides):
    payload = {
        "production_state_dir": str(tmp_path / "state"),
        "output_dir": str(tmp_path / "production"),
        "run_mode": "shadow_only",
        "trade_date": "20240104",
        "as_of_date": "20240104",
        "data_dir": str(tmp_path / "data"),
        "factor_store_dir": str(tmp_path / "store"),
        "approval_store_dir": str(tmp_path / "approvals"),
        "paper_account_dir": str(tmp_path / "account"),
        "orders_dir": str(tmp_path / "orders"),
        "incident_store_dir": str(tmp_path / "incidents"),
    }
    payload.update(overrides)
    return ProductionOrchestratorConfig(**payload)


def test_plan_day_writes_shadow_phase_plan(tmp_path):
    runner = ProductionOrchestratorRunner(_config(tmp_path))
    result = runner.plan_day()

    assert result["status"] == "planned"
    plan_path = tmp_path / "production" / "production_run_plan.json"
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert "shadow_execute" in plan["phases"]
    assert "execute_approved" not in plan["phases"]
    assert (tmp_path / "production" / "production_orchestrator_report.json").exists()


def test_blocked_active_model_gate_creates_incident(tmp_path):
    runner = ProductionOrchestratorRunner(
        _config(
            tmp_path,
            model_registry_dir=str(tmp_path / "model_registry"),
            require_active_model=True,
        )
    )

    result = runner.run_day()

    assert result["status"] == "blocked"
    incidents_path = tmp_path / "incidents" / "incident_records.jsonl"
    assert incidents_path.exists()
    incidents = [json.loads(line) for line in incidents_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(item["code"] == "active_model" for item in incidents)
    assert (tmp_path / "production" / "production_readiness_report.json").exists()
