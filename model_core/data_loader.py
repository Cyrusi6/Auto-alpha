"""JSONL data loader for A-share factor research."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .config import ModelConfig
from .factors import AShareFeatureEngineer
from research_firewall import DateFirewall, ResearchDataView


class AShareDataLoader:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        device: torch.device | str | None = None,
        universe_file: str | Path | None = None,
        universe_name: str | None = None,
        matrix_cache_dir: str | Path | None = None,
        use_matrix_cache: bool = False,
        point_in_time: bool = False,
        as_of_date: str | None = None,
        feature_cutoff_mode: str = "same_day_after_close",
        min_listing_days: int = 0,
        exclude_st: bool = False,
        active_security_mask_path: str | Path | None = None,
        allow_inactive_securities: bool = True,
        leakage_guard: bool = False,
        corporate_action_aware: bool = False,
        corporate_action_dir: str | Path | None = None,
        target_return_mode: str = "adjusted_close",
        corporate_action_cash_field: str = "cash_div",
        corporate_action_application_mode: str = "ex_date",
        feature_set_name: str = "ashare_features_v1",
        feature_set_manifest_path: str | Path | None = None,
        research_end_date: str | None = None,
        holdout_start_date: str | None = None,
        label_horizon: int = 1,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else Path(ModelConfig.DATA_DIR)
        self.device = torch.device(device) if device is not None else ModelConfig.DEVICE
        self.universe_file = Path(universe_file) if universe_file is not None else None
        self.universe_name = universe_name
        self.matrix_cache_dir = Path(matrix_cache_dir) if matrix_cache_dir is not None else self.data_dir / "matrix_cache"
        self.use_matrix_cache = bool(use_matrix_cache)
        self.point_in_time = bool(point_in_time)
        self.as_of_date = as_of_date
        self.feature_cutoff_mode = feature_cutoff_mode
        self.min_listing_days = int(min_listing_days)
        self.exclude_st = bool(exclude_st)
        self.active_security_mask_path = Path(active_security_mask_path) if active_security_mask_path is not None else None
        self.allow_inactive_securities = bool(allow_inactive_securities)
        self.leakage_guard = bool(leakage_guard)
        self.corporate_action_aware = bool(corporate_action_aware)
        self.corporate_action_dir = Path(corporate_action_dir) if corporate_action_dir is not None else None
        self.target_return_mode = target_return_mode
        self.corporate_action_cash_field = corporate_action_cash_field
        self.corporate_action_application_mode = corporate_action_application_mode
        self.feature_set_name = feature_set_name
        self.feature_set_manifest_path = Path(feature_set_manifest_path) if feature_set_manifest_path is not None else None
        self.label_horizon = int(label_horizon)
        self.date_firewall = DateFirewall(research_end_date, holdout_start_date, label_horizon) if research_end_date else None
        self.firewall_source_trade_dates: list[str] = []
        self.ts_codes: list[str] = []
        self.trade_dates: list[str] = []
        self.security_metadata: dict[str, dict[str, object]] = {}
        self.industry_codes: torch.Tensor | None = None
        self.raw_data_cache: dict[str, torch.Tensor] = {}
        self.raw_validity_cache: dict[str, torch.Tensor] = {}
        self.feature_validity: torch.Tensor | None = None
        self.target_available: torch.Tensor | None = None
        self.feature_v3_extended_summary: dict[str, object] | None = None
        self.raw_corporate_actions: list[dict[str, object]] = []
        self.corporate_action_events: list[dict[str, object]] = []
        self.feat_tensor: torch.Tensor | None = None
        self.target_ret: torch.Tensor | None = None

    def load_data(self) -> "AShareDataLoader":
        if self.use_matrix_cache:
            return self._load_from_matrix_cache()

        securities = self._read_jsonl("securities")
        calendar = self._read_jsonl("trade_calendar")

        universe_codes = self._load_universe_codes()
        selected_securities = [
            record for record in securities if universe_codes is None or str(record.get("ts_code")) in universe_codes
        ]
        self.ts_codes = sorted(str(record["ts_code"]) for record in selected_securities)
        self.trade_dates = sorted(record["trade_date"] for record in calendar if record.get("is_open", False))
        if self.date_firewall is not None:
            self.trade_dates = [date for date in self.trade_dates if date <= self.date_firewall.research_end_date]
        if not self.ts_codes or not self.trade_dates:
            raise ValueError("A-share data directory does not contain aligned securities and trade dates")
        self.security_metadata = {
            str(record["ts_code"]): dict(record)
            for record in selected_securities
        }
        selected_code_set = set(self.ts_codes)

        bars = self._read_jsonl("daily_bars", ts_codes=selected_code_set)
        daily_basic = self._read_jsonl("daily_basic", ts_codes=selected_code_set)
        financial_features = self._read_jsonl("financial_features", ts_codes=selected_code_set)
        daily_limits = self._read_optional_jsonl("daily_limits", ts_codes=selected_code_set)
        adjustment_factors = self._read_optional_jsonl("adjustment_factors", ts_codes=selected_code_set)
        index_members = self._read_optional_jsonl("index_members", ts_codes=selected_code_set)
        corporate_actions = (
            self._read_optional_jsonl("corporate_actions", ts_codes=selected_code_set)
            if self.corporate_action_aware
            else []
        )
        self.raw_corporate_actions = list(corporate_actions)

        bar_df = self._filter_frame_by_selected_codes(pd.DataFrame(bars), selected_code_set)
        basic_df = self._filter_frame_by_selected_codes(pd.DataFrame(daily_basic), selected_code_set)
        financial_df = self._filter_frame_by_selected_codes(pd.DataFrame(financial_features), selected_code_set)
        limit_df = self._filter_frame_by_selected_codes(pd.DataFrame(daily_limits), selected_code_set)
        adj_df = self._filter_frame_by_selected_codes(pd.DataFrame(adjustment_factors), selected_code_set)
        index_df = self._filter_frame_by_selected_codes(pd.DataFrame(index_members), selected_code_set)

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
            "ps_ttm": self._pivot_daily_basic(basic_df, "ps_ttm"),
            "total_mv": self._pivot_daily_basic(basic_df, "total_mv"),
            "roe": self._align_financial(financial_df, "roe"),
            "revenue_yoy": self._align_financial(financial_df, "revenue_yoy"),
            "adj_factor": self._pivot_adjustment_factor(adj_df),
            "up_limit": self._pivot_optional_market(limit_df, "up_limit"),
            "down_limit": self._pivot_optional_market(limit_df, "down_limit"),
            "is_suspended": self._derive_suspension_matrix(bar_df),
            "index_member_matrix": self._align_index_members(index_df),
        }

        self.raw_data_cache = {
            key: self._to_float_tensor(value)
            for key, value in raw.items()
        }
        self.raw_data_cache["adjusted_close"] = self.raw_data_cache["close"] * self.raw_data_cache["adj_factor"]
        self.raw_data_cache["adjusted_open"] = self.raw_data_cache["open"] * self.raw_data_cache["adj_factor"]
        self.raw_data_cache["limit_up_flag"] = self._limit_flag(
            self.raw_data_cache["close"],
            self.raw_data_cache["up_limit"],
            direction="up",
        )
        self.raw_data_cache["limit_down_flag"] = self._limit_flag(
            self.raw_data_cache["close"],
            self.raw_data_cache["down_limit"],
            direction="down",
        )
        self.raw_data_cache["log_mkt_cap"] = torch.log1p(torch.clamp(self.raw_data_cache["total_mv"], min=0.0))
        if self.corporate_action_aware:
            self._attach_corporate_action_matrices(corporate_actions)
        self.industry_codes = self._build_industry_codes()
        self.raw_data_cache["industry_codes"] = self.industry_codes
        self.raw_data_cache["industry_code_matrix"] = self.industry_codes.unsqueeze(1).expand(-1, len(self.trade_dates))
        if self.point_in_time:
            self._attach_point_in_time_masks(securities)
        self.feat_tensor = self._compute_feature_tensor()
        self.target_ret = self._compute_target_ret(self._target_price_matrix(), self.label_horizon)
        self._apply_research_view()
        return self

    def _load_from_matrix_cache(self) -> "AShareDataLoader":
        strict_manifest_path = self.matrix_cache_dir / "task_052a_strict_matrix_manifest.json"
        if strict_manifest_path.exists():
            return self._load_from_strict_engineering_matrix(strict_manifest_path)
        if not (self.matrix_cache_dir / "metadata.json").exists():
            raise FileNotFoundError(f"matrix cache metadata not found: {self.matrix_cache_dir / 'metadata.json'}")

        from matrix_store import MatrixStoreReader

        reader = MatrixStoreReader(self.matrix_cache_dir)
        metadata = reader.load_metadata()
        if self.universe_name is None:
            self.universe_name = metadata.get("effective_universe_name") or metadata.get("universe_name")
        self.ts_codes = reader.load_ts_codes()
        self.trade_dates = reader.load_trade_dates()
        if not self.ts_codes or not self.trade_dates:
            raise ValueError(f"matrix cache does not contain aligned securities and trade dates: {self.matrix_cache_dir}")
        raw_metadata = metadata.get("security_metadata")
        if isinstance(raw_metadata, dict):
            self.security_metadata = {str(key): dict(value) for key, value in raw_metadata.items() if isinstance(value, dict)}
        else:
            self.security_metadata = {ts_code: {"ts_code": ts_code} for ts_code in self.ts_codes}

        self.raw_data_cache = reader.to_raw_data_cache(device=self.device)
        self._apply_firewall_source_boundary()
        if "adj_factor" not in self.raw_data_cache:
            self.raw_data_cache["adj_factor"] = torch.ones_like(self.raw_data_cache["close"])
        if "adjusted_close" not in self.raw_data_cache:
            self.raw_data_cache["adjusted_close"] = self.raw_data_cache["close"] * self.raw_data_cache["adj_factor"]
        if "adjusted_open" not in self.raw_data_cache and "open" in self.raw_data_cache:
            self.raw_data_cache["adjusted_open"] = self.raw_data_cache["open"] * self.raw_data_cache["adj_factor"]
        if self.corporate_action_aware and "total_return_close" not in self.raw_data_cache:
            self._attach_corporate_action_matrices([])
        if "limit_up_flag" not in self.raw_data_cache and {"close", "up_limit"} <= set(self.raw_data_cache):
            self.raw_data_cache["limit_up_flag"] = self._limit_flag(
                self.raw_data_cache["close"],
                self.raw_data_cache["up_limit"],
                direction="up",
            )
        if "limit_down_flag" not in self.raw_data_cache and {"close", "down_limit"} <= set(self.raw_data_cache):
            self.raw_data_cache["limit_down_flag"] = self._limit_flag(
                self.raw_data_cache["close"],
                self.raw_data_cache["down_limit"],
                direction="down",
            )
        if "ps_ttm" not in self.raw_data_cache:
            self.raw_data_cache["ps_ttm"] = torch.zeros_like(self.raw_data_cache["close"])
        if "log_mkt_cap" not in self.raw_data_cache and "total_mv" in self.raw_data_cache:
            self.raw_data_cache["log_mkt_cap"] = torch.log1p(torch.clamp(self.raw_data_cache["total_mv"], min=0.0))
        if "industry_codes" in self.raw_data_cache:
            self.industry_codes = self.raw_data_cache["industry_codes"].to(dtype=torch.long, device=self.device)
        else:
            self.industry_codes = self._build_industry_codes()
            self.raw_data_cache["industry_codes"] = self.industry_codes
        if "industry_code_matrix" not in self.raw_data_cache:
            self.raw_data_cache["industry_code_matrix"] = self.industry_codes.unsqueeze(1).expand(-1, len(self.trade_dates))
        if self.point_in_time and not {"active_mask", "listing_age_days", "pit_available_mask"} <= set(self.raw_data_cache):
            self._attach_point_in_time_masks(None)

        self.feat_tensor = self._compute_feature_tensor()
        self.target_ret = self._compute_target_ret(self._target_price_matrix(), self.label_horizon)
        self._apply_research_view()
        return self

    def _load_from_strict_engineering_matrix(self, manifest_path: Path) -> "AShareDataLoader":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.ts_codes = [str(item) for item in json.loads((self.matrix_cache_dir / "ts_codes.json").read_text(encoding="utf-8"))]
        self.trade_dates = [str(item) for item in json.loads((self.matrix_cache_dir / "trade_dates.json").read_text(encoding="utf-8"))]
        self.security_metadata = {ts_code: {"ts_code": ts_code} for ts_code in self.ts_codes}
        raw: dict[str, torch.Tensor] = {}
        strict_fields = list(manifest.get("raw_fields", [])) + [
            "adj_factor", "turnover_rate", "volume_ratio", "total_mv", "pb", "pe_ttm", "roe", "revenue_yoy"
        ]
        for field in dict.fromkeys(strict_fields):
            path = self.matrix_cache_dir / f"{field}.npy"
            if path.exists():
                raw[str(field)] = torch.tensor(np.asarray(np.load(path, mmap_mode="r"), dtype=np.float32), device=self.device)
            validity_path = next(
                (candidate for candidate in (
                    self.matrix_cache_dir / f"{field}_validity.npy",
                    self.matrix_cache_dir / f"{field}_valid_mask.npy",
                ) if candidate.exists()),
                None,
            )
            if validity_path is not None:
                self.raw_validity_cache[str(field)] = torch.tensor(
                    np.asarray(np.load(validity_path, mmap_mode="r"), dtype=np.bool_), device=self.device
                )
        aliases = {
            "index_member_matrix": "membership.npy",
            "active_mask": "active.npy",
            "pit_available_mask": "signal_eligible_at_close.npy",
            "signal_eligible_at_close": "signal_eligible_at_close.npy",
            "bar_observed_mask": "bar_observed.npy",
            "membership_known": "membership_known.npy",
        }
        for name, filename in aliases.items():
            path = self.matrix_cache_dir / filename
            if path.exists():
                raw[name] = torch.tensor(np.asarray(np.load(path, mmap_mode="r"), dtype=np.float32), device=self.device)
        if "close" not in raw or "adj_factor" not in raw:
            raise ValueError("strict engineering matrix requires explicit close and adj_factor")
        raw["adjusted_close"] = raw["close"] * raw["adj_factor"]
        if "open" in raw:
            raw["adjusted_open"] = raw["open"] * raw["adj_factor"]
        if "total_mv" in raw:
            raw["log_mkt_cap"] = torch.log1p(torch.clamp(raw["total_mv"], min=0.0))
            if "total_mv" in self.raw_validity_cache:
                self.raw_validity_cache["log_mkt_cap"] = self.raw_validity_cache["total_mv"]
        self.raw_data_cache = raw
        original_dates = list(self.trade_dates)
        selected = [index for index, date in enumerate(original_dates) if self.date_firewall is None or date <= self.date_firewall.research_end_date]
        self._apply_firewall_source_boundary()
        target_name = str((manifest.get("target_contract") or {}).get("name") or "next_open_t1_t2_return")
        target_path = self.matrix_cache_dir / f"{target_name}.npy"
        if not target_path.exists():
            raise ValueError(f"strict persisted target missing: {target_path}")
        target = torch.tensor(np.asarray(np.load(target_path, mmap_mode="r"), dtype=np.float32), device=self.device)
        if len(selected) != len(original_dates):
            target = target.index_select(1, torch.tensor(selected, dtype=torch.long, device=self.device))
        self.target_return_mode = target_name
        industry_path = self.matrix_cache_dir / "industry_code_matrix.npy"
        if industry_path.exists():
            industry_matrix = torch.tensor(np.asarray(np.load(industry_path, mmap_mode="r")), dtype=torch.long, device=self.device)
            self.raw_data_cache["industry_code_matrix"] = industry_matrix
            self.industry_codes = industry_matrix[:, -1]
            self.raw_data_cache["industry_codes"] = self.industry_codes
        available_path = self.matrix_cache_dir / "target_available_mask.npy"
        if not available_path.exists():
            raise ValueError("strict persisted target availability missing")
        self.target_available = torch.tensor(
            np.asarray(np.load(available_path, mmap_mode="r"), dtype=np.bool_), device=self.device
        )
        if len(selected) != len(original_dates):
            self.target_available = self.target_available.index_select(
                1, torch.tensor(selected, dtype=torch.long, device=self.device)
            )
        self.raw_data_cache["target_available_mask"] = self.target_available
        if self.feature_set_manifest_path is not None:
            from feature_factory.builder import load_feature_manifest
            from feature_factory.validity import build_feature_values_and_validity

            feature_manifest = load_feature_manifest(self.feature_set_manifest_path)
            self.feat_tensor, self.feature_validity, self.feature_validity_summary = build_feature_values_and_validity(
                self, feature_manifest
            )
        else:
            self.feat_tensor = self._compute_feature_tensor()
        self.target_ret = target
        self._apply_research_view()
        return self

    def _filter_frame_by_selected_codes(self, df: pd.DataFrame, selected_codes: set[str]) -> pd.DataFrame:
        if df.empty or "ts_code" not in df.columns:
            return df
        return df[df["ts_code"].astype(str).isin(selected_codes)].copy()

    def _compute_feature_tensor(self) -> torch.Tensor:
        if self.feature_set_manifest_path is not None:
            from feature_factory.builder import load_feature_manifest

            manifest = load_feature_manifest(self.feature_set_manifest_path)
            self._attach_extended_feature_matrices_if_needed(manifest)
            return AShareFeatureEngineer.compute_features(
                self.raw_data_cache,
                feature_set_manifest=manifest,
            )
        if self.feature_set_name != "ashare_features_v1":
            from feature_factory.catalog import build_feature_set_manifest

            manifest = build_feature_set_manifest(
                self.feature_set_name,
                point_in_time=self.point_in_time,
                corporate_action_aware=self.corporate_action_aware,
                target_return_mode=self.target_return_mode,
            )
            self._attach_extended_feature_matrices_if_needed(manifest)
            return AShareFeatureEngineer.compute_features(
                self.raw_data_cache,
                feature_set_manifest=manifest,
            )
        return AShareFeatureEngineer.compute_features(self.raw_data_cache)

    def _attach_extended_feature_matrices_if_needed(self, manifest) -> None:
        if getattr(manifest, "feature_set_name", "") != "ashare_features_v3":
            return
        from feature_factory.extended_builder import attach_extended_feature_matrices

        self.feature_v3_extended_summary = attach_extended_feature_matrices(self, manifest)

    def _read_jsonl(self, dataset: str, ts_codes: set[str] | None = None) -> list[dict[str, object]]:
        path = self.data_dir / dataset / "records.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"missing A-share dataset file: {path}")
        return self._read_jsonl_path(path, ts_codes=ts_codes)

    def _read_optional_jsonl(self, dataset: str, ts_codes: set[str] | None = None) -> list[dict[str, object]]:
        path = self.data_dir / dataset / "records.jsonl"
        if not path.exists():
            return []
        return self._read_jsonl_path(path, ts_codes=ts_codes)

    def _read_jsonl_path(self, path: Path, ts_codes: set[str] | None = None) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if self.date_firewall is not None and not self._record_inside_research(record):
                    continue
                if ts_codes is not None and "ts_code" in record and str(record.get("ts_code")) not in ts_codes:
                    continue
                records.append(record)
                if self.date_firewall is not None:
                    dates = [str(record[field]) for field in ("trade_date", "ann_date", "f_ann_date", "availability_date") if record.get(field) not in {None, ""}]
                    for date in dates:
                        self.date_firewall.access_audit.append(
                            {
                                "component": f"data_loader:{path.stem}",
                                "purpose": "raw_record",
                                "access_type": "observation_read",
                                "view": "research_source",
                                "date": date,
                                "allowed": True,
                            }
                        )
        return records

    def _record_inside_research(self, record: dict[str, object]) -> bool:
        cutoff = self.date_firewall.research_end_date
        for field in ("trade_date", "ann_date", "f_ann_date", "availability_date"):
            value = record.get(field)
            if value not in {None, ""} and str(value) > cutoff:
                return False
        return True

    def _load_universe_codes(self) -> set[str] | None:
        path: Path | None = self.universe_file
        if path is None and self.universe_name is not None:
            path = self.data_dir / "universe" / f"{self.universe_name}.jsonl"
        if path is None:
            return None
        if not path.exists():
            raise FileNotFoundError(f"missing A-share universe file: {path}")
        with path.open(encoding="utf-8") as handle:
            return {str(record["ts_code"]) for line in handle if line.strip() for record in [json.loads(line)] if record.get("ts_code")}

    def _pivot_market(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        if df.empty or column not in df.columns:
            return [[float("nan") for _ in self.trade_dates] for _ in self.ts_codes]
        pivot = df.pivot_table(index="trade_date", columns="ts_code", values=column, aggfunc="last")
        pivot = pivot.reindex(index=self.trade_dates, columns=self.ts_codes)
        return pivot.to_numpy(dtype="float32").T.tolist()

    def _pivot_optional_market(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        if df.empty or column not in df.columns:
            return [[float("nan") for _ in self.trade_dates] for _ in self.ts_codes]
        pivot = df.pivot_table(index="trade_date", columns="ts_code", values=column, aggfunc="last")
        pivot = pivot.reindex(index=self.trade_dates, columns=self.ts_codes)
        return pivot.to_numpy(dtype="float32").T.tolist()

    def _pivot_adjustment_factor(self, df: pd.DataFrame) -> list[list[float]]:
        if df.empty or "adj_factor" not in df.columns:
            return [[1.0 for _ in self.trade_dates] for _ in self.ts_codes]
        pivot = df.pivot_table(index="trade_date", columns="ts_code", values="adj_factor", aggfunc="last")
        pivot = pivot.reindex(index=self.trade_dates, columns=self.ts_codes)
        pivot = pivot.ffill().fillna(1.0)
        return pivot.to_numpy(dtype="float32").T.tolist()

    def _derive_suspension_matrix(self, df: pd.DataFrame) -> list[list[float]]:
        if df.empty:
            return [[1.0 for _ in self.trade_dates] for _ in self.ts_codes]
        presence = df.assign(_present=1.0).pivot_table(
            index="trade_date",
            columns="ts_code",
            values="_present",
            aggfunc="last",
        )
        presence = presence.reindex(index=self.trade_dates, columns=self.ts_codes).fillna(0.0)
        if "is_suspended" in df.columns:
            suspended = df.pivot_table(index="trade_date", columns="ts_code", values="is_suspended", aggfunc="last")
            suspended = suspended.reindex(index=self.trade_dates, columns=self.ts_codes).fillna(False)
        else:
            suspended = presence * 0.0
        result = ((presence <= 0.0) | suspended.astype(bool)).astype("float32")
        return result.to_numpy(dtype="float32").T.tolist()

    def _pivot_daily_basic(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        if column not in df.columns:
            return [[float("nan") for _ in self.trade_dates] for _ in self.ts_codes]
        pivot = df.pivot_table(index="trade_date", columns="ts_code", values=column, aggfunc="last")
        pivot = pivot.reindex(index=self.trade_dates, columns=self.ts_codes)
        return pivot.to_numpy(dtype="float32").T.tolist()

    def _align_financial(self, df: pd.DataFrame, column: str) -> list[list[float]]:
        if df.empty or column not in df.columns:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        date_col = "announce_date" if "announce_date" in df.columns else ("ann_date" if "ann_date" in df.columns else None)
        if date_col is None or "ts_code" not in df.columns:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        work_cols = ["ts_code", date_col, column]
        sort_cols = ["ts_code", date_col]
        for optional in ("report_period", "end_date", "update_flag"):
            if optional in df.columns:
                work_cols.append(optional)
                sort_cols.append(optional)
        work = df.loc[:, list(dict.fromkeys(work_cols))].copy()
        work["ts_code"] = work["ts_code"].astype(str)
        work[date_col] = work[date_col].astype(str)
        work = work[
            work["ts_code"].isin(self.ts_codes)
            & work[date_col].str.fullmatch(r"\d{8}", na=False)
        ]
        if work.empty:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0.0)
        work = work.sort_values(sort_cols)
        pivot = work.pivot_table(index=date_col, columns="ts_code", values=column, aggfunc="last")
        return self._forward_align_pivot(pivot, fill_value=0.0)

    def _align_index_members(self, df: pd.DataFrame) -> list[list[float]]:
        if df.empty:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        date_col = "trade_date" if "trade_date" in df.columns else ("weight_date" if "weight_date" in df.columns else None)
        if date_col is None or "ts_code" not in df.columns:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        work = df.loc[:, ["ts_code", date_col]].copy()
        work["ts_code"] = work["ts_code"].astype(str)
        work[date_col] = work[date_col].astype(str)
        work = work[
            work["ts_code"].isin(self.ts_codes)
            & work[date_col].str.fullmatch(r"\d{8}", na=False)
        ]
        if work.empty:
            return [[0.0 for _ in self.trade_dates] for _ in self.ts_codes]
        work["_member"] = 1.0
        work = work.drop_duplicates(["ts_code", date_col], keep="last").sort_values(["ts_code", date_col])
        pivot = work.pivot_table(index=date_col, columns="ts_code", values="_member", aggfunc="last")
        return self._forward_align_pivot(pivot, fill_value=0.0)

    def _forward_align_pivot(self, pivot: pd.DataFrame, fill_value: float = 0.0) -> np.ndarray:
        if pivot.empty:
            return np.full((len(self.ts_codes), len(self.trade_dates)), fill_value, dtype="float32")
        pivot.index = pivot.index.astype(str)
        all_dates = sorted(set(self.trade_dates).union(str(item) for item in pivot.index if str(item)))
        aligned = (
            pivot.reindex(index=all_dates, columns=self.ts_codes)
            .ffill()
            .reindex(index=self.trade_dates)
            .fillna(fill_value)
        )
        return aligned.to_numpy(dtype="float32").T

    def _attach_corporate_action_matrices(self, records: list[dict[str, object]]) -> None:
        if self.corporate_action_dir is not None:
            event_path = self.corporate_action_dir / "corporate_action_events.jsonl"
            if event_path.exists():
                records = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if records and "action_id" in records[0]:
            events = records
        else:
            from corporate_actions.normalizer import normalize_corporate_action_records

            events = [event.to_dict() for event in normalize_corporate_action_records(records, cash_field=self.corporate_action_cash_field)]
        if self.point_in_time:
            filtered = []
            for event in events:
                availability_date = event.get("availability_date") or event.get("imp_ann_date") or event.get("ann_date")
                if self.as_of_date and availability_date and str(availability_date) > self.as_of_date:
                    continue
                filtered.append(event)
            events = filtered
        self.corporate_action_events = [dict(event) for event in events]
        shape = self.raw_data_cache["close"].shape
        cash = torch.zeros(shape, dtype=torch.float32, device=self.device)
        cash_tax = torch.zeros_like(cash)
        stock_ratio = torch.zeros_like(cash)
        flag = torch.zeros_like(cash)
        stock_index = {ts_code: idx for idx, ts_code in enumerate(self.ts_codes)}
        date_index = {trade_date: idx for idx, trade_date in enumerate(self.trade_dates)}
        for event in events:
            action_type = str(event.get("action_type") or "")
            if action_type == "proposal_only":
                continue
            ts_code = str(event.get("ts_code") or "")
            event_date = str(event.get("effective_date") or event.get("ex_date") or "")
            si = stock_index.get(ts_code)
            di = date_index.get(event_date)
            if si is None or di is None:
                continue
            cash[si, di] += float(event.get("cash_div_per_share") or event.get("cash_div") or 0.0)
            cash_tax[si, di] += float(event.get("cash_div_tax_per_share") or event.get("cash_div_tax") or 0.0)
            stock_ratio[si, di] += float(event.get("stock_distribution_ratio") or 0.0)
            flag[si, di] = 1.0
        total_return_close = self.raw_data_cache["close"] * (1.0 + stock_ratio) + cash
        self.raw_data_cache["cash_dividend"] = cash
        self.raw_data_cache["cash_dividend_tax"] = cash_tax
        self.raw_data_cache["stock_distribution_ratio"] = stock_ratio
        self.raw_data_cache["corporate_action_flag"] = flag
        self.raw_data_cache["total_return_close"] = total_return_close
        self.raw_data_cache["total_return"] = self._compute_target_ret(total_return_close, self.label_horizon)

    def _build_industry_codes(self) -> torch.Tensor:
        industries = [
            str(self.security_metadata.get(ts_code, {}).get("industry") or "UNKNOWN")
            for ts_code in self.ts_codes
        ]
        mapping = {industry: idx for idx, industry in enumerate(sorted(set(industries)))}
        values = [mapping[industry] for industry in industries]
        return torch.tensor(values, dtype=torch.long, device=self.device)

    def _attach_point_in_time_masks(self, securities: list[dict[str, object]] | None) -> None:
        active_mask, listing_age = self._build_or_load_active_mask(securities)
        self.raw_data_cache["active_mask"] = active_mask
        self.raw_data_cache["listing_age_days"] = listing_age
        base_available = active_mask.clone()
        if "close" in self.raw_data_cache:
            base_available = base_available * (self.raw_data_cache["close"] > 0).to(dtype=torch.float32)
        if self.feature_cutoff_mode in {"next_trade_day_open", "previous_trade_day_close"} and base_available.shape[1] > 0:
            cutoff = torch.zeros_like(base_available)
            cutoff[:, 1:] = base_available[:, :-1]
            base_available = cutoff
        self.raw_data_cache["pit_available_mask"] = base_available
        if not self.allow_inactive_securities:
            for key, value in list(self.raw_data_cache.items()):
                if value.shape == active_mask.shape and key not in {"listing_age_days"}:
                    self.raw_data_cache[key] = value * active_mask

    def _build_or_load_active_mask(self, securities: list[dict[str, object]] | None) -> tuple[torch.Tensor, torch.Tensor]:
        if self.active_security_mask_path is not None and self.active_security_mask_path.exists():
            records = [
                json.loads(line)
                for line in self.active_security_mask_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            if securities is None:
                securities = [dict(self.security_metadata.get(ts_code, {"ts_code": ts_code})) for ts_code in self.ts_codes]
            from point_in_time.security_master import build_active_security_mask, build_security_lifecycle

            lifecycle = build_security_lifecycle(securities)
            records = [
                item.to_dict()
                for item in build_active_security_mask(
                    lifecycle,
                    self.trade_dates,
                    min_listing_days=self.min_listing_days,
                    exclude_st=self.exclude_st,
                )
            ]
        stock_index = {ts_code: idx for idx, ts_code in enumerate(self.ts_codes)}
        date_index = {trade_date: idx for idx, trade_date in enumerate(self.trade_dates)}
        active = torch.zeros((len(self.ts_codes), len(self.trade_dates)), dtype=torch.float32, device=self.device)
        age = torch.zeros_like(active)
        for record in records:
            si = stock_index.get(str(record.get("ts_code")))
            di = date_index.get(str(record.get("trade_date")))
            if si is None or di is None:
                continue
            active[si, di] = 1.0 if record.get("is_active") else 0.0
            age[si, di] = float(record.get("listing_age_days", 0) or 0)
        return active, age

    def _target_price_matrix(self) -> torch.Tensor:
        if self.target_return_mode == "raw_close":
            return self.raw_data_cache["close"]
        if self.target_return_mode == "corporate_action_total_return":
            return self.raw_data_cache.get("total_return_close", self.raw_data_cache["adjusted_close"])
        return self.raw_data_cache["adjusted_close"]

    @staticmethod
    def _compute_target_ret(close: torch.Tensor, horizon: int = 1) -> torch.Tensor:
        target = torch.zeros_like(close)
        horizon = max(1, int(horizon))
        if close.shape[1] > horizon:
            current = torch.clamp(close[:, :-horizon], min=1e-6)
            endpoint = torch.clamp(close[:, horizon:], min=1e-6)
            target[:, :-horizon] = torch.log(endpoint / current)
        return torch.nan_to_num(target, nan=0.0, posinf=0.0, neginf=0.0).to(dtype=torch.float32)

    def _apply_research_view(self) -> None:
        if self.date_firewall is None:
            return
        source_dates = list(self.trade_dates)
        view = ResearchDataView(self.date_firewall, tuple(source_dates))
        self.firewall_source_trade_dates = source_dates
        indices = list(view.eligible_indices)
        self.trade_dates = list(view.eligible_dates)
        index = torch.tensor(indices, dtype=torch.long, device=self.device)
        for name, value in list(self.raw_data_cache.items()):
            if isinstance(value, torch.Tensor) and value.ndim >= 2 and value.shape[-1] == len(source_dates):
                self.raw_data_cache[name] = value.index_select(value.ndim - 1, index)
        for name, value in list(self.raw_validity_cache.items()):
            if isinstance(value, torch.Tensor) and value.ndim >= 2 and value.shape[-1] == len(source_dates):
                self.raw_validity_cache[name] = value.index_select(value.ndim - 1, index)
        if self.feat_tensor is not None:
            self.feat_tensor = self.feat_tensor.index_select(2, index)
        if self.feature_validity is not None:
            self.feature_validity = self.feature_validity.index_select(2, index)
        if self.target_ret is not None:
            self.target_ret = self.target_ret.index_select(1, index)
            if self.target_available is not None and self.target_available.shape[1] == len(source_dates):
                self.target_available = self.target_available.index_select(1, index)
            self.date_firewall.audit_observation_access(
                self.trade_dates,
                component="data_loader",
                purpose="target_and_feature_view",
                view="research",
            )
            for position in indices:
                endpoint = position + self.label_horizon
                if endpoint < len(source_dates):
                    self.date_firewall.assert_target_access(
                        source_dates[position],
                        source_dates[endpoint],
                        component="data_loader",
                        purpose=f"target_t_plus_{self.label_horizon}",
                    )

    def _apply_firewall_source_boundary(self) -> None:
        if self.date_firewall is None:
            return
        source_dates = list(self.trade_dates)
        selected = [index for index, date in enumerate(source_dates) if date <= self.date_firewall.research_end_date]
        bounded_dates = [source_dates[index] for index in selected]
        index = torch.tensor(selected, dtype=torch.long, device=self.device)
        for name, value in list(self.raw_data_cache.items()):
            if isinstance(value, torch.Tensor) and value.ndim >= 2 and value.shape[-1] == len(source_dates):
                self.raw_data_cache[name] = value.index_select(value.ndim - 1, index)
        for name, value in list(self.raw_validity_cache.items()):
            if isinstance(value, torch.Tensor) and value.ndim >= 2 and value.shape[-1] == len(source_dates):
                self.raw_validity_cache[name] = value.index_select(value.ndim - 1, index)
        self.trade_dates = bounded_dates
        self.date_firewall.audit_observation_access(
            bounded_dates,
            component="matrix_data_loader",
            purpose="source_view_before_compute",
            view="research",
        )

    @staticmethod
    def _limit_flag(close: torch.Tensor, limit: torch.Tensor, direction: str) -> torch.Tensor:
        valid = limit > 0
        tolerance = torch.clamp(limit.abs() * 1e-4, min=1e-4)
        if direction == "up":
            flag = valid & (close >= limit - tolerance)
        else:
            flag = valid & (close <= limit + tolerance)
        return flag.to(dtype=torch.float32)

    def _to_float_tensor(self, value: object) -> torch.Tensor:
        if isinstance(value, np.ndarray) and not value.flags.writeable:
            value = np.array(value, copy=True)
        return torch.as_tensor(value, dtype=torch.float32, device=self.device)
