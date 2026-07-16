from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from task_055_g.access import AccessBroker, AccessPlanError, canonical_hash, validate_access_ledger
from task_055_g.contracts import ACCESS_PLAN_SCHEMA


def _plan(tmp_path: Path, entry: dict) -> Path:
    semantic = {
        "schema_version": ACCESS_PLAN_SCHEMA,
        "status": "sealed",
        "plan_scope": "test",
        "max_allowed_date": "20260630",
        "entry_count": 1,
        "entries_root": canonical_hash([entry]),
        "entries": [entry],
    }
    payload = semantic | {"content_hash": canonical_hash(semantic), "generation_id": "test"}
    path = tmp_path / "access_plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _entry(path: Path, sha: str, *, maximum: str = "20260630", parser: str = "daily_envelope") -> dict:
    return {
        "relative_path": path.name,
        "dataset_role": "daily_cache",
        "parent_generation": "test_generation",
        "expected_sha256": sha,
        "read_mode": "json",
        "date_parser": parser,
        "declared_min_date": None,
        "declared_max_date": maximum,
        "byte_range": None,
    }


def test_future_declared_file_is_blocked_before_open(tmp_path):
    payload = {"records": [{"trade_date": "20260701"}]}
    source = tmp_path / "future.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    opens = []
    plan = _plan(tmp_path, _entry(source, hashlib.sha256(source.read_bytes()).hexdigest(), maximum="20260701"))
    with pytest.raises(AccessPlanError, match="declared_read_range_exceeds_boundary"):
        AccessBroker(tmp_path, plan, open_bytes=lambda path: opens.append(path) or path.read_bytes())
    assert opens == []


def test_opened_future_payload_sets_accessed_even_when_raising(tmp_path):
    source = tmp_path / "payload.json"
    source.write_text(json.dumps({"records": [{"trade_date": "20260701"}]}), encoding="utf-8")
    plan = _plan(tmp_path, _entry(source, hashlib.sha256(source.read_bytes()).hexdigest()))
    broker = AccessBroker(tmp_path, plan)
    with pytest.raises(AccessPlanError, match="actual_read_date_exceeds_boundary"):
        broker.read_json(source, principal="test")
    assert broker.prospective_holdout_accessed is True
    assert broker.rows[-1]["decision"] == "opened_policy_violation"


def test_dataset_specific_parser_checks_source_date(tmp_path):
    source = tmp_path / "inventory.json"
    source.write_text(json.dumps({"source_date": "20260701"}), encoding="utf-8")
    entry = _entry(source, hashlib.sha256(source.read_bytes()).hexdigest(), parser="inventory")
    plan = _plan(tmp_path, entry)
    broker = AccessBroker(tmp_path, plan)
    with pytest.raises(AccessPlanError):
        broker.read_json(source, principal="test")


def test_unsealed_path_never_opens(tmp_path):
    source = tmp_path / "sealed.json"
    source.write_text("{}", encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    opens = []
    plan = _plan(tmp_path, _entry(source, hashlib.sha256(source.read_bytes()).hexdigest(), parser="manifest_metadata"))
    broker = AccessBroker(tmp_path, plan, open_bytes=lambda path: opens.append(path) or path.read_bytes())
    with pytest.raises(AccessPlanError, match="access_path_not_sealed"):
        broker.read_json(other, principal="test")
    assert opens == []


def test_access_ledger_recomputes_future_flag(tmp_path):
    source = tmp_path / "payload.json"
    source.write_text(json.dumps({"trade_date": "20240530"}), encoding="utf-8")
    plan = _plan(tmp_path, _entry(source, hashlib.sha256(source.read_bytes()).hexdigest()))
    broker = AccessBroker(tmp_path, plan)
    assert broker.read_json(source, principal="test")["trade_date"] == "20240530"
    ledger = broker.publish_ledger(tmp_path / "ledger")
    validated = validate_access_ledger(ledger["manifest_path"], plan=plan)
    assert validated["decision_counts"]["opened_allowed"] == 1
    assert validated["prospective_holdout_accessed"] is False
