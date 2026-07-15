from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_055_b import run as module


def _config(tmp_path: Path) -> dict:
    return {
        "observation_seal": "seal.json",
        "task055a_simulation_bundle": "bundle.json",
        "inventory_manifest": "inventory.json",
        "request_plan_manifest": "plan.json",
        "evidence_overlay": "evidence",
        "valuation_overlay": "valuation",
        "valuation_preflight": "preflight.json",
        "physical_state_roots": {name: str(tmp_path / name) for name in module.PHYSICAL_STATE_NAMES},
        "output_root": str(tmp_path / "final"),
    }


def _patch(monkeypatch: pytest.MonkeyPatch, *, preflight_status: str = "blocked") -> None:
    monkeypatch.setattr(module, "validate_observation_boundary_seal", lambda *a, **k: {"content_hash": "s" * 64})
    monkeypatch.setattr(module, "validate_simulation_bundle", lambda *a, **k: {"content_hash": "b" * 64})
    monkeypatch.setattr(module, "validate_gap_inventory", lambda *a, **k: {
        "content_hash": "i" * 64, "cell_count": 2, "episode_count": 1, "first_blocker_count": 100,
        "first_blocker_semantics": "censored_first_failure_samples_not_inventory_total",
        "state_counts": {"DATA_SOURCE_GAP": 2}, "probe_results": [], "readiness": {},
    })
    monkeypatch.setattr(module, "validate_request_plan", lambda *a, **k: {
        "content_hash": "p" * 64, "gap_cell_count": 2, "request_count": 4,
        "unique_gap_dates": ["20240102"], "affected_ts_codes": ["000001.SZ"], "max_network_requests": 10,
    })
    monkeypatch.setattr(module, "validate_evidence_overlay", lambda *a, **k: {
        "content_hash": "e" * 64, "record_count": 2, "state_counts": {"DATA_SOURCE_GAP": 2}, "review_version": "v1",
    })
    monkeypatch.setattr(module, "validate_valuation_overlay", lambda *a, **k: {
        "content_hash": "v" * 64, "evidence_content_hash": "e" * 64, "record_count": 4,
        "state_counts": {"UNRESOLVED": 4},
    })
    monkeypatch.setattr(module, "validate_preflight_report", lambda *a, **k: {
        "content_hash": "f" * 64, "status": preflight_status, "evidence_content_hash": "e" * 64,
        "valuation_content_hash": "v" * 64,
        "readiness": {"factor_replay_ready": True, "continuous_portfolio_valuation_ready": False, "future_research_data_ready": False},
        "metrics": {"unresolved": 4},
    })
    monkeypatch.setattr(module, "inspect_physical_states", lambda roots: {
        name: {"record_count": 0, "path": roots[name]} for name in module.PHYSICAL_STATE_NAMES
    })


def test_runner_publishes_blocked_native_evidence_and_keeps_queues_zero(tmp_path, monkeypatch):
    _patch(monkeypatch)
    result = module.run_task055b(_config(tmp_path))
    assert result["status"] == module.BLOCKED_STATUS
    assert result["readiness"]["factor_replay_ready"] is True
    assert all(value == 0 for value in result["queues"].values())
    assert {row["code"] for row in result["blockers"]} >= {
        "security_date_evidence_unresolved", "valuation_reporting_points_unresolved",
        "governed_backfill_requests_remaining", "simulation_replay_not_started_preflight_blocked",
    }
    assert Path(result["manifest_path"]).is_file()
    assert module.validate_task055b_final_report(result["manifest_path"])["content_hash"] == result["content_hash"]


def test_runner_rejects_request_plan_inventory_drift(tmp_path, monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(module, "validate_request_plan", lambda *a, **k: {"gap_cell_count": 1})
    with pytest.raises(module.Task055BOrchestrationError, match="request_plan_inventory_count_mismatch"):
        module.run_task055b(_config(tmp_path))


def test_runner_rejects_forged_replay_before_closure(tmp_path, monkeypatch):
    _patch(monkeypatch)
    config = _config(tmp_path)
    config["simulation_replay_evidence"] = {
        "primary_terminal_count": 100,
        "sibling_terminal_count": 100,
        "resume_hit_count": 100,
        "truth_hash_match": True,
        "independent_verifier_passed": True,
    }
    with pytest.raises(module.Task055BOrchestrationError, match="injected_simulation_replay_evidence_forbidden"):
        module.run_task055b(config)


def test_final_verifier_rejects_tampered_summary(tmp_path, monkeypatch):
    _patch(monkeypatch)
    result = module.run_task055b(_config(tmp_path))
    path = Path(result["manifest_path"])
    payload = json.loads(path.read_text())
    payload["queues"]["certification_queue"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.Task055BOrchestrationError, match="content_hash_mismatch"):
        module.validate_task055b_final_report(path)
