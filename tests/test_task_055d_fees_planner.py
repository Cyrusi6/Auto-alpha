from __future__ import annotations

import hashlib
import json

import pytest

from task_055_d.fees import FeeScheduleV2Error, publish_fee_schedule_v2, validate_fee_schedule_v2
from task_055_d.planner import PlanError, build_l2_child_plan


def _document(tmp_path):
    text = "Official Exchange Notice " + "effective clause: governed fee rule " + ("x" * 300)
    path = tmp_path / "official.html"
    path.write_text(text)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _rules(tmp_path):
    document, digest = _document(tmp_path)
    rules = []
    for component in ("stamp_duty", "transfer_fee", "handling_fee"):
        for market in ("SSE", "SZSE"):
            for side in ("BUY", "SELL"):
                zero = component == "stamp_duty" and side == "BUY"
                rules.append({
                    "rule_id": f"{component}_{market}_{side}", "component": component,
                    "market": market, "side": side, "effective_start": "20240101", "effective_end": "20241231",
                    "rate": 0.0 if zero else 0.00001, "basis": "notional", "rounding": "cent_half_up",
                    "minimum_cny": 0.0, "explicit_zero": zero, "evidence_class": "governed_official",
                    "publisher": "Shanghai Stock Exchange", "official_url": "https://www.sse.com.cn/official",
                    "document_relative_path": document.name, "document_sha256": digest,
                    "retrieval_receipt": "receipt-1", "page_or_clause": "clause 1",
                    "effective_clause": "effective clause: governed fee rule",
                })
    for component in ("commission", "slippage", "impact"):
        for market in ("SSE", "SZSE"):
            for side in ("BUY", "SELL"):
                rules.append({
                    "rule_id": f"{component}_{market}_{side}", "component": component,
                    "market": market, "side": side, "effective_start": "20240101", "effective_end": "20241231",
                    "rate": 0.0001, "basis": "notional", "rounding": "cent_half_up", "minimum_cny": 0.0,
                    "explicit_zero": False, "evidence_class": "modeled", "model_name": component,
                    "model_version": "v1", "calibration_status": "uncalibrated_modeled",
                })
    return rules


def test_fee_schedule_v2_requires_documents_explicit_zero_and_complete_coverage(tmp_path):
    published = publish_fee_schedule_v2(output_root=tmp_path / "out", document_root=tmp_path, rules=_rules(tmp_path), simulation_start="20240101", simulation_end="20241231")
    assert validate_fee_schedule_v2(published["manifest_path"])["content_hash"] == published["content_hash"]
    rules = _rules(tmp_path)
    rules = [rule for rule in rules if rule["rule_id"] != "stamp_duty_SSE_BUY"]
    with pytest.raises(FeeScheduleV2Error, match="coverage_gap"):
        publish_fee_schedule_v2(output_root=tmp_path / "bad", document_root=tmp_path, rules=rules, simulation_start="20240101", simulation_end="20241231")


def test_fee_schedule_v2_rejects_dummy_url_and_document_tampering(tmp_path):
    rules = _rules(tmp_path)
    rules[0]["official_url"] = "https://example.invalid/fake"
    with pytest.raises(FeeScheduleV2Error, match="not_official"):
        publish_fee_schedule_v2(output_root=tmp_path / "bad", document_root=tmp_path, rules=rules, simulation_start="20240101", simulation_end="20241231")
    published = publish_fee_schedule_v2(output_root=tmp_path / "out", document_root=tmp_path, rules=_rules(tmp_path), simulation_start="20240101", simulation_end="20241231")
    copied = next((tmp_path / "out" / "generations" / published["generation_id"] / "documents").rglob("*.html"))
    copied.write_text("tampered")
    with pytest.raises(FeeScheduleV2Error, match="document_sha"):
        validate_fee_schedule_v2(published["manifest_path"])


def test_l2_cannot_exist_before_applied_l1(tmp_path):
    with pytest.raises(PlanError, match="requires_applied"):
        build_l2_child_plan(l1_plan={"content_hash": "a" * 64}, l1_reconciliation={"status": "blocked"}, remaining_rows=[], output_root=tmp_path)


def test_transport_and_evidence_use_identity_are_separate():
    from task_055_d.cache import evidence_use_identity, transport_identity
    from task_055_d.contracts import DAILY_FIELDS
    params = {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240131"}
    transport = transport_identity("daily", params, DAILY_FIELDS)
    left = evidence_use_identity(task="task_055_d", stage="L1", parent_plan_hash="a" * 64, valuation_key_hash="b" * 64, transport_hash=transport)
    right = evidence_use_identity(task="task_055_d", stage="L1", parent_plan_hash="a" * 64, valuation_key_hash="c" * 64, transport_hash=transport)
    assert left != right
    assert transport == transport_identity("daily", params, DAILY_FIELDS)
