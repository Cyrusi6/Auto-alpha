from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from task_055_a.policy import ScenarioPolicy
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker
from task_055_e.domains import _minimal_network_plan, _reproject_anchors
from task_055_e.provenance import (
    _classify,
    _legacy_cache_key,
    _legacy_stable_hash,
    _scan_legacy_daily_cache,
    _scan_normalized_daily_jsonl,
)
from task_055_e.run import Task055EOfflineError, _validate_no_network


def _bar(code: str, date: str, close: float = 10.0) -> dict:
    return {
        "ts_code": code,
        "trade_date": date,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "pre_close": close - 0.1,
        "volume": 1000.0,
        "amount": 10000.0,
    }


def test_naked_normalized_row_cannot_become_raw_repair():
    evidence = {
        "lake": [{"record_valid": True, "formal_reuse_eligible": False, "record": _bar("000001.SZ", "20240102")}],
        "matrix": [{"record_valid": False}],
    }
    category, reason, reusable = _classify(evidence)
    assert category == "raw/lake/matrix_conflict"
    assert "presence" in reason
    assert reusable is None


def test_normalized_scan_preserves_byte_level_provenance(tmp_path):
    root = tmp_path / "lake"
    path = root / "freeze" / "records.jsonl"
    path.parent.mkdir(parents=True)
    rows = [_bar("000001.SZ", "20240102"), _bar("000002.SZ", "20240102", 11.0)]
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    import hashlib

    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    result = _scan_normalized_daily_jsonl(
        governed_root=root,
        path=path,
        target_keys={("000001.SZ", "20240102")},
        source_kind="test",
        source_generation="g1",
        expected_sha256=expected,
    )
    assert result["summary"]["record_count"] == 2
    assert result["summary"]["matched_target_rows"] == 1
    proof = result["provenance"][0]
    assert proof["raw_envelope_validated"] is False
    assert proof["formal_reuse_eligible"] is False
    with path.open("rb") as handle:
        handle.seek(proof["byte_offset"])
        assert handle.read(proof["byte_length"]).startswith(b'{"amount"')


def test_anchor_reprojection_uses_real_prior_close_not_old_source_date():
    codes = ["000001.SZ"]
    dates = [f"2024{month:02d}{day:02d}" for month, day in ((1, 2), (1, 3), (1, 4), (1, 5))]
    close = np.array([[10.0, 0.0, 0.0, 0.0]])
    validity = np.array([[True, False, False, False]])
    truth = {
        "records": [
            {
                "ts_code": "000001.SZ",
                "trade_date": dates[-1],
                "valuation_domain_intersection": True,
                "state": "VENDOR_DAILY_NON_TRADING_MODELED",
                "daily_bar": "absent",
                "listed": True,
                "active": True,
                "lifecycle_corporate_action_conflict": False,
                "evidence_hash": "e" * 64,
            }
        ]
    }
    rows, _ = _reproject_anchors(
        truth=truth,
        codes=codes,
        dates=dates,
        close=close,
        close_valid=validity,
        code_index={"000001.SZ": 0},
        date_index={date: index for index, date in enumerate(dates)},
        simulation_start="20240102",
        simulation_end="20240105",
    )
    assert rows == []
    extended_dates = [f"2024{index // 28 + 1:02d}{index % 28 + 1:02d}" for index in range(252)]
    extended_close = np.zeros((1, 252))
    extended_valid = np.zeros((1, 252), dtype=bool)
    extended_close[0, 0] = 10.0
    extended_valid[0, 0] = True
    truth["records"][0]["trade_date"] = extended_dates[-1]
    rows, _ = _reproject_anchors(
        truth=truth,
        codes=codes,
        dates=extended_dates,
        close=extended_close,
        close_valid=extended_valid,
        code_index={"000001.SZ": 0},
        date_index={date: index for index, date in enumerate(extended_dates)},
        simulation_start=extended_dates[0],
        simulation_end=extended_dates[-1],
    )
    assert rows[0]["cause"] == "stale_age_gt_250"
    assert rows[0]["prior_close_date"] == extended_dates[0]
    assert rows[0]["stale_age_trade_days"] == 251


def test_legacy_physical_cache_is_not_formal_reuse(tmp_path):
    root = tmp_path / "lake"
    cache = root / "cache" / ".cache" / "tushare"
    cache.mkdir(parents=True)
    (root / "data").mkdir()
    date = "20240102"
    params = {"start_date": date, "end_date": date}
    fields = "ts_code,trade_date,open,high,low,close,pre_close,vol,amount"
    key = _legacy_cache_key("daily", params, fields)
    record = _bar("000001.SZ", date)
    record["vol"] = record.pop("volume")
    payload = {"metadata": {"api_name": "daily", "params_hash": _legacy_stable_hash(params), "records": 1}, "records": [record]}
    (cache / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")
    audit = {"api_name": "daily", "dataset": "daily_bars", "start_date": date, "end_date": date, "status": "success", "records": 1}
    (root / "data" / "api_audit.jsonl").write_text(json.dumps(audit) + "\n", encoding="utf-8")
    scan = _scan_legacy_daily_cache(governed_root=root, cache_root=cache, target_keys={("000001.SZ", date)})
    row = scan["observations"][("000001.SZ", date)][0]
    assert row["record_valid"] is True
    assert row["raw_envelope_validated"] is False
    assert row["formal_reuse_eligible"] is False
    category, _, reusable = _classify({"legacy_daily": [row]})
    assert category == "raw/lake/matrix_conflict"
    assert reusable is None


def test_network_plan_uses_only_causal_missing_keys():
    reconciliation = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "classification": "complete_range_response_without_row", "formal_raw_repair_eligible": False},
        {"ts_code": "000002.SZ", "trade_date": "20240103", "classification": "genuinely_not_found_offline", "formal_raw_repair_eligible": False},
    ]
    plan = _minimal_network_plan(
        reconciliation=reconciliation,
        causal={"unique_missing_keys": [("000002.SZ", "20240103")]},
        trade_dates=["20240102", "20240103"],
        simulation_start="20240102",
        simulation_end="20240103",
    )
    assert plan["remaining_stock_count"] == 1
    assert plan["estimated_daily_request_count"] == 1
    assert plan["daily_requests"][0]["ts_code"] == "000002.SZ"
    assert plan["network_executed"] is False


def test_mark_observer_reports_actual_held_asset_before_blocker():
    policy = ScenarioPolicy(name="test", top_n=1, minimum_commission=0.0, commission_rate=0.0, stamp_duty_rate=0.0, transfer_fee_rate=0.0, slippage_bps=0.0, impact_bps=0.0)
    market = {
        "dates": ["20240102", "20240103", "20240104"],
        "assets": ["000001.SZ"],
        "open": np.array([[10.0], [10.0], [10.0]]),
        "close": np.array([[10.0], [10.0], [10.0]]),
        "valuation_open": np.array([[10.0], [10.0], [np.nan]]),
        "valuation_close": np.array([[10.0], [10.0], [np.nan]]),
        "adv": np.array([[100000.0], [100000.0], [100000.0]]),
    }
    observed = []
    with pytest.raises(SimulationDataBlocker):
        EventLedgerSimulator(policy).run(
            market,
            np.array([[1.0], [1.0], [1.0]]),
            masks={"buy": np.ones((3, 1), dtype=bool), "sell": np.ones((3, 1), dtype=bool), "select": np.ones((3, 1), dtype=bool)},
            diagnostic_mark_observer=lambda index, date, point, held, prices: observed.append((index, date, point, dict(held), prices)),
        )
    assert observed[-1][1:3] == ("20240104", "open_pretrade")
    assert observed[-1][3] == {"000001.SZ": 10000}
    assert np.isnan(observed[-1][4][0])
    assert observed[-1][4].flags.writeable is False


def test_offline_config_rejects_any_network_authorization():
    _validate_no_network({"allow_network": False, "request_budget": 0})
    with pytest.raises(Task055EOfflineError):
        _validate_no_network({"allow_network": True})
    with pytest.raises(Task055EOfflineError):
        _validate_no_network({"request_plan_hash": "sealed"})
