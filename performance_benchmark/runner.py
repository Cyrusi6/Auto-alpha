"""Run lightweight local benchmarks for data access and research flow."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Callable

import torch

from backtest import run_backtest
from factor_store import FactorRecord, LocalFactorStore, make_factor_id, stable_formula_hash
from formula_search.models import FormulaSearchConfig
from formula_search.search import FormulaSearchRunner
from model_core.data_loader import AShareDataLoader
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from research import BatchFactorResearchRunner, BatchResearchConfig
from research.candidates import default_candidates

from .models import BenchmarkItemResult, BenchmarkResult
from .report import write_benchmark_report
from .timer import Timer


def run_benchmark(
    data_dir: str | Path,
    output_dir: str | Path,
    matrix_cache_dir: str | Path | None = None,
) -> BenchmarkResult:
    data_path = Path(data_dir)
    output_path = Path(output_dir)
    cache_path = Path(matrix_cache_dir) if matrix_cache_dir is not None else None
    output_path.mkdir(parents=True, exist_ok=True)

    items = [
        _run_item("jsonl_loader_load_data", lambda: _bench_loader(data_path, None, False)),
        _run_item("matrix_loader_load_data", lambda: _bench_loader(data_path, cache_path, True), skip=not _cache_exists(cache_path)),
        _run_item("stackvm_execute_default_formulas", lambda: _bench_stackvm(data_path)),
        _run_item("research_batch_small", lambda: _bench_research_batch(data_path, output_path)),
        _run_item("formula_search_small", lambda: _bench_formula_search(data_path, output_path)),
        _run_item("backtest_equal_weight", lambda: _bench_backtest(data_path, output_path, "equal_weight")),
        _run_item("backtest_risk_aware", lambda: _bench_backtest(data_path, output_path, "risk_aware")),
    ]
    summary = {
        "items": len(items),
        "successful_items": sum(1 for item in items if item.success),
        "failed_items": sum(1 for item in items if not item.success),
        "total_wall_time_seconds": float(sum(item.wall_time_seconds for item in items)),
    }
    result = BenchmarkResult(
        data_dir=str(data_path),
        matrix_cache_dir=str(cache_path) if cache_path is not None else None,
        output_dir=str(output_path),
        items=items,
        summary=summary,
    )
    write_benchmark_report(result, output_path)
    return result


def _run_item(name: str, func: Callable[[], dict[str, float | int]], skip: bool = False) -> BenchmarkItemResult:
    if skip:
        return BenchmarkItemResult(name=name, wall_time_seconds=0.0, success=False, error="skipped")
    try:
        with Timer() as timer:
            payload = func()
        elapsed = timer.elapsed
        records = int(payload.get("records_read", 0))
        formulas = int(payload.get("formulas_evaluated", 0))
        return BenchmarkItemResult(
            name=name,
            wall_time_seconds=float(elapsed),
            n_stocks=int(payload.get("n_stocks", 0)),
            n_dates=int(payload.get("n_dates", 0)),
            n_features=int(payload.get("n_features", 0)),
            records_read=records,
            formulas_evaluated=formulas,
            throughput_estimate=float((records or formulas or 1) / max(elapsed, 1e-9)),
            success=True,
        )
    except Exception as exc:
        elapsed = timer.elapsed if "timer" in locals() else 0.0
        return BenchmarkItemResult(
            name=name,
            wall_time_seconds=float(elapsed),
            success=False,
            error=str(exc),
        )


def _bench_loader(data_dir: Path, cache_dir: Path | None, use_cache: bool) -> dict[str, int]:
    loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        matrix_cache_dir=cache_dir,
        use_matrix_cache=use_cache,
    ).load_data()
    return _loader_payload(loader)


def _bench_stackvm(data_dir: Path) -> dict[str, int]:
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    vm = StackVM()
    formulas = [
        [FORMULA_VOCAB.encode_name("RET_1D")],
        [FORMULA_VOCAB.encode_name("ROE"), FORMULA_VOCAB.encode_name("CS_RANK")],
        [FORMULA_VOCAB.encode_name("RET_1D"), FORMULA_VOCAB.encode_name("CS_ZSCORE")],
    ]
    for tokens in formulas:
        result = vm.execute(tokens, loader.feat_tensor)
        if result is None:
            raise RuntimeError(f"formula failed: {tokens}")
    payload = _loader_payload(loader)
    payload["formulas_evaluated"] = len(formulas)
    return payload


def _bench_research_batch(data_dir: Path, output_dir: Path) -> dict[str, int]:
    batch_dir = output_dir / "research_batch"
    store_dir = output_dir / "store"
    result = BatchFactorResearchRunner(
        BatchResearchConfig(
            data_dir=str(data_dir),
            universe_name=None,
            universe_file=None,
            factor_store_dir=str(store_dir),
            report_dir=str(output_dir / "reports"),
            output_dir=str(batch_dir),
            factor_transform="winsorize_zscore",
            enable_gate=True,
            min_coverage=0.5,
            correlation_threshold=0.99,
            top_k=2,
            disable_composite=True,
        ),
        candidates=default_candidates()[:2],
    ).run()
    return {"formulas_evaluated": len(result.results), **_loader_payload(AShareDataLoader(data_dir=data_dir, device="cpu").load_data())}


def _bench_formula_search(data_dir: Path, output_dir: Path) -> dict[str, int]:
    result = FormulaSearchRunner(
        search_config=FormulaSearchConfig(
            seed=7,
            population_size=4,
            generations=1,
            max_formula_len=6,
            max_complexity=16,
            max_lookback=10,
            top_k=2,
            candidate_batch_size=2,
        ),
        data_dir=str(data_dir),
        universe_name=None,
        universe_file=None,
        factor_store_dir=str(output_dir / "search_store"),
        report_dir=str(output_dir / "search_reports"),
        output_dir=str(output_dir / "formula_search"),
        factor_transform="winsorize_zscore",
        enable_gate=True,
        correlation_threshold=0.99,
        min_coverage=0.5,
        composite_method="rank_average",
    ).run()
    return {"formulas_evaluated": result.candidates_evaluated, **_loader_payload(AShareDataLoader(data_dir=data_dir, device="cpu").load_data())}


def _bench_backtest(data_dir: Path, output_dir: Path, portfolio_method: str) -> dict[str, int]:
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store_dir = output_dir / "backtest_store"
    factor_id = _ensure_benchmark_factor(store_dir, loader)
    target_dir = output_dir / f"backtest_{portfolio_method}"
    args = [
        "--data-dir",
        str(data_dir),
        "--factor-store-dir",
        str(store_dir),
        "--output-dir",
        str(target_dir),
        "--factor-id",
        factor_id,
        "--top-n",
        "2",
        "--max-weight",
        "0.10",
        "--portfolio-method",
        portfolio_method,
    ]
    if portfolio_method == "risk_aware":
        args.extend(["--index-code", "000300.SH", "--risk-report-dir", str(target_dir / "risk")])
    with contextlib.redirect_stdout(io.StringIO()):
        exit_code = run_backtest.main(args)
    if exit_code != 0:
        raise RuntimeError(f"backtest returned {exit_code}")
    return _loader_payload(loader) | {"formulas_evaluated": 1}


def _ensure_benchmark_factor(store_dir: Path, loader: AShareDataLoader) -> str:
    store = LocalFactorStore(store_dir)
    formula_tokens = [FORMULA_VOCAB.encode_name("ROE")]
    formula_names = ["ROE"]
    formula_hash = stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1")
    factor_id = make_factor_id(formula_hash)
    if store.find_factor_by_hash(formula_hash) is None:
        store.save_factor(
            FactorRecord(
                factor_id=factor_id,
                formula=formula_names,
                formula_tokens=formula_tokens,
                formula_hash=formula_hash,
                feature_version="ashare_features_v1",
                operator_version="ashare_ops_v1",
                lookback_days=1,
                created_at="benchmark",
                status="approved",
                metrics={"score": 0.0},
                factor_type="composite",
                metadata={"type": "benchmark"},
            )
        )
    values = loader.raw_data_cache.get("roe")
    if values is None:
        values = torch.zeros((len(loader.ts_codes), len(loader.trade_dates)), dtype=torch.float32)
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    return factor_id


def _loader_payload(loader: AShareDataLoader) -> dict[str, int]:
    n_features = int(loader.feat_tensor.shape[1]) if loader.feat_tensor is not None else 0
    return {
        "n_stocks": len(loader.ts_codes),
        "n_dates": len(loader.trade_dates),
        "n_features": n_features,
        "records_read": len(loader.ts_codes) * len(loader.trade_dates),
    }


def _cache_exists(cache_path: Path | None) -> bool:
    return cache_path is not None and (cache_path / "metadata.json").exists()
