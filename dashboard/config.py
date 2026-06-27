"""Dashboard configuration for local A-share artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DashboardConfig:
    data_dir: Path = Path("data/ashare")
    factor_store_dir: Path = Path("artifacts/factor_store")
    report_dir: Path = Path("artifacts/reports")
    backtest_dir: Path = Path("artifacts/backtest")
    orders_dir: Path = Path("artifacts/orders")
    approval_store_dir: Path = Path("artifacts/approvals")
    paper_account_dir: Path = Path("artifacts/account")
    production_dir: Path = Path("artifacts/production")
    monitoring_dir: Path = Path("artifacts/monitoring")
    matrix_cache_dir: Path = Path("data/ashare/matrix_cache")
    benchmark_dir: Path = Path("artifacts/benchmark")
    cross_source_dir: Path = Path("artifacts/cross_source")

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        return cls(
            data_dir=Path(os.getenv("ASHARE_DASHBOARD_DATA_DIR") or os.getenv("ASHARE_DATA_DIR") or "data/ashare"),
            factor_store_dir=Path(
                os.getenv("ASHARE_DASHBOARD_FACTOR_STORE_DIR")
                or os.getenv("ASHARE_FACTOR_STORE_DIR")
                or "artifacts/factor_store"
            ),
            report_dir=Path(os.getenv("ASHARE_DASHBOARD_REPORT_DIR") or "artifacts/reports"),
            backtest_dir=Path(os.getenv("ASHARE_DASHBOARD_BACKTEST_DIR") or "artifacts/backtest"),
            orders_dir=Path(os.getenv("ASHARE_DASHBOARD_ORDERS_DIR") or os.getenv("ASHARE_ORDER_OUTPUT_DIR") or "artifacts/orders"),
            approval_store_dir=Path(os.getenv("ASHARE_DASHBOARD_APPROVAL_STORE_DIR") or "artifacts/approvals"),
            paper_account_dir=Path(os.getenv("ASHARE_DASHBOARD_PAPER_ACCOUNT_DIR") or "artifacts/account"),
            production_dir=Path(os.getenv("ASHARE_DASHBOARD_PRODUCTION_DIR") or "artifacts/production"),
            monitoring_dir=Path(os.getenv("ASHARE_DASHBOARD_MONITORING_DIR") or "artifacts/monitoring"),
            matrix_cache_dir=Path(os.getenv("ASHARE_DASHBOARD_MATRIX_CACHE_DIR") or "data/ashare/matrix_cache"),
            benchmark_dir=Path(os.getenv("ASHARE_DASHBOARD_BENCHMARK_DIR") or "artifacts/benchmark"),
            cross_source_dir=Path(os.getenv("ASHARE_DASHBOARD_CROSS_SOURCE_DIR") or "artifacts/cross_source"),
        )
