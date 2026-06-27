"""Configuration for the A-share data pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .validators import is_valid_yyyymmdd


@dataclass(frozen=True)
class AShareDataConfig:
    provider: str = "tushare"
    tushare_token: str | None = None
    database_url: str | None = None
    data_dir: Path = Path("data/ashare")
    start_date: str = "20150101"
    end_date: str | None = None
    adjust: str = "qfq"
    universe: str = "all_a"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AShareDataConfig":
        """Build config from environment variables only when explicitly called."""
        env = os.environ if environ is None else environ

        database_url = env.get("ASHARE_DATABASE_URL") or env.get("DATABASE_URL") or None
        end_date = env.get("ASHARE_END_DATE")
        if end_date == "":
            end_date = None

        return cls(
            provider=env.get("ASHARE_PROVIDER", "tushare"),
            tushare_token=env.get("TUSHARE_TOKEN") or None,
            database_url=database_url,
            data_dir=Path(env.get("ASHARE_DATA_DIR") or "data/ashare"),
            start_date=env.get("ASHARE_START_DATE", "20150101"),
            end_date=end_date,
            adjust=(env.get("ASHARE_ADJUST", "qfq")).lower(),
            universe=env.get("ASHARE_UNIVERSE", "all_a"),
        )

    def __post_init__(self) -> None:
        data_dir = Path(self.data_dir)
        object.__setattr__(self, "data_dir", data_dir)

        if self.adjust not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust must be one of: none, qfq, hfq")

        if not is_valid_yyyymmdd(self.start_date):
            raise ValueError("start_date must be a real date in YYYYMMDD format")

        if self.end_date is not None and not is_valid_yyyymmdd(self.end_date):
            raise ValueError("end_date must be a real date in YYYYMMDD format")
