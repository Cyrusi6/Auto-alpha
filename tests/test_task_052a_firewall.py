from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from feature_factory.validity import build_feature_validity_tensor
from formula_batch_eval.evaluator import FormulaBatchEvaluator
from formula_batch_eval.models import FormulaEvalRequest
from research_firewall import DateFirewall, ResearchDataView
from research_firewall.lineage import build_loader_lineage
from validation_lab.run_validation import _load_governed_matrix_context, _screening_reproduction


def test_task052_firewall_cutoff_diagnostic_and_actual_read_audit():
    dates = ("20240529", "20240530", "20240531", "20240603", "20240604")
    firewall = DateFirewall("20240530", "20240531", label_horizon=2)
    view = ResearchDataView(firewall, dates)
    assert view.eligible_dates == ()
    assert view.diagnostic_dates == ("20240531",)
    selected = firewall.filter_records(
        [{"trade_date": "20240530"}, {"trade_date": "20240531"}],
        date_field="trade_date",
        component="sentinel",
    )
    assert selected == [{"trade_date": "20240530"}]
    assert firewall.proof(dates, raw_truncated_before_compute=True)["out_of_bounds_access_count"] == 0
    assert {row["date"] for row in firewall.access_audit} == {"20240530"}


def test_v3_validity_requires_every_declared_dependency(tmp_path: Path):
    tensor = np.ones((1, 1, 4), dtype=np.float32)
    manifest = SimpleNamespace(
        feature_definitions=[{"feature_name": "BENCHMARK_RELATIVE_RETURN_5D", "source_fields": ["close", "index_daily_bars.close"], "lookback": 1}],
        feature_count=1,
        content_hash="manifest",
    )
    payload = build_feature_validity_tensor(
        manifest,
        tensor,
        {"close": np.ones((1, 4), dtype=np.bool_)},
        np.ones((1, 4), dtype=np.bool_),
        tmp_path,
    )
    validity = np.load(payload["tensor_path"])
    assert not validity.any()
    summary = payload["feature_summaries"][0]
    assert summary["blocker"] == "missing_validity_dependency"
    assert summary["missing_validity_dependencies"] == ["index_daily_bars.close"]


def test_strict_validation_uses_persisted_next_open_target_and_diagnostic_segments(tmp_path: Path):
    dates = ["20240529", "20240530", "20240531", "20240603", "20240604", "20240605"]
    stocks = ["000001.SZ", "000002.SZ"]
    (tmp_path / "trade_dates.json").write_text(json.dumps(dates), encoding="utf-8")
    (tmp_path / "ts_codes.json").write_text(json.dumps(stocks), encoding="utf-8")
    shape = (len(stocks), len(dates))
    for name in ["bar_observed_mask", "index_membership", "membership_known_mask"]:
        np.save(tmp_path / f"{name}.npy", np.ones(shape, dtype=np.bool_))
    target = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    target_validity = np.ones(shape, dtype=np.bool_)
    target_validity[:, -2:] = False
    np.save(tmp_path / "next_open_t1_t2_return.npy", target)
    np.save(tmp_path / "target_available_mask.npy", target_validity)
    (tmp_path / "task_052a_strict_matrix_manifest.json").write_text(
        json.dumps(
            {
                "semantic_hash": "strict",
                "target_contract": {
                    "name": "next_open_t1_t2_return",
                    "signal_date": "t",
                    "entry_price": "open[t+1]",
                    "exit_price": "open[t+2]",
                },
                "universe_mode": "daily_pit_constituents",
                "historical_constituent_proof": True,
            }
        ),
        encoding="utf-8",
    )
    context = _load_governed_matrix_context(
        Namespace(matrix_cache_dir=str(tmp_path), label_horizon=2, holdout_start_date="20240531")
    )
    assert torch.equal(context["target_ret"], torch.from_numpy(target))
    assert context["target_path"].endswith("next_open_t1_t2_return.npy")
    assert context["eligibility"].eligible_mask.tolist() == [False, False, True, True, False, False]


def test_screening_is_not_rejected_when_contract_changed():
    record = SimpleNamespace(
        metadata={
            "metrics_by_split": {"all": {"rank_ic_mean": 0.1}},
            "evaluation_lineage": {
                "target_return_mode": "adjusted_close",
                "label_horizon": 1,
                "eligible_date_hash": "old",
            },
        }
    )
    result = _screening_reproduction(
        record,
        torch.ones((2, 2)),
        torch.ones((2, 2)),
        ["20240531", "20240603"],
        current_contract={
            "target_return_mode": "next_open_t1_t2_return",
            "label_horizon": 2,
            "eligible_date_hash": "new",
        },
    )
    assert result["status"] == "not_comparable_due_to_contract_change"


def test_proxy_and_eval_lineage_changes_before_cache_reuse(tmp_path: Path):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    metadata = matrix / "metadata.json"
    metadata.write_text(json.dumps({"content_hash": "one"}), encoding="utf-8")
    loader = SimpleNamespace(
        ts_codes=["A"],
        trade_dates=["20240529"],
        firewall_source_trade_dates=["20240529", "20240530", "20240531"],
        feat_tensor=torch.ones((1, 1, 1)),
        target_ret=torch.ones((1, 1)),
        target_return_mode="next_open_t1_t2_return",
        label_horizon=2,
        date_firewall=DateFirewall("20240530", "20240531", label_horizon=2),
        feature_set_manifest_path=None,
        matrix_cache_dir=matrix,
    )
    first = build_loader_lineage(loader, stage="formula_batch_eval")
    metadata.write_text(json.dumps({"content_hash": "two"}), encoding="utf-8")
    second = build_loader_lineage(loader, stage="formula_batch_eval")
    assert first["lineage_hash"] != second["lineage_hash"]


def test_skip_existing_happens_only_after_lineage_match():
    evaluator = FormulaBatchEvaluator.__new__(FormulaBatchEvaluator)
    evaluator.config = SimpleNamespace(
        skip_existing=True,
        use_eval_cache=False,
        feature_set_name="ashare_features_v3",
        alpha_campaign_id="campaign",
        factor_transform="raw",
        train_ratio=0.6,
        valid_ratio=0.2,
        universe_name=None,
        universe_file=None,
        research_end_date="20240530",
        holdout_start_date="20240531",
        label_horizon=2,
        eligible_date_hash=None,
    )
    evaluator.feature_version = "ashare_features_v3"
    evaluator.vocab = SimpleNamespace(encode_name=lambda name: 0)
    evaluator.lineage = {"lineage_hash": "current"}
    evaluator.loader = SimpleNamespace(
        trade_dates=["20240529"],
        feat_tensor=torch.ones((1, 1, 1)),
    )
    evaluator.vm = SimpleNamespace(validate_with_reason=lambda tokens: (True, "ok"))
    existing = SimpleNamespace(
        factor_id="factor",
        metrics={"score": 1.0},
        metadata={"evaluation_lineage_hash": "current", "max_abs_correlation": 0.0},
    )
    evaluator.store = SimpleNamespace(find_factor_by_hash=lambda formula_hash: existing)
    request = FormulaEvalRequest("factor", [0], ["RET_1D"], "formula_hash")
    result = evaluator._run_request(request, "batch", "2026-01-01T00:00:00Z")
    assert result.status == "skipped_existing"

    existing.metadata["evaluation_lineage_hash"] = "stale"
    evaluator.vm = SimpleNamespace(
        validate_with_reason=lambda tokens: (True, "ok"),
        execute=lambda tokens, tensor: (_ for _ in ()).throw(RuntimeError("lineage_miss_recomputed")),
    )
    with pytest.raises(RuntimeError, match="lineage_miss_recomputed"):
        evaluator._run_request(request, "batch", "2026-01-01T00:00:00Z")
