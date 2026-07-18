from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import TushareHttpClient, TushareNetworkError, TushareResponseEnvelope
from data_pipeline.ashare.request_normalization import stable_json_hash, tushare_code_semantic_hash
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_g.truth import publish_truth_successor, validate_truth_v2
from task_052_a.backfill import GovernedBackfillConfig, run_governed_backfill
from data_source_validation.probe import probe_provider
from data_backfill.run_backfill import main as data_backfill_main
from data_source_validation.run_smoke import main as data_source_smoke_main
from real_data_ops.run_real_data import main as real_data_main
from task_055_h.io import canonical_hash, publish_generation
from task_055_j import network_cli
from task_055_j.application_tree import publish_application_tree_seal, validate_application_preflight
from task_055_j.contracts import CANARY, READY_STATUS
from task_055_j.executor import (
    Task055JExecutionError,
    _execute_synthetic_test_only,
    _load_credential_file,
    _verify_and_accept_synthetic_test_only,
)
from task_055_j.ledger import DurableHashJournal
from task_055_j.rehearsal import _publish_synthetic_authority, _synthetic_seal_validator
from task_055_j.verifier import Task055JScrubbedEvidenceError, verify_scrubbed_evidence


def test_client_requires_task055j_capability_or_explicit_test_transport() -> None:
    config = AShareDataConfig(tushare_token="synthetic", tushare_retry_count=1)
    with pytest.raises(TushareNetworkError, match="task055j_execution_capability"):
        TushareHttpClient(config)
    with pytest.raises(TushareNetworkError, match="test_only_marker"):
        TushareHttpClient(config, urlopen=lambda *_args, **_kwargs: None)
    client = TushareHttpClient(
        config,
        urlopen=lambda *_args, **_kwargs: None,
        test_only_transport=True,
    )
    assert client.retry_count == 1


def test_legacy_backfill_and_online_probe_fail_before_credential_or_transport(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="superseded_by_task055j"):
        run_governed_backfill(
            GovernedBackfillConfig(
                union_path=tmp_path / "missing-union.jsonl",
                securities_path=tmp_path / "missing-securities.jsonl",
                output_root=tmp_path / "output",
            )
        )
    result = probe_provider(
        AShareDataConfig(tushare_token="must-not-be-used"),
        allow_network=True,
    )
    assert len(result) == 1
    assert result[0].message == "superseded_by_task055j"
    assert result[0].credential_present is False
    assert result[0].network_allowed is False


def test_generic_online_clis_fail_before_environment_credential_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden_from_env(*_args, **_kwargs):
        raise AssertionError("credential environment must not be read")

    monkeypatch.setattr(AShareDataConfig, "from_env", forbidden_from_env)
    invocations = (
        (
            data_backfill_main,
            [
                "execute", "--provider", "tushare", "--allow-network",
                "--data-dir", str(tmp_path / "backfill-data"),
                "--output-dir", str(tmp_path / "backfill-output"),
            ],
        ),
        (
            data_source_smoke_main,
            [
                "--provider", "tushare", "--allow-network",
                "--data-dir", str(tmp_path / "smoke-data"),
                "--output-dir", str(tmp_path / "smoke-output"),
            ],
        ),
        (
            real_data_main,
            [
                "run", "--provider", "tushare", "--allow-network",
                "--output-dir", str(tmp_path / "real-data-output"),
            ],
        ),
    )
    for entrypoint, argv in invocations:
        assert entrypoint(argv) == 2
        assert json.loads(capsys.readouterr().out) == {
            "reason": "superseded_by_task055j",
            "status": "blocked",
        }


def test_cli_has_single_canary_acceptance_and_offline_apply_only() -> None:
    source = inspect.getsource(network_cli)
    assert "canary-apply" in source
    assert 'add_parser("resume")' not in source
    assert 'add_parser("batch")' not in source
    assert "request_executor" not in source
    assert "client_factory" not in source
    assert "credential_loader" not in source


def test_application_preflight_resolves_sibling_artifact_tree(tmp_path: Path) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    source = governed / "artifact.json"
    source.write_text(json.dumps({"max_date": "20260630"}), encoding="utf-8")
    output = tmp_path / "preflight"
    tree = publish_application_tree_seal(
        governed_root=governed,
        roles={"fixture": source},
        output_root=output / "artifact_tree",
    )
    semantic = {
        "schema_version": "task055j_production_application_preflight_v1",
        "status": "passed",
        "max_allowed_date": "20260630",
        "max_validated_source_date": "20260630",
        "parent_runtime_authority_content_hash": "1" * 64,
        "application_artifact_tree_content_hash": tree["content_hash"],
        "application_artifact_tree_root": tree["tree_root"],
        "native_validation": {},
        "exact20_ids": [f"factor-{index:02d}" for index in range(20)],
        "exact20_identity_root": "2" * 64,
        "research_cutoff": "20240530",
        "target_endpoint_horizon": 2,
        "production_context_parsed": True,
        "network_accessed": False,
        "credential_read_count": 0,
        "prospective_holdout_accessed": False,
    }
    preflight = publish_generation(
        output,
        prefix="task055j_application_preflight",
        manifest_name="application_preflight.json",
        semantic=semantic,
    )
    assert validate_application_preflight(
        preflight["manifest_path"], governed_root=governed
    )["application_artifact_tree_content_hash"] == tree["content_hash"]


def test_journal_detects_manual_chain_and_checkpoint_tampering(tmp_path: Path) -> None:
    journal = DurableHashJournal(tmp_path / "journal", name="test")
    journal.append({"event_id": "one", "event": "first"})
    journal.append({"event_id": "two", "event": "second"})
    rows = journal.rows()
    assert journal.checkpoint()["root"] == rows[-1]["event_hash"]
    checkpoint = tmp_path / "journal/checkpoint.json"
    payload = json.loads(checkpoint.read_text())
    payload["root"] = "0" * 64
    checkpoint.write_text(json.dumps(payload))
    with pytest.raises(Exception, match="checkpoint_mismatch"):
        journal.checkpoint()


def test_task055j_credential_file_contract_rejects_relative_symlink_root_mode_and_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    governed = tmp_path / "governed"
    authority = governed / "authority"
    for root in (repository, governed, authority):
        root.mkdir(parents=True, exist_ok=True)
    seal = {
        "repository_root": str(repository),
        "governed_root": str(governed),
        "authority_root": str(authority),
    }
    outside = tmp_path / "credential.txt"
    outside.write_text("synthetic-secret", encoding="utf-8")
    outside.chmod(0o600)
    with pytest.raises(Task055JExecutionError, match="absolute_regular_file_required"):
        _load_credential_file(Path("credential.txt"), seal)
    link = tmp_path / "credential-link"
    link.symlink_to(outside)
    with pytest.raises(Task055JExecutionError, match="absolute_regular_file_required"):
        _load_credential_file(link, seal)
    inside = authority / "credential.txt"
    inside.write_text("synthetic-secret", encoding="utf-8")
    inside.chmod(0o600)
    with pytest.raises(Task055JExecutionError, match="inside_forbidden_root"):
        _load_credential_file(inside, seal)
    outside.chmod(0o644)
    with pytest.raises(Task055JExecutionError, match="owner_or_mode_invalid"):
        _load_credential_file(outside, seal)
    outside.chmod(0o600)
    current_uid = os.getuid()
    monkeypatch.setattr("task_055_j.executor.os.getuid", lambda: current_uid + 1)
    with pytest.raises(Task055JExecutionError, match="owner_or_mode_invalid"):
        _load_credential_file(outside, seal)


def test_receipt_before_cache_recovers_without_second_post(tmp_path: Path) -> None:
    runtime = _runtime_fixture(tmp_path)
    authority_root, seal = _publish_synthetic_authority(tmp_path / "authority", runtime)
    calls = {"count": 0}

    def transport(request):
        calls["count"] += 1
        return _envelope(request, [_positive_row()])

    with pytest.raises(Exception):
        _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=transport,
            crash_point="after_receipt_before_cache",
        )
    recovered = _execute_synthetic_test_only(
        final_execution_seal=seal["manifest_path"],
        reviewed_hash=seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
        transport=transport,
    )
    assert recovered["status"] == "completed"
    assert recovered["crash_recovered"] is True
    assert calls["count"] == 1
    accepted = _verify_and_accept_synthetic_test_only(
        final_execution_seal=seal["manifest_path"],
        reviewed_hash=seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
    )
    assert accepted["physical_post_count"] == 1


def test_spend_intent_without_receipt_is_permanently_blocked(tmp_path: Path) -> None:
    runtime = _runtime_fixture(tmp_path)
    _, seal = _publish_synthetic_authority(tmp_path / "authority", runtime)
    calls = {"count": 0}

    def transport(request):
        calls["count"] += 1
        return _envelope(request, [_positive_row()])

    with pytest.raises(Exception):
        _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=transport,
            crash_point="after_spend_intent_before_post",
        )
    with pytest.raises(Task055JExecutionError, match="ambiguous_post_without_transport_receipt"):
        _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=transport,
        )
    assert calls["count"] == 0


def test_manual_started_events_plus_cache_cannot_enter_acceptance(tmp_path: Path) -> None:
    runtime = _runtime_fixture(tmp_path)
    authority_root, seal = _publish_synthetic_authority(tmp_path / "authority", runtime)
    attempt = canonical_hash([seal["content_hash"], CANARY["transport_hash"], "single_physical_attempt", 1])
    DurableHashJournal(authority_root / "network_journal", name="network").append(
        {"event_id": f"attempt-intent:{attempt}", "event": "attempt_intent", "attempt_id": attempt, "transport_hash": CANARY["transport_hash"]}
    )
    DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend").append(
        {"event_id": f"post-intent:{attempt}", "event": "physical_post_intent", "attempt_id": attempt, "transport_hash": CANARY["transport_hash"]}
    )
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    cache.write(
        "daily",
        params={"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"]},
        fields=CANARY["fields"],
        records=[_positive_row()],
        response_fields=CANARY["fields"],
        response_fields_observed=True,
        endpoint=CANONICAL_ORIGIN,
    )
    with pytest.raises(Task055JExecutionError, match="ambiguous_post_without_transport_receipt"):
        _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=lambda request: _envelope(request, [_positive_row()]),
        )


def test_empty_cache_endpoint_schema_proof_is_recomputed_from_native_receipt(tmp_path: Path) -> None:
    runtime = _runtime_fixture(tmp_path)
    authority_root, seal = _publish_synthetic_authority(tmp_path / "authority", runtime)
    _execute_synthetic_test_only(
        final_execution_seal=seal["manifest_path"],
        reviewed_hash=seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
        transport=lambda request: _envelope(request, []),
    )
    cache_path = next((authority_root / "cache_data/.cache/tushare").glob("*.json"))
    payload = json.loads(cache_path.read_text())
    proof = dict(payload["endpoint_schema_proof"])
    proof["source_receipt_content_hash"] = "0" * 64
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    proof["proof_hash"] = stable_json_hash(unsigned)
    payload["endpoint_schema_proof"] = proof
    cache_path.write_text(json.dumps(payload, sort_keys=True))
    with pytest.raises(Task055JExecutionError, match="endpoint_schema_proof_invalid"):
        _verify_and_accept_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
        )


def test_truth_successor_preserves_full_key_set_and_empty_is_not_suspension(tmp_path: Path) -> None:
    parent = _truth_fixture(tmp_path / "truth")
    request = {
        "api_name": "daily",
        "ts_code": CANARY["ts_code"],
        "trade_date": CANARY["trade_date"],
        "params": {"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"]},
        "transport_hash": CANARY["transport_hash"],
    }
    successor = publish_truth_successor(
        parent_truth_manifest=parent["manifest_path"],
        api_name="daily",
        request=request,
        records=[],
        response_evidence={"cache_sha256": "a" * 64, "transport_receipt_content_hash": "b" * 64},
        output_root=tmp_path / "successor",
        parent_apply_hash="c" * 64,
        expected_record_count=2,
    )
    validated = validate_truth_v2(successor["manifest_path"])
    assert validated["record_count"] == 2
    row = next(row for row in validated["records"] if row["ts_code"] == CANARY["ts_code"])
    assert row["state"] == "DATA_SOURCE_GAP"
    assert row["task055j_vendor_daily_absence"] is True


def test_standalone_verifier_rejects_empty_keys_roots_and_forged_source(tmp_path: Path) -> None:
    payload = _scrubbed_fixture()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload))
    assert verify_scrubbed_evidence(path)["verified"] is True
    for mutation in ("keys", "roots", "source"):
        forged = json.loads(json.dumps(payload))
        if mutation == "keys":
            forged["ordered_exact_daily_keys"] = []
        elif mutation == "roots":
            forged["root_binding_hashes"] = {}
        else:
            forged["source_entries"] = []
        forged["content_hash"] = canonical_hash({key: value for key, value in forged.items() if key != "content_hash"})
        path.write_text(json.dumps(forged))
        with pytest.raises(Task055JScrubbedEvidenceError):
            verify_scrubbed_evidence(path)


def test_standalone_verifier_enforces_ready_and_blocked_status_semantics(tmp_path: Path) -> None:
    payload = _scrubbed_fixture()
    path = tmp_path / "evidence.json"
    payload["engineering_blockers"] = ["unexpected"]
    payload["content_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "content_hash"})
    path.write_text(json.dumps(payload))
    with pytest.raises(Task055JScrubbedEvidenceError, match="status_blocker_mismatch"):
        verify_scrubbed_evidence(path)
    payload["status"] = "task055j_single_canary_production_closure_blocked_no_network_executed"
    payload["content_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "content_hash"})
    path.write_text(json.dumps(payload))
    assert verify_scrubbed_evidence(path)["verified"] is True


def _runtime_fixture(tmp_path: Path) -> dict:
    evidence = json.loads(Path("evidence/task_055_h/scrubbed_authorization_evidence.json").read_text())
    lineage = dict(evidence["plan_lineage"])
    lineage["parent_task055g_plan_hash"] = evidence["plan_hash"]
    ordered = list(evidence["ordered_exact_daily_keys"])
    first = dict(ordered[0])
    plan = {
        "schema_version": "task055g_dynamic_network_plan_v1",
        "status": "sealed_single_exact_daily_canary_only",
        "stage": "L1",
        "round_id": 1,
        "frontier_root": evidence["frontier_root"],
        "parent_apply_hash": None,
        "lineage": lineage,
        "requests": [
            {
                **{key: first[key] for key in ("api_name", "ts_code", "trade_date", "fields", "transport_hash", "evidence_use_hash")},
                "params": {"ts_code": first["ts_code"], "trade_date": first["trade_date"]},
                "round_id": 1,
                "stage": "L1",
            }
        ],
        "plan_hash": evidence["canary_execution_plan_hash"],
    }
    return {
        "content_hash": "1" * 64,
        "governed_root": str(tmp_path / "governed"),
        "repository_root": str(Path.cwd()),
        "parent_canary_plan_hash": evidence["canary_execution_plan_hash"],
        "ordered_exact_daily_keys": ordered,
        "ordered_key_count": 17,
        "ordered_key_root": canonical_hash(ordered),
        "single_request_plan": plan,
        "budgets": {"physical_attempts": 0, "limits": {"unique_security_dates": 64, "logical_requests": 128, "physical_attempts": 160}},
        "root_identities": {},
    }


def _envelope(request, rows):
    return TushareResponseEnvelope(
        api_name="daily",
        params_without_token=dict(request["params"]),
        requested_fields=",".join(request["fields"]),
        response_code=0,
        response_message="",
        response_fields=list(request["fields"]),
        records=rows,
        item_count=len(rows),
        duration_seconds=0.001,
        request_fingerprint=request["transport_hash"],
        code_semantic_hash=tushare_code_semantic_hash(),
        endpoint=CANONICAL_ORIGIN,
        provider_api_version="tushare_pro_http.v1",
        response_payload_hash=stable_json_hash(rows),
    )


def _positive_row():
    return {"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"], "open": 10.0, "high": 10.4, "low": 9.8, "close": 10.2, "pre_close": 10.0, "vol": 1000.0, "amount": 10000.0}


def _truth_fixture(root: Path) -> dict:
    rows = []
    for code, date in ((CANARY["ts_code"], CANARY["trade_date"]), ("000001.SZ", "20160726")):
        row = {
            "ts_code": code,
            "trade_date": date,
            "state": "DATA_SOURCE_GAP",
            "reason_code": "no_complete_bar_and_no_exact_positive_suspend_event",
            "daily_bar_status": "absent_or_invalid",
            "matrix_bar": None,
            "inventory_bar_observed": False,
            "suspend_type": "none",
            "suspend_timing_status": "none",
            "suspension_events": [],
            "suspension_source_coverage": "complete",
            "suspension_response_evidence": [],
            "daily_response_evidence": [],
            "listed": True,
            "active": True,
            "membership": True,
            "membership_known": True,
            "corporate_action_validity": True,
            "valuation_domain_intersection": True,
            "regression_probe": False,
            "modeled_stale_candidate": False,
            "stale_mark_authorized": False,
            "stale_mark_authorization_note": "truth_v2_never_authorizes_price_without_prior_close_and_stale_policy",
        }
        row["evidence_hash"] = canonical_hash(row)
        rows.append(row)
    rows.sort(key=lambda row: (row["ts_code"], row["trade_date"]))
    data = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows).encode()
    import hashlib

    partition = {"path": "truth_v2_rows.jsonl", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    semantic = {
        "schema_version": "task055g_security_date_truth_v2",
        "status": "published",
        "review_version": "fixture",
        "max_date": "20260630",
        "record_count": 2,
        "key_root": canonical_hash(sorted((row["ts_code"], row["trade_date"]) for row in rows)),
        "state_counts": {"DATA_SOURCE_GAP": 2},
        "suspend_type_counts": {"none": 2},
        "daily_empty_response_counts": [],
        "suspend_empty_response_counts": [],
        "valuation_domain_count": 2,
        "modeled_candidate_count": 0,
        "timing_uncertified_candidate_count": 0,
        "lineage": {},
        "partitions": {"rows": partition},
        "certification_blockers": ["suspension_timing_semantics_uncertified", "vendor_historical_revision_risk"],
    }
    return publish_generation(root, prefix="truth_v2", manifest_name="truth_v2_manifest.json", semantic=semantic, extra_files={"truth_v2_rows.jsonl": data})


def _scrubbed_fixture() -> dict:
    evidence = json.loads(Path("evidence/task_055_h/scrubbed_authorization_evidence.json").read_text())
    ordered = list(evidence["ordered_exact_daily_keys"])
    source_entries = [
        {"path": path, "sha256": "a" * 64, "size_bytes": 1, "mode": 420}
        for path in (
            "task_055_j/executor.py", "task_055_j/application.py", "task_055_j/authority.py", "task_055_j/verifier.py",
            "data_pipeline/ashare/providers/tushare_client.py", "validation_lab/materialization.py", "model_core/vm.py", "task_055_a/simulator.py",
        )
    ]
    roles = ["source_tree_seal", "application_preflight", "application_tree_seal", "runtime_authority", "execution_authorization", "native_rehearsal", "rehearsal_independent_verification", "final_report", "final_independent_verification", "final_execution_seal"]
    lineage = {key: "d" * 64 for key in (
        "runtime_authority_content_hash", "execution_authorization_content_hash", "application_preflight_content_hash", "application_tree_content_hash", "rehearsal_content_hash", "rehearsal_verification_content_hash", "final_report_content_hash", "final_verification_content_hash", "final_execution_seal_content_hash",
    )}
    role_lineage = {
        "runtime_authority": "runtime_authority_content_hash",
        "execution_authorization": "execution_authorization_content_hash",
        "application_preflight": "application_preflight_content_hash",
        "application_tree_seal": "application_tree_content_hash",
        "native_rehearsal": "rehearsal_content_hash",
        "rehearsal_independent_verification": "rehearsal_verification_content_hash",
        "final_report": "final_report_content_hash",
        "final_independent_verification": "final_verification_content_hash",
        "final_execution_seal": "final_execution_seal_content_hash",
    }
    catalog = [
        {
            "role": role,
            "sha256": "b" * 64,
            "content_hash": lineage[role_lineage[role]] if role in role_lineage else "c" * 64,
        }
        for role in roles
    ]
    semantic = {
        "schema_version": "task055j_scrubbed_execution_evidence_v1",
        "status": READY_STATUS,
        "implementation_commit": "e" * 40,
        "parent_authorization_seal_hash": "6c32e777374319026c1db23b10686bf9c245595b170a76f8e29e2f8259ca9b72",
        "parent_canary_plan_hash": "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae",
        "ordered_exact_daily_keys": ordered,
        "ordered_key_root": canonical_hash(ordered),
        "canary": CANARY,
        "budgets": {"physical_attempts": 0, "limits": {"unique_security_dates": 64, "logical_requests": 128, "physical_attempts": 160}},
        "root_binding_hashes": {role: "f" * 64 for role in ("repository", "governed", "authority", "network_journal", "transport_spend", "cache", "receipts", "applications", "single_flight_lock", "application_lock")},
        "source_entries": source_entries,
        "source_root": canonical_hash(source_entries),
        "application_role_roots": {"matrix": "1" * 64},
        "application_tree_root": "2" * 64,
        "artifact_catalog": catalog,
        "artifact_catalog_root": canonical_hash(catalog),
        "lineage": lineage,
        "network_execution": {"credential_read_count": 0, "tushare_post_count": 0, "other_market_http_count": 0, "prospective_holdout_accessed": False, "max_read_date": "20260630"},
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_unproven": True,
        "engineering_blockers": [],
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_blockers": ["historical_selection_contamination"],
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
    }
    return semantic | {"content_hash": canonical_hash(semantic)}
