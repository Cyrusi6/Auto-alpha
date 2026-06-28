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

    def load_corporate_actions_report(self) -> dict[str, Any]:
        for path in self._corporate_action_candidates("corporate_actions_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_corporate_action_events(self) -> pd.DataFrame:
        for path in self._corporate_action_candidates("corporate_action_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_total_return_report(self) -> dict[str, Any]:
        for path in self._corporate_action_candidates("total_return_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_total_return_series(self) -> pd.DataFrame:
        for path in self._corporate_action_candidates("total_return_series.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_adjustment_reconciliation(self) -> dict[str, Any]:
        for path in self._corporate_action_candidates("adjustment_factor_reconciliation.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_capacity_report(self) -> dict[str, Any]:
        for path in self._execution_plan_candidates("capacity_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_execution_plan(self) -> dict[str, Any]:
        for path in self._execution_plan_candidates("execution_plan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_parent_orders(self) -> pd.DataFrame:
        for path in self._execution_plan_candidates("parent_orders.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_child_orders(self) -> pd.DataFrame:
        for path in self._execution_plan_candidates("child_orders.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_child_fills(self) -> pd.DataFrame:
        for path in self._execution_plan_candidates("child_fills.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_execution_quality(self) -> dict[str, Any]:
        for path in self._execution_plan_candidates("execution_quality.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_report(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_reconciliation(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_reconciliation.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_orders(self) -> pd.DataFrame:
        for path in self._broker_artifact_candidates("broker_orders.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_events(self) -> pd.DataFrame:
        for path in self._broker_artifact_candidates("broker_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_fills(self) -> pd.DataFrame:
        for path in self._broker_artifact_candidates("broker_fills.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_batch_summary(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_batch_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_instruction_manifest(self) -> dict[str, Any]:
        for path in self._broker_artifact_candidates("broker_instruction_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_statement_manifest(self) -> dict[str, Any]:
        for path in self._statement_artifact_candidates("broker_statement_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_statement_import_report(self) -> dict[str, Any]:
        for path in self._statement_artifact_candidates("broker_statement_import_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_broker_statement_parse_issues(self) -> pd.DataFrame:
        for path in self._statement_artifact_candidates("broker_statement_parse_issues.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_broker_statement_validation_report(self) -> dict[str, Any]:
        for path in self._statement_artifact_candidates("broker_statement_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_normalized_external(self, dataset: str) -> pd.DataFrame:
        filename = f"normalized_external_{dataset}.jsonl"
        for path in self._statement_artifact_candidates(filename):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_eod_reconciliation_report(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("eod_reconciliation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_reconciliation_breaks(self) -> pd.DataFrame:
        for path in self._reconciliation_artifact_candidates("reconciliation_breaks.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_external_account_mirror(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("external_account_mirror.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_external_mirror_table(self, name: str) -> pd.DataFrame:
        filename = f"external_{name}_mirror.jsonl"
        for path in self._reconciliation_artifact_candidates(filename):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_adjustment_proposals(self) -> pd.DataFrame:
        for path in self._reconciliation_artifact_candidates("adjustment_proposals.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_adjustment_proposal_batch(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("adjustment_proposal_batch.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_adjustment_application_result(self) -> dict[str, Any]:
        for path in self._reconciliation_artifact_candidates("adjustment_application_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_adjustment_ledger(self) -> pd.DataFrame:
        for path in self._account_candidates("adjustment_ledger.jsonl") + self._reconciliation_artifact_candidates("adjustment_ledger.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

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

    def load_settlement_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("settlement_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_settlement_events(self) -> pd.DataFrame:
        for path in self._settlement_candidates("settlement_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_cash_buckets(self) -> pd.DataFrame:
        for path in self._settlement_candidates("cash_buckets.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_position_lots(self) -> pd.DataFrame:
        for path in self._settlement_candidates("position_lots.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_position_availability(self) -> pd.DataFrame:
        for path in self._settlement_candidates("position_availability.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_realized_pnl(self) -> pd.DataFrame:
        for path in self._settlement_candidates("realized_pnl.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_account_nav(self) -> pd.DataFrame:
        for path in self._settlement_candidates("account_nav.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_account_reconciliation_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("account_reconciliation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_account_performance_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("account_performance_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_fee_tax_report(self) -> dict[str, Any]:
        for path in self._settlement_candidates("fee_tax_report.json"):
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

    def load_corporate_action_ledger(self) -> pd.DataFrame:
        for path in self._account_candidates("corporate_action_ledger.jsonl"):
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

    def load_risk_control_report(self) -> dict[str, Any]:
        for path in self._risk_control_candidates("risk_control_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_risk_control_breaches(self) -> pd.DataFrame:
        for path in self._risk_control_candidates("risk_control_breaches.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_risk_limit_usage(self) -> pd.DataFrame:
        for path in self._risk_control_candidates("risk_limit_usage.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_kill_switch_state(self) -> dict[str, Any]:
        for path in self._risk_control_candidates("kill_switch_state.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_risk_override_records(self) -> pd.DataFrame:
        for path in self._risk_control_candidates("risk_override_records.jsonl"):
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

    def load_data_source_smoke_report(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("data_source_smoke_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_provider_probe(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("provider_probe.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_field_coverage_report(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("field_coverage.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_data_source_audit_summary(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("audit_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_incremental_recovery_report(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("incremental_recovery_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_baseline_compare_summary(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("baseline_compare_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_dataset_contracts(self) -> dict[str, Any]:
        for path in self._data_source_smoke_candidates("dataset_contracts.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_plan(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_plan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_run_report(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_run_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_coverage_report(self) -> dict[str, Any]:
        for path in self._backfill_candidates("backfill_coverage_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backfill_job_results(self) -> pd.DataFrame:
        for path in self._backfill_candidates("backfill_job_results.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_data_lake_report(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("data_lake_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_dataset_version_manifest(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("dataset_version_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_research_data_freeze(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("research_data_freeze.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_freeze_validation_report(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("freeze_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_data_lineage_graph(self) -> dict[str, Any]:
        for path in self._data_lake_candidates("data_lineage_graph.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_artifact_validation_report(self) -> dict[str, Any]:
        for path in self._schema_artifact_candidates("artifact_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_artifact_schema_manifest(self) -> dict[str, Any]:
        for path in self._schema_artifact_candidates("artifact_schema_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_release_gate_report(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("release_gate_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_release_manifest(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("release_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_dependency_inventory(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("dependency_inventory.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_module_inventory(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("module_inventory.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_cli_inventory(self) -> dict[str, Any]:
        for path in self._release_artifact_candidates("cli_inventory.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_ci_report(self) -> dict[str, Any]:
        for path in self._ci_artifact_candidates("ci_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_formula_corpus_stats(self) -> dict[str, Any]:
        for path in self._formula_corpus_candidates("formula_corpus_stats.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_formula_corpus(self) -> pd.DataFrame:
        for path in self._formula_corpus_candidates("formula_corpus.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_formula_batch_eval_result(self) -> dict[str, Any]:
        for path in self._formula_batch_eval_candidates("formula_batch_eval_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_formula_eval_results(self) -> pd.DataFrame:
        for path in self._formula_batch_eval_candidates("formula_eval_results.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_alphagpt_pretrain_result(self) -> dict[str, Any]:
        for path in self._pretrain_artifact_candidates("alphagpt_pretrain_result.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_alphagpt_pretrain_history(self) -> pd.DataFrame:
        for path in self._pretrain_artifact_candidates("alphagpt_pretrain_history.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_registry_report(self) -> dict[str, Any]:
        for path in self._model_registry_candidates("model_registry_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_model_registry_manifest(self) -> dict[str, Any]:
        for path in self._model_registry_candidates("model_registry_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_model_versions(self) -> pd.DataFrame:
        for path in self._model_registry_candidates("model_versions.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_deployments(self) -> pd.DataFrame:
        for path in self._model_registry_candidates("model_deployments.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_lifecycle_events(self) -> pd.DataFrame:
        for path in self._model_registry_candidates("lifecycle_events.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_lineage_graph(self) -> dict[str, Any]:
        for path in self._model_registry_candidates("model_lineage_graph.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_factor_lifecycle_report(self) -> dict[str, Any]:
        for path in self._model_lifecycle_candidates("factor_lifecycle_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_factor_health_checks(self) -> pd.DataFrame:
        for path in self._model_lifecycle_candidates("factor_health_checks.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_lifecycle_decisions(self) -> pd.DataFrame:
        for path in self._model_lifecycle_candidates("lifecycle_decisions.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_model_review_package(self) -> dict[str, Any]:
        for path in self._model_lifecycle_candidates("model_review_package.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_pit_validation_report(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("pit_validation_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_pit_dataset_manifest(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("pit_dataset_manifest.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_pit_dataset_contracts(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("pit_dataset_contracts.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_security_lifecycle(self) -> pd.DataFrame:
        for path in self._pit_artifact_candidates("security_lifecycle.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_active_security_mask(self) -> pd.DataFrame:
        for path in self._pit_artifact_candidates("active_security_mask.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_survivorship_bias_report(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("survivorship_bias_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_universe_pit_summary(self) -> dict[str, Any]:
        for path in self._pit_artifact_candidates("universe_pit_summary.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_leakage_audit_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("leakage_audit_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_leakage_issues(self) -> pd.DataFrame:
        for path in self._leakage_artifact_candidates("leakage_issues.jsonl"):
            frame = self._read_jsonl(path)
            if not frame.empty:
                return frame
        return pd.DataFrame()

    def load_formula_leakage_scan(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("formula_leakage_scan.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_truncation_consistency_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("truncation_consistency_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_factor_value_leakage_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("factor_value_leakage_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def load_backtest_leakage_report(self) -> dict[str, Any]:
        for path in self._leakage_artifact_candidates("backtest_leakage_report.json"):
            payload = self._read_json(path)
            if payload:
                return payload
        return {}

    def _neural_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.report_dir / filename,
            root / "neural" / filename,
            root / "search" / filename,
            root / "search" / "neural" / filename,
            root / "suite" / "search" / filename,
            root / "suite" / "search" / "neural" / filename,
            root / "suite" / "alphagpt_pretrain" / filename,
            self.config.pretrain_dir / filename,
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
            root / "suite" / "alphagpt_pretrain" / "checkpoints",
            self.config.pretrain_dir / "checkpoints",
        ]

    def _formula_corpus_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.formula_corpus_dir / filename,
            root / "formula_corpus" / filename,
            root / "suite" / "formula_corpus" / filename,
        ]

    def _formula_batch_eval_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.formula_batch_eval_dir / filename,
            root / "formula_batch_eval" / filename,
            root / "suite" / "formula_batch_eval" / filename,
        ]

    def _pretrain_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.pretrain_dir / filename,
            root / "alphagpt_pretrain" / filename,
            root / "suite" / "alphagpt_pretrain" / filename,
        ]

    def _model_registry_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.model_registry_dir / filename,
            root / "model_registry" / filename,
            root / "suite" / "model_registry" / filename,
            root / "production" / filename,
            root / "production_execute" / filename,
        ]

    def _model_lifecycle_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.model_lifecycle_dir / filename,
            root / "model_lifecycle" / filename,
            root / "suite" / "model_lifecycle" / filename,
        ]

    def _pit_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.pit_dir / filename,
            root / "pit" / filename,
            root / "pit_mask" / filename,
            root / "suite_pit" / filename,
            root / "suite" / "point_in_time" / filename,
            root / "data" / "universe" / filename,
        ]

    def _leakage_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.leakage_dir / filename,
            root / "leakage" / filename,
            root / "suite_leakage" / filename,
            root / "backtest_leakage" / filename,
            root / "suite" / "leakage_audit" / filename,
            self.config.backtest_dir / "leakage_audit" / filename,
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

    def _settlement_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.paper_account_dir / filename,
            self.config.paper_account_dir / "settlement" / filename,
            self.config.backtest_dir / "settlement" / filename,
            self.config.backtest_dir / filename,
            self.config.orders_dir / "settlement" / filename,
            self.config.orders_dir / filename,
            root / "settlement" / filename,
            root / "settlement" / "backtest" / filename,
            root / "settlement" / "orders" / filename,
            root / "production" / "settlement" / filename,
            root / "production_execute" / "settlement" / filename,
            root / "production_execute_replay" / "settlement" / filename,
            root / "account" / filename,
        ]

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

    def _data_source_smoke_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.data_source_smoke_dir / filename,
            root / "data_source_smoke" / filename,
            root / "sample_smoke" / filename,
            root / "fake_tushare_smoke" / filename,
            root / "real_tushare_smoke" / filename,
        ]

    def _backfill_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.backfill_dir / filename,
            root / "backfill" / filename,
            root / "data_backfill" / filename,
            root / "sample_backfill" / filename,
            root / "fake_tushare_backfill" / filename,
            self.config.data_dir / filename,
        ]

    def _data_lake_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.data_lake_dir / filename,
            root / "data_lake" / filename,
            root / "suite" / "data_version" / filename,
            root / "suite" / "freeze_validation" / filename,
            root / "sample_smoke" / "data_lake" / filename,
            root / "sample_smoke" / "research_freeze" / filename,
            root / "research_freeze" / filename,
        ]

    def _schema_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.schema_validation_dir / filename,
            root / "schema_validation" / filename,
            root / "schema" / filename,
            self.config.ci_dir / "schema" / filename,
            self.config.release_dir / filename,
        ]

    def _release_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.release_dir / filename,
            root / "release" / filename,
            self.config.ci_dir / "release" / filename,
        ]

    def _ci_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.ci_dir / filename,
            root / "local_ci" / filename,
            root / "ci" / filename,
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

    def _risk_control_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.orders_dir / "risk_controls" / filename,
            self.config.orders_dir / filename,
            self.config.backtest_dir / "risk_controls" / filename,
            root / "risk_controls" / filename,
            root / "production" / "risk_controls" / filename,
            root / "production_execute" / "risk_controls" / filename,
            root / "production_execute_replay" / "risk_controls" / filename,
            root / "backtest" / "risk_controls" / filename,
            root / "backtest_direct" / "risk_controls" / filename,
        ]

    def _execution_plan_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.orders_dir / "plan" / filename,
            self.config.orders_dir / filename,
            self.config.backtest_dir / "execution_plan" / filename,
            root / "execution_plan" / filename,
            root / "orders_capacity" / "plan" / filename,
            root / "daily_orders" / "plan" / filename,
            root / "daily_orders_execute" / "plan" / filename,
        ]

    def _corporate_action_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        candidates = [
            self.config.report_dir / filename,
            self.config.report_dir / "corporate_actions" / filename,
            self.config.backtest_dir / "corporate_actions" / filename,
            self.config.orders_dir / "corporate_actions" / filename,
            root / "corporate_actions" / filename,
            root / "suite" / "corporate_actions" / filename,
            root / "production" / "corporate_actions" / filename,
            root / "production_execute" / "corporate_actions" / filename,
        ]
        if filename == "corporate_action_events.jsonl":
            candidates.append(self.config.data_dir / "corporate_actions" / "records.jsonl")
        else:
            candidates.append(self.config.data_dir / filename)
        return candidates

    def _broker_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            self.config.orders_dir / "broker" / filename,
            self.config.orders_dir / filename,
            self.config.orders_dir / "outbox" / filename,
            root / "production_execute" / "broker" / filename,
            root / "production_execute_replay" / "broker" / filename,
            root / "broker" / filename,
            root / "broker_file" / "outbox" / filename,
        ]

    def _statement_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            root / "statement_import_zero" / filename,
            root / "statement_import_mismatch" / filename,
            root / "statement_import" / filename,
            root / "external_statement_zero" / filename,
            root / "external_statement_mismatch" / filename,
            root / "production_reconcile_only" / "eod_reconciliation" / "statement_import" / filename,
            root / "production_execute" / "eod_reconciliation" / "statement_import" / filename,
        ]

    def _reconciliation_artifact_candidates(self, filename: str) -> list[Path]:
        root = self.config.report_dir.parent
        return [
            root / "eod_reconciliation_zero" / filename,
            root / "eod_reconciliation_mismatch" / filename,
            root / "eod_adjustment_approval" / filename,
            root / "eod_adjustment_apply" / filename,
            root / "operations_eod_reconciliation" / filename,
            root / "production_reconcile_only" / filename,
            root / "production_reconcile_only" / "eod_reconciliation" / filename,
            root / "production_execute" / "eod_reconciliation" / filename,
            root / "monitoring" / filename,
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
