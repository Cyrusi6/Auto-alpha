"""A-share strategy configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AShareStrategyConfig:
    data_dir: Path = Path("data/ashare")
    factor_store_dir: Path = Path("artifacts/factor_store")
    output_dir: Path = Path("artifacts/orders")
    top_n: int = 20
    max_weight: float = 0.10
    rebalance_date: str | None = None

    @classmethod
    def from_env(cls) -> "AShareStrategyConfig":
        rebalance_date = os.getenv("ASHARE_REBALANCE_DATE") or None
        return cls(
            data_dir=Path(os.getenv("ASHARE_DATA_DIR", "data/ashare")),
            factor_store_dir=Path(os.getenv("ASHARE_FACTOR_STORE_DIR", "artifacts/factor_store")),
            output_dir=Path(os.getenv("ASHARE_ORDER_OUTPUT_DIR", "artifacts/orders")),
            top_n=int(os.getenv("ASHARE_TOP_N", "20")),
            max_weight=float(os.getenv("ASHARE_MAX_WEIGHT", "0.10")),
            rebalance_date=rebalance_date,
        )
