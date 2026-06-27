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

    def load_dataset(self, name: str, limit: int | None = 200) -> pd.DataFrame:
        frame = self._read_jsonl(self.config.data_dir / name / "records.jsonl")
        if limit is not None and len(frame) > limit:
            return frame.head(limit)
        return frame

    def load_factors(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.factor_store_dir / "factors.jsonl")

    def load_experiments(self) -> pd.DataFrame:
        return self._read_jsonl(self.config.factor_store_dir / "experiments.jsonl")

    def load_latest_factor_metrics(self) -> dict[str, Any]:
        factors = self.load_factors()
        if factors.empty:
            return {}
        latest = factors.iloc[-1].to_dict()
        metrics = latest.get("metrics")
        return metrics if isinstance(metrics, dict) else {}

    def load_factor_report_json(self) -> dict[str, Any]:
        return self._read_json(self.config.report_dir / "factor_report.json")

    def load_factor_report_markdown(self) -> str:
        path = self.config.report_dir / "factor_report.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

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
