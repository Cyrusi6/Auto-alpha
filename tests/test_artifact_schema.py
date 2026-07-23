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


def test_task055f_native_manifest_schemas(tmp_path):
    truth = tmp_path / "truth_v2_manifest.json"
    truth.write_text(json.dumps({
        "schema_version": "task055f_security_date_truth_v2", "status": "published",
        "record_count": 1, "key_root": "k", "state_counts": {}, "suspend_type_counts": {},
        "daily_empty_response_counts": [], "suspend_empty_response_counts": [],
        "valuation_domain_count": 1, "modeled_candidate_count": 0, "lineage": {},
        "partitions": {}, "content_hash": "c", "generation_id": "g",
    }), encoding="utf-8")
    projection = tmp_path / "valuation_projection_manifest.json"
    projection.write_text(json.dumps({
        "schema_version": "task055f_compact_valuation_projection_v1", "status": "blocked",
        "shape": [1, 1, 2], "dates": ["20240530"], "assets": ["000001.SZ"],
        "date_axis_hash": "d", "stock_axis_hash": "s", "truth_v2_content_hash": "t",
        "matrix_content_hash": "m", "builder_code_hash": "b", "method_codes": {},
        "unresolved_reporting_point_count": 1, "blocker_root": "r", "partitions": {},
        "content_hash": "c", "generation_id": "g",
    }), encoding="utf-8")
    report = tmp_path / "task055f_report.json"
    report.write_text(json.dumps({
        "schema_version": "task055f_engineering_report_v1",
        "status": "task055f_governed_evidence_or_fee_or_dynamic_simulation_closure_blocked",
        "stage": "offline_truth_hardening_completed", "network_accessed": False,
        "network_request_count": 0, "prospective_holdout_accessed": False,
        "max_read_date": "20260630", "git": {}, "observation_boundary": {},
        "parent_lineage": {}, "truth_v2": {}, "fee_schedule_v2": None,
        "causal_frontier": None, "credential": {}, "operational_state": {},
        "native_replay": None, "ready_for_canary": False, "artifacts": {},
        "readiness": {}, "engineering_blockers": [], "certification_blockers": [],
        "blockers": [], "content_hash": "c", "generation_id": "g",
    }), encoding="utf-8")
    assert validate_artifact(truth, strict=True).valid is True
    assert validate_artifact(projection, strict=True).valid is True
    assert validate_artifact(report, strict=True).valid is True


def test_task055g_native_manifest_schemas(tmp_path):
    root = tmp_path / "task_055_g_run"
    root.mkdir()
    artifacts = {
        "access_plan.json": ({
            "schema_version": "task055g_immutable_access_plan_v1", "status": "sealed",
            "plan_scope": "production", "max_allowed_date": "20260630", "entry_count": 0,
            "entries_root": "e", "entries": [], "content_hash": "c", "generation_id": "g",
        }, "task055g_access_plan"),
        "access_ledger_manifest.json": ({
            "schema_version": "task055g_attempted_access_ledger_v1", "status": "published",
            "access_plan_content_hash": "p", "max_allowed_date": "20260630", "record_count": 0,
            "rows_root": "r", "max_read_date": None, "prospective_holdout_accessed": False,
            "decision_counts": {}, "partition": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_access_ledger_manifest"),
        "truth_v2_manifest.json": ({
            "schema_version": "task055g_security_date_truth_v2", "status": "published",
            "review_version": "v1", "max_date": "20260630", "record_count": 1, "key_root": "k",
            "state_counts": {}, "suspend_type_counts": {}, "daily_empty_response_counts": [],
            "suspend_empty_response_counts": [], "valuation_domain_count": 1, "modeled_candidate_count": 0,
            "timing_uncertified_candidate_count": 0, "lineage": {}, "partitions": {},
            "certification_blockers": [], "content_hash": "c", "generation_id": "g",
        }, "task055g_truth_v2_manifest"),
        "fee_plan.json": ({
            "schema_version": "task055g_fee_plan_v2", "status": "sealed",
            "simulation_start": "20160104", "simulation_end": "20240530",
            "policy_seal_hash": "s", "policy_seal_sha256": "q",
            "policy_seal_relative_path": "inputs/policy.json", "documents": [],
            "extractors": [], "max_documents": 20, "network_contract": {},
            "semantic_source_hashes": {}, "builder_semantic_hash": "b",
            "content_hash": "c", "generation_id": "g",
        }, "task055g_fee_plan"),
        "fee_document_acquisition.json": ({
            "schema_version": "task055g_fee_document_acquisition_v2", "status": "passed",
            "evidence_scope": "real_official_https", "plan_content_hash": "p", "policy_seal_hash": "s",
            "documents": [], "transport_ledger_relative_path": "transport_ledger.jsonl",
            "transport_ledger_sha256": "t", "transport_ledger_root": "r", "document_merkle_root": "m",
            "source_hash": "h", "content_hash": "c", "generation_id": "g",
        }, "task055g_fee_document_acquisition"),
        "fee_document_verification.json": ({
            "schema_version": "task055g_fee_document_verification_v2", "status": "passed",
            "plan_content_hash": "p", "acquisition_content_hash": "a",
            "document_merkle_root": "m", "transport_ledger_root": "t",
            "evidence_scope": "real_official_https", "verifier_source_hash": "v",
            "content_hash": "c", "generation_id": "g",
        }, "task055g_fee_document_verification"),
        "fee_rule_extraction.json": ({
            "schema_version": "task055g_fee_rule_extraction_v2", "status": "passed",
            "plan_content_hash": "p", "acquisition_content_hash": "a",
            "document_verification_content_hash": "v", "policy_seal_hash": "s",
            "evidence_scope": "real_official_https", "parser_source_hash": "h",
            "assertions": [], "assertion_root": "r", "content_hash": "c",
            "generation_id": "g",
        }, "task055g_fee_rule_extraction"),
        "fee_schedule_v2_manifest.json": ({
            "schema_version": "task055g_fee_schedule_v2", "status": "passed",
            "evidence_scope": "real_official_https", "simulation_start": "20160104", "simulation_end": "20240530",
            "plan_content_hash": "p", "document_acquisition_content_hash": "a",
            "document_verification_content_hash": "v", "rule_extraction_content_hash": "x",
            "transport_ledger_root": "t", "document_merkle_root": "d", "assertion_root": "a",
            "policy_seal_hash": "s", "policy_seal_sha256": "q", "semantic_source_hashes": {},
            "builder_semantic_hash": "b", "statutory_components": [], "modeled_components": [],
            "modeled_evidence_level": "uncalibrated_modeled", "certification_ready": False,
            "rules": [], "rules_root": "r", "native_artifacts": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_fee_schedule_v2"),
        "fee_independent_verification.json": ({
            "schema_version": "task055g_fee_independent_verification_v2", "status": "passed",
            "schedule_content_hash": "s", "policy_seal_hash": "p",
            "document_acquisition_content_hash": "a", "document_merkle_root": "m",
            "transport_ledger_root": "t", "assertion_receipt_root": "x",
            "rules_root": "r", "rule_count": 0, "coverage": {},
            "certification_ready": False, "verifier_source_hash": "v",
            "content_hash": "c", "generation_id": "g",
        }, "task055g_fee_independent_verification"),
        "operational_seal.json": ({
            "schema_version": "task055g_authoritative_operational_seal_v1", "status": "passed",
            "writer_registry_content_hash": "w", "physical_scan_content_hash": "p", "genesis_content_hash": None,
            "state_counts": {}, "total_operational_record_count": 0, "blockers": [],
            "certification_ready": False, "portfolio_ready": False, "paper_ready": False,
            "live_ready": False, "immutable": True, "content_hash": "c", "generation_id": "g",
        }, "task055g_authoritative_operational_seal"),
        "causal_frontier_manifest.json": ({
            "schema_version": "task055g_fee_aware_causal_frontier_v1", "status": "blocked",
            "scope": "causal_held_position", "exact20_ids": [], "run_count": 0, "terminal_counts": {},
            "round_one_frontier_count": 0, "round_one_frontier_semantics": "first_blocker_only",
            "held_mark_count": 0, "authorized_modeled_held_mark_count": 0, "run_rows_root": "r",
            "held_mark_root": "h", "missing_key_root": "m", "lineage": {}, "valuation_projection": {},
            "partitions": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_causal_frontier"),
        "round_one_exact_daily_plan.json": ({
            "schema_version": "task055f_exact_frontier_network_plan_v1", "status": "sealed_round_one_daily_only",
            "frontier_root": "f", "requests": [], "plan_hash": "p",
        }, "task055g_network_plan"),
        "l2_plan_manifest.json": ({
            "schema_version": "task055g_dynamic_network_plan_v1", "status": "sealed_dynamic_exact_suspend_l2",
            "stage": "L2", "round_id": 1, "parent_apply_hash": "a", "lineage": {},
            "frontier_root": "f", "requests": [], "limits": {}, "plan_hash": "p",
            "content_hash": "c", "generation_id": "g",
        }, "task055g_network_plan"),
        "l1_canary_manifest.json": ({
            "schema_version": "task055g_request_execution_v1", "status": "canary_completed",
            "stage": "L1", "round_id": 1, "plan_hash": "p", "must_stop_after_canary": True,
            "batch_started": False, "attempts_recorded_in_ledger": True, "results": [],
            "ledger": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_network_execution"),
        "l1_resume_manifest.json": ({
            "schema_version": "task055g_request_execution_v1", "status": "resume_completed",
            "stage": "L1", "round_id": 1, "plan_hash": "p", "canary_content_hash": "a",
            "attempts_recorded_in_ledger": True, "results": [], "remaining_request_count": 0,
            "ledger": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_network_execution"),
        "l2_canary_manifest.json": ({
            "schema_version": "task055g_request_execution_v1", "status": "canary_completed",
            "stage": "L2", "round_id": 1, "plan_hash": "p", "must_stop_after_canary": True,
            "batch_started": False, "attempts_recorded_in_ledger": True, "results": [],
            "ledger": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_network_execution"),
        "l2_resume_manifest.json": ({
            "schema_version": "task055g_request_execution_v1", "status": "resume_completed",
            "stage": "L2", "round_id": 1, "plan_hash": "p", "canary_content_hash": "a",
            "attempts_recorded_in_ledger": True, "results": [], "remaining_request_count": 0,
            "ledger": {}, "content_hash": "c", "generation_id": "g",
        }, "task055g_network_execution"),
        "l1_apply_manifest.json": ({
            "schema_version": "task055g_response_apply_v1", "status": "applied",
            "stage": "L1", "round_id": 1, "plan_hash": "p", "plan": {},
            "parent_apply_hash": None, "lineage": {}, "result_count": 0, "results": [],
            "response_lineage_root": "r", "cache_inputs": [], "cache_input_root": "i",
            "application_actions": [], "next_truth_required_inputs": {}, "ledger": {},
            "content_hash": "c", "generation_id": "g",
        }, "task055g_network_apply"),
        "l2_apply_manifest.json": ({
            "schema_version": "task055g_response_apply_v1", "status": "applied",
            "stage": "L2", "round_id": 1, "plan_hash": "p", "plan": {},
            "parent_apply_hash": "a", "lineage": {}, "result_count": 0, "results": [],
            "response_lineage_root": "r", "cache_inputs": [], "cache_input_root": "i",
            "application_actions": [], "next_truth_required_inputs": {}, "ledger": {},
            "content_hash": "c", "generation_id": "g",
        }, "task055g_network_apply"),
        "network_state_verification.json": ({
            "schema_version": "task055g_network_state_verification_v1", "status": "verified",
            "network_accessed": False, "request_count": 0, "max_request_date": None,
            "logical_request_count": 0, "physical_attempt_count": 0,
            "unique_security_date_count": 0, "terminal_counts": {}, "ledger_root": "l",
            "artifact_count": 0, "artifact_root": "a", "applied_plan_count": 0,
            "offline_default_proven": True, "content_hash": "c", "generation_id": "g",
        }, "task055g_network_state_verification"),
        "semantic_verification.json": ({
            "schema_version": "task055g_independent_semantic_verification_v1", "status": "passed",
            "parent_lineage_content_hash": "p", "access_plan_content_hash": "a",
            "producer_truth_content_hash": "t", "truth": {}, "causal": {},
            "read_ledger_content_hash": "l", "read_ledger_manifest": "ledger.json",
            "max_read_date": "20260630", "prospective_holdout_accessed": False,
            "content_hash": "x", "generation_id": "g",
        }, "task055g_independent_semantic_verification"),
        "task055g_report.json": ({
            "schema_version": "task055g_engineering_report_v1",
            "status": "task055g_fee_aware_frontier_sealed_waiting_for_network_authorization",
            "stage": "round_one_plan_sealed", "network_accessed": False, "network_request_count": 0,
            "prospective_holdout_accessed": False, "max_read_date": "20260630", "parent_lineage": {},
            "access_plan": {}, "read_ledger": {}, "truth_v2": {}, "fee_schedule_v2": {},
            "operational_state": {}, "causal_frontier": {}, "network_plan": {}, "semantic_verification": {},
            "readiness": {"certification_ready": False, "portfolio_ready": False, "paper_ready": False, "live_ready": False},
            "queues": {}, "engineering_blockers": [], "certification_blockers": [], "blockers": [],
            "content_hash": "c", "generation_id": "g",
        }, "task055g_final_report"),
        "task055g_final_verification.json": ({
            "schema_version": "task055g_independent_final_verification_v1",
            "status": "verified_waiting_for_network_authorization",
            "top_status": "task055g_fee_aware_frontier_sealed_waiting_for_network_authorization",
            "report_content_hash": "r", "validated_artifacts": {}, "missing_artifacts": [],
            "engineering_blocker_stages": [], "access_plan_content_hash": "a",
            "access_ledger_content_hash": "l", "truth_content_hash": "t",
            "fee_content_hash": "f", "fee_independent_verification_content_hash": "fi",
            "operational_content_hash": "o", "causal_content_hash": "c",
            "semantic_verification_content_hash": "s",
            "network_state_verification_content_hash": "n", "frontier_count": 1,
            "frontier_root": "fr", "network_physical_attempt_count": 0,
            "prospective_holdout_accessed": False,
            "operational_queues_verified_empty": True, "content_hash": "h",
            "generation_id": "g",
        }, "task055g_independent_final_verification"),
    }
    for filename, (payload, artifact_type) in artifacts.items():
        path = root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = validate_artifact(path, strict=True)
        assert result.valid is True
        assert result.artifact_type == artifact_type

    transport = root / "transport_ledger.jsonl"
    transport.write_text(json.dumps({
        "logical_index": 0, "document_id": "doc", "request_url": "https://example.test/doc",
        "final_url": "https://example.test/doc", "redirect_chain": [], "http_status": 200,
        "tls_verified": True, "hostname_verified": True, "peer_certificate_sha256": "a" * 64,
        "retrieved_at": "2026-07-16T00:00:00+08:00", "response_headers_sha256": "h",
        "body_sha256": "b", "body_size_bytes": 1, "evidence_scope": "real_official_https",
        "transport_receipt_hash": "r",
    }) + "\n", encoding="utf-8")
    transport_result = validate_artifact(transport, strict=True)
    assert transport_result.valid is True
    assert transport_result.artifact_type == "task055g_fee_transport_ledger"


def test_task055g_network_and_fee_schema_reject_missing_native_fields(tmp_path):
    root = tmp_path / "task_055_g_bad"
    root.mkdir()
    bad_canary = root / "l1_canary_manifest.json"
    bad_canary.write_text(json.dumps({
        "schema_version": "task055g_request_execution_v1", "status": "canary_completed",
        "stage": "L1", "round_id": 1, "plan_hash": "p", "results": [], "ledger": {},
        "content_hash": "c", "generation_id": "g",
    }), encoding="utf-8")
    bad_fee_verifier = root / "fee_independent_verification.json"
    bad_fee_verifier.write_text(json.dumps({
        "schema_version": "task055g_fee_independent_verification_v2", "status": "passed",
        "schedule_content_hash": "s", "policy_seal_hash": "p",
        "document_acquisition_content_hash": "a", "document_merkle_root": "m",
        "transport_ledger_root": "t", "rules_root": "r", "rule_count": 0,
        "coverage": {}, "certification_ready": False, "verifier_source_hash": "v",
        "content_hash": "c", "generation_id": "g",
    }), encoding="utf-8")

    canary_result = validate_artifact(bad_canary, strict=True)
    verifier_result = validate_artifact(bad_fee_verifier, strict=True)
    assert canary_result.valid is False
    assert verifier_result.valid is False
    assert any("attempts_recorded_in_ledger" in issue.message for issue in canary_result.issues)
    assert any("assertion_receipt_root" in issue.message for issue in verifier_result.issues)


def test_task055h_native_manifest_schemas(tmp_path):
    root = tmp_path / "task_055_h_run"
    root.mkdir()
    artifacts = {
        "authorization_seal.json": ({
            "schema_version": "task055h_network_authorization_seal_v1", "status": "canary_authorization_ready_no_network_executed",
            "baseline_commit": "b", "implementation_commit": "i", "task055g_report_content_hash": "r",
                "task055g_final_verifier_content_hash": "v", "task055g_plan_hash": "p", "task055g_plan_lineage": {}, "frontier_root": "f",
                "ordered_exact_daily_key_count": 17, "ordered_exact_daily_keys": [], "ordered_key_root": "k", "canary": {},
                "canary_execution_plan": {}, "canary_execution_plan_hash": "x",
                "canary_retry_count": 1, "resume_requires_separate_authorization": True, "resume_authorized": False,
                "root_identities": {}, "canonical_roots": {}, "parent_network_ledger_root": "l",
                "authorization_network_ledger_root": "a", "authorization_transport_spend_root": "t", "budgets": {}, "consolidation_content_hash": "c",
            "access_journal_content_hash": "j", "fee_attestation_content_hash": "f", "operational_seal_content_hash": "o",
            "independent_causal_attestation": {}, "artifact_sha_catalog": [], "semantic_source_hashes": {},
            "semantic_source_root": "s", "network_execution": {}, "engineering_blockers": [],
            "certification_ready": False, "portfolio_ready": False, "paper_ready": False, "live_ready": False,
            "content_hash": "h", "generation_id": "g",
        }, "task055h_authorization_seal"),
        "fee_attestation.json": ({
            "schema_version": "task055h_fee_schedule_attestation_v1", "status": "passed", "production_spec_hash": "p",
            "schedule_content_hash": "s", "schedule_manifest_sha256": "m", "independent_verification_content_hash": "i",
            "policy_seal_hash": "q", "document_count": 7, "document_catalog": [],
            "official_rate_or_statutory_interval_record_count": 28, "uncalibrated_modeled_record_count": 12,
            "evidence_counts": {}, "projected_rules_root": "r", "commission_interpretations": {},
            "content_hash": "h", "generation_id": "g",
        }, "task055h_fee_attestation"),
        "operational_seal.json": ({
            "schema_version": "task055h_authoritative_operational_seal_v1", "status": "passed",
            "writer_registry_source_hash": "w", "writer_count": 6, "writers": [], "state_counts": {}, "blockers": [],
            "shadow_governed_artifacts_authoritative": False, "runtime_default_roots_scanned": True,
            "content_hash": "h", "generation_id": "g",
        }, "task055h_operational_seal"),
        "task055h_report.json": ({
            "schema_version": "task055h_engineering_report_v1", "status": "canary_authorization_ready_no_network_executed",
            "implementation_commit": "i", "authorization_seal_content_hash": "a", "scrubbed_evidence_content_hash": "s",
            "fee_attestation_content_hash": "f", "operational_seal_content_hash": "o", "frontier_count": 17,
            "frontier_root": "r", "plan_hash": "p", "canary": {}, "credential_read_count": 0,
            "tushare_request_count": 0, "other_network_request_count": 0, "prospective_holdout_accessed": False,
            "resume_authorized": False, "engineering_blockers": [], "readiness": {}, "content_hash": "h", "generation_id": "g",
        }, "task055h_final_report"),
        "task055h_final_verification.json": ({
            "schema_version": "task055h_independent_final_verification_v1", "status": "passed",
            "top_status": "canary_authorization_ready_no_network_executed", "report_content_hash": "r",
            "authorization_seal_content_hash": "a", "scrubbed_evidence_verification_hash": "s", "frontier_count": 17,
            "frontier_root": "f", "plan_hash": "p", "credential_read_count": 0, "tushare_request_count": 0,
            "other_network_request_count": 0, "prospective_holdout_accessed": False, "content_hash": "h", "generation_id": "g",
        }, "task055h_final_verification"),
    }
    for filename, (payload, expected) in artifacts.items():
        path = root / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = validate_artifact(path, strict=True)
        assert result.valid is True
        assert result.artifact_type == expected


def test_registered_declared_artifact_type_overrides_generic_filename(tmp_path):
    path = tmp_path / "freeze_manifest.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "task_052a_governed_freeze_manifest",
                "schema_version": "1.0",
                "generation_id": "freeze_1",
                "content_hash": "a" * 64,
                "semantic_hash": "b" * 64,
                "source_lineage_manifest_sha256": "c" * 64,
                "artifacts": [],
                "immutable": True,
                "publication": "atomic_directory_rename",
            }
        ),
        encoding="utf-8",
    )
    result = validate_artifact(path, strict=True)
    assert result.valid is True
    assert result.artifact_type == "task_052a_governed_freeze_manifest"


def test_task055kr_native_and_intermediate_manifest_schemas(tmp_path):
    root = tmp_path / "task_055_k_kr_fixture"
    artifacts = {
        "acceptance_validation.json": ({
            "schema_version": "task055kr_response_acceptance_validation_v1", "status": "passed",
            "evidence_scope": "synthetic_rehearsal_only", "candidate_checkpoint_content_hash": "a",
            "acceptance_content_hash": "b", "reservation_content_hash": "c", "receipt_content_hash": "d",
            "cache_sha256": "e", "request": {}, "item_count": 0,
            "empty_response_semantics": "vendor_absence_only", "content_hash": "f", "generation_id": "g",
        }, "task055kr_response_acceptance_validation"),
        "raw_repair.json": ({
            "schema_version": "task055kr_no_raw_repair_v1", "status": "vendor_daily_absence_no_raw_mutation",
            "security_date": ["000413.SZ", "20160726"], "parent_freeze_content_hash": "a",
            "content_hash": "b", "generation_id": "g",
        }, "task055kr_raw_repair"),
        "freeze_reference.json": ({
            "schema_version": "task055kr_parent_freeze_reference_v1", "status": "validated_parent_reference",
            "freeze_content_hash": "a", "content_hash": "b", "generation_id": "g",
        }, "task055kr_parent_freeze_reference"),
        "matrix_reference.json": ({
            "schema_version": "task055kr_parent_matrix_reference_v1", "status": "validated_parent_reference",
            "role": "matrix", "content_hash_reference": "a", "context_root": "b",
            "content_hash": "c", "generation_id": "g",
        }, "task055kr_parent_generation_reference"),
        "tensor_reference.json": ({
            "schema_version": "task055kr_parent_tensor_reference_v1", "status": "validated_parent_reference",
            "role": "tensor", "content_hash_reference": "a", "context_root": "b",
            "content_hash": "c", "generation_id": "g",
        }, "task055kr_parent_generation_reference"),
        "materializations_reference.json": ({
            "schema_version": "task055kr_parent_materializations_reference_v1", "status": "validated_parent_reference",
            "exact20_identity_root": "a", "materialization_root": "b", "content_hash": "c", "generation_id": "g",
        }, "task055kr_parent_materializations_reference"),
        "sentinel_reference.json": ({
            "schema_version": "task055kr_sentinel_reference_v1", "status": "validated_content_cache_reference",
            "cache_identity": "a", "sentinel_content_hash": "b", "exact_run_count": 12,
            "evidence_scope": "synthetic_rehearsal_only", "content_hash": "c", "generation_id": "g",
        }, "task055kr_sentinel_reference"),
        "application_final.json": ({
            "schema_version": "task055kr_application_final_v1", "status": "domain_blocked", "branch": "empty",
            "net_replay_content_hash": "a", "all_in_replay_content_hash": "b",
            "net_terminal_counts": {}, "all_in_terminal_counts": {}, "net_run_count": 100,
            "all_in_run_count": 100, "frontier_union": [], "frontier_union_root": "c",
            "dynamic_l2_content_hash": "d", "dynamic_l2_status": "sealed_not_authorized",
            "candidate_reselection_allowed": False, "content_hash": "e", "generation_id": "g",
        }, "task055kr_application_final"),
        "dynamic_l2_plan.json": ({
            "schema_version": "task055j_dynamic_exact_suspend_l2_v1", "status": "sealed_not_authorized",
            "parent_truth_content_hash": "a", "parent_replay_content_hash": "b", "requests": [{}],
            "request_count": 1, "network_executed": False, "resume_authorized": False,
            "application_support": "unsupported_waiting_for_separate_authority",
            "daily_empty_semantics": "vendor_absence_only_not_full_day_suspension_proof",
            "content_hash": "c", "generation_id": "g",
        }, "task055kr_dynamic_l2_plan"),
        "application_lock_identity.json": ({"st_dev": 1, "st_ino": 2}, "task055kr_application_lock_identity"),
        "current.json": ({
            "content_hash": "a", "generation_id": "g", "manifest": "generations/g/manifest.json",
        }, "task055kr_generation_pointer"),
        "checkpoint.json": ({"name": "journal", "root": "r", "sequence": 1}, "task055kr_durable_journal_checkpoint"),
    }
    for filename, (payload, expected) in artifacts.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = validate_artifact(path, strict=True)
        assert result.valid is True
        assert result.artifact_type == expected

    jsonl = {
        "events.jsonl": ({
            "event_id": "e", "event": "stage_started", "sequence": 1,
            "previous_event_hash": "", "event_hash": "h",
        }, "task055kr_durable_journal_events"),
        "blockers.jsonl": ({
            "ts_code": "000413.SZ", "trade_date": "20160726", "reporting_point": "open_pretrade",
            "reason": "missing",
        }, "task055kr_valuation_blockers"),
        "run_rows.jsonl": ({
            "factor_id": "factor", "scenario": "baseline", "terminal_state": "causal_valuation_blocked",
        }, "task055kr_fee_replay_run_rows"),
        "held_marks.jsonl": ({
            "factor_id": "factor", "scenario": "baseline", "ts_code": "000413.SZ",
            "trade_date": "20160726", "reporting_point": "close",
        }, "task055kr_fee_replay_held_marks"),
    }
    for filename, (payload, expected) in jsonl.items():
        path = root / filename
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        result = validate_artifact(path, strict=True)
        assert result.valid is True
        assert result.artifact_type == expected

    cache = root / "authority/cache_data/.cache/tushare/abc.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({
        "schema_version": "tushare_cache_envelope.v3", "request": {}, "request_fingerprint": "a",
        "provider": {}, "response": {}, "records": [], "metadata": {}, "source_code_hash": "b",
        "code_semantic_hash": "c", "endpoint_schema_proof": {},
    }), encoding="utf-8")
    cache_result = validate_artifact(cache, strict=True)
    assert cache_result.valid is True
    assert cache_result.artifact_type == "task055kr_tushare_cache_envelope"
