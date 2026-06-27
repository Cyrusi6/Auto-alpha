"""JSONL data loader for A-share factor research."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import torch

from .config import ModelConfig
from .factors import AShareFeatureEngineer


class AShareDataLoader:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        device: torch.device | str | None = None,
        universe_file: str | Path | None = None,
        universe_name: str | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else Path(ModelConfig.DATA_DIR)
        self.device = torch.device(device) if device is not None else ModelConfig.DEVICE
        self.universe_file = Path(universe_file) if universe_file is not None else None
        self.universe_name = universe_name
        self.ts_codes: list[str] = []
        self.trade_dates: list[str] = []
        self.security_metadata: dict[str, dict[str, object]] = {}
        self.industry_codes: torch.Tensor | None = None
        self.raw_data_cache: dict[str, torch.Tensor] = {}
        self.feat_tensor: torch.Tensor | None = None
        self.target_ret: torch.Tensor | None = None

    def load_data(self) -> "AShareDataLoader":
        securities = self._read_jsonl("securities")
        calendar = self._read_jsonl("trade_calendar")
        bars = self._read_jsonl("daily_bars")
        daily_basic = self._read_jsonl("daily_basic")
        financial_features = self._read_jsonl("financial_features")

        universe_codes = self._load_universe_codes()
        selected_securities = [
            record for record in securities if universe_codes is None or str(record.get("ts_code")) in universe_codes
        ]
        self.ts_codes = sorted(str(record["ts_code"]) for record in selected_securities)
        self.trade_dates = sorted(record["trade_date"] for record in calendar if record.get("is_open", False))
        if not self.ts_codes or not self.trade_dates:
            raise ValueError("A-share data directory does not contain aligned securities and trade dates")
        self.security_metadata = {
            str(record["ts_code"]): dict(record)
            for record in selected_securities
        }

        bar_df = pd.DataFrame(bars)
        basic_df = pd.DataFrame(daily_basic)
        financial_df = pd.DataFrame(financial_features)

        raw = {
            "open": self._pivot_market(bar_df, "open"),
            "high": self._pivot_market(bar_df, "high"),
            "low": self._pivot_market(bar_df, "low"),
            "close": self._pivot_market(bar_df, "close"),
            "pre_close": self._pivot_market(bar_df, "pre_close"),
            "volume": self._pivot_market(bar_df, "volume"),
            "amount": self._pivot_market(bar_df, "amount"),
            "turnover_rate": self._pivot_daily_basic(basic_df, "turnover_rate"),
            "volume_ratio": self._pivot_daily_basic(basic_df, "volume_ratio"),
            "pe_ttm": self._pivot_daily_basic(basic_df, "pe_ttm"),
            "pb": self._pivot_daily_basic(basic_df, "pb"),
            "total_mv": self._pivot_daily_basic(basic_df, "total_mv"),
            "roe": self._align_financial(financial_df, "roe"),
            "revenue_yoy": self._align_financial(financial_df, "revenue_yoy"),
        }

        self.raw_data_cache = {
            key: torch.tensor(value, dtype=torch.float32, device=self.device)
            for key, value in raw.items()
        }
        self.raw_data_cache["log_mkt_cap"] = torch.log1p(torch.clamp(self.raw_data_cache["total_mv"], min=0.0))
        self.industry_codes = self._build_industry_codes()
        self.raw_data_cache["industry_codes"] = self.industry_codes
        self.raw_data_cache["industry_code_matrix"] = self.industry_codes.unsqueeze(1).expand(-1, len(self.trade_dates))
        self.feat_tensor = AShareFeatureEngineer.compute_features(self.raw_data_cache)
        self.target_ret = self._compute_target_ret(self.raw_data_cache["close"])
        return self

    def _read_jsonl(self, dataset: str) -> list[dict[str, object]]:
        path = self.data_dir / dataset / "records.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"missing A-share dataset file: {path}")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _load_universe_codes(self) -> set[str] | None:
        path: Path | None = self.universe_file
        if path is None and self.universe_name is not None:
            path = self.data_dir / "universe" / f"{self.universe_name}.jsonl"
        if path is None:
            return None
        if not path.exists():
            raise FileNotFoundError(f"missing A-share universe file: {path}")
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return {str(record["ts_code"]) for record in records if record.get("ts_code")}

    def _pivot_market(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        pivot = df.pivot_table(index="trade_date", columns="ts_code", values=column, aggfunc="last")
        pivot = pivot.reindex(index=self.trade_dates, columns=self.ts_codes)
        pivot = pivot.ffill().fillna(0.0)
        return pivot.to_numpy(dtype="float32").T.tolist()

    def _pivot_daily_basic(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        if column not in df.columns:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        pivot = df.pivot_table(index="trade_date", columns="ts_code", values=column, aggfunc="last")
        pivot = pivot.reindex(index=self.trade_dates, columns=self.ts_codes)
        pivot = pivot.ffill().fillna(0.0)
        return pivot.to_numpy(dtype="float32").T.tolist()

    def _align_financial(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        if df.empty or column not in df.columns:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]

        aligned: list[list[float]] = []
        for ts_code in self.ts_codes:
            stock_df = df[df["ts_code"] == ts_code].sort_values(["announce_date", "report_period"])
            values: list[float] = []
            for trade_date in self.trade_dates:
                available = stock_df[stock_df["announce_date"] <= trade_date]
                if available.empty:
                    values.append(0.0)
                else:
                    value = available.iloc[-1].get(column, 0.0)
                    values.append(0.0 if pd.isna(value) else float(value))
            aligned.append(values)
        return aligned

    def _build_industry_codes(self) -> torch.Tensor:
        industries = [
            str(self.security_metadata.get(ts_code, {}).get("industry") or "UNKNOWN")
            for ts_code in self.ts_codes
        ]
        mapping = {industry: idx for idx, industry in enumerate(sorted(set(industries)))}
        values = [mapping[industry] for industry in industries]
        return torch.tensor(values, dtype=torch.long, device=self.device)

    @staticmethod
    def _compute_target_ret(close: torch.Tensor) -> torch.Tensor:
        target = torch.zeros_like(close)
        if close.shape[1] > 1:
            current = torch.clamp(close[:, :-1], min=1e-6)
            nxt = torch.clamp(close[:, 1:], min=1e-6)
            target[:, :-1] = torch.log(nxt / current)
        return torch.nan_to_num(target, nan=0.0, posinf=0.0, neginf=0.0).to(dtype=torch.float32)
