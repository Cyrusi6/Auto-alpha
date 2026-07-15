from __future__ import annotations

import json

import pytest

from task_055_b.evidence import (
    EvidenceError,
    SecurityDateState,
    classify_security_date,
    publish_evidence_overlay,
    validate_evidence_overlay,
)
from task_055_b.preflight import run_valuation_closure_preflight
from task_055_b.valuation import (
    ValuationError,
    build_valuation_marks,
    publish_valuation_overlay,
    validate_valuation_overlay,
)


BAR = {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "vol": 1000.0, "amount": 10000.0}


def _row(date="20240527", **updates):
    row = {
        "ts_code": "600000.SH",
        "trade_date": date,
        "trade_calendar_session": True,
        "primary_bar": BAR,
        "membership": True,
        "membership_known": True,
        "listed": True,
        "active": True,
        "valuation_required": True,
        "signal_used": False,
        "target_used": False,
        "source_hashes": {"daily": "a" * 64},
    }
    row.update(updates)
    return row


def _official_no_trade(date="20240528", **updates):
    row = _row(date, primary_bar=None, official_no_trade_proof={
        "source_sha256": "b" * 64,
        "request_or_document_hash": "c" * 64,
    })
    row.update(updates)
    return row


def test_deleted_real_bar_is_gap_and_cannot_carry():
    evidence = classify_security_date(_row(primary_bar=None), review_version="v1")
    assert evidence.state == SecurityDateState.DATA_SOURCE_GAP
    marks = build_valuation_marks([evidence.to_dict()], initial_authoritative_marks={
        "600000.SH": {"price": 9.9, "source_date": "20240524"}
    })
    assert all(mark.mark_price is None for mark in marks)


@pytest.mark.parametrize("suspend_type,timing", [("S", None), ("R", None), ("S", "intraday")])
def test_unproven_suspension_variants_do_not_authorize_stale_carry(suspend_type, timing):
    row = _row(primary_bar=None, suspension_rows=[{"suspend_type": suspend_type, "suspend_timing": timing}])
    evidence = classify_security_date(row, review_version="v1")
    assert evidence.state == SecurityDateState.DATA_SOURCE_GAP


def test_exact_empty_api_response_is_not_non_trading_proof():
    row = _row(primary_bar=None, vendor_daily_no_trade_proof={
        "source_sha256": "d" * 64,
        "request_or_document_hash": "e" * 64,
        "query_geometry": "exact_trade_date_and_security_window",
        "bar_row_count": 0,
        "cross_geometry_agrees": True,
        "item_count": 0,
    })
    assert classify_security_date(row, review_version="v1").state == SecurityDateState.DATA_SOURCE_GAP


def test_modeled_daily_no_trade_requires_exact_s_and_cross_geometry():
    row = _row(primary_bar=None, suspension_rows=[{"suspend_type": "S", "suspend_timing": None}], vendor_daily_no_trade_proof={
        "source_sha256": "d" * 64,
        "request_or_document_hash": "e" * 64,
        "query_geometry": "exact_trade_date_and_security_window",
        "bar_row_count": 0,
        "cross_geometry_agrees": True,
    })
    evidence = classify_security_date(row, review_version="v1")
    assert evidence.state == SecurityDateState.VENDOR_DAILY_NON_TRADING_MODELED
    marks = build_valuation_marks([evidence.to_dict()], initial_authoritative_marks={
        "600000.SH": {"price": 10.0, "source_date": "20240527"}
    })
    assert all(not mark.execution_allowed for mark in marks)
    assert all(mark.mark_price == 10.0 for mark in marks)


def test_source_normalization_zero_fill_is_explicit_blocker():
    evidence = classify_security_date(_row(primary_bar={**BAR, "open": 0.0}, source_normalization_zero_fill=True), review_version="v1")
    assert evidence.state == SecurityDateState.SOURCE_NORMALIZATION_ZERO_FILL


def test_removed_member_can_sell_and_remains_valued():
    evidence = classify_security_date(_row(membership=False), review_version="v1")
    marks = build_valuation_marks([evidence.to_dict()], holdings_by_key={"600000.SH|20240527": 100})
    assert all(not mark.buy_allowed for mark in marks)
    assert all(mark.sell_allowed for mark in marks)
    assert marks[-1].mark_price == pytest.approx(10.2)


def test_corporate_action_preserves_stale_interval_value():
    rows = [
        classify_security_date(_row(), review_version="v1").to_dict(),
        classify_security_date(_official_no_trade(corporate_action={
            "valuation_transform_proven": True,
            "source_sha256": "f" * 64,
            "share_ratio": 2.0,
            "cash_dividend_per_old_share": 0.2,
        }), review_version="v1").to_dict(),
    ]
    marks = build_valuation_marks(rows, holdings_by_key={"600000.SH|20240528": 100})
    stale_close = marks[-1]
    assert stale_close.mark_price == pytest.approx((10.2 - 0.2) / 2.0)
    assert stale_close.continuity_error_cny <= 0.01


def test_recovery_price_only_changes_marks_from_recovery_date():
    rows = [
        classify_security_date(_row(), review_version="v1").to_dict(),
        classify_security_date(_official_no_trade(), review_version="v1").to_dict(),
        classify_security_date(_row("20240529", primary_bar={**BAR, "open": 12.0, "close": 12.5}), review_version="v1").to_dict(),
    ]
    baseline = build_valuation_marks(rows)
    changed_rows = json.loads(json.dumps(rows))
    changed_rows[-1]["primary_bar"]["open"] = 20.0
    changed_rows[-1]["primary_bar"]["close"] = 21.0
    changed = build_valuation_marks(changed_rows)
    assert [mark.to_dict() for mark in baseline[:4]] == [mark.to_dict() for mark in changed[:4]]
    assert baseline[-1].mark_price != changed[-1].mark_price


def test_overlay_is_content_addressed_and_tamper_detected(tmp_path):
    evidence = publish_evidence_overlay(tmp_path / "evidence", [_row(), _official_no_trade()], source_lineage={"seal": "a" * 64}, review_version="v1")
    valuation = publish_valuation_overlay(tmp_path / "valuation", evidence_overlay=tmp_path / "evidence")
    assert validate_evidence_overlay(tmp_path / "evidence")["content_hash"] == evidence["content_hash"]
    assert validate_valuation_overlay(tmp_path / "valuation", evidence_overlay=tmp_path / "evidence")["content_hash"] == valuation["content_hash"]
    marks = tmp_path / "valuation" / "generations" / valuation["generation_id"] / "valuation_marks.jsonl"
    marks.write_text(marks.read_text().replace("10.2", "99.0", 1))
    with pytest.raises(ValuationError, match="valuation_partition_mismatch"):
        validate_valuation_overlay(tmp_path / "valuation", evidence_overlay=tmp_path / "evidence")


def test_preflight_splits_factor_and_continuous_readiness(tmp_path):
    rows = [_row(), _row("20240528", primary_bar=None, valuation_required=True, signal_used=False)]
    publish_evidence_overlay(tmp_path / "evidence", rows, source_lineage={"seal": "a" * 64}, review_version="v1")
    publish_valuation_overlay(tmp_path / "valuation", evidence_overlay=tmp_path / "evidence")
    report = run_valuation_closure_preflight(evidence_overlay=tmp_path / "evidence", valuation_overlay=tmp_path / "valuation")
    assert report["status"] == "blocked"
    assert report["readiness"]["factor_replay_ready"] is True
    assert report["readiness"]["continuous_portfolio_valuation_ready"] is False
    assert report["readiness"]["future_research_data_ready"] is False


def test_duplicate_security_date_is_rejected(tmp_path):
    with pytest.raises(EvidenceError, match="duplicate_security_date_key"):
        publish_evidence_overlay(tmp_path, [_row(), _row()], source_lineage={}, review_version="v1")


def test_lifecycle_termination_without_settlement_is_unresolved():
    row = _row(primary_bar=None, lifecycle_event={
        "event_type": "delisted", "source_sha256": "a" * 64, "request_or_document_hash": "b" * 64
    })
    evidence = classify_security_date(row, review_version="v1")
    assert evidence.state == SecurityDateState.LIFECYCLE_TERMINATED
    assert all(mark.mark_price is None for mark in build_valuation_marks([evidence.to_dict()]))
