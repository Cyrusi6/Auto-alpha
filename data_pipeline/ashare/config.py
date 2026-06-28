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
    tushare_api_url: str = "http://api.tushare.pro"
    tushare_timeout_seconds: int = 30
    tushare_retry_count: int = 3
    database_url: str | None = None
    data_dir: Path = Path("data/ashare")
    start_date: str = "20150101"
    end_date: str | None = None
    adjust: str = "qfq"
    universe: str = "all_a"
    index_codes: tuple[str, ...] = ("000300.SH",)
    security_list_statuses: tuple[str, ...] = ("L",)
    include_corporate_actions: bool = True
    corporate_action_query_date_field: str = "ex_date"
    corporate_action_apply_statuses: tuple[str, ...] = ("实施",)
    corporate_action_cash_field: str = "cash_div"

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
            tushare_api_url=env.get("TUSHARE_API_URL") or "http://api.tushare.pro",
            tushare_timeout_seconds=env.get("TUSHARE_TIMEOUT_SECONDS") or 30,
            tushare_retry_count=env.get("TUSHARE_RETRY_COUNT") or 3,
            database_url=database_url,
            data_dir=Path(env.get("ASHARE_DATA_DIR") or "data/ashare"),
            start_date=env.get("ASHARE_START_DATE", "20150101"),
            end_date=end_date,
            adjust=(env.get("ASHARE_ADJUST", "qfq")).lower(),
            universe=env.get("ASHARE_UNIVERSE", "all_a"),
            index_codes=_parse_csv_tuple(env.get("ASHARE_INDEX_CODES")) or ("000300.SH",),
            security_list_statuses=_parse_csv_tuple(env.get("ASHARE_SECURITY_LIST_STATUSES")) or ("L",),
            include_corporate_actions=_parse_bool(env.get("ASHARE_INCLUDE_CORPORATE_ACTIONS"), default=True),
            corporate_action_query_date_field=env.get("ASHARE_CORPORATE_ACTION_QUERY_DATE_FIELD") or "ex_date",
            corporate_action_apply_statuses=_parse_csv_tuple(env.get("ASHARE_CORPORATE_ACTION_APPLY_STATUSES"))
            or ("实施",),
            corporate_action_cash_field=env.get("ASHARE_CORPORATE_ACTION_CASH_FIELD") or "cash_div",
        )

    def __post_init__(self) -> None:
        data_dir = Path(self.data_dir)
        object.__setattr__(self, "data_dir", data_dir)
        object.__setattr__(
            self,
            "tushare_timeout_seconds",
            self._coerce_positive_int(self.tushare_timeout_seconds, "tushare_timeout_seconds"),
        )
        object.__setattr__(
            self,
            "tushare_retry_count",
            self._coerce_positive_int(self.tushare_retry_count, "tushare_retry_count"),
        )

        if self.adjust not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust must be one of: none, qfq, hfq")

        if not is_valid_yyyymmdd(self.start_date):
            raise ValueError("start_date must be a real date in YYYYMMDD format")

        if self.end_date is not None and not is_valid_yyyymmdd(self.end_date):
            raise ValueError("end_date must be a real date in YYYYMMDD format")

        object.__setattr__(self, "index_codes", tuple(str(code).strip() for code in self.index_codes if str(code).strip()))
        if not self.index_codes:
            raise ValueError("index_codes must include at least one index code")
        statuses = tuple(str(status).strip().upper() for status in self.security_list_statuses if str(status).strip())
        if not statuses:
            raise ValueError("security_list_statuses must include at least one status")
        unsupported = sorted(set(statuses) - {"L", "D", "P"})
        if unsupported:
            raise ValueError(f"security_list_statuses only supports L,D,P: {', '.join(unsupported)}")
        object.__setattr__(self, "security_list_statuses", statuses)
        if self.corporate_action_query_date_field not in {"ex_date", "ann_date", "record_date", "imp_ann_date"}:
            raise ValueError("corporate_action_query_date_field must be one of: ex_date, ann_date, record_date, imp_ann_date")
        if self.corporate_action_cash_field not in {"cash_div", "cash_div_tax"}:
            raise ValueError("corporate_action_cash_field must be one of: cash_div, cash_div_tax")
        apply_statuses = tuple(str(status).strip() for status in self.corporate_action_apply_statuses if str(status).strip())
        object.__setattr__(self, "corporate_action_apply_statuses", apply_statuses or ("实施",))

    @staticmethod
    def _coerce_positive_int(value: int | str, field_name: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a positive integer") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name} must be a positive integer")
        return parsed


def _parse_csv_tuple(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    parsed = tuple(item.strip() for item in value.split(",") if item.strip())
    return parsed or None


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
