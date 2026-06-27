"""One-click research suite workflow."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backtest import run_backtest
from data_pipeline import run_pipeline
from factor_store import LocalFactorStore
from formula_search import run_search
from model_core.data_loader import AShareDataLoader
from strategy_manager import runner as strategy_runner
from universe import run_universe

from .catalog import register_artifact, write_artifact_catalog
from .models import ArtifactCatalog, PromotionConfig, ResearchSuiteConfig, ResearchSuiteResult, SuiteStageResult
from .promotion import promote_factor_if_eligible
from .report import write_promotion_decision, write_suite_report
from .walk_forward import build_walk_forward_windows, evaluate_factor_walk_forward


class ResearchSuiteRunner:
    def __init__(self, config: ResearchSuiteConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.catalog = ArtifactCatalog(suite_name=config.suite_name, created_at=_utc_now())
        self.stages: list[SuiteStageResult] = []
        self.selected_factor_id: str | None = None
        self.promotion_decision = None
        self.backtest_summary: dict[str, Any] = {}

    def run(self) -> ResearchSuiteResult:
        started_at = _utc_now()
        status = "success"
        try:
            if not self.config.skip_data_sync:
                self._append_stage("data_sync", self._stage_data_sync)
            if not self.config.skip_universe:
                self._append_stage("universe", self._stage_universe)
            self._append_stage("formula_search", self._stage_formula_search)
            self._append_stage("backtest", self._stage_backtest)
            if not self.config.skip_orders:
                self._append_stage("orders", self._stage_orders)
            self._append_stage("walk_forward", self._stage_walk_forward)
            if not self.config.disable_promotion and self.config.promote_latest_composite:
                self._append_stage("promotion", self._stage_promotion)
        except Exception:
            status = "failed"

        finished_at = _utc_now()
        result = ResearchSuiteResult(
            suite_name=self.config.suite_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            stages=self.stages,
            selected_factor_id=self.selected_factor_id,
            promotion_decision=self.promotion_decision,
            paths={
                "suite_result_path": str(self.output_dir / "suite_result.json"),
                "suite_report_path": str(self.output_dir / "suite_report.md"),
                "artifact_catalog_path": str(self.output_dir / "artifact_catalog.json"),
                "artifact_catalog_md_path": str(self.output_dir / "artifact_catalog.md"),
                "promotion_decision_path": str(self.output_dir / "promotion_decision.json"),
            },
            summary={
                "suite_name": self.config.suite_name,
                "status": status,
                "selected_factor_id": self.selected_factor_id,
                "backtest_metrics": self.backtest_summary.get("metrics", {}),
            },
        )
        suite_json, suite_md = write_suite_report(result, self.output_dir)
        promotion_path = write_promotion_decision(self.promotion_decision, self.output_dir)
        self.catalog = register_artifact(self.catalog, "suite_result", suite_json, "json", "suite")
        self.catalog = register_artifact(self.catalog, "suite_report", suite_md, "markdown", "suite")
        self.catalog = register_artifact(self.catalog, "promotion_decision", promotion_path, "json", "promotion")
        catalog_json, catalog_md = write_artifact_catalog(self.catalog, self.output_dir)
        self.catalog = register_artifact(self.catalog, "artifact_catalog", catalog_json, "json", "suite")
        self.catalog = register_artifact(self.catalog, "artifact_catalog_markdown", catalog_md, "markdown", "suite")
        write_artifact_catalog(self.catalog, self.output_dir)
        return result

    def _append_stage(self, name: str, func: Callable[[], tuple[dict[str, Any], dict[str, str]]]) -> None:
        started_at = _utc_now()
        try:
            summary, output_paths = func()
            stage = SuiteStageResult(
                name=name,
                status="success",
                started_at=started_at,
                finished_at=_utc_now(),
                output_paths=output_paths,
                summary=summary,
            )
            self.stages.append(stage)
        except Exception as exc:
            stage = SuiteStageResult(
                name=name,
                status="failed",
                started_at=started_at,
                finished_at=_utc_now(),
                error=str(exc),
            )
            self.stages.append(stage)
            raise

    def _stage_data_sync(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            run_pipeline.main,
            [
                "--sync",
                "--provider",
                self.config.provider,
                "--data-dir",
                self.config.data_dir,
                "--validate",
                "--mode",
                "overwrite",
                "--index-codes",
                self.config.index_code,
            ],
        )
        output_paths = {
            "manifest": str(Path(self.config.data_dir) / "manifest.json"),
            "quality_report": str(Path(self.config.data_dir) / "quality_report.json"),
            "pipeline_state": str(Path(self.config.data_dir) / "pipeline_state.json"),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, "json", "data_sync")
        return payload, output_paths

    def _stage_universe(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            run_universe.main,
            [
                "--data-dir",
                self.config.data_dir,
                "--as-of-date",
                self.config.as_of_date,
                "--universe-name",
                self.config.universe_name,
                "--use-index-members",
                "--index-code",
                self.config.index_code,
                "--min-listed-days",
                "0",
                "--min-amount",
                "0",
            ],
        )
        output_paths = {
            "universe": str(payload.get("output_path", "")),
            "universe_summary": str(payload.get("summary_path", "")),
        }
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, "jsonl" if name == "universe" else "json", "universe")
        return payload, output_paths

    def _stage_formula_search(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            run_search.main,
            [
                "--data-dir",
                self.config.data_dir,
                "--universe-name",
                self.config.universe_name,
                "--factor-store-dir",
                self.config.factor_store_dir,
                "--report-dir",
                self.config.report_dir,
                "--output-dir",
                str(Path(self.config.output_dir) / "search"),
                "--seed",
                str(self.config.search_seed),
                "--population-size",
                str(self.config.search_population_size),
                "--generations",
                str(self.config.search_generations),
                "--max-formula-len",
                "8",
                "--max-complexity",
                "24",
                "--max-lookback",
                "10",
                "--candidate-batch-size",
                str(self.config.search_max_candidates or self.config.search_population_size),
                "--factor-transform",
                self.config.factor_transform,
                "--enable-gate",
                "--top-k",
                str(self.config.top_k),
                "--composite-method",
                self.config.composite_method,
                "--correlation-threshold",
                "0.99",
                "--min-coverage",
                "0.5",
            ],
        )
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        output_paths = {
            "search_result": paths.get("search_result_path", str(Path(self.config.output_dir) / "search" / "search_result.json")),
            "search_candidates": paths.get("search_candidates_path", str(Path(self.config.output_dir) / "search" / "search_candidates.jsonl")),
            "search_report": paths.get("search_report_json_path", str(Path(self.config.output_dir) / "search" / "search_report.json")),
            "search_report_markdown": paths.get("search_report_md_path", str(Path(self.config.output_dir) / "search" / "search_report.md")),
            "factors": str(Path(self.config.factor_store_dir) / "factors.jsonl"),
            "experiments": str(Path(self.config.factor_store_dir) / "experiments.jsonl"),
        }
        for name, path in output_paths.items():
            kind = "markdown" if path.endswith(".md") else ("jsonl" if path.endswith(".jsonl") else "json")
            self.catalog = register_artifact(self.catalog, name, path, kind, "formula_search")
        self.selected_factor_id = _select_latest_composite(self.config.factor_store_dir)
        return payload, output_paths

    def _stage_backtest(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            run_backtest.main,
            [
                "--data-dir",
                self.config.data_dir,
                "--factor-store-dir",
                self.config.factor_store_dir,
                "--output-dir",
                self.config.backtest_dir,
                "--latest-approved",
                "--factor-type",
                "composite",
                "--top-n",
                "2",
                "--max-weight",
                "0.10",
                "--portfolio-method",
                self.config.portfolio_method,
                "--index-code",
                self.config.index_code,
                "--risk-aversion",
                str(self.config.risk_aversion),
                "--turnover-penalty",
                str(self.config.turnover_penalty),
                "--max-turnover",
                str(self.config.max_turnover),
                "--max-industry-active-weight",
                str(self.config.max_industry_active_weight),
                "--max-tracking-error",
                str(self.config.max_tracking_error),
                "--risk-report-dir",
                str(Path(self.config.backtest_dir) / "risk"),
            ],
        )
        self.backtest_summary = payload
        self.selected_factor_id = str(payload.get("factor_id") or self.selected_factor_id)
        output_paths = {
            "backtest_result": str(Path(self.config.backtest_dir) / "backtest_result.json"),
            "equity_curve": str(Path(self.config.backtest_dir) / "equity_curve.jsonl"),
            "trades": str(Path(self.config.backtest_dir) / "trades.jsonl"),
        }
        if payload.get("risk_report_path"):
            output_paths["risk_report"] = str(payload["risk_report_path"])
        if payload.get("risk_report_md_path"):
            output_paths["risk_report_markdown"] = str(payload["risk_report_md_path"])
        if payload.get("optimization_result_path"):
            output_paths["optimization_result"] = str(payload["optimization_result_path"])
        if self.selected_factor_id:
            output_paths["selected_factor_values"] = str(
                Path(self.config.factor_store_dir) / "factor_values" / f"{self.selected_factor_id}.jsonl"
            )
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "backtest")
        return payload, output_paths

    def _stage_orders(self) -> tuple[dict[str, Any], dict[str, str]]:
        payload = _run_json_main(
            strategy_runner.main,
            [
                "--data-dir",
                self.config.data_dir,
                "--factor-store-dir",
                self.config.factor_store_dir,
                "--output-dir",
                self.config.orders_dir,
                "--latest-approved",
                "--factor-type",
                "composite",
                "--top-n",
                "2",
                "--max-weight",
                "0.10",
                "--portfolio-value",
                "1000000",
                "--portfolio-method",
                self.config.portfolio_method,
                "--index-code",
                self.config.index_code,
                "--risk-aversion",
                str(self.config.risk_aversion),
                "--turnover-penalty",
                str(self.config.turnover_penalty),
                "--max-turnover",
                str(self.config.max_turnover),
                "--max-industry-active-weight",
                str(self.config.max_industry_active_weight),
                "--max-tracking-error",
                str(self.config.max_tracking_error),
            ],
        )
        output_paths = {
            "target_positions": str(Path(self.config.orders_dir) / "target_positions.jsonl"),
            "orders": str(Path(self.config.orders_dir) / "orders.jsonl"),
            "paper_fills": str(Path(self.config.orders_dir) / "paper_fills.jsonl"),
        }
        if payload.get("risk_report_path"):
            output_paths["orders_risk_report"] = str(payload["risk_report_path"])
        if payload.get("optimization_result_path"):
            output_paths["orders_optimization_result"] = str(payload["optimization_result_path"])
        for name, path in output_paths.items():
            self.catalog = register_artifact(self.catalog, name, path, _artifact_kind(path), "orders")
        return payload, output_paths

    def _stage_walk_forward(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for walk-forward evaluation")
        loader = AShareDataLoader(data_dir=self.config.data_dir, device="cpu", universe_name=self.config.universe_name).load_data()
        store = LocalFactorStore(self.config.factor_store_dir)
        windows = build_walk_forward_windows(
            loader.trade_dates,
            self.config.walk_forward_train_size,
            self.config.walk_forward_test_size,
            self.config.walk_forward_step_size,
        )
        result = evaluate_factor_walk_forward(loader, store, self.selected_factor_id, windows)
        path = Path(self.config.output_dir) / "walk_forward_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.catalog = register_artifact(self.catalog, "walk_forward_result", path, "json", "walk_forward")
        return result.to_dict(), {"walk_forward_result": str(path)}

    def _stage_promotion(self) -> tuple[dict[str, Any], dict[str, str]]:
        if not self.selected_factor_id:
            raise ValueError("no selected factor for promotion")
        walk_path = Path(self.config.output_dir) / "walk_forward_result.json"
        walk_payload = _read_json(walk_path)
        from .models import WalkForwardResult

        walk_result = WalkForwardResult(
            factor_id=self.selected_factor_id,
            windows=walk_payload.get("windows", []),
            summary=walk_payload.get("summary", {}),
        )
        store = LocalFactorStore(self.config.factor_store_dir)
        self.promotion_decision = promote_factor_if_eligible(
            store,
            self.selected_factor_id,
            walk_result,
            self.backtest_summary.get("metrics", {}),
            PromotionConfig(),
        )
        path = write_promotion_decision(self.promotion_decision, self.config.output_dir)
        self.catalog = register_artifact(self.catalog, "promotion_decision", path, "json", "promotion")
        return self.promotion_decision.to_dict(), {"promotion_decision": str(path)}


def _run_json_main(main_func: Callable[[list[str] | None], int], argv: list[str]) -> dict[str, Any]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = main_func(argv)
    output = buffer.getvalue().strip()
    if exit_code != 0:
        raise RuntimeError(f"stage command failed with exit code {exit_code}: {' '.join(argv)}")
    if not output:
        return {}
    return json.loads(output)


def _select_latest_composite(factor_store_dir: str) -> str | None:
    record = LocalFactorStore(factor_store_dir).load_latest_factor(status="approved", factor_type="composite")
    return record.factor_id if record is not None else None


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _artifact_kind(path: str) -> str:
    if path.endswith(".md"):
        return "markdown"
    if path.endswith(".jsonl"):
        return "jsonl"
    return "json"
