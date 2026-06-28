from __future__ import annotations

import json

from incident_response import IncidentStatus, LocalIncidentStore, detect_incidents, write_incident_report


def test_incident_detection_is_idempotent_and_status_updates(tmp_path):
    report_path = tmp_path / "production_orchestrator_report.json"
    report_path.write_text(
        json.dumps(
            {
                "production_run_id": "prod_incident_test",
                "phase_runs": [{"phase": "generate_orders", "status": "failed", "error": "risk gate failed"}],
                "gate_results": [{"gate_id": "active_model", "status": "blocked", "reason": "missing active model"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = LocalIncidentStore(tmp_path / "incidents")

    first = detect_incidents(store, "prod_incident_test", "20240104", {"production_orchestrator_report_path": report_path})
    second = detect_incidents(store, "prod_incident_test", "20240104", {"production_orchestrator_report_path": report_path})

    assert len(first) == 2
    assert len(second) == 2
    assert len(store.list_incidents()) == 2
    incident = store.list_incidents()[0]
    updated = store.update_status(incident.incident_id, IncidentStatus.acknowledged, actor="tester", comment="reviewed")
    assert updated.status == IncidentStatus.acknowledged
    paths = write_incident_report(store, production_run_id="prod_incident_test", trade_date="20240104")
    payload = json.loads((tmp_path / "incidents" / "incident_report.json").read_text(encoding="utf-8"))
    assert paths["incident_report_path"].endswith("incident_report.json")
    assert payload["summary"]["incident_count"] == 2
