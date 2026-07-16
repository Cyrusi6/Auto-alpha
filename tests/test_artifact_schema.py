import json

from artifact_schema.manifest import build_artifact_manifest, write_artifact_manifest
from artifact_schema.run_validate import main as validate_main
from artifact_schema.validator import validate_artifact
from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


def test_json_artifact_strict_and_legacy_validation(tmp_path):
    path = tmp_path / "capacity_report.json"
    write_json_artifact(
        path,
        {"trade_date": "20240104", "config": {}, "portfolio": {}},
        artifact_type="capacity_report",
        producer="test",
    )

    result = validate_artifact(path, strict=True)

    assert result.valid is True
    assert result.compatibility_mode == "strict"
    assert not [issue for issue in result.issues if issue.severity == "error"]

    legacy = tmp_path / "legacy" / "capacity_report.json"
    legacy.parent.mkdir()
    legacy.write_text('{"trade_date":"20240104","config":{},"portfolio":{}}', encoding="utf-8")
    legacy_result = validate_artifact(legacy)

    assert legacy_result.valid is True
    assert legacy_result.compatibility_mode == "legacy"
    assert any(issue.code == "legacy_artifact" for issue in legacy_result.issues)


def test_filename_inferred_versioned_json_is_strict(tmp_path):
    artifact = tmp_path / "task054c_cpu_preflight.json"
    artifact.write_text(
        '{"schema_version":"task054c_cpu_preflight_v1","all_materialized":true,'
        '"candidate_count":20,"candidate_root":"root","content_hash":"hash",'
        '"isolated_from_gpu_cache":true,"seal_hash":"seal","source_preflight_sha256":"source"}',
        encoding="utf-8",
    )

    result = validate_artifact(artifact, strict=True)

    assert result.valid is True
    assert result.compatibility_mode == "strict"
    assert not any(issue.code == "legacy_artifact" for issue in result.issues)


def test_task055a_native_manifest_schemas(tmp_path):
    seal = tmp_path / "task055a_observation_boundary_seal.json"
    seal.write_text(
        json.dumps(
            {
                "schema_version": "task055a_observation_boundary_seal_v1",
                "status": "sealed_waiting_for_future_data",
                "effective_at": "2026-07-15T00:00:00+08:00",
                "effective_timezone": "Asia/Shanghai",
                "observation": {},
                "prospective_holdout": {},
                "contaminated_period": {},
                "append_only": True,
                "content_hash": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    bundle = tmp_path / "simulation_bundle_manifest.json"
    bundle.write_text(
        json.dumps(
            {
                "schema_version": "task055a_simulation_bundle_v1",
                "status": "blocked",
                "signal_cutoff": "20240528",
                "execution_cutoff": "20240530",
                "valuation_cutoff": "20240530",
                "physical_signal_view": True,
                "fallback_allowed": False,
                "exact20_ids": [],
                "axes": {},
                "source_identity": {},
                "artifacts": {},
                "blockers": [{"code": "fixture"}],
                "generation_id": "simulation_bundle_fixture",
                "content_hash": "b" * 64,
            }
        ),
        encoding="utf-8",
    )

    assert validate_artifact(seal, strict=True).valid is True
    assert validate_artifact(bundle, strict=True).valid is True


def test_jsonl_sidecar_manifest_and_malformed_errors(tmp_path):
    orders_path = tmp_path / "orders.jsonl"
    write_jsonl_artifact(
        orders_path,
        [{"trade_date": "20240104", "ts_code": "000001.SZ", "side": "BUY"}],
        artifact_type="orders",
        producer="test",
    )

    result = validate_artifact(orders_path, strict=True)
    manifest = build_artifact_manifest([orders_path], root_dir=tmp_path)
    manifest_json, manifest_md = write_artifact_manifest(manifest, tmp_path / "manifest")

    assert result.valid is True
    assert (tmp_path / "orders.jsonl.schema.json").exists()
    assert manifest.entries[0].record_count == 1
    assert manifest.entries[0].sha256
    assert manifest_json.exists()
    assert manifest_md.exists()

    bad_json = tmp_path / "capacity_report.json"
    bad_json.write_text("{", encoding="utf-8")
    bad_result = validate_artifact(bad_json)
    assert bad_result.valid is False
    assert any(issue.code == "malformed_json" for issue in bad_result.issues)

    bad_jsonl = tmp_path / "paper_fills.jsonl"
    bad_jsonl.write_text("{not-json}\n", encoding="utf-8")
    bad_jsonl_result = validate_artifact(bad_jsonl)
    assert bad_jsonl_result.valid is False
    assert any(issue.code == "malformed_jsonl" for issue in bad_jsonl_result.issues)


def test_run_validate_cli_scans_dirs_and_catalog(tmp_path, capsys):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    write_json_artifact(
        artifact_dir / "monitoring_report.json",
        {"as_of_date": "20240104", "checks": {}, "alerts": []},
        artifact_type="monitoring_report",
        producer="test",
    )
    catalog_path = artifact_dir / "artifact_catalog.json"
    write_json_artifact(
        catalog_path,
        {
            "suite_name": "test_suite",
            "created_at": "2026-06-28T00:00:00Z",
            "entries": [{"name": "monitoring", "path": str(artifact_dir / "monitoring_report.json"), "kind": "json", "stage": "test"}],
        },
        artifact_type="artifact_catalog",
        producer="test",
    )

    exit_code = validate_main(
        [
            "--artifact-dir",
            str(artifact_dir),
            "--artifact-catalog-path",
            str(catalog_path),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["artifact_count"] >= 2
    assert (tmp_path / "schema" / "artifact_validation_report.json").exists()
    assert (tmp_path / "schema" / "artifact_schema_manifest.json").exists()


def test_settlement_artifacts_are_registered(tmp_path):
    settlement_report = tmp_path / "settlement_report.json"
    write_json_artifact(
        settlement_report,
        {
            "account_id": "paper_ashare",
            "as_of_date": "20240104",
            "settlement_aware": True,
            "settlement_profile": "cn_ashare_paper_default",
            "pending_settlement_event_count": 0,
            "failed_settlement_event_count": 0,
            "cash_buckets": {},
            "position_count": 0,
            "position_lot_count": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "nav_difference": 0.0,
            "fee_tax_total": 0.0,
            "reconciliation_error_count": 0,
        },
        artifact_type="settlement_report",
        producer="test",
    )
    settlement_events = tmp_path / "settlement_events.jsonl"
    write_jsonl_artifact(
        settlement_events,
        [{"settlement_event_id": "se_1", "account_id": "paper_ashare", "status": "pending", "event_type": "trade_buy_cash"}],
        artifact_type="settlement_events",
        producer="test",
    )

    assert validate_artifact(settlement_report, strict=True).valid is True
    assert validate_artifact(settlement_events, strict=True).valid is True
    manifest = build_artifact_manifest([settlement_report, settlement_events], root_dir=tmp_path)
    assert {entry.artifact_type for entry in manifest.entries} == {"settlement_report", "settlement_events"}


def test_broker_statement_and_eod_artifacts_are_registered(tmp_path):
    statement_report = tmp_path / "broker_statement_import_report.json"
    write_json_artifact(
        statement_report,
            {
                "statement_id": "stmt_1",
                "status": "ok",
                "record_counts": {"cash": 1},
                "paths": {"broker_statement_manifest_path": "broker_statement_manifest.json"},
            },
            artifact_type="broker_statement_import_report",
            producer="test",
    )
    eod_report = tmp_path / "eod_reconciliation_report.json"
    write_json_artifact(
        eod_report,
        {"statement_id": "stmt_1", "status": "ok", "summary": {"break_count": 0}},
        artifact_type="eod_reconciliation_report",
        producer="test",
    )
    breaks = tmp_path / "reconciliation_breaks.jsonl"
    write_jsonl_artifact(
        breaks,
        [{"break_id": "brk_1", "break_type": "cash_balance_mismatch", "severity": "error"}],
        artifact_type="reconciliation_breaks",
        producer="test",
    )
    proposals = tmp_path / "adjustment_proposals.jsonl"
    write_jsonl_artifact(
        proposals,
        [{"adjustment_id": "adj_1", "adjustment_type": "cash_manual_adjustment"}],
        artifact_type="adjustment_proposals",
        producer="test",
    )

    assert validate_artifact(statement_report, strict=True).valid is True
    assert validate_artifact(eod_report, strict=True).valid is True
    assert validate_artifact(breaks, strict=True).valid is True
    assert validate_artifact(proposals, strict=True).valid is True
    manifest = build_artifact_manifest([statement_report, eod_report, breaks, proposals], root_dir=tmp_path)
    assert {"broker_statement_import_report", "eod_reconciliation_report", "reconciliation_breaks", "adjustment_proposals"} <= {
        entry.artifact_type for entry in manifest.entries
    }


def test_compute_and_experiment_artifacts_are_registered(tmp_path):
    compute_report = tmp_path / "compute_run_report.json"
    write_json_artifact(
        compute_report,
        {"run_id": "run_1", "status": "success", "job_count": 1},
        artifact_type="compute_run_report",
        producer="test",
    )
    compute_jobs = tmp_path / "compute_jobs.jsonl"
    write_jsonl_artifact(
        compute_jobs,
        [{"job_id": "job_1", "job_kind": "shell_command"}],
        artifact_type="compute_jobs",
        producer="test",
    )
    experiment_plan = tmp_path / "experiment_plan.json"
    write_json_artifact(
        experiment_plan,
        {"experiment_id": "exp_1", "workflow": "full_research_compute_smoke", "compute_jobs": []},
        artifact_type="experiment_plan",
        producer="test",
    )
    experiment_report = tmp_path / "experiment_run_report.json"
    write_json_artifact(
        experiment_report,
        {"experiment_id": "exp_1", "workflow": "full_research_compute_smoke", "status": "success"},
        artifact_type="experiment_run_report",
        producer="test",
    )

    for path in [compute_report, compute_jobs, experiment_plan, experiment_report]:
        assert validate_artifact(path, strict=True).valid is True
    manifest = build_artifact_manifest([compute_report, compute_jobs, experiment_plan, experiment_report], root_dir=tmp_path)
    assert {"compute_run_report", "compute_jobs", "experiment_plan", "experiment_run_report"} <= {
        entry.artifact_type for entry in manifest.entries
    }


def test_task055b_native_manifest_schemas(tmp_path):
    inventory = tmp_path / "inventory_manifest.json"
    inventory.write_text(json.dumps({
        "schema_version": "task055b_security_date_gap_inventory_v1", "status": "blocked",
        "content_hash": "a" * 64, "generation_id": "inventory", "cell_count": 1,
        "episode_count": 1, "first_blocker_count": 1,
        "first_blocker_semantics": "censored_first_failure_samples_not_inventory_total",
        "state_counts": {"DATA_SOURCE_GAP": 1}, "probe_results": [], "readiness": {}, "partitions": {},
    }), encoding="utf-8")
    preflight = tmp_path / "valuation_closure_preflight.json"
    preflight.write_text(json.dumps({
        "schema_version": "task055b_valuation_closure_preflight_v1", "status": "blocked",
        "evidence_content_hash": "b" * 64, "valuation_content_hash": "c" * 64,
        "readiness": {}, "metrics": {}, "blockers": [], "policy": {}, "content_hash": "d" * 64,
    }), encoding="utf-8")
    report = tmp_path / "task055b_final_report.json"
    report.write_text(json.dumps({
        "schema_version": "task055b_final_report_v1", "status": "task055b_security_date_evidence_remediation_blocked",
        "historical_selection_contaminated": True, "execution_evidence_level": "modeled_daily_bar_proxy",
        "prospective_holdout_opened": False, "inventory": {}, "request_plan": {}, "network_execution": {},
        "evidence_overlay": {}, "valuation_overlay": {}, "valuation_preflight": {}, "readiness": {},
        "physical_state_inventory": {}, "queues": {}, "blockers": [], "certification_blockers": [],
        "content_hash": "e" * 64, "generation_id": "result",
    }), encoding="utf-8")
    assert validate_artifact(inventory, strict=True).valid is True
    assert validate_artifact(preflight, strict=True).valid is True
    assert validate_artifact(report, strict=True).valid is True


def test_task055e_offline_native_manifest_schemas(tmp_path):
    provenance = tmp_path / "provenance_manifest.json"
    provenance.write_text(json.dumps({
        "schema_version": "task055e_row_provenance_index_v1", "status": "published",
        "network_accessed": False, "prospective_holdout_accessed": False,
        "max_allowed_date": "20260630", "target_key_count": 1, "target_key_hash": "a", "builder_code_hash": "c",
        "provenance_record_count": 1, "classification_counts": {}, "offline_raw_repair_count": 0,
        "source_summaries": {}, "partitions": {}, "content_hash": "b", "generation_id": "g",
    }), encoding="utf-8")
    domains = tmp_path / "domain_manifest.json"
    domains.write_text(json.dumps({
        "schema_version": "task055e_valuation_domains_v1", "status": "published",
        "network_accessed": False, "prospective_holdout_accessed": False, "lineage": {},
        "anchor_count": 0, "anchor_cause_counts": {}, "causal_terminal_counts": {},
        "causal_remaining_security_dates": 0, "partitions": {}, "content_hash": "b", "generation_id": "g",
    }), encoding="utf-8")
    report = tmp_path / "task055e_offline_report.json"
    report.write_text(json.dumps({
        "schema_version": "task055e_offline_source_salvage_report_v1",
        "status": "task055e_governed_acquisition_or_dynamic_simulation_closure_blocked",
        "offline_stage_status": "offline_source_salvage_completed", "network_accessed": False,
        "network_request_count": 0, "credential_required": False, "prospective_holdout_accessed": False,
        "max_read_or_request_date": "20260630", "git": {}, "observation_boundary": {}, "lineage": {},
        "target_summary": {}, "classification_counts": {}, "offline_raw_repair_count": 0,
        "anchor_count": 0, "anchor_cause_counts": {}, "valuation_domains": {},
        "minimal_network_plan": {}, "artifacts": {}, "readiness": {},
        "simulator_success_evidence_created": False, "blockers": [], "content_hash": "b", "generation_id": "g",
    }), encoding="utf-8")
    assert validate_artifact(provenance, strict=True).valid is True
    assert validate_artifact(domains, strict=True).valid is True
    assert validate_artifact(report, strict=True).valid is True
