from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from task_055_a.artifacts import (
    ResumeDriftError,
    canonical_hash,
    publish_simulation_run,
    resume_simulation_run,
)
from task_055_a.policy import BASELINE
from task_055_a.simulator import EventLedgerSimulator
from task_055_a.verifier import (
    SimulationVerificationError,
    compare_replay_truth,
    verify_simulation_run,
)


def _fixture():
    dates = ["20240527", "20240528", "20240529", "20240530"]
    assets = ["000001.SZ", "600000.SH"]
    market = {
        "dates": dates,
        "assets": assets,
        "open": np.array([[10.0, 20.0], [11.0, 19.0], [12.0, 18.0], [13.0, 17.0]]),
        "close": np.array([[10.5, 19.5], [11.5, 18.5], [12.5, 17.5], [13.5, 16.5]]),
        "adv": np.full((4, 2), 100_000.0),
    }
    scores = np.array([[2.0, 1.0], [1.0, 2.0], [2.0, 1.0], [1.0, 2.0]])
    masks = {"tradable": np.ones((4, 2), dtype=bool)}
    policy = replace(
        BASELINE,
        top_n=1,
        minimum_commission=0.0,
        commission_rate=0.0003,
        slippage_bps=1.0,
        impact_bps=2.0,
    )
    result = EventLedgerSimulator(policy, initial_cash=1_000_000.0).run(
        market,
        scores,
        masks=masks,
    )
    spec = {"initial_cash": 1_000_000.0, "policy": policy.to_dict(), "factor_id": "factor_probe"}
    lineage = {"bundle_hash": "a" * 64, "factor_values_sha256": "b" * 64}
    benchmark = {"dates": dates, "open": [4000.0, 4010.0, 3990.0, 4020.0]}
    return result, market, spec, lineage, benchmark


def _publish(root):
    result, market, spec, lineage, benchmark = _fixture()
    return publish_simulation_run(
        output_root=root,
        result=result,
        spec=spec,
        input_lineage=lineage,
        market=market,
        benchmark=benchmark,
    )


def test_publish_and_independently_verify_complete_ledger(tmp_path):
    published = _publish(tmp_path / "run")
    verified = verify_simulation_run(tmp_path / "run")
    assert published["truth_hash"] == verified["truth_hash"]
    assert verified["verification"]["status"] == "passed"
    assert verified["verification"]["metrics"]["benchmark"]["status"] == "available"
    assert verified["verification"]["metrics"]["total_cost"] > 0
    assert verified["verification"]["record_counts"]["orders"] > 0


def test_verifier_rejects_tampered_partition(tmp_path):
    published = _publish(tmp_path / "run")
    fills = tmp_path / "run" / "generations" / published["generation_id"] / "fills.jsonl"
    rows = [json.loads(line) for line in fills.read_text().splitlines()]
    rows[0]["price"] += 1.0
    fills.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(SimulationVerificationError, match="run_partition_mismatch:fills.jsonl"):
        verify_simulation_run(tmp_path / "run")


def test_uncached_ab_truth_hashes_match(tmp_path):
    _publish(tmp_path / "primary")
    _publish(tmp_path / "sibling")
    comparison = compare_replay_truth(tmp_path / "primary", tmp_path / "sibling")
    assert comparison["status"] == "passed"
    assert comparison["primary_content_hash"] == comparison["sibling_content_hash"]


def test_resume_requires_exact_spec_and_input_lineage(tmp_path):
    published = _publish(tmp_path / "run")
    resumed = resume_simulation_run(
        tmp_path / "run",
        expected_spec_hash=published["spec_hash"],
        expected_input_lineage_hash=published["input_lineage_hash"],
    )
    assert resumed["resume_hit"] is True
    with pytest.raises(ResumeDriftError, match="resume_spec_drift"):
        resume_simulation_run(
            tmp_path / "run",
            expected_spec_hash=canonical_hash({"changed": True}),
            expected_input_lineage_hash=published["input_lineage_hash"],
        )
    with pytest.raises(ResumeDriftError, match="resume_input_lineage_drift"):
        resume_simulation_run(
            tmp_path / "run",
            expected_spec_hash=published["spec_hash"],
            expected_input_lineage_hash=canonical_hash({"changed": True}),
        )


def test_missing_benchmark_is_fail_closed(tmp_path):
    result, market, spec, lineage, _ = _fixture()
    publish_simulation_run(
        output_root=tmp_path / "run",
        result=result,
        spec=spec,
        input_lineage=lineage,
        market=market,
    )
    with pytest.raises(SimulationVerificationError, match="benchmark_missing"):
        verify_simulation_run(tmp_path / "run")


def test_verifier_closes_lots_dividend_and_share_action(tmp_path):
    dates = ["20240527", "20240528", "20240529"]
    market = {
        "dates": dates,
        "assets": ["000001.SZ"],
        "open": np.array([[10.0], [5.0], [5.5]]),
        "close": np.array([[10.0], [5.0], [5.5]]),
        "adv": np.full((3, 1), 100_000.0),
    }
    policy = replace(BASELINE, top_n=0, zero_all_costs=True)
    actions = [{
        "action_id": "action:split_dividend",
        "asset": "000001.SZ",
        "effective_index": 1,
        "pay_index": 2,
        "share_ratio": 2.0,
        "cash_dividend_per_share": 1.0,
    }]
    result = EventLedgerSimulator(policy, initial_cash=1_000.0).run(
        market,
        np.full((3, 1), np.nan),
        masks={"tradable": np.zeros((3, 1), dtype=bool)},
        corporate_actions=actions,
        initial_positions={"000001.SZ": 100},
    )
    publish_simulation_run(
        output_root=tmp_path / "run",
        result=result,
        spec={"initial_cash": 1_000.0, "policy": policy.to_dict()},
        input_lineage={"bundle_hash": "c" * 64},
        market=market,
        benchmark={"dates": dates, "open": [4000.0, 4010.0, 4020.0]},
        initial_positions={"000001.SZ": 100},
    )
    verified = verify_simulation_run(tmp_path / "run")
    assert verified["verification"]["final_positions"] == {"000001.SZ": 200}
    assert verified["verification"]["final_cash"]["available"] == pytest.approx(1_100.0)


def test_blocked_run_artifact_is_tamper_evident(tmp_path: Path):
    from task_055_a.artifacts import publish_blocked_simulation_run

    root = tmp_path / "blocked"
    published = publish_blocked_simulation_run(
        output_root=root,
        spec={"factor_id": "f", "scenario": "baseline", "terminal_state": "data_blocked"},
        input_lineage={"bundle": "a" * 64},
        blocker={"code": "gap", "detail": "missing valuation"},
    )
    verified = verify_simulation_run(root)
    assert verified["truth_hash"] == published["truth_hash"]
    blocker = Path(verified["root"]) / "blocker.json"
    blocker.write_text('{"code":"forged"}\n', encoding="utf-8")
    with pytest.raises(SimulationVerificationError, match="partition_mismatch"):
        verify_simulation_run(root)
