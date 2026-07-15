from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from task_055_a.artifacts import SimulationArtifactError, publish_simulation_run
from task_055_a.policy import BASELINE
from task_055_a.simulator import EventLedgerSimulator
from task_055_b.fees import (
    FeeScheduleError,
    default_fee_rules,
    fee_components_for_fill,
    publish_fee_schedule,
    validate_fee_schedule,
)
from task_055_b.verifier import (
    Task055BVerificationError,
    build_mark_matrices,
    make_official_mark_rows,
    verify_task055b_simulation_run,
)


def _schedule(tmp_path):
    sources = {
        "stamp_duty": {"url": "https://example.invalid/stamp", "document_sha256": "a" * 64},
        "transfer_fee": {"url": "https://example.invalid/transfer", "document_sha256": "b" * 64},
    }
    return publish_fee_schedule(
        output_root=tmp_path / "fees",
        rules=default_fee_rules(acquired_at="2026-07-15T00:00:00+08:00", statutory_sources=sources),
        acquired_at="2026-07-15T00:00:00+08:00",
    )


def _run(tmp_path):
    dates = ["20240527", "20240528", "20240529"]
    assets = ["000001.SZ"]
    market = {
        "dates": dates,
        "assets": assets,
        "open": np.array([[10.0], [11.0], [12.0]]),
        "close": np.array([[10.5], [11.5], [12.5]]),
        "adv": np.full((3, 1), 100_000.0),
    }
    policy = replace(BASELINE, top_n=1)
    result = EventLedgerSimulator(policy).run(
        market,
        np.ones((3, 1)),
        masks={"tradable": np.ones((3, 1), dtype=bool)},
    )
    evidence = {
        (date, assets[0]): {"source": "governed_primary_bar", "partition_sha256": str(index + 1) * 64}
        for index, date in enumerate(dates)
    }
    marks = make_official_mark_rows(
        dates=dates,
        assets=assets,
        open_prices=market["open"],
        close_prices=market["close"],
        raw_quote_evidence=evidence,
    )
    schedule = _schedule(tmp_path)
    published = publish_simulation_run(
        output_root=tmp_path / "run",
        result=result,
        spec={"initial_cash": 1_000_000.0, "policy": policy.to_dict()},
        input_lineage={"bundle_hash": "c" * 64},
        market=market,
        benchmark={"dates": dates, "open": [4000.0, 4010.0, 4020.0]},
        valuation_marks=marks,
        fee_schedule_manifest=schedule["manifest_path"],
    )
    return published, schedule


def test_fee_schedule_is_immutable_and_separates_governed_modeled(tmp_path):
    schedule = _schedule(tmp_path)
    verified = validate_fee_schedule(tmp_path / "fees")
    assert verified["content_hash"] == schedule["content_hash"]
    assert verified["governed_fee_types"] == ["stamp_duty", "transfer_fee"]
    assert verified["modeled_fee_types"] == ["commission", "impact", "slippage"]
    manifest = tmp_path / "fees" / "generations" / schedule["generation_id"] / "fee_schedule_manifest.json"
    payload = json.loads(manifest.read_text())
    payload["rules"][0]["rate"] = 0.9
    manifest.write_text(json.dumps(payload))
    with pytest.raises(FeeScheduleError, match="content_hash_mismatch"):
        validate_fee_schedule(tmp_path / "fees")


def test_fee_boundaries_and_modeled_multiplier():
    sources = {
        "stamp_duty": {"url": "https://example.invalid/stamp", "document_sha256": "a" * 64},
        "transfer_fee": {"url": "https://example.invalid/transfer", "document_sha256": "b" * 64},
    }
    schedule = {"rules": default_fee_rules(acquired_at="now", statutory_sources=sources)}
    before = fee_components_for_fill({"date": "20230827", "side": "SELL", "notional": 100_000}, schedule)
    after = fee_components_for_fill({"date": "20230828", "side": "SELL", "notional": 100_000}, schedule)
    doubled = fee_components_for_fill(
        {"date": "20230828", "side": "SELL", "notional": 100_000}, schedule, modeled_cost_multiplier=2.0
    )
    assert before["stamp_duty"] == 100.0
    assert after["stamp_duty"] == 50.0
    assert doubled["stamp_duty"] == after["stamp_duty"]
    assert doubled["commission"] == after["commission"] * 2


def test_task055b_verifier_requires_explicit_fee_manifest(tmp_path):
    dates = ["20240527"]
    market = {"dates": dates, "assets": ["000001.SZ"], "open": [[10.0]], "close": [[10.0]], "adv": [[1000.0]]}
    result = EventLedgerSimulator(replace(BASELINE, top_n=0)).run(market, np.zeros((1, 1)), masks={"tradable": [[True]]})
    publish_simulation_run(
        output_root=tmp_path / "run", result=result, spec={"policy": BASELINE.to_dict()},
        input_lineage={}, market=market, benchmark={"dates": dates, "open": [4000.0]},
    )
    with pytest.raises(Task055BVerificationError, match="embedded_fee_schedule_missing"):
        verify_task055b_simulation_run(tmp_path / "run")


def test_independent_fee_and_mark_verification_detects_tampering(tmp_path):
    published, schedule = _run(tmp_path)
    verified = verify_task055b_simulation_run(tmp_path / "run", expected_fee_schedule=schedule["manifest_path"])
    assert verified["status"] == "verified"
    marks = tmp_path / "run" / "generations" / published["generation_id"] / "valuation_marks.jsonl"
    rows = [json.loads(line) for line in marks.read_text().splitlines()]
    rows[0]["mark_price"] += 1.0
    marks.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(Exception, match="run_partition_mismatch:valuation_marks.jsonl"):
        verify_task055b_simulation_run(tmp_path / "run")


def test_stale_mark_requires_no_trade_evidence_and_source_transform():
    dates = ["20240527", "20240528"]
    assets = ["000001.SZ"]
    raw_open = np.array([[10.0], [np.nan]])
    raw_close = np.array([[10.5], [np.nan]])
    evidence = {(dates[0], assets[0]): {"source": "primary", "sha256": "a" * 64}}
    rows = make_official_mark_rows(
        dates=dates[:1], assets=assets, open_prices=raw_open[:1], close_prices=raw_close[:1], raw_quote_evidence=evidence
    )
    for point, price in (("open", 10.0), ("close", 10.5)):
        ev = {"status": "single_vendor_s_without_provenance"}
        rows.append({
            "schema_version": "task055b_security_date_mark_evidence_v1", "date": dates[1], "asset": assets[0],
            "reporting_point": point, "mark_price": price, "mark_method": "STALE_OFFICIAL_NON_TRADING",
            "mark_source_date": dates[0], "stale_age_trade_days": 1, "market_session_state": "DATA_SOURCE_GAP",
            "execution_allowed": False, "corporate_action_transform": {"type": "none", "price_multiplier": 1.0},
            "stale_mark_notional": 0.0, "stale_mark_nav_ratio": 0.0, "evidence": ev,
            "evidence_hash": __import__("task_055_a.artifacts", fromlist=["canonical_hash"]).canonical_hash(ev),
        })
    _, _, issues = build_mark_matrices(rows, dates=dates, assets=assets, raw_open=raw_open, raw_close=raw_close)
    assert any(issue.startswith("blocked_mark_state_used") for issue in issues)
    assert any(issue.startswith("stale_mark_provenance_invalid") for issue in issues)


def test_governed_simulator_uses_fee_manifest_not_embedded_policy_rates(tmp_path):
    from dataclasses import replace
    from task_055_b.simulator import GovernedEventLedgerSimulator

    schedule = _schedule(tmp_path)
    policy = replace(
        BASELINE,
        top_n=1,
        minimum_commission=999.0,
        commission_rate=0.9,
        stamp_duty_rate=0.9,
        transfer_fee_rate=0.9,
        slippage_bps=9000.0,
        impact_bps=9000.0,
    )
    simulator = GovernedEventLedgerSimulator(policy, fee_schedule=schedule["manifest_path"])
    costs = simulator._costs("SELL", 100_000.0, "20230828")
    assert costs["stamp_duty"] == 50.0
    assert costs["commission"] == pytest.approx(30.0)
    assert costs["total"] < 1_000.0


def test_governed_simulator_requires_explicit_valuation_marks(tmp_path):
    from task_055_b.simulator import GovernedEventLedgerSimulator

    schedule = _schedule(tmp_path)
    simulator = GovernedEventLedgerSimulator(BASELINE, fee_schedule=schedule["manifest_path"])
    with pytest.raises(ValueError, match="explicit_valuation_marks_required"):
        simulator.run(
            {"dates": ["20240527"], "assets": ["000001.SZ"], "open": [[10.0]], "close": [[10.0]], "adv": [[1000.0]]},
            [[0.0]],
        )
