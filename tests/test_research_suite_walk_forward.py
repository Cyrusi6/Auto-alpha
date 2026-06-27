import math

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import LocalFactorStore
from formula_search.models import FormulaSearchConfig
from formula_search.search import FormulaSearchRunner
from model_core.data_loader import AShareDataLoader
from research_suite.walk_forward import (
    build_walk_forward_windows,
    evaluate_factor_walk_forward,
    summarize_walk_forward,
)


def test_build_walk_forward_windows_are_ordered_and_non_overlapping():
    windows = build_walk_forward_windows(["20240101", "20240102", "20240103", "20240104"], 2, 1, 1)

    assert windows[0].train_dates == ["20240101", "20240102"]
    assert windows[0].test_dates == ["20240103"]
    assert windows[1].train_dates == ["20240102", "20240103"]
    assert windows[1].test_dates == ["20240104"]
    assert set(windows[0].train_dates).isdisjoint(windows[0].test_dates)


def test_walk_forward_evaluates_sample_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    result = FormulaSearchRunner(
        search_config=FormulaSearchConfig(seed=5, population_size=5, generations=1, top_k=2),
        data_dir=str(data_dir),
        universe_name=None,
        universe_file=None,
        factor_store_dir=str(store_dir),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "search"),
        factor_transform="winsorize_zscore",
        enable_gate=True,
        correlation_threshold=0.99,
        min_coverage=0.5,
    ).run()
    factor_id = result.composite_factor_id or result.approved_factor_ids[0]
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    windows = build_walk_forward_windows(loader.trade_dates, 1, 1, 1)
    wf = evaluate_factor_walk_forward(loader, LocalFactorStore(store_dir), factor_id, windows)
    summary = summarize_walk_forward(wf)

    assert wf.windows
    assert summary["n_windows"] >= 1
    assert all(math.isfinite(value) for value in summary.values())
