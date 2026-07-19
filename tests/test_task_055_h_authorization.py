from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from task_055_f.network import _append_spend_event
from task_055_g import network_state
from task_055_h.authorization import (
    EXPECTED_G_FRONTIER_ROOT,
    EXPECTED_G_PLAN_HASH,
    EXPECTED_ORDERED_REQUESTS,
    _canary_execution_plan,
    validate_authorization_seal,
    verify_scrubbed_evidence_package,
)
from task_055_h.contracts import AUTHORIZATION_SEAL_SCHEMA, READY_STATUS, SCRUBBED_EVIDENCE_SCHEMA
from task_055_h.io import canonical_hash, publish_generation
from task_055_h.journal import DurableAccessError, DurableAccessJournal
from task_055_h.network import load_file_credential_after_offline_gates, ordered_future_canary_gate


DAILY_FIELDS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]


def ready_seal(root: Path) -> dict:
    for relative in ("network_state", "network_cache_data", "transport_spend"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    keys = [
        {
            "ordinal": index,
            "api_name": "daily",
            "ts_code": code,
            "trade_date": date,
            "fields": DAILY_FIELDS,
            "transport_hash": transport_hash,
            "evidence_use_hash": evidence_hash,
        }
        for index, (code, date, transport_hash, evidence_hash) in enumerate(EXPECTED_ORDERED_REQUESTS, start=1)
    ]
    plan_lineage = {
        "truth_content_hash": "1" * 64,
        "matrix_content_hash": "2" * 64,
        "simulation_bundle_content_hash": "3" * 64,
        "fee_schedule_content_hash": "4" * 64,
        "frontier_root": EXPECTED_G_FRONTIER_ROOT,
        "key_root": "5" * 64,
    }
    parent_plan = {
        "plan_hash": EXPECTED_G_PLAN_HASH,
        "frontier_root": EXPECTED_G_FRONTIER_ROOT,
        "lineage": plan_lineage,
    }
    canary_plan = _canary_execution_plan(parent_plan, keys[0])
    state_plan = network_state._make_plan(
        stage="L1",
        round_id=1,
        requests=[
            {
                "stage": "L1",
                "round_id": 1,
                "api_name": row["api_name"],
                "params": {"ts_code": row["ts_code"], "trade_date": row["trade_date"]},
                "fields": row["fields"],
                "ts_code": row["ts_code"],
                "trade_date": row["trade_date"],
                "transport_hash": row["transport_hash"],
                "evidence_use_hash": row["evidence_use_hash"],
            }
            for row in keys
        ],
        lineage=plan_lineage,
        frontier_root=EXPECTED_G_FRONTIER_ROOT,
        parent_apply_hash=None,
        status="sealed_round_one_exact_daily_l1",
    )
    state = network_state.consolidate(state_root=root / "network_state", plan_manifest=state_plan)["ledger"]
    empty_spend = publish_generation(
        root / "transport_spend",
        prefix="network_spend",
        manifest_name="network_spend_ledger.json",
        semantic={
            "schema_version": "task055f_append_only_network_spend_v1",
            "events": [],
            "physical_attempt_count": 0,
            "logical_transport_count": 0,
        },
    )
    semantic = {
        "schema_version": AUTHORIZATION_SEAL_SCHEMA,
        "status": READY_STATUS,
        "baseline_commit": "5bc179de10a921e9547d63c393643d4438b126f3",
        "implementation_commit": "a" * 40,
        "task055g_report_content_hash": "b" * 64,
        "task055g_final_verifier_content_hash": "c" * 64,
        "task055g_plan_hash": EXPECTED_G_PLAN_HASH,
        "task055g_plan_lineage": plan_lineage,
        "frontier_root": EXPECTED_G_FRONTIER_ROOT,
        "ordered_exact_daily_key_count": 17,
        "ordered_exact_daily_keys": keys,
        "ordered_key_root": canonical_hash(keys),
        "canary": keys[0],
        "canary_execution_plan": canary_plan,
        "canary_execution_plan_hash": canary_plan["plan_hash"],
        "canary_retry_count": 1,
        "resume_requires_separate_authorization": True,
        "resume_authorized": False,
        "root_identities": {
            "output": _root_identity(root),
            "state": _root_identity(root / "network_state"),
            "cache": _root_identity(root / "network_cache_data"),
            "transport_spend": _root_identity(root / "transport_spend"),
        },
        "canonical_roots": {
            "task055h_output_relative_to_governed": "validation_runs/task_055_h_test",
            "state_relative_to_output": "network_state",
            "cache_data_relative_to_output": "network_cache_data",
            "transport_spend_relative_to_output": "transport_spend",
        },
        "parent_network_ledger_root": "d" * 64,
        "authorization_network_ledger_root": state["ledger_root"],
        "authorization_transport_spend_root": empty_spend["content_hash"],
        "budgets": {"unique_security_dates": state["unique_security_date_count"], "logical_requests": state["request_count"], "physical_attempts": state["physical_attempt_count"], "limits": {"unique_security_dates": 64, "logical_requests": 128, "physical_attempts": 160}},
        "consolidation_content_hash": "f" * 64,
        "access_journal_content_hash": "1" * 64,
        "fee_attestation_content_hash": "2" * 64,
        "operational_seal_content_hash": "3" * 64,
        "independent_causal_attestation": {},
        "artifact_sha_catalog": [],
        "semantic_source_hashes": {},
        "semantic_source_root": "4" * 64,
        "network_execution": {"credential_read_count": 0, "tushare_request_count": 0, "other_network_request_count": 0, "prospective_holdout_accessed": False},
        "engineering_blockers": [],
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    return publish_generation(root / "authorization_seal", prefix="authorization_seal", manifest_name="authorization_seal.json", semantic=semantic)


def test_authorization_rejects_reordered_or_forged_request(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    validate_authorization_seal(seal["manifest_path"])
    payload = json.loads(Path(seal["manifest_path"]).read_text())
    payload["ordered_exact_daily_keys"][0], payload["ordered_exact_daily_keys"][1] = payload["ordered_exact_daily_keys"][1], payload["ordered_exact_daily_keys"][0]
    payload["ordered_key_root"] = canonical_hash(payload["ordered_exact_daily_keys"])
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    forged = _republish_seal(tmp_path, semantic)
    with pytest.raises(Exception, match="ordered_request_evidence_mismatch"):
        validate_authorization_seal(forged["manifest_path"])


def test_scrubbed_package_rejects_absolute_path_and_tamper(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    payload = {
        "schema_version": SCRUBBED_EVIDENCE_SCHEMA,
        "status": READY_STATUS,
        "authorization_seal_content_hash": seal["content_hash"],
        "baseline_commit": "b",
        "implementation_commit": "i",
        "task055g_report_content_hash": "r",
        "task055g_final_verifier_content_hash": "v",
        "plan_hash": EXPECTED_G_PLAN_HASH,
        "frontier_root": EXPECTED_G_FRONTIER_ROOT,
        "ordered_exact_daily_keys": seal["ordered_exact_daily_keys"],
        "ordered_key_root": seal["ordered_key_root"],
        "canary": seal["canary"],
        "root_identity_hashes": {},
        "parent_network_ledger_root": "p",
        "authorization_network_ledger_root": "a",
        "budgets": seal["budgets"],
        "fee_attestation_content_hash": "f",
        "operational_seal_content_hash": "o",
        "artifact_sha_catalog": [{"role": "bad", "relative_id": "/home/private", "sha256": "x"}],
        "semantic_source_root": "s",
        "engineering_blockers": [],
        "contains_absolute_paths": False,
        "contains_market_values": False,
        "contains_credentials": False,
    }
    package = publish_generation(tmp_path / "scrubbed", prefix="scrubbed_authorization_evidence", manifest_name="scrubbed_authorization_evidence.json", semantic=payload)
    with pytest.raises(Exception, match="sensitive_content"):
        verify_scrubbed_evidence_package(package["manifest_path"])


def test_scrubbed_package_verifies_as_standalone_file(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    from task_055_h.authorization import publish_scrubbed_evidence_package

    package = publish_scrubbed_evidence_package(seal, tmp_path / "native_scrubbed")
    standalone = tmp_path / "scrubbed_authorization_evidence.json"
    standalone.write_bytes(Path(package["manifest_path"]).read_bytes())
    verified = verify_scrubbed_evidence_package(standalone)
    assert verified["status"] == "passed"
    assert verified["package_content_hash"] == package["content_hash"]


def test_durable_journal_blocks_future_file_before_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    future = governed / "future.json"
    future.write_text('{"trade_date":"20260701"}\n')
    calls = 0
    original = Path.read_bytes

    def spy(path: Path):
        nonlocal calls
        if path == future:
            calls += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", spy)
    journal = DurableAccessJournal(governed, tmp_path / "journal")
    with pytest.raises(DurableAccessError, match="declared_date_exceeds_boundary"):
        journal.read_bytes("future.json", principal="test", expected_sha256="0" * 64, declared_max_date="20260701", date_parser="plan")
    assert calls == 0
    assert journal.summary()["event_count"] == 1


def test_parse_failure_is_durable(tmp_path: Path) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    source = governed / "bad.json"
    source.write_text("{bad")
    import hashlib

    journal = DurableAccessJournal(governed, tmp_path / "journal")
    with pytest.raises(Exception):
        journal.read_json("bad.json", principal="test", expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), declared_max_date=None, date_parser="none")
    events = journal._events()
    assert events[-1]["event"] == "parse_failure"


def test_opened_future_payload_is_durably_recorded_as_accessed(tmp_path: Path) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    source = governed / "future.json"
    source.write_text('{"trade_date":"20260701"}\n', encoding="utf-8")
    import hashlib

    journal = DurableAccessJournal(governed, tmp_path / "journal")
    with pytest.raises(DurableAccessError, match="actual_date_exceeds_boundary"):
        journal.read_bytes(
            "future.json",
            principal="test",
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            declared_max_date="20260630",
            date_parser="plan",
        )
    assert journal.summary()["prospective_holdout_accessed"] is True
    assert journal.summary()["max_read_date"] == "20260701"
    assert journal._events()[-1]["event"] == "opened_policy_violation"


def test_credential_file_rejects_inline_relative_symlink_and_forbidden_root(tmp_path: Path) -> None:
    forbidden = tmp_path / "governed"
    forbidden.mkdir()
    outside = tmp_path / "credential.txt"
    outside.write_text("token", encoding="utf-8")
    outside.chmod(0o600)

    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        load_file_credential_after_offline_gates(
            credential_file="inline-token",
            forbidden_root_identities={"governed": forbidden},
        )

    link = tmp_path / "credential-link"
    link.symlink_to(outside)
    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        load_file_credential_after_offline_gates(
            credential_file=link.resolve().parent / link.name,
            forbidden_root_identities={"governed": forbidden},
        )

    inside = forbidden / "credential.txt"
    inside.write_text("token", encoding="utf-8")
    inside.chmod(0o600)
    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        load_file_credential_after_offline_gates(
            credential_file=inside,
            forbidden_root_identities={"governed": forbidden},
        )


def test_credential_file_rejects_wrong_permissions_and_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    credential = tmp_path / "credential.txt"
    credential.write_text("token", encoding="utf-8")
    credential.chmod(0o644)
    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        load_file_credential_after_offline_gates(
            credential_file=credential,
            forbidden_root_identities={},
        )

    credential.chmod(0o600)
    current_uid = os.getuid()
    monkeypatch.setattr("task_055_h.network.os.getuid", lambda: current_uid + 1)
    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        load_file_credential_after_offline_gates(
            credential_file=credential,
            forbidden_root_identities={},
        )


def test_seal_root_identity_substitution_is_rejected(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    payload = json.loads(Path(seal["manifest_path"]).read_text(encoding="utf-8"))
    payload["canonical_roots"]["cache_data_relative_to_output"] = "substituted-cache"
    (tmp_path / "substituted-cache").mkdir()
    payload["root_identities"]["cache"] = _root_identity(tmp_path / "substituted-cache")
    forged = _republish_seal(tmp_path, payload)
    with pytest.raises(Exception):
        validate_authorization_seal(forged["manifest_path"])


def test_physical_spend_cannot_be_reset_to_zero_by_seal_summary(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    _append_spend_event(
        tmp_path / "transport_spend",
        {
            "event": "physical_post_started",
            "transport_hash": seal["canary"]["transport_hash"],
        },
    )
    with pytest.raises(Exception):
        validate_authorization_seal(seal["manifest_path"])


def test_nonzero_budget_blocks_before_tls_and_credential(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    payload = json.loads(Path(seal["manifest_path"]).read_text(encoding="utf-8"))
    payload["budgets"]["physical_attempts"] = 1
    forged = _republish_seal(tmp_path, payload)
    calls = {"tls": 0, "credential": 0}

    def tls() -> dict:
        calls["tls"] += 1
        return {}

    def credential() -> str:
        calls["credential"] += 1
        return "secret"

    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        ordered_future_canary_gate(
            authorization_seal=forged["manifest_path"],
            allow_network=True,
            sealed_plan_hash=forged["task055g_plan_hash"],
            tls_checker=tls,
            credential_loader=credential,
        )
    assert calls == {"tls": 0, "credential": 0}


def test_parent_plan_seal_substitution_is_rejected_before_credential(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    payload = json.loads(Path(seal["manifest_path"]).read_text(encoding="utf-8"))
    payload["task055g_plan_hash"] = "0" * 64
    forged = _republish_seal(tmp_path, payload)
    calls = {"tls": 0, "credential": 0}
    with pytest.raises(Exception, match="superseded_by_task055k_transport_broker"):
        ordered_future_canary_gate(
            authorization_seal=forged["manifest_path"],
            allow_network=True,
            sealed_plan_hash="0" * 64,
            tls_checker=lambda: calls.__setitem__("tls", calls["tls"] + 1) or {},
            credential_loader=lambda: calls.__setitem__("credential", calls["credential"] + 1) or "secret",
        )
    assert calls == {"tls": 0, "credential": 0}


def _root_identity(path: Path) -> dict:
    metadata = path.stat()
    return {
        "identity_hash": canonical_hash([str(path.resolve()), metadata.st_dev, metadata.st_ino]),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def _republish_seal(root: Path, payload: dict) -> dict:
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    return publish_generation(
        root / "authorization_seal",
        prefix="authorization_seal",
        manifest_name="authorization_seal.json",
        semantic=semantic,
    )
