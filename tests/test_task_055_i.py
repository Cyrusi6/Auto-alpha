from __future__ import annotations

import argparse
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from artifact_schema.validator import validate_artifact
from monitoring.checks import check_task055i_single_canary_authority
from task_055_g.network_cli import _dispatch
from task_055_g.network_state import Task055GNetworkStateError
from task_055_h.io import canonical_hash
from task_055_i import network_cli
from task_055_i.contracts import (
    CANARY,
    PARENT_AUTHORIZATION_SEAL_HASH,
    PARENT_CANARY_PLAN_HASH,
    PARENT_GIT_EVIDENCE_HASH,
    READY_STATUS,
)
from task_055_i.executor import execute_single_canary
from task_055_i.verifier import ScrubbedEvidenceError, verify_scrubbed_evidence


def test_fixed_parent_and_first_canary_identity_are_exact():
    assert PARENT_AUTHORIZATION_SEAL_HASH == "6c32e777374319026c1db23b10686bf9c245595b170a76f8e29e2f8259ca9b72"
    assert PARENT_GIT_EVIDENCE_HASH == "2ef732ecb20eebcbf0dede46a058cb5e1730ea2bea94a98f02afac9d09b2fa20"
    assert PARENT_CANARY_PLAN_HASH == "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae"
    assert CANARY == {
        "api_name": "daily",
        "ts_code": "000413.SZ",
        "trade_date": "20160726",
        "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
        "transport_hash": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
        "evidence_use_hash": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
    }


def test_production_cli_exposes_only_canary_and_acceptance_without_injection():
    result = subprocess.run(
        [sys.executable, "-m", "task_055_i.network_cli", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "canary-verify" in result.stdout
    assert "resume" not in result.stdout
    signature = inspect.signature(execute_single_canary)
    assert list(signature.parameters) == [
        "runtime_authority", "reviewed_authority_hash", "credential_file", "allow_network"
    ]
    assert "request_executor" not in inspect.getsource(network_cli)
    assert "client_factory" not in inspect.getsource(network_cli)
    assert "credential_loader" not in inspect.getsource(network_cli)


def test_task055g_network_commands_are_superseded_before_config_or_credentials():
    for command in ("l1-canary", "l1-resume", "l2-canary", "l2-resume"):
        with pytest.raises(Task055GNetworkStateError, match="superseded_by_task055k_transport_broker"):
            _dispatch(
                argparse.Namespace(command=command, allow_network=False, sealed_plan_hash=None),
                {},
            )


def test_native_rehearsal_is_superseded_by_task055k_before_execution():
    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        execute_single_canary(
            runtime_authority="unused",
            reviewed_authority_hash="unused",
            credential_file="unused",
            allow_network=True,
        )


def test_scrubbed_verifier_and_artifact_schema_detect_tampering(tmp_path):
    catalog = [{"role": "runtime", "sha256": "1" * 64, "content_hash": "2" * 64}]
    semantic = {
        "schema_version": "task055i_scrubbed_execution_authorization_v1",
        "status": READY_STATUS,
        "implementation_commit": "3" * 40,
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_git_evidence_hash": PARENT_GIT_EVIDENCE_HASH,
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "runtime_authority_content_hash": "4" * 64,
        "execution_authorization_content_hash": "5" * 64,
        "rehearsal_content_hash": "6" * 64,
        "rehearsal_artifact_root": "7" * 64,
        "semantic_source_root": "8" * 64,
        "canary": CANARY,
        "ordered_exact_daily_key_count": 17,
        "ordered_exact_daily_keys": [],
        "budgets": {"physical_attempts": 0, "limits": {"unique_security_dates": 64, "logical_requests": 128, "physical_attempts": 160}},
        "root_binding_hashes": {"authority": "9" * 64},
        "initial_network_ledger_root": "a" * 64,
        "initial_transport_spend_root": "b" * 64,
        "artifact_catalog": catalog,
        "artifact_catalog_root": canonical_hash(catalog),
        "network_execution": {"credential_read_count": 0, "tushare_request_count": 0, "other_network_request_count": 0, "prospective_holdout_accessed": False},
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_unproven": True,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_blockers": ["historical_selection_contamination"],
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
    }
    payload = semantic | {"content_hash": canonical_hash(semantic)}
    path = tmp_path / "task_055_i_20260717" / "task055i_scrubbed_evidence.json"
    path.parent.mkdir()
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert verify_scrubbed_evidence(path)["verified"] is True
    assert validate_artifact(path, strict=True).valid is True
    payload["canary"] = dict(CANARY, trade_date="20160727")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScrubbedEvidenceError):
        verify_scrubbed_evidence(path)


def test_monitoring_never_claims_global_queue_emptiness_for_task055i(tmp_path):
    canary = dict(CANARY)
    authorization = {
        "status": READY_STATUS,
        "content_hash": "a" * 64,
        "canary": canary,
        "resume_authorized": False,
    }
    report = {
        "status": READY_STATUS,
        "content_hash": "b" * 64,
        "execution_authorization_content_hash": authorization["content_hash"],
        "canary": canary,
        "network_execution": {"credential_read_count": 0, "tushare_request_count": 0, "other_network_request_count": 0, "prospective_holdout_accessed": False},
        "real_canary_executed": False,
        "real_response_applied": False,
        "real_gpu_started": False,
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_unproven": True,
        "engineering_blockers": [],
        "readiness": {"single_canary_execution_ready": True, "certification_ready": False, "portfolio_ready": False, "paper_ready": False, "live_ready": False},
    }
    verification = {
        "status": "passed",
        "top_status": READY_STATUS,
        "report_content_hash": report["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "credential_read_count": 0,
        "tushare_request_count": 0,
        "other_network_request_count": 0,
        "prospective_holdout_accessed": False,
    }
    paths = []
    for name, value in (("report.json", report), ("authorization.json", authorization), ("verification.json", verification)):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    details, alerts = check_task055i_single_canary_authority(*paths)
    assert alerts == []
    assert details["task055i_boundary_valid"] is True
    assert details["global_downstream_queues_proven_empty"] is False
    assert details["operational_state_unproven"] is True
