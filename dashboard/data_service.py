"""Local artifact reader for the A-share dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DashboardConfig


class AshareDashboardService:
    def __init__(self, config: DashboardConfig | None = None):
        self.config = config or DashboardConfig.from_env()

    def load_manifest(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "manifest.json")

    def load_quality_report(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "quality_report.json")

    def load_sync_plan(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "sync_plan.json")

    def load_pipeline_state(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "pipeline_state.json")

    def load_api_audit(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.data_dir / "api_audit.jsonl")

    def load_dataset_stats(self) -> dict[str, Any]:
        return self._read_json(self.config.data_dir / "dataset_stats.json")

    def load_snapshot_summary(self) -> pd.DataFrame:
        snapshots_dir = self.config.data_dir / "snapshots"
        records: list[dict[str, Any]] = []
        if not snapshots_dir.exists():
            return pd.DataFrame()
        for snapshot_dir in sorted(path for path in snapshots_dir.iterdir() if path.is_dir()):
            datasets = sorted(
                dataset_dir.name
                for dataset_dir in snapshot_dir.iterdir()
                if (dataset_dir / "records.jsonl").exists()
            )
            records.append(
                {
                    "snapshot": snapshot_dir.name,
                    "datasets": len(datasets),
                    "dataset_names": ", ".join(datasets),
                }
            )
        return pd.DataFrame(records)

    def load_dataset(self, name: str, limit: int | None = 200) -> pd.DataFrame:
        frame = self._read_jsonl(self.config.data_dir / name / "records.jsonl")
        if limit is not None and len(frame) > limit:
            return frame.head(limit)
        return frame

    def load_factors(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.factor_store_dir / "factors.jsonl")

    def load_factor_overview(self) -> pd.DataFrame:
        factors = self.load_factors()
        if factors.empty:
            return factors

        records: list[dict[str, Any]] = []
        for _, row in factors.iterrows():
            metadata = row.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            similar = metadata.get("similar_factors")
            similar = similar if isinstance(similar, list) else []
            components = row.get("parent_factor_ids") or metadata.get("component_factor_ids") or []
            components = components if isinstance(components, list) else []
            gate_reasons = row.get("gate_reasons")
            gate_reasons = gate_reasons if isinstance(gate_reasons, list) else []
            records.append(
                {
                    "factor_id": row.get("factor_id"),
                    "factor_type": row.get("factor_type") or "single",
                    "batch_id": row.get("batch_id") or metadata.get("batch_id") or "",
                    "component_factor_ids": ", ".join(str(item) for item in components),
                    "formula_complexity": metadata.get("formula_complexity", ""),
                    "formula_lookback": metadata.get("formula_lookback", ""),
                    "formula_source": metadata.get("formula_source", ""),
                    "generation": metadata.get("generation", ""),
                    "status": row.get("status") or "candidate",
                    "transform_method": row.get("transform_method") or "raw",
                    "gate_status": row.get("gate_status") or "",
                    "gate_reasons": ", ".join(str(reason) for reason in gate_reasons),
                    "max_abs_correlation": float(metadata.get("max_abs_correlation", 0.0) or 0.0),
                    "similar_factors": len(similar),
                    "score": self._metric_value(row.get("metrics"), "score"),
                }
            )
        return pd.DataFrame(records)

    def load_experiments(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.factor_store_dir / "experiments.jsonl")

    def load_latest_factor_metrics(self) -> dict[str, Any]:
        factors = self.load_factors()
        if factors.empty:
            return {}
        latest = factors.iloc[-1].to_dict()
        metrics = latest.get("metrics")
        return metrics if isinstance(metrics, dict) else {}

    @staticmethod
    def _metric_value(metrics: Any, key: str) -> float:
        if isinstance(metrics, dict):
            return float(metrics.get(key, 0.0) or 0.0)
        return 0.0

    def load_factor_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "factor_report.json")

    def load_factor_report_markdown(self) -> str:
        path = self.config.report_dir / "factor_report.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_batch_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "batch_report.json") or self._read_json(
            self.config.report_dir.parent / "batch" / "batch_report.json"
        )

    def load_batch_report_markdown(self) -> str:
        candidates = [
            self.config.report_dir / "batch_report.md",
            self.config.report_dir.parent / "batch" / "batch_report.md",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_search_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "search_report.json") or self._read_json(
            self.config.report_dir.parent / "search" / "search_report.json"
        )

    def load_search_report_markdown(self) -> str:
        candidates = [
            self.config.report_dir / "search_report.md",
            self.config.report_dir.parent / "search" / "search_report.md",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_neural_search_result(self) -> dict[str, Any]:
        for path in self._neural_artifact_candidates("neural_search_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_neural_training_history(self) -> pd.DataFrame:
        for path in self._neural_artifact_candidates("neural_training_history.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_neural_search_report_markdown(self) -> str:
        for path in self._neural_artifact_candidates("neural_search_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_neural_checkpoints(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for directory in self._neural_checkpoint_candidates():
            if not directory.exists() or not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.pt")):
                records.append({"path": str(path), "name": path.name, "size_bytes": path.stat().st_size})
        return pd.DataFrame(records)

    def load_suite_result(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir.parent / "suite" / "suite_result.json")

    def load_suite_report_markdown(self) -> str:
        path = self.config.report_dir.parent / "suite" / "suite_report.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def load_artifact_catalog(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir.parent / "suite" / "artifact_catalog.json")

    def load_promotion_decision(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir.parent / "suite" / "promotion_decision.json")

    def load_risk_report_json(self) -> dict[str, Any]:
        for path in [
            self.config.backtest_dir / "risk" / "risk_model_report.json",
            self.config.backtest_dir / "risk" / "risk_report.json",
            self.config.backtest_dir / "risk_model_report.json",
            self.config.backtest_dir / "risk_report.json",
            self.config.orders_dir / "risk_model_report.json",
            self.config.orders_dir / "risk_report.json",
        ]:
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_risk_report_markdown(self) -> str:
        for path in [
            self.config.backtest_dir / "risk" / "risk_model_report.md",
            self.config.backtest_dir / "risk" / "risk_report.md",
            self.config.backtest_dir / "risk_model_report.md",
            self.config.backtest_dir / "risk_report.md",
            self.config.orders_dir / "risk_model_report.md",
            self.config.orders_dir / "risk_report.md",
        ]:
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_risk_exposures(self) -> pd.DataFrame:
        for path in self._risk_artifact_candidates("risk_exposures.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_risk_decomposition(self) -> pd.DataFrame:
        for path in self._risk_artifact_candidates("risk_decomposition.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_return_attribution(self) -> pd.DataFrame:
        for path in self._risk_artifact_candidates("return_attribution.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_optimization_result(self) -> dict[str, Any]:
        for path in [
            self.config.backtest_dir / "optimization_result.json",
            self.config.orders_dir / "optimization_result.json",
            self.config.report_dir.parent / "optimize" / "optimization_result.json",
        ]:
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backtest_result(self) -> dict[str, Any]:
        return self._read_json(self.config.backtest_dir / "backtest_result.json")

    def load_equity_curve(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.backtest_dir / "equity_curve.jsonl")

    def load_trades(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.backtest_dir / "trades.jsonl")

    def load_target_positions(self) -> pd.DataFrame:
        return self._read_table("target_positions")

    def load_orders(self) -> pd.DataFrame:
        return self._read_table("orders")

    def load_paper_fills(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.orders_dir / "paper_fills.jsonl")

    def load_production_run(self) -> dict[str, Any]:
        for path in self._production_candidates("production_run.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_production_run_markdown(self) -> str:
        for path in self._production_candidates("production_run.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_approvals(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for approvals_dir in self._approval_dir_candidates():
            if not approvals_dir.exists():
                continue
            for path in sorted((approvals_dir / "approvals").glob("*.json")):
                payload = self._read_json(path)
                if payload:
                    records.append(payload)
        return pd.DataFrame(records)

    def load_approval_log(self) -> pd.DataFrame:
        for approvals_dir in self._approval_dir_candidates():
            frame = self._read_jsonl(approvals_dir / "approval_log.jsonl")
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_paper_account_state(self) -> dict[str, Any]:
        for path in self._account_candidates("account_state.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_paper_positions(self) -> pd.DataFrame:
        for path in self._account_candidates("positions.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_account_snapshots(self) -> pd.DataFrame:
        for path in self._account_candidates("account_snapshots.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_trade_ledger(self) -> pd.DataFrame:
        for path in self._account_candidates("trade_ledger.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_monitoring_report(self) -> dict[str, Any]:
        for path in self._monitoring_candidates("monitoring_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_monitoring_report_markdown(self) -> str:
        for path in self._monitoring_candidates("monitoring_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_monitoring_alerts(self) -> pd.DataFrame:
        for path in self._monitoring_candidates("alerts.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_matrix_metadata(self) -> dict[str, Any]:
        for path in self._matrix_candidates("metadata.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_matrix_validation_report(self) -> dict[str, Any]:
        for path in self._matrix_candidates("matrix_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_benchmark_result(self) -> dict[str, Any]:
        for path in self._benchmark_candidates("benchmark_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_benchmark_report_markdown(self) -> str:
        for path in self._benchmark_candidates("benchmark_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def load_cross_source_report(self) -> dict[str, Any]:
        for path in self._cross_source_candidates("cross_source_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_cross_source_report_markdown(self) -> str:
        for path in self._cross_source_candidates("cross_source_report.md"):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    def _neural_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.report_dir / filename,
            root / "neural" / filename,
            root / "search" / filename,
            root / "search" / "neural" / filename,
            root / "suite" / "search" / filename,
            root / "suite" / "search" / "neural" / filename,
        ]

    def _neural_checkpoint_candidates(self) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.report_dir / "checkpoints",
            root / "neural" / "checkpoints",
            root / "search" / "checkpoints",
            root / "search" / "neural" / "checkpoints",
            root / "suite" / "search" / "checkpoints",
            root / "suite" / "search" / "neural" / "checkpoints",
        ]

    def _production_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.production_dir / filename,
            root / "production" / filename,
            root / "production_execute" / filename,
        ]

    def _approval_dir_candidates(self) -> list[Path]:
        root = self.config.report_dir.parent
        return [self.config.approval_store_dir, root / "approvals"]

    def _account_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [self.config.paper_account_dir / filename, root / "account" / filename]

    def _monitoring_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [self.config.monitoring_dir / filename, root / "monitoring" / filename]

    def _matrix_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.matrix_cache_dir / filename,
            self.config.data_dir / "matrix_cache" / filename,
            root / "data" / "matrix_cache" / filename,
        ]

    def _benchmark_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.benchmark_dir / filename,
            root / "benchmark" / filename,
            root / "suite_benchmark" / filename,
            root / "suite" / "benchmark" / filename,
        ]

    def _cross_source_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.cross_source_dir / filename,
            root / "cross_source" / filename,
        ]

    def _risk_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backtest_dir / filename,
            self.config.backtest_dir / "risk" / filename,
            self.config.orders_dir / filename,
            root / "backtest" / filename,
            root / "backtest_direct" / filename,
        ]

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _read_jsonl(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return pd.DataFrame(records)

    def _read_table(self, stem: str) -> pd.DataFrame:
        jsonl_path = self.config.orders_dir / f"{stem}.jsonl"
        csv_path = self.config.orders_dir / f"{stem}.csv"
        frame = self._read_jsonl(jsonl_path)
        if not frame.empty:
            return frame
        if csv_path.exists():
            return pd.read_csv(csv_path)
        return pd.DataFrame()


DashboardService = AshareDashboardService
