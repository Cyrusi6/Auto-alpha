"""Chunked local formula evaluator."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import torch

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from evaluation import build_factor_report, split_trade_dates, write_factor_report
from factor_engine import FactorGateConfig, FactorResearchPipeline
from factor_store import (
    ExperimentRecord,
    FactorRecord,
    LocalFactorStore,
    make_experiment_id,
    make_factor_id,
    stable_formula_hash,
)
from model_core.backtest import AShareFactorEvaluator
from model_core.data_loader import AShareDataLoader
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from research.models import FactorCandidate

from .models import (
    FormulaBatchEvalBenchmark,
    FormulaBatchEvalConfig,
    FormulaBatchEvalResult,
    FormulaEvalCacheManifest,
    FormulaEvalRequest,
    FormulaEvalResult,
)


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


class FormulaBatchEvaluator:
    def __init__(self, config: FormulaBatchEvalConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.store = LocalFactorStore(config.factor_store_dir)
        self.device = _resolve_device(config.device, config.strict_device)
        self.vm = StackVM()
        self.evaluator = AShareFactorEvaluator()
        self.loader = AShareDataLoader(
            data_dir=config.data_dir,
            device=self.device,
            universe_name=config.universe_name,
            universe_file=config.universe_file,
            matrix_cache_dir=config.matrix_cache_dir,
            use_matrix_cache=config.use_matrix_cache,
            feature_set_name=config.feature_set_name,
            feature_set_manifest_path=config.feature_set_manifest_path,
        )
        self.feature_version = config.feature_set_name or FEATURE_VERSION
        self.cache_dir = Path(config.eval_cache_dir) if config.eval_cache_dir else self.output_dir / "eval_cache"
        self._cache: dict[str, dict[str, Any]] = {}
        self.cache_hits = 0
        self.cache_writes = 0

    def run(self, requests: list[FormulaEvalRequest]) -> FormulaBatchEvalResult:
        created_at = _utc_now()
        batch_id = self.config.batch_id or _make_batch_id(created_at)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.loader.load_data()
        if self.config.use_eval_cache:
            self._cache = self._load_cache()

        start = time.perf_counter()
        results: list[FormulaEvalResult] = []
        for chunk in _chunks(requests, max(1, self.config.chunk_size)):
            for request in chunk:
                try:
                    results.append(self._run_request(request, batch_id, created_at))
                except Exception as exc:
                    if not self.config.continue_on_error:
                        raise
                    results.append(
                        FormulaEvalResult(
                            request=request,
                            factor_id=None,
                            status="error",
                            score=0.0,
                            metrics_by_split={},
                            gate_reasons=[str(exc)],
                            max_abs_correlation=0.0,
                            error=str(exc),
                            feature_set_name=self.config.feature_set_name,
                            feature_version=self.feature_version,
                            campaign_id=self.config.alpha_campaign_id,
                            alpha_candidate_id=(request.metadata or {}).get("alpha_candidate_id"),
                            family_tags=(request.metadata or {}).get("alpha_family_tags"),
                            proxy_score=(request.metadata or {}).get("proxy_score"),
                        )
                    )
        elapsed = time.perf_counter() - start
        evaluated = sum(1 for result in results if result.status not in {"invalid", "error", "skipped_existing"})
        benchmark = FormulaBatchEvalBenchmark(
            formulas_requested=len(requests),
            formulas_evaluated=evaluated,
            elapsed_seconds=float(elapsed),
            formulas_per_second=float(evaluated / elapsed) if elapsed > 1e-12 else 0.0,
            device=str(self.device),
            matrix_cache_used=bool(self.config.use_matrix_cache),
            chunk_size=int(self.config.chunk_size),
        )
        cache_manifest = self._write_cache_manifest()
        paths = {
            "formula_batch_eval_result_path": str(self.output_dir / "formula_batch_eval_result.json"),
            "formula_eval_results_path": str(self.output_dir / "formula_eval_results.jsonl"),
            "formula_batch_eval_report_path": str(self.output_dir / "formula_batch_eval_report.md"),
            "formula_eval_cache_manifest_path": str(self.output_dir / "formula_eval_cache_manifest.json"),
            "formula_batch_eval_benchmark_path": str(self.output_dir / "formula_batch_eval_benchmark.json"),
            "resource_usage_path": str(self.output_dir / "resource_usage.json"),
        }
        result = FormulaBatchEvalResult(
            batch_id=batch_id,
            created_at=created_at,
            status="success",
            results=results,
            summary=_summary(results),
            paths=paths,
            cache_manifest=cache_manifest.to_dict(),
            benchmark=benchmark.to_dict(),
        )
        self._write_outputs(result)
        self._write_resource_usage(result)
        return result

    def _run_request(self, request: FormulaEvalRequest, batch_id: str, created_at: str) -> FormulaEvalResult:
        started = time.perf_counter()
        valid, reason = self.vm.validate_with_reason(request.formula_tokens)
        if not valid:
            return FormulaEvalResult(
                request=request,
                factor_id=None,
                status="invalid",
                score=0.0,
                metrics_by_split={},
                gate_reasons=[reason],
                max_abs_correlation=0.0,
                elapsed_seconds=time.perf_counter() - started,
            )
        formula_hash = request.formula_hash or stable_formula_hash(
            request.formula_tokens,
            request.formula_names,
            self.feature_version,
            OPERATOR_VERSION,
        )
        existing = self.store.find_factor_by_hash(formula_hash)
        if existing is not None and self.config.skip_existing:
            return FormulaEvalResult(
                request=request,
                factor_id=existing.factor_id,
                status="skipped_existing",
                score=_score(existing.metrics),
                metrics_by_split={"all": existing.metrics or {}},
                gate_reasons=["skipped_existing"],
                max_abs_correlation=float((existing.metadata or {}).get("max_abs_correlation", 0.0) or 0.0),
                elapsed_seconds=time.perf_counter() - started,
                feature_set_name=self.config.feature_set_name,
                feature_version=self.feature_version,
                campaign_id=self.config.alpha_campaign_id,
                alpha_candidate_id=(request.metadata or {}).get("alpha_candidate_id"),
                family_tags=(request.metadata or {}).get("alpha_family_tags"),
                proxy_score=(request.metadata or {}).get("proxy_score"),
            )

        cache_key = self._cache_key(request)
        if self.config.use_eval_cache and cache_key in self._cache:
            self.cache_hits += 1
            payload = self._cache[cache_key]
            return _result_from_cache_payload(request, payload, time.perf_counter() - started)

        raw_factors = self.vm.execute(request.formula_tokens, self.loader.feat_tensor)
        if raw_factors is None:
            raise RuntimeError(f"formula execution failed: {request.name}")

        split_result = split_trade_dates(
            self.loader.trade_dates,
            train_ratio=self.config.train_ratio,
            valid_ratio=self.config.valid_ratio,
        )
        research = FactorResearchPipeline(
            evaluator=self.evaluator,
            gate_config=FactorGateConfig(
                min_coverage=self.config.min_coverage,
                max_abs_correlation=self.config.correlation_threshold,
            ),
            enable_gate=self.config.enable_gate,
            correlation_threshold=self.config.correlation_threshold,
        ).run(
            factors=raw_factors,
            raw_data=self.loader.raw_data_cache,
            target_ret=self.loader.target_ret,
            trade_dates=self.loader.trade_dates,
            ts_codes=self.loader.ts_codes,
            store=self.store,
            transform_method=self.config.factor_transform,
            train_ratio=self.config.train_ratio,
            valid_ratio=self.config.valid_ratio,
        )
        gate_reasons = research.gate_decision.reasons if research.gate_decision is not None else []
        factor_id = make_factor_id(formula_hash)
        report_json_path = None
        report_md_path = None
        should_register = self.config.register_approved and research.status == "approved"
        if should_register or (self.config.register_approved and existing is None and research.status == "candidate"):
            experiment_id = make_experiment_id(factor_id, created_at)
            gate_payload = research.gate_decision.to_dict() if research.gate_decision is not None else None
            metadata = {
                "formula_source": request.source,
                "formula_complexity": request.complexity,
                "formula_lookback": request.lookback,
                "batch_id": batch_id,
                "max_abs_correlation": float(research.max_abs_correlation),
                "similar_factors": research.similar_factors,
                "gate_decision": gate_payload,
                **(request.metadata or {}),
            }
            self.store.save_factor(
                FactorRecord(
                    factor_id=factor_id,
                    formula=request.formula_names,
                    formula_tokens=request.formula_tokens,
                    formula_hash=formula_hash,
                    feature_version=self.feature_version,
                    operator_version=OPERATOR_VERSION,
                    lookback_days=int(request.lookback or self.vm.formula_lookback(request.formula_tokens)),
                    created_at=created_at,
                    status=research.status,
                    description=request.description,
                    metrics=research.metrics_by_split.get("all", {}),
                    transform_method=research.transform_method,
                    gate_status=research.gate_decision.status if research.gate_decision is not None else None,
                    gate_reasons=gate_reasons or None,
                    metadata=metadata,
                    factor_type="single",
                    batch_id=batch_id,
                )
            )
            self.store.save_experiment(
                ExperimentRecord(
                    experiment_id=experiment_id,
                    factor_id=factor_id,
                    data_dir=self.config.data_dir,
                    output_dir=self.config.output_dir,
                    train_dates=split_result.train_dates,
                    valid_dates=split_result.valid_dates,
                    test_dates=split_result.test_dates,
                    metrics_by_split=research.metrics_by_split,
                    created_at=created_at,
                    notes=f"formula_batch_eval={batch_id}; request={request.name}",
                )
            )
            self.store.save_factor_values(factor_id, self.loader.ts_codes, self.loader.trade_dates, research.transformed_factors)
            report = build_factor_report(
                factor_id=factor_id,
                experiment_id=experiment_id,
                formula=request.formula_names,
                formula_tokens=request.formula_tokens,
                metrics_by_split=research.metrics_by_split,
                n_stocks=len(self.loader.ts_codes),
                n_dates=len(self.loader.trade_dates),
                n_features=int(self.loader.feat_tensor.shape[1]),
                train_dates=split_result.train_dates,
                valid_dates=split_result.valid_dates,
                test_dates=split_result.test_dates,
                created_at=created_at,
                transform_method=research.transform_method,
                gate_decision=gate_payload,
                max_abs_correlation=research.max_abs_correlation,
                similar_factors=research.similar_factors,
                status=research.status,
            )
            report_json, report_md = write_factor_report(report, Path(self.config.report_dir) / "factors" / factor_id)
            report_json_path = str(report_json)
            report_md_path = str(report_md)

        result = FormulaEvalResult(
            request=request,
            factor_id=factor_id,
            status=research.status,
            score=_score(research.metrics_by_split.get("all")),
            metrics_by_split=research.metrics_by_split,
            gate_reasons=gate_reasons,
            max_abs_correlation=float(research.max_abs_correlation),
            cache_hit=False,
            elapsed_seconds=time.perf_counter() - started,
            report_json_path=report_json_path,
            report_md_path=report_md_path,
            feature_set_name=self.config.feature_set_name,
            feature_version=self.feature_version,
            campaign_id=self.config.alpha_campaign_id,
            alpha_candidate_id=(request.metadata or {}).get("alpha_candidate_id"),
            family_tags=(request.metadata or {}).get("alpha_family_tags"),
            proxy_score=(request.metadata or {}).get("proxy_score"),
            final_score=(request.metadata or {}).get("final_score"),
        )
        if self.config.use_eval_cache:
            self._cache[cache_key] = result.to_dict()
            self.cache_writes += 1
            self._write_cache_record(cache_key, result)
        return result

    def _cache_key(self, request: FormulaEvalRequest) -> str:
        payload = {
            "formula_hash": request.formula_hash,
            "transform": self.config.factor_transform,
            "train_ratio": self.config.train_ratio,
            "valid_ratio": self.config.valid_ratio,
            "universe_name": self.config.universe_name,
            "universe_file": self.config.universe_file,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        path = self.cache_dir / "formula_eval_cache.jsonl"
        cache: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return cache
        for row in _read_jsonl(path):
            key = str(row.get("cache_key") or "")
            payload = row.get("result")
            if key and isinstance(payload, dict):
                cache[key] = payload
        return cache

    def _write_cache_record(self, cache_key: str, result: FormulaEvalResult) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with (self.cache_dir / "formula_eval_cache.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "cache_key": cache_key,
                        "created_at": _utc_now(),
                        "formula_hash": result.request.formula_hash,
                        "result": result.to_dict(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            handle.write("\n")

    def _write_cache_manifest(self) -> FormulaEvalCacheManifest:
        manifest = FormulaEvalCacheManifest(
            cache_dir=str(self.cache_dir),
            enabled=bool(self.config.use_eval_cache),
            cache_hits=int(self.cache_hits),
            cache_writes=int(self.cache_writes),
            cache_records=len(self._cache),
            keys=sorted(self._cache)[:1000],
        )
        write_json_artifact(
            self.output_dir / "formula_eval_cache_manifest.json",
            manifest.to_dict(),
            "formula_eval_cache_manifest",
            "formula_batch_eval",
        )
        return manifest

    def _write_outputs(self, result: FormulaBatchEvalResult) -> None:
        write_json_artifact(
            self.output_dir / "formula_batch_eval_result.json",
            result.to_dict(),
            "formula_batch_eval_result",
            "formula_batch_eval",
        )
        write_jsonl_artifact(
            self.output_dir / "formula_eval_results.jsonl",
            [row.to_dict() for row in result.results],
            "formula_eval_results",
            "formula_batch_eval",
        )
        write_json_artifact(
            self.output_dir / "formula_batch_eval_benchmark.json",
            result.benchmark,
            "formula_batch_eval_benchmark",
            "formula_batch_eval",
        )
        (self.output_dir / "formula_batch_eval_report.md").write_text(_render_report(result), encoding="utf-8")

    def _write_resource_usage(self, result: FormulaBatchEvalResult) -> None:
        fallback_to_cpu = str(self.config.device or "auto").startswith("cuda") and str(self.device) == "cpu"
        payload = {
            "device_requested": self.config.device,
            "device_resolved": str(self.device),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "fallback_to_cpu": bool(fallback_to_cpu),
            "shard_id": self.config.shard_id,
            "shard_count": self.config.shard_count,
            "formulas_per_second": float(result.benchmark.get("formulas_per_second", 0.0) or 0.0),
            "formulas_evaluated": int(result.benchmark.get("formulas_evaluated", 0) or 0),
        }
        path = Path(self.config.resource_report_path) if self.config.resource_report_path else self.output_dir / "resource_usage.json"
        write_json_artifact(path, payload, "resource_usage_report", "formula_batch_eval")


def requests_from_candidates(candidates: Iterable[FactorCandidate]) -> list[FormulaEvalRequest]:
    requests = []
    vm = StackVM()
    for idx, candidate in enumerate(candidates):
        tokens = [int(token) for token in candidate.formula_tokens]
        names = list(candidate.formula_names) if candidate.formula_names else FORMULA_VOCAB.decode_tokens(tokens)
        formula_hash = candidate.formula_hash or stable_formula_hash(tokens, names, FEATURE_VERSION, OPERATOR_VERSION)
        requests.append(
            FormulaEvalRequest(
                name=candidate.name or f"candidate_{idx}",
                formula_tokens=tokens,
                formula_names=names,
                formula_hash=formula_hash,
                description=candidate.description,
                source=candidate.source,
                complexity=candidate.complexity or vm.formula_complexity(tokens),
                lookback=candidate.lookback or vm.formula_lookback(tokens),
                metadata={
                    "parent_hashes": candidate.parent_hashes or [],
                    "generation": candidate.generation,
                    "validation_reason": candidate.validation_reason,
                },
            )
        )
    return requests


def requests_from_corpus(path: str | Path, max_records: int | None = None) -> list[FormulaEvalRequest]:
    requests = []
    for idx, payload in enumerate(_read_jsonl(Path(path))):
        if not payload.get("valid", True):
            continue
        tokens = [int(token) for token in payload.get("formula_tokens", [])]
        if not tokens:
            continue
        names = payload.get("formula_names") or FORMULA_VOCAB.decode_tokens(tokens)
        formula_hash = str(payload.get("formula_hash") or stable_formula_hash(tokens, names, FEATURE_VERSION, OPERATOR_VERSION))
        requests.append(
            FormulaEvalRequest(
                name=f"corpus_{idx}_{formula_hash[:8]}",
                formula_tokens=tokens,
                formula_names=[str(name) for name in names],
                formula_hash=formula_hash,
                source="corpus",
                complexity=payload.get("complexity"),
                lookback=payload.get("lookback"),
                metadata={"corpus_sources": payload.get("sources", [])},
            )
        )
        if max_records is not None and len(requests) >= max_records:
            break
    return requests


def requests_from_requests_json(path: str | Path) -> list[FormulaEvalRequest]:
    target = Path(path)
    if not target.exists():
        raise ValueError(f"requests JSON file not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid requests JSON: {target}: {exc}") from exc
    if isinstance(payload, dict):
        rows = payload.get("requests")
        if not isinstance(rows, list):
            raise ValueError("requests JSON object must contain requests: list[dict]")
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("requests JSON must be list[dict] or object with requests: list[dict]")
    return [_request_from_payload(row, idx) for idx, row in enumerate(rows)]


def requests_from_requests_jsonl(path: str | Path) -> list[FormulaEvalRequest]:
    target = Path(path)
    requests: list[FormulaEvalRequest] = []
    if not target.exists():
        raise ValueError(f"requests JSONL file not found: {target}")
    for idx, line in enumerate(target.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at request[{idx}]: {exc}") from exc
        requests.append(_request_from_payload(payload, idx))
    return requests


def _request_from_payload(payload: Any, idx: int) -> FormulaEvalRequest:
    if not isinstance(payload, dict):
        raise ValueError(f"request[{idx}] must be an object")
    required = ("name", "formula_tokens", "formula_names", "formula_hash")
    for field in required:
        if field not in payload:
            raise ValueError(f"request[{idx}] missing required field: {field}")
        value = payload.get(field)
        if value is None or value == "":
            raise ValueError(f"request[{idx}] has empty required field: {field}")
    tokens = payload.get("formula_tokens")
    names = payload.get("formula_names")
    if not isinstance(tokens, list) or not tokens:
        raise ValueError(f"request[{idx}] formula_tokens must be a non-empty list")
    if not isinstance(names, list) or not names:
        raise ValueError(f"request[{idx}] formula_names must be a non-empty list")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"request[{idx}] metadata must be an object when provided")
    try:
        formula_tokens = [int(token) for token in tokens]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"request[{idx}] formula_tokens must contain integers") from exc
    try:
        complexity = int(payload["complexity"]) if payload.get("complexity") is not None else None
        lookback = int(payload["lookback"]) if payload.get("lookback") is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"request[{idx}] complexity/lookback must be integers when provided") from exc
    return FormulaEvalRequest(
        name=str(payload["name"]),
        formula_tokens=formula_tokens,
        formula_names=[str(name) for name in names],
        formula_hash=str(payload["formula_hash"]),
        description=str(payload["description"]) if payload.get("description") is not None else None,
        source=str(payload["source"]) if payload.get("source") is not None else None,
        complexity=complexity,
        lookback=lookback,
        metadata=metadata,
    )


def _resolve_device(device: str, strict: bool) -> torch.device:
    requested = str(device or "auto")
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested.startswith("cuda") and not torch.cuda.is_available():
        if strict:
            raise ValueError("cuda requested but not available")
        return torch.device("cpu")
    return torch.device(requested)


def _result_from_cache_payload(request: FormulaEvalRequest, payload: dict[str, Any], elapsed: float) -> FormulaEvalResult:
    result = dict(payload)
    result["request"] = request
    result["cache_hit"] = True
    result["elapsed_seconds"] = float(elapsed)
    return FormulaEvalResult(**result)


def _summary(results: list[FormulaEvalResult]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    ranked = sorted(results, key=lambda item: item.score, reverse=True)
    scores = [float(result.score) for result in results]
    unique_hashes = {result.request.formula_hash for result in results if result.request.formula_hash}
    split_summary: dict[str, dict[str, float]] = {}
    for split in ("train", "valid", "test", "all"):
        split_scores = [
            float((result.metrics_by_split.get(split) or {}).get("score", 0.0) or 0.0)
            for result in results
            if isinstance(result.metrics_by_split, dict) and split in result.metrics_by_split
        ]
        split_summary[split] = _distribution(split_scores)
    return {
        "total": len(results),
        "evaluated_trial_count": sum(1 for result in results if result.status not in {"invalid", "error"}),
        "unique_formula_hash_count": len(unique_hashes),
        "score_distribution": _distribution(scores),
        "train_valid_test_metric_summary": split_summary,
        "status_counts": counts,
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "errors": counts.get("error", 0),
        "cache_hits": sum(1 for result in results if result.cache_hit),
        "top": [
            {
                "name": result.request.name,
                "formula_hash": result.request.formula_hash,
                "factor_id": result.factor_id,
                "status": result.status,
                "score": float(result.score),
            }
            for result in ranked[:20]
        ],
    }


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0.0, "min": 0.0, "median": 0.0, "max": 0.0, "mean": 0.0}
    ordered = sorted(values)
    return {
        "count": float(len(values)),
        "min": float(ordered[0]),
        "median": float(ordered[len(ordered) // 2]),
        "max": float(ordered[-1]),
        "mean": float(sum(values) / len(values)),
    }


def _score(metrics: dict[str, float] | None) -> float:
    if not isinstance(metrics, dict):
        return 0.0
    try:
        return float(metrics.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _render_report(result: FormulaBatchEvalResult) -> str:
    lines = [
        "# Formula Batch Evaluation Report",
        "",
        f"- batch_id: `{result.batch_id}`",
        f"- status: `{result.status}`",
        f"- formulas: {result.summary.get('total', 0)}",
        f"- device: `{result.benchmark.get('device')}`",
        f"- formulas_per_second: {float(result.benchmark.get('formulas_per_second', 0.0)):.6f}",
        "",
        "## Status Counts",
        "",
        "| status | count |",
        "| --- | ---: |",
    ]
    for status, count in sorted((result.summary.get("status_counts") or {}).items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## Top Results", "", "| formula | status | score | factor |", "| --- | --- | ---: | --- |"])
    for row in result.summary.get("top", []):
        lines.append(
            f"| `{row.get('name')}` | {row.get('status')} | {float(row.get('score', 0.0)):.6f} | `{row.get('factor_id')}` |"
        )
    return "\n".join(lines) + "\n"


def _chunks(items: list[FormulaEvalRequest], size: int):
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _make_batch_id(created_at: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    return f"formula_eval_{safe}"
