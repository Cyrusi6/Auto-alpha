from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from task_055_a.policy import ScenarioPolicy
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker
from task_055_f.causal import _seal_round_one_plan
from task_055_f.fees import (
    FeeScheduleError,
    FeeScheduleCalculator,
    acquire_official_fee_documents,
    publish_fee_schedule_v2,
    validate_fee_schedule_v2,
    validate_official_fee_document_acquisition,
    validate_synthetic_fee_schedule_v2,
)
from task_055_f.read_ledger import AuditedReader, ReadLedgerError, canonical_hash
from task_055_f.truth_v2 import _classify_cell


def _cell() -> dict:
    return {
        "ts_code": "000001.SZ",
        "trade_date": "20240102",
        "bar_observed": False,
        "lifecycle": {"listed": True, "active": True},
        "corporate_action_validity": None,
        "membership": True,
        "membership_known": True,
        "valuation_closure_domain": True,
    }


def _coverage() -> list[dict]:
    return [
        {
            "api": "suspend_d",
            "source_kind": "tushare_cache_envelope.v2",
            "proof_quality": "validated_historical_governed_envelope",
            "outcome": "matching_row",
            "proof_hash": "p" * 64,
        }
    ]


def _event(kind: str, timing=None) -> dict:
    return {
        "ts_code": "000001.SZ",
        "trade_date": "20240102",
        "suspend_type": kind,
        "suspend_timing": timing,
        "row_hash": canonical_hash((kind, timing)),
        "evidence_hash": canonical_hash(("e", kind, timing)),
    }


def test_truth_v2_distinguishes_suspend_resume_and_timing():
    s = _classify_cell(_cell(), {"complete": False}, [_event("S")], _coverage(), [])
    assert s["state"] == "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE"
    assert s["modeled_stale_candidate"] is True
    assert s["stale_mark_authorized"] is False

    r = _classify_cell(_cell(), {"complete": False}, [_event("R")], _coverage(), [])
    assert r["state"] == "RESUME_EVENT_WITHOUT_SUSPENSION_EVIDENCE"
    assert r["modeled_stale_candidate"] is False

    conflict = _classify_cell(
        _cell(), {"complete": False}, [_event("S"), _event("R")], _coverage(), []
    )
    assert conflict["state"] == "SUSPENSION_EVENT_CONFLICT"

    intraday = _classify_cell(
        _cell(), {"complete": False}, [_event("S", "10:00-10:30")], _coverage(), []
    )
    assert intraday["state"] == "SUSPENSION_INTRADAY_UNSUPPORTED"


def test_read_ledger_does_not_open_unindexed_future_cache(tmp_path):
    governed = tmp_path / "governed"
    governed.mkdir()
    indexed = governed / "indexed.json"
    indexed.write_text(json.dumps({"records": [{"trade_date": "20260630"}]}), encoding="utf-8")
    unrelated = governed / "unrelated.json"
    unrelated.write_text(json.dumps({"records": [{"trade_date": "20260701"}]}), encoding="utf-8")
    reader = AuditedReader(governed)
    reader.read_json(indexed, component="test", dataset="indexed")
    assert reader.max_read_date == "20260630"
    assert {row["relative_path"] for row in reader.rows} == {"indexed.json"}
    with pytest.raises(ReadLedgerError, match="actual_read_date_exceeds_boundary"):
        reader.read_json(unrelated, component="test", dataset="unindexed")


def _fee_schedule(tmp_path: Path) -> Path:
    clause = "自2016年1月1日起，卖方印花税税率为0.1%，买方不征收；过户费率为0.002%；经手费率为0.00487%；证管费率为0.002%。"
    body = ("<html><body>中国证券费用正式通知" + clause * 20 + "</body></html>").encode()
    acquisition = acquire_official_fee_documents(
        output_root=tmp_path / "acquisition",
        documents=[
            {
                "document_id": "official_fee",
                "publisher": "上海证券交易所",
                "request_url": "https://www.sse.com.cn/official-fee",
            }
        ],
        allow_network=True,
        fetcher=lambda url: {
            "body": body,
            "final_url": url,
            "redirect_chain": [url],
            "http_status": 200,
            "tls_verified": True,
            "hostname_verified": True,
            "peer_certificate_sha256": "c" * 64,
            "retrieved_at": "2026-07-16T00:00:00+08:00",
            "response_headers": {"content-type": "text/html"},
        },
    )
    rules = []
    official_tokens = {
        "stamp_duty": ("0.1%", "卖方"),
        "transfer_fee": ("0.002%", "过户费"),
        "handling_fee": ("0.00487%", "经手费"),
        "securities_management_fee": ("0.002%", "证管费"),
    }
    rates = {
        "stamp_duty": 0.001,
        "transfer_fee": 0.00002,
        "handling_fee": 0.0000487,
        "securities_management_fee": 0.00002,
    }
    for market in ("SSE", "SZSE"):
        for side in ("BUY", "SELL"):
            for component, rate in rates.items():
                zero = component == "stamp_duty" and side == "BUY"
                rate_text, direction = official_tokens[component]
                rules.append(
                    {
                        "rule_id": f"{component}:{market}:{side}",
                        "component": component,
                        "market": market,
                        "side": side,
                        "effective_start": "20160104",
                        "effective_end": "20240530",
                        "rate": 0.0 if zero else rate,
                        "basis": "notional",
                        "rounding": "cent_half_up",
                        "minimum_cny": 0.0,
                        "explicit_zero": zero,
                        "evidence_class": "governed_official",
                        "document_id": "official_fee",
                        "page_or_clause": "正文",
                        "clause_text": clause,
                        "rate_text": "不征收" if zero else rate_text,
                        "effective_date_text": "2016年1月1日",
                        "direction_text": "买方" if side == "BUY" and component == "stamp_duty" else direction,
                    }
                )
            for component, rate, minimum in (
                ("commission", 0.0003, 5.0),
                ("slippage", 0.0005, 0.0),
                ("impact", 0.0005, 0.0),
            ):
                rules.append(
                    {
                        "rule_id": f"{component}:{market}:{side}",
                        "component": component,
                        "market": market,
                        "side": side,
                        "effective_start": "20160104",
                        "effective_end": "20240530",
                        "rate": rate,
                        "basis": "notional",
                        "rounding": "cent_half_up",
                        "minimum_cny": minimum,
                        "explicit_zero": False,
                        "evidence_class": "modeled",
                        "model_name": component,
                        "model_version": "v1",
                        "calibration_status": "uncalibrated_modeled",
                        "inclusion_contract": (
                            "exclusive_of_statutory_components"
                            if component == "commission"
                            else "not_a_fee_component"
                        ),
                    }
                )
    result = publish_fee_schedule_v2(
        output_root=tmp_path / "schedule",
        document_acquisition_manifest=acquisition["manifest_path"],
        rules=rules,
        simulation_start="20160104",
        simulation_end="20240530",
        policy_seal_hash="p" * 64,
        builder_code_hash="c" * 64,
        allow_synthetic_test_fixture=True,
    )
    return Path(result["manifest_path"])


def test_fee_v2_preserves_statutory_components_when_modeled_cost_doubles(tmp_path):
    schedule = _fee_schedule(tmp_path)
    with pytest.raises(Exception, match="synthetic_fee_schedule_forbidden"):
        validate_fee_schedule_v2(schedule)
    calculator = FeeScheduleCalculator(schedule, allow_synthetic_test_fixture=True)
    base = calculator.calculate(
        date="20240102",
        market="SSE",
        side="SELL",
        notional=100000.0,
        shares=1000,
        zero_all_costs=False,
        modeled_multiplier=1.0,
    )
    doubled = calculator.calculate(
        date="20240102",
        market="SSE",
        side="SELL",
        notional=100000.0,
        shares=1000,
        zero_all_costs=False,
        modeled_multiplier=2.0,
    )
    for component in ("stamp_duty", "transfer_fee", "handling_fee", "securities_management_fee"):
        assert doubled[component] == base[component]
    for component in ("commission", "slippage", "impact"):
        assert doubled[component] == 2 * base[component]


def test_fee_v2_rejects_modeled_commission_that_can_double_count_statutory_fees(tmp_path):
    schedule = _fee_schedule(tmp_path)
    manifest = json.loads(schedule.read_text(encoding="utf-8"))
    commission = next(rule for rule in manifest["rules"] if rule["component"] == "commission")
    commission["inclusion_contract"] = "includes_exchange_and_tax_fees"
    manifest["content_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    )
    schedule.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FeeScheduleError, match="modeled_fee_contract_invalid"):
        validate_synthetic_fee_schedule_v2(schedule)


def test_fee_document_acquisition_rejects_tampered_native_receipt(tmp_path):
    acquisition = acquire_official_fee_documents(
        output_root=tmp_path / "docs",
        documents=[
            {
                "document_id": "official",
                "publisher": "上海证券交易所",
                "request_url": "https://www.sse.com.cn/official-fee",
            }
        ],
        allow_network=True,
        fetcher=lambda url: {
            "body": b"<html><body>official evidence" + b"x" * 200 + b"</body></html>",
            "final_url": url,
            "http_status": 200,
            "tls_verified": True,
            "hostname_verified": True,
            "peer_certificate_sha256": "a" * 64,
            "retrieved_at": "2026-07-16T00:00:00+08:00",
            "response_headers": {"content-type": "text/html"},
        },
    )
    manifest_path = Path(acquisition["manifest_path"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["documents"][0]["retrieval_receipt"]["body_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FeeScheduleError):
        validate_official_fee_document_acquisition(
            manifest_path,
            allow_synthetic_test_fixture=True,
        )


def test_strict_simulator_records_actual_held_mark_metadata(tmp_path):
    calculator = FeeScheduleCalculator(_fee_schedule(tmp_path), allow_synthetic_test_fixture=True)
    policy = ScenarioPolicy(name="test", top_n=1, minimum_commission=0.0)
    shape = (3, 1)
    market = {
        "dates": ["20240102", "20240103", "20240104"],
        "assets": ["000001.SZ"],
        "open": np.full(shape, 10.0),
        "close": np.full(shape, 10.0),
        "valuation_open": np.full(shape, 10.0),
        "valuation_close": np.full(shape, 10.0),
        "adv": np.full(shape, 100000.0),
    }
    for point in ("open", "close"):
        market[f"valuation_{point}_method"] = np.full(shape, "OFFICIAL_CLOSE", dtype=object)
        market[f"valuation_{point}_source_date"] = np.asarray([[date] for date in market["dates"]], dtype=object)
        market[f"valuation_{point}_stale_age"] = np.zeros(shape, dtype=np.int32)
        market[f"valuation_{point}_evidence_id"] = np.full(shape, "e" * 64, dtype=object)
    observed = []
    EventLedgerSimulator(
        policy,
        fee_calculator=calculator,
        require_external_fee_schedule=True,
        require_explicit_valuation_marks=True,
    ).run(
        market,
        np.ones(shape),
        masks={name: np.ones(shape, dtype=bool) for name in ("buy", "sell", "select")},
        diagnostic_mark_observer_v2=lambda index, date, point, rows: observed.extend(rows),
    )
    assert observed
    assert all(row["shares"] > 0 for row in observed)
    assert all(row["evidence_id"] == "e" * 64 for row in observed)


def test_round_one_plan_is_exact_daily_only_and_not_total_gap_claim():
    truth = {
        "content_hash": "t" * 64,
        "records": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "suspend_type": "S"},
            {"ts_code": "000002.SZ", "trade_date": "20240103", "suspend_type": "none"},
        ],
    }
    trace = {
        "round_one_frontier": [("000001.SZ", "20240102"), ("000002.SZ", "20240103")],
        "missing_key_root": canonical_hash([("000001.SZ", "20240102"), ("000002.SZ", "20240103")]),
    }
    plan = _seal_round_one_plan(
        trace=trace,
        truth=truth,
        matrix_content_hash="m" * 64,
        bundle_content_hash="b" * 64,
        fee_content_hash="f" * 64,
        builder_code_hash="c" * 64,
    )
    assert plan["frontier_semantics"].startswith("round_1")
    assert plan["l2_requests"] == []
    assert all(row["api_name"] == "daily" and set(row["params"]) == {"ts_code", "trade_date"} for row in plan["requests"])
    assert plan["requests"][0]["post_empty_route"] == "historical_anchor_or_authority_blocker"
