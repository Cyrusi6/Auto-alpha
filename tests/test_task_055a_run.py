from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from task_055_a.run import (
    BLOCKED_STATUS,
    CONFIG_SCHEMA,
    PHYSICAL_STATE_NAMES,
    SUCCESS_STATUS,
    Task055AOrchestrationError,
    run_task055a,
)
from task_055_a.simulator import simulate_event_ledger


def _files(tmp_path: Path):
    observation = tmp_path / "observation.json"
    bundle = tmp_path / "bundle.json"
    observation.write_text("{}\n", encoding="utf-8")
    bundle.write_text("{}\n", encoding="utf-8")
    states = {}
    for name in PHYSICAL_STATE_NAMES:
        root = tmp_path / "states" / name
        root.mkdir(parents=True)
        states[name] = str(root)
    config = {
        "schema_version": CONFIG_SCHEMA,
        "observation_seal": str(observation),
        "simulation_bundle": str(bundle),
        "output_root": str(tmp_path / "output"),
        "physical_state_roots": states,
    }
    return config, observation, bundle


def _fake_evidence(tmp_path: Path):
    ids = [f"factor_{index:02d}" for index in range(20)]
    signal_dates = ["20240527", "20240528"]
    execution_dates = signal_dates + ["20240529", "20240530"]
    assets = ["000001.SZ", "000002.SZ"]
    shape_signal = (len(assets), len(signal_dates))
    shape_execution = (len(assets), len(execution_dates))
    signal_masks = {
        "signal_candidate_cells": np.ones(shape_signal, dtype=bool),
        "membership": np.ones(shape_signal, dtype=bool),
        "membership_known": np.ones(shape_signal, dtype=bool),
        "active": np.ones(shape_signal, dtype=bool),
        "listed": np.ones(shape_signal, dtype=bool),
        "st_effective": np.zeros(shape_signal, dtype=bool),
        "st_status_known": np.ones(shape_signal, dtype=bool),
        "st_information_available": np.ones(shape_signal, dtype=bool),
        "signal_eligible_at_close": np.ones(shape_signal, dtype=bool),
        "unexplained_data_gap": np.zeros(shape_signal, dtype=bool),
    }
    execution_masks = {
        "membership": np.ones(shape_execution, dtype=bool),
        "membership_known": np.ones(shape_execution, dtype=bool),
        "active": np.ones(shape_execution, dtype=bool),
        "listed": np.ones(shape_execution, dtype=bool),
        "open_execution_known": np.ones(shape_execution, dtype=bool),
        "open_execution_value": np.ones(shape_execution, dtype=bool),
        "buyable_at_open": np.ones(shape_execution, dtype=bool),
        "sellable_at_open": np.ones(shape_execution, dtype=bool),
        "suspension_source_covered": np.ones(shape_execution, dtype=bool),
        "suspension_event_present": np.zeros(shape_execution, dtype=bool),
        "suspension_associated_bar_absence": np.zeros(shape_execution, dtype=bool),
        "conservative_open_excluded": np.zeros(shape_execution, dtype=bool),
        "unexplained_data_gap": np.zeros(shape_execution, dtype=bool),
        "corporate_action_validity": np.ones(shape_execution, dtype=bool),
    }
    manifest = {
        "content_hash": "b" * 64,
        "exact20_ids": ids,
        "artifacts": {
            key: {"sha256": ("c" if kind == "values" else "d") * 64}
            for factor_id in ids
            for kind in ("values", "validity")
            for key in (f"factor:{factor_id}:{kind}",)
        },
    }
    loaded = {
        "manifest": manifest,
        "trade_dates": signal_dates,
        "execution_dates": execution_dates,
        "ts_codes": assets,
        "factor_values": {factor_id: np.full(shape_signal, index + 1.0) for index, factor_id in enumerate(ids)},
        "factor_validity": {factor_id: np.ones(shape_signal, dtype=bool) for factor_id in ids},
        "strict_masks": signal_masks,
        "execution_masks": execution_masks,
        "raw": {
            "open": np.full(shape_execution, 10.0),
            "close": np.full(shape_execution, 10.0),
            "vol": np.full(shape_execution, 100_000.0),
            "amount": np.full(shape_execution, 1_000_000.0),
        },
        "raw_validity": {name: np.ones(shape_execution, dtype=bool) for name in ("open", "close", "vol", "amount")},
        "corporate_actions": [],
        "benchmark_index_bars": [
            {"trade_date": date, "open": 4000.0 + index}
            for index, date in enumerate(execution_dates)
        ],
    }
    seal = {"content_hash": "a" * 64, "status": "sealed_waiting_for_future_data"}
    return ids, manifest, loaded, seal


def test_orchestrator_seals_policy_before_data_load_runs_ab_and_resume(tmp_path):
    config, _, _ = _files(tmp_path)
    ids, manifest, loaded, seal = _fake_evidence(tmp_path)
    events = []
    calls = []

    def bundle_validator(path, require_ready=True):
        assert require_ready is True
        events.append("bundle_validated")
        return manifest

    def seal_validator(path, rescan=True):
        assert rescan is True
        events.append("seal_validated")
        return seal

    def bundle_loader(path):
        policy_current = Path(config["output_root"]) / "policy_seal" / "current.json"
        assert policy_current.is_file()
        events.append("bundle_loaded")
        return loaded

    def simulator(market, scores, *, masks, corporate_actions, policy):
        calls.append((policy.name, float(np.nanmax(scores))))
        return simulate_event_ledger(
            market,
            scores,
            masks=masks,
            corporate_actions=corporate_actions,
            policy=policy,
        )

    result = run_task055a(
        config,
        bundle_validator=bundle_validator,
        bundle_loader=bundle_loader,
        seal_validator=seal_validator,
        simulator=simulator,
    )
    assert result["status"] == SUCCESS_STATUS
    assert result["terminal_count"] == 100
    assert result["immutable_resume_hit"] is True
    assert result["primary_truth_hash"] == result["sibling_truth_hash"] == result["resume_truth_hash"]
    assert len(calls) == 20 * 5 * 2
    assert events.index("bundle_loaded") > events.index("bundle_validated")
    assert result["queues"] == {name: 0 for name in PHYSICAL_STATE_NAMES}
    assert set(ids) == {f"factor_{index:02d}" for index in range(20)}

    resumed = run_task055a(
        config,
        bundle_validator=bundle_validator,
        bundle_loader=lambda *args, **kwargs: pytest.fail("top-level resume must not map arrays"),
        seal_validator=seal_validator,
        simulator=lambda *args, **kwargs: pytest.fail("top-level resume must not simulate"),
    )
    assert resumed["orchestrator_resume_hit"] is True


def test_nonempty_physical_queue_blocks_before_bundle_load(tmp_path):
    config, _, _ = _files(tmp_path)
    ids, manifest, _, seal = _fake_evidence(tmp_path)
    Path(config["physical_state_roots"]["portfolio_campaign"]).joinpath("queue.jsonl").write_text(
        json.dumps({"factor_id": ids[0]}) + "\n", encoding="utf-8"
    )
    result = run_task055a(
        config,
        bundle_validator=lambda *args, **kwargs: manifest,
        bundle_loader=lambda *args, **kwargs: pytest.fail("blocked queues must prevent data mapping"),
        seal_validator=lambda *args, **kwargs: seal,
    )
    assert result["status"] == BLOCKED_STATUS
    assert result["queues"]["portfolio_campaign"] == 1
    assert result["terminal_count"] == 0


def test_missing_strict_execution_mask_blocks_without_simulation(tmp_path):
    config, _, _ = _files(tmp_path)
    _, manifest, loaded, seal = _fake_evidence(tmp_path)
    loaded["execution_masks"].pop("buyable_at_open")
    result = run_task055a(
        config,
        bundle_validator=lambda *args, **kwargs: manifest,
        bundle_loader=lambda *args, **kwargs: loaded,
        seal_validator=lambda *args, **kwargs: seal,
        simulator=lambda *args, **kwargs: pytest.fail("missing mask must fail before simulation"),
    )
    assert result["status"] == BLOCKED_STATUS
    assert "buyable_at_open" in result["blockers"][0]["detail"]


def test_forbidden_raw_or_factor_store_override_is_rejected(tmp_path):
    config, _, _ = _files(tmp_path)
    config["data_dir"] = str(tmp_path / "raw")
    with pytest.raises(Task055AOrchestrationError, match="forbidden_config_keys"):
        run_task055a(config)


def test_resume_rejects_tampered_run_artifact(tmp_path):
    config, _, _ = _files(tmp_path)
    _, manifest, loaded, seal = _fake_evidence(tmp_path)
    kwargs = {
        "bundle_validator": lambda *args, **kwargs: manifest,
        "bundle_loader": lambda *args, **kwargs: loaded,
        "seal_validator": lambda *args, **kwargs: seal,
        "simulator": simulate_event_ledger,
    }
    result = run_task055a(config, **kwargs)
    primary = Path(result["result_path"]).parents[3] / "primary"
    pointer = json.loads((primary / "current.json").read_text(encoding="utf-8"))
    generation = primary / pointer["manifest"]
    run_manifest = json.loads(generation.read_text(encoding="utf-8"))
    run_root = primary / run_manifest["runs"][0]["path"]
    pointer = json.loads((run_root / "current.json").read_text(encoding="utf-8"))
    artifact = run_root / pointer["manifest"]
    fills = artifact.parent / "fills.jsonl"
    fills.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(Exception, match="run_partition_mismatch:fills.jsonl"):
        run_task055a(config, **kwargs)


def test_data_blockers_publish_exact100_verified_terminal_artifacts(tmp_path):
    from task_055_a.simulator import SimulationDataBlocker

    config, _, _ = _files(tmp_path)
    _, manifest, loaded, seal = _fake_evidence(tmp_path)

    def blocked_simulator(*args, **kwargs):
        raise SimulationDataBlocker("valuation_open_blocked:20200102:asset")

    result = run_task055a(
        config,
        bundle_validator=lambda *args, **kwargs: manifest,
        bundle_loader=lambda *args, **kwargs: loaded,
        seal_validator=lambda *args, **kwargs: seal,
        simulator=blocked_simulator,
    )
    assert result["status"] == BLOCKED_STATUS
    assert result["terminal_count"] == 100
    assert result["immutable_resume_hit"] is True
    assert any(row["code"] == "task055a_data_blocked_runs" for row in result["blockers"])
