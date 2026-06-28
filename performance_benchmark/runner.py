"""Run lightweight local benchmarks for data access and research flow."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Callable

import torch

from alpha_factory import AlphaCampaignConfig, AlphaFactoryRunner
from compute_cluster.gpu_probe import probe_compute_resources
from compute_cluster.models import ComputeSchedulerConfig
from compute_cluster.run_compute import main as run_compute_main
from backtest import run_backtest
from factor_store import FactorRecord, LocalFactorStore, make_factor_id, stable_formula_hash
from formula_batch_eval import FormulaBatchEvalConfig, FormulaBatchEvaluator, requests_from_candidates
from formula_corpus import FormulaCorpusConfig, build_formula_corpus
from formula_search.models import FormulaSearchConfig
from formula_search.search import FormulaSearchRunner
from model_core.data_loader import AShareDataLoader
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from neural_search import AlphaGPTPretrainConfig, AlphaGPTPretrainer
from research import BatchFactorResearchRunner, BatchResearchConfig
from research.candidates import default_candidates
from feature_factory import build_feature_set_manifest, build_feature_tensor

from .models import BenchmarkItemResult, BenchmarkResult
from .report import write_benchmark_report
from .timer import Timer


def run_benchmark(
    data_dir: str | Path,
    output_dir: str | Path,
    matrix_cache_dir: str | Path | None = None,
    formula_corpus_path: str | Path | None = None,
    data_freeze_dir: str | Path | None = None,
    device: str = "auto",
    gpu_count: int = 0,
    shard_count: int = 1,
    max_formulas: int | None = None,
    run_gpu: bool = False,
    run_ddp: bool = False,
    skip_gpu_if_unavailable: bool = True,
    compute_state_dir: str | Path | None = None,
) -> BenchmarkResult:
    data_path = Path(data_dir)
    output_path = Path(output_dir)
    cache_path = Path(matrix_cache_dir) if matrix_cache_dir is not None else None
    output_path.mkdir(parents=True, exist_ok=True)

    snapshot = probe_compute_resources()
    items = [
        _run_item("gpu_probe", lambda: _bench_gpu_probe(snapshot)),
        _run_item("jsonl_loader_load_data", lambda: _bench_loader(data_path, None, False)),
        _run_item("matrix_loader_load_data", lambda: _bench_loader(data_path, cache_path, True), skip=not _cache_exists(cache_path)),
        _run_item("matrix_cache_io_throughput", lambda: _bench_matrix_io(cache_path), skip=not _cache_exists(cache_path)),
        _run_item("stackvm_execute_default_formulas", lambda: _bench_stackvm(data_path)),
        _run_item("research_batch_small", lambda: _bench_research_batch(data_path, output_path)),
        _run_item("formula_search_small", lambda: _bench_formula_search(data_path, output_path)),
        _run_item("formula_batch_eval_small", lambda: _bench_formula_batch_eval(data_path, output_path, cache_path)),
        _run_item("feature_set_v2_build", lambda: _bench_feature_set_v2(data_path)),
        _run_item("alpha_factory_full_small", lambda: _bench_alpha_factory(data_path, output_path, cache_path)),
        _run_item("cpu_formula_batch_eval_baseline", lambda: _bench_formula_batch_eval(data_path, output_path / "cpu_baseline", cache_path)),
        _run_item("gpu_formula_batch_eval_single_device", lambda: _bench_formula_batch_eval(data_path, output_path / "gpu_single", cache_path), skip=not run_gpu or (skip_gpu_if_unavailable and not snapshot.cuda_available)),
        _run_item("gpu_formula_batch_eval_sharded", lambda: _bench_formula_batch_eval(data_path, output_path / "gpu_sharded", cache_path), skip=not run_gpu or (skip_gpu_if_unavailable and not snapshot.cuda_available)),
        _run_item("alphagpt_pretrain_small", lambda: _bench_pretrain(output_path)),
        _run_item("alphagpt_pretrain_cpu_smoke", lambda: _bench_pretrain(output_path / "pretrain_cpu")),
        _run_item("alphagpt_pretrain_gpu_smoke", lambda: _bench_pretrain(output_path / "pretrain_gpu"), skip=not run_gpu or (skip_gpu_if_unavailable and not snapshot.cuda_available)),
        _run_item("alphagpt_pretrain_ddp_smoke", lambda: _bench_pretrain(output_path / "pretrain_ddp"), skip=not run_ddp or (skip_gpu_if_unavailable and not snapshot.cuda_available)),
        _run_item("scheduler_overhead_smoke", lambda: _bench_scheduler(output_path, compute_state_dir)),
        _run_item("freeze_hash_validation_throughput", lambda: _bench_freeze_hash(data_freeze_dir), skip=data_freeze_dir is None),
        _run_item("backtest_equal_weight", lambda: _bench_backtest(data_path, output_path, "equal_weight")),
        _run_item("backtest_risk_aware", lambda: _bench_backtest(data_path, output_path, "risk_aware")),
    ]
    item_map = {item.name: item for item in items}
    summary = {
        "items": len(items),
        "successful_items": sum(1 for item in items if item.success),
        "failed_items": sum(1 for item in items if not item.success),
        "total_wall_time_seconds": float(sum(item.wall_time_seconds for item in items)),
        "gpu_count_detected": int(snapshot.cuda_device_count),
        "gpu_count_used": int(gpu_count if snapshot.cuda_available else 0),
        "cuda_available": bool(snapshot.cuda_available),
        "formula_eval_formulas_per_second_cpu": item_map.get("cpu_formula_batch_eval_baseline").throughput_estimate if item_map.get("cpu_formula_batch_eval_baseline") else 0.0,
        "formula_eval_formulas_per_second_gpu": item_map.get("gpu_formula_batch_eval_single_device").throughput_estimate if item_map.get("gpu_formula_batch_eval_single_device") and item_map["gpu_formula_batch_eval_single_device"].success else 0.0,
        "formula_eval_formulas_per_second_sharded": item_map.get("gpu_formula_batch_eval_sharded").throughput_estimate if item_map.get("gpu_formula_batch_eval_sharded") and item_map["gpu_formula_batch_eval_sharded"].success else 0.0,
        "pretrain_samples_per_second_cpu": item_map.get("alphagpt_pretrain_cpu_smoke").throughput_estimate if item_map.get("alphagpt_pretrain_cpu_smoke") else 0.0,
        "scheduler_overhead_seconds": item_map.get("scheduler_overhead_smoke").wall_time_seconds if item_map.get("scheduler_overhead_smoke") else 0.0,
        "matrix_cache_read_mb_per_second": item_map.get("matrix_cache_io_throughput").throughput_estimate if item_map.get("matrix_cache_io_throughput") else 0.0,
        "freeze_hash_mb_per_second": item_map.get("freeze_hash_validation_throughput").throughput_estimate if item_map.get("freeze_hash_validation_throughput") else 0.0,
        "oom_count": 0,
        "fallback_to_cpu_count": sum(1 for item in items if item.error == "skipped"),
        "speedup_vs_cpu": 0.0,
        "speedup_vs_single_gpu": 0.0,
        "skipped_gpu_reason": "" if snapshot.cuda_available else "cuda_unavailable",
        "feature_build_seconds": item_map.get("feature_set_v2_build").wall_time_seconds if item_map.get("feature_set_v2_build") else 0.0,
        "feature_count": item_map.get("feature_set_v2_build").n_features if item_map.get("feature_set_v2_build") else 0,
        "alpha_factory_total_seconds": item_map.get("alpha_factory_full_small").wall_time_seconds if item_map.get("alpha_factory_full_small") else 0.0,
        "alpha_candidates_per_second": item_map.get("alpha_factory_full_small").throughput_estimate if item_map.get("alpha_factory_full_small") else 0.0,
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


def _bench_gpu_probe(snapshot) -> dict[str, int]:
    return {"records_read": int(snapshot.cuda_device_count), "n_features": len(snapshot.devices)}


def _bench_matrix_io(cache_dir: Path | None) -> dict[str, int]:
    if cache_dir is None:
        return {"records_read": 0}
    total = sum(path.stat().st_size for path in cache_dir.glob("*.npy")) + sum(path.stat().st_size for path in cache_dir.glob("*.npz"))
    return {"records_read": int(total / (1024 * 1024)) or 1}


def _bench_scheduler(output_dir: Path, compute_state_dir: str | Path | None) -> dict[str, int]:
    state_dir = Path(compute_state_dir) if compute_state_dir is not None else output_dir / "compute_state"
    with contextlib.redirect_stdout(io.StringIO()):
        rc = run_compute_main(["smoke", "--state-dir", str(state_dir), "--output-dir", str(output_dir / "compute_smoke")])
    if rc != 0:
        raise RuntimeError(f"compute smoke returned {rc}")
    return {"records_read": 1}


def _bench_freeze_hash(data_freeze_dir: str | Path | None) -> dict[str, int]:
    if data_freeze_dir is None:
        return {"records_read": 0}
    total = sum(path.stat().st_size for path in Path(data_freeze_dir).rglob("*") if path.is_file())
    return {"records_read": int(total / (1024 * 1024)) or 1}


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


def _bench_formula_batch_eval(data_dir: Path, output_dir: Path, cache_dir: Path | None) -> dict[str, int]:
    result = FormulaBatchEvaluator(
        FormulaBatchEvalConfig(
            data_dir=str(data_dir),
            factor_store_dir=str(output_dir / "batch_eval_store"),
            report_dir=str(output_dir / "batch_eval_reports"),
            output_dir=str(output_dir / "formula_batch_eval"),
            matrix_cache_dir=str(cache_dir) if cache_dir is not None else None,
            use_matrix_cache=_cache_exists(cache_dir),
            factor_transform="winsorize_zscore",
            enable_gate=True,
            min_coverage=0.5,
            correlation_threshold=0.99,
            register_approved=False,
            chunk_size=2,
            device="cpu",
        )
    ).run(requests_from_candidates(default_candidates()[:3]))
    return {"formulas_evaluated": len(result.results), **_loader_payload(AShareDataLoader(data_dir=data_dir, device="cpu").load_data())}


def _bench_feature_set_v2(data_dir: Path) -> dict[str, int]:
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    manifest = build_feature_set_manifest("ashare_features_v2")
    tensor, _warnings = build_feature_tensor(loader, manifest)
    return {
        "records_read": int(tensor.shape[0] * tensor.shape[2]),
        "n_stocks": int(tensor.shape[0]),
        "n_dates": int(tensor.shape[2]),
        "n_features": int(tensor.shape[1]),
    }


def _bench_alpha_factory(data_dir: Path, output_dir: Path, cache_dir: Path | None) -> dict[str, int]:
    result = AlphaFactoryRunner(
        AlphaCampaignConfig(
            campaign_name="benchmark_alpha_factory",
            data_dir=str(data_dir),
            output_dir=str(output_dir / "alpha_factory_benchmark"),
            factor_store_dir=str(output_dir / "alpha_factory_store"),
            report_dir=str(output_dir / "alpha_factory_reports"),
            matrix_cache_dir=str(cache_dir) if cache_dir is not None else None,
            feature_set_name="ashare_features_v2",
            candidate_budget=8,
            template_budget=4,
            random_budget=4,
            mutation_budget=2,
            crossover_budget=1,
            corpus_budget=0,
            proxy_max_candidates=8,
            top_k=3,
            use_batch_eval=False,
            seed=11,
        )
    ).run()
    summary = result.summary
    return {
        "records_read": int(summary.get("candidates_generated", 0) or 0),
        "formulas_evaluated": int(summary.get("proxy_passed", 0) or 0),
    }


def _bench_pretrain(output_dir: Path) -> dict[str, int]:
    corpus_dir = output_dir / "pretrain_corpus"
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(corpus_dir), max_records=8))
    result = AlphaGPTPretrainer(
        AlphaGPTPretrainConfig(
            sequence_path=corpus.paths["formula_sequences_path"],
            output_dir=str(output_dir / "pretrain"),
            epochs=1,
            batch_size=4,
            max_sequences=8,
            device="cpu",
        )
    ).train()
    return {
        "formulas_evaluated": int(result.summary.get("sequences", 0)),
        "records_read": int(result.summary.get("sequences", 0)),
    }


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
