"""A-share paper execution configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AShareExecutionConfig:
    output_dir: Path
    default_price_field: str = "close"
    paper_account_id: str = "paper_ashare"
    allow_live_trading: bool = False

    @classmethod
    def from_env(cls) -> "AShareExecutionConfig":
        return cls(output_dir=Path(os.getenv("ASHARE_EXECUTION_OUTPUT_DIR", "artifacts/execution")))
