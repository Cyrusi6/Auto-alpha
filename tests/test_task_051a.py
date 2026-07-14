from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from backtest.simulator import AShareBacktestSimulator
from backtest.time_contract import BacktestTimeContract
from data_lake.freeze import create_research_freeze
from data_lake.models import DatasetVersionRecord
from feature_factory.validity import build_feature_validity_tensor
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from research_firewall import DateFirewall, FirewallAccessError, ResearchDataView
from risk_model.covariance import estimate_return_covariance
from universe.historical import (
    HistoricalUniverseBlocker,
    SnapshotPolicy,
    align_daily_fields,
    build_historical_index_universe,
    build_lifecycle_mask,
    build_st_masks,
    normalize_suspensions,
    target_available_mask,
)
from validation_lab.eligibility import build_common_eligibility, eligible_date_segments
from validation_lab.metrics import evaluate_factor_splits
from validation_lab.models import ValidationSplit
from validation_lab.policy import EngineeringRobustnessPolicy


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _snapshot_fixture(tmp_path: Path):
    calendar = tmp_path / "calendar.jsonl"
    dates = ["20240102", "20240103", "20240104", "20240201", "20240320"]
    _write_jsonl(calendar, ({"trade_date": date, "is_open": True} for date in dates))
    members = tmp_path / "members.jsonl"
    rows = []
    for date, codes in [("20240102", ["A", "B"]), ("20240201", ["B", "C"])]:
        rows.extend({"index_code": "000300.SH", "trade_date": date, "ts_code": code, "weight": 50.0} for code in codes)
    _write_jsonl(members, rows)
    policy = SnapshotPolicy("000300.SH", expected_member_count=2, min_weight_sum=99.5, max_weight_sum=100.5, max_staleness_calendar_days=45)
    return members, calendar, policy


def test_snapshot_full_replacement_transitions_and_staleness(tmp_path):
    members, calendar, policy = _snapshot_fixture(tmp_path)
    result = build_historical_index_universe(members, calendar, tmp_path / "out", policy)
    root = Path(result.output_dir)
    codes = json.loads((root / "ts_codes.json").read_text())
    dates = json.loads((root / "trade_dates.json").read_text())
    matrix = np.load(root / "index_membership.npy")
    known = np.load(root / "membership_known.npy")
    assert matrix[codes.index("A"), dates.index("20240104")]
    assert not matrix[codes.index("A"), dates.index("20240201")]
    assert matrix[codes.index("C"), dates.index("20240201")]
    assert not known[dates.index("20240320")]


def test_future_snapshot_does_not_change_past_matrix(tmp_path):
    members, calendar, policy = _snapshot_fixture(tmp_path)
    first = build_historical_index_universe(members, calendar, tmp_path / "out", policy)
    first_matrix = np.load(Path(first.output_dir) / "index_membership.npy").copy()
    with members.open("a") as handle:
        for code in ["A", "C"]:
            handle.write(json.dumps({"index_code": "000300.SH", "trade_date": "20240320", "ts_code": code, "weight": 50.0}) + "\n")
    second = build_historical_index_universe(members, calendar, tmp_path / "out", policy)
    second_matrix = np.load(Path(second.output_dir) / "index_membership.npy")
    assert np.array_equal(first_matrix[:, :4], second_matrix[: first_matrix.shape[0], :4])


def test_multi_index_isolation_and_alias_requires_proof(tmp_path):
    members, calendar, policy = _snapshot_fixture(tmp_path)
    with members.open("a") as handle:
        handle.write(json.dumps({"index_code": "000905.SH", "trade_date": "20240102", "ts_code": "X", "weight": 100.0}) + "\n")
    result = build_historical_index_universe(members, calendar, tmp_path / "out", policy)
    assert "X" not in json.loads((Path(result.output_dir) / "ts_codes.json").read_text())
    with pytest.raises(HistoricalUniverseBlocker):
        build_historical_index_universe(members, calendar, tmp_path / "alias", SnapshotPolicy("399300.SZ", expected_member_count=2))


def test_lifecycle_and_st_are_point_in_time():
    dates = ["20200101", "20210101", "20220101", "20230101"]
    active = build_lifecycle_mask([{"ts_code": "A", "list_date": "20200101", "delist_date": "20220101", "list_status": "D", "is_st": True}], ["A"], dates)
    assert active.tolist() == [[True, True, False, False]]
    st, known = build_st_masks([{"ts_code": "A", "start_date": "20210101", "end_date": "20230101", "ann_date": "20220101", "name": "ST A"}], ["A"], dates)
    assert not st[0, 1] and st[0, 2] and known[0, 2]


def test_exact_market_alignment_suspension_and_target_validity():
    values, validity = align_daily_fields([{"ts_code": "A", "trade_date": "20240102", "open": 10.0}], ["A"], ["20240102", "20240103"], ["open"])
    assert values["open"][0, 0] == 10.0 and np.isnan(values["open"][0, 1])
    assert validity["open"].tolist() == [[True, False]]
    normalized, blockers = normalize_suspensions([{"ts_code": "A", "trade_date": "20240103", "suspend_type": "S", "suspend_timing": "D"}, {"ts_code": "B"}])
    assert normalized[0]["start_date"] == "20240103" and blockers == ["suspension_date_unknown:B"]
    assert target_available_mask(np.array([[True, False, True], [True, True, True]])).tolist() == [[False, False, False], [True, True, False]]


def test_feature_and_vm_validity_propagation(tmp_path):
    tensor = np.arange(8, dtype=np.float32).reshape(2, 1, 4)
    manifest = SimpleNamespace(feature_definitions=[{"feature_name": "X", "source_fields": ["x"], "lookback": 2}], feature_count=1, content_hash="m")
    payload = build_feature_validity_tensor(manifest, tensor, {"x": np.array([[True, True, False, True], [True, True, True, True]])}, np.ones((2, 4), dtype=bool), tmp_path)
    validity = np.load(payload["tensor_path"])
    assert validity[:, 0, :].tolist() == [[False, True, False, False], [False, True, True, True]]
    vm = StackVM(FORMULA_VOCAB)
    executed = vm.execute_with_validity([0], torch.from_numpy(tensor), torch.from_numpy(np.ones_like(tensor, dtype=bool)))
    assert executed is not None and bool(executed[1].all())


def test_cutoff_sentinel_and_target_access_audit():
    firewall = DateFirewall("20240103", "20240104", label_horizon=1)
    view = ResearchDataView(firewall, ("20240102", "20240103", "20240104"))
    assert view.eligible_dates == ("20240102",)
    before = view.truncate_axis(np.array([[1.0, 2.0, 3.0]])).copy()
    mutated = view.truncate_axis(np.array([[1.0, 999.0, -999.0]]))
    assert np.array_equal(before, mutated)
    with pytest.raises(FirewallAccessError): firewall.assert_target_access("20240103", "20240104", component="full_eval", purpose="label")
    assert firewall.proof(["20240102", "20240103", "20240104"], raw_truncated_before_compute=True)["out_of_bounds_access_count"] == 1


def test_validation_eligibility_segments_and_empty_window_is_data_blocked():
    dates = ["1", "2", "3", "4", "5"]
    eligibility = build_common_eligibility(dates, membership_known=np.array([0, 1, 1, 0, 1]), snapshot_valid=np.ones(5), target_data_valid=np.ones(5), structural_gap_free=np.ones(5))
    assert eligible_date_segments(dates, eligibility) == [["2", "3"], ["5"]]
    split = ValidationSplit("s", "rolling", ["1"], [], ["4"])
    policy = EngineeringRobustnessPolicy(min_evaluable_windows=0, min_cumulative_oos_dates=0)
    results, summary, issues = evaluate_factor_splits(torch.ones((2, 5)), torch.ones((2, 5)), dates, [split], "f", eligible_date_mask=torch.from_numpy(eligibility.eligible_mask), policy=policy)
    assert results == [] and summary.status == "data_blocked"
    assert "data_blocked_window" in {issue.code for issue in issues}


def test_copy_freeze_is_immutable_after_source_mutation(tmp_path):
    data = tmp_path / "data"; _write_jsonl(data / "daily_bars" / "records.jsonl", [{"ts_code": "A", "trade_date": "20240102"}])
    version = DatasetVersionRecord(dataset_version_id="version", provider="sample", data_dir=str(data), start_date="20240102", end_date="20240102", datasets=["daily_bars"], dataset_fingerprints=[], created_at="now", content_hash="hash")
    freeze = tmp_path / "freeze"
    create_research_freeze(data, freeze, version, "test", mode="copy")
    frozen = (freeze / "data" / "daily_bars" / "records.jsonl").read_text()
    (data / "daily_bars" / "records.jsonl").write_text("mutated", encoding="utf-8")
    assert (freeze / "data" / "daily_bars" / "records.jsonl").read_text() == frozen
    with pytest.raises(ValueError): create_research_freeze(data, tmp_path / "bad", version, "bad", mode="hardlink")


def test_next_open_fill_and_risk_prefix_invariance():
    loader = SimpleNamespace(
        ts_codes=["A"], trade_dates=["20240102", "20240103", "20240104"],
        raw_data_cache={
            "open": torch.tensor([[10.0, 11.0, 12.0]]), "volume": torch.full((1, 3), 1_000_000.0),
            "active_mask": torch.ones((1, 3)), "is_suspended": torch.zeros((1, 3)),
            "limit_up_flag": torch.zeros((1, 3)), "limit_down_flag": torch.zeros((1, 3)),
        }, target_ret=torch.tensor([[0.1, 0.1, 0.0]]), data_dir=".", industry_codes=torch.zeros(1, dtype=torch.long), security_metadata={"A": {"industry": "x"}},
    )
    factors = torch.tensor([[float("nan"), 1.0, 1.0]])
    result = AShareBacktestSimulator(initial_cash=100_000, top_n=1, max_weight=1.0, time_contract=BacktestTimeContract()).simulate(factors, loader)
    fill = next(item for item in result.fills if item.status in {"FILLED", "PARTIAL"})
    assert fill.trade_date == "20240103" and fill.price == 11.0
    historical = estimate_return_covariance(loader, as_of_index=2, shrinkage=0.0)
    loader.target_ret[:, 2] = 999.0
    assert torch.equal(historical, estimate_return_covariance(loader, as_of_index=2, shrinkage=0.0))
