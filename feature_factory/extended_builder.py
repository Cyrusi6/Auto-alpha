"""PIT-safe expanded feature matrix builders for ashare_features_v3."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import torch

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .catalog import FEATURE_SET_V3
from .models import FeatureSetManifest


BASE_DATASET_KEYS = {
    "daily_bars": ("close", "open", "high", "low", "amount", "volume"),
    "daily_basic": ("turnover_rate", "volume_ratio", "pe_ttm", "pb", "ps_ttm", "total_mv"),
    "financial_features": ("roe", "revenue_yoy"),
    "daily_limits": ("limit_up_flag", "limit_down_flag"),
    "adjustment_factors": ("adj_factor",),
    "index_members": ("index_member_matrix",),
    "securities": ("industry_codes", "listing_age_days"),
}


def attach_extended_feature_matrices(loader, manifest: FeatureSetManifest) -> dict[str, Any]:
    """Attach v3 feature matrices to ``loader.raw_data_cache`` and return summaries.

    The builder is intentionally conservative: missing expanded datasets become
    zero matrices plus structured warnings instead of hard failures. Financial
    and event-like datasets use the configured availability date and never look
    ahead of the current trade date.
    """

    if manifest.feature_set_name != FEATURE_SET_V3:
        return _empty_summary(manifest)

    raw = loader.raw_data_cache
    if "close" not in raw:
        return _empty_summary(manifest, warning="missing close matrix; v3 features not attached")

    data_dir = Path(getattr(loader, "data_dir", "."))
    dataset_cache: dict[str, list[dict[str, Any]]] = {}
    summaries: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    pit_rows: list[dict[str, Any]] = []
    family_stats: dict[str, dict[str, Any]] = defaultdict(_family_row)

    for payload in manifest.feature_definitions:
        if payload.get("feature_set_name") != FEATURE_SET_V3 and payload.get("feature_version") != FEATURE_SET_V3:
            continue
        feature_name = str(payload.get("feature_name"))
        family = str(payload.get("family") or "unknown")
        required = [str(item) for item in payload.get("required_datasets", [])]
        optional = [str(item) for item in payload.get("optional_datasets", [])]
        pit_safety = str(payload.get("pit_safety") or "pit_safe")
        default_enabled = bool(payload.get("default_enabled", True))
        used_for_alpha = bool(payload.get("used_for_alpha", True))
        availability_field = payload.get("availability_field")

        stats = family_stats[family]
        stats["family"] = family
        stats["feature_count"] += 1
        stats["enabled_feature_count"] += int(default_enabled)
        stats["alpha_feature_count"] += int(used_for_alpha)
        stats["weak_pit_feature_count"] += int(pit_safety != "pit_safe")
        stats["required_datasets"] = sorted(set(stats["required_datasets"]) | set(required))
        stats["optional_datasets"] = sorted(set(stats["optional_datasets"]) | set(optional))

        direct_matrix = raw.get(feature_name.lower())
        missing = [] if direct_matrix is not None else [
            dataset
            for dataset in required
            if not _dataset_available(dataset, raw, data_dir, dataset_cache)
        ]
        if direct_matrix is not None:
            matrix = direct_matrix
        elif missing:
            matrix = _zeros(raw)
            message = f"missing required datasets for {feature_name}: {','.join(missing)}"
            warnings.append(_warning(feature_name, family, "warning", "missing_dataset", message))
            stats["missing_datasets"] = sorted(set(stats["missing_datasets"]) | set(missing))
        else:
            matrix = _build_feature(loader, payload, dataset_cache)
            if matrix is None:
                matrix = _zeros(raw)
                message = f"feature {feature_name} has no implemented v3 proxy builder"
                warnings.append(_warning(feature_name, family, "warning", "builder_missing", message))

        matrix = torch.nan_to_num(matrix.to(dtype=torch.float32, device=raw["close"].device), nan=0.0, posinf=0.0, neginf=0.0)
        raw[feature_name.lower()] = matrix
        coverage = float((matrix != 0).to(dtype=torch.float32).mean().item()) if matrix.numel() else 0.0
        stats["coverage_sum"] += coverage
        stats["coverage_observations"] += 1
        if coverage <= 0.0 and not missing:
            stats["zero_coverage_features"] += 1
        summaries.append(
            {
                "feature_name": feature_name,
                "family": family,
                "coverage": coverage,
                "default_enabled": default_enabled,
                "used_for_alpha": used_for_alpha,
                "pit_safety": pit_safety,
                "missing_datasets": missing,
            }
        )
        pit_status = "safe"
        if pit_safety != "pit_safe":
            pit_status = "weak_pit_disabled_for_alpha" if not used_for_alpha else "weak_pit_warning"
        if availability_field is None and family in {"financial_statement", "earnings_event", "holder_structure", "pledge_repurchase_unlock"}:
            pit_status = "missing_availability_warning"
        pit_rows.append(
            {
                "feature_name": feature_name,
                "family": family,
                "pit_safety": pit_safety,
                "availability_field": availability_field,
                "default_enabled": default_enabled,
                "used_for_alpha": used_for_alpha,
                "status": pit_status,
            }
        )

    family_readiness = []
    for row in family_stats.values():
        observations = max(int(row["coverage_observations"]), 1)
        coverage_mean = float(row["coverage_sum"]) / observations
        missing_count = len(row["missing_datasets"])
        readiness = "ready"
        if missing_count:
            readiness = "insufficient_data"
        elif row["enabled_feature_count"] <= 0:
            readiness = "disabled"
        elif coverage_mean <= 0.0:
            readiness = "warning"
        row["coverage_mean"] = coverage_mean
        row["readiness"] = readiness
        family_readiness.append(dict(row))

    weak_count = sum(1 for item in summaries if item["pit_safety"] != "pit_safe")
    disabled_count = sum(1 for item in summaries if not item["default_enabled"])
    return {
        "feature_set_name": manifest.feature_set_name,
        "feature_set_version": manifest.feature_set_version,
        "feature_set_hash": manifest.content_hash,
        "feature_count": len(summaries),
        "enabled_feature_count": sum(1 for item in summaries if item["default_enabled"]),
        "weak_pit_feature_count": weak_count,
        "disabled_feature_count": disabled_count,
        "feature_summaries": summaries,
        "feature_family_readiness": sorted(family_readiness, key=lambda item: item["family"]),
        "feature_pit_alignment": pit_rows,
        "warnings": warnings,
        "feature_pit_alignment_status": "warning" if weak_count or warnings else "ok",
    }


def write_extended_feature_reports(output_dir: str | Path, summary: dict[str, Any]) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "feature_family_readiness_path": str(
            write_json_artifact(
                target / "feature_family_readiness.json",
                {
                    "feature_set_name": summary.get("feature_set_name"),
                    "feature_set_version": summary.get("feature_set_version"),
                    "feature_set_hash": summary.get("feature_set_hash"),
                    "families": summary.get("feature_family_readiness", []),
                    "summary": _family_summary(summary),
                },
                "feature_family_readiness",
                "feature_factory",
            )
        ),
        "feature_pit_alignment_report_path": str(
            write_json_artifact(
                target / "feature_pit_alignment_report.json",
                {
                    "feature_set_name": summary.get("feature_set_name"),
                    "feature_set_version": summary.get("feature_set_version"),
                    "feature_set_hash": summary.get("feature_set_hash"),
                    "status": summary.get("feature_pit_alignment_status", "unknown"),
                    "weak_pit_feature_count": summary.get("weak_pit_feature_count", 0),
                    "disabled_feature_count": summary.get("disabled_feature_count", 0),
                    "features": summary.get("feature_pit_alignment", []),
                },
                "feature_pit_alignment_report",
                "feature_factory",
            )
        ),
        "feature_build_warnings_path": str(
            write_jsonl_artifact(
                target / "feature_build_warnings.jsonl",
                summary.get("warnings", []),
                "feature_build_warnings",
                "feature_factory",
            )
        ),
    }
    (target / "feature_family_readiness.md").write_text(_family_markdown(summary), encoding="utf-8")
    return paths


def _build_feature(loader, payload: dict[str, Any], dataset_cache: dict[str, list[dict[str, Any]]]) -> torch.Tensor | None:
    name = str(payload.get("feature_name"))
    family = str(payload.get("family") or "")
    raw = loader.raw_data_cache
    data_dir = Path(getattr(loader, "data_dir", "."))
    if family == "index_market":
        return _index_market_feature(loader, name, dataset_cache, data_dir)
    if family == "industry":
        return _industry_feature(loader, name)
    if family == "suspension_status":
        return _suspension_feature(loader, name, dataset_cache, data_dir)
    if family == "financial_statement":
        return _financial_feature(loader, name, dataset_cache, data_dir)
    if family == "earnings_event":
        return _event_feature(loader, name, dataset_cache, data_dir)
    if family == "moneyflow":
        return _moneyflow_feature(loader, name, dataset_cache, data_dir)
    if family == "margin":
        return _margin_feature(loader, name, dataset_cache, data_dir)
    if family == "abnormal_trading":
        return _abnormal_feature(loader, name, dataset_cache, data_dir)
    if family == "holder_structure":
        return _holder_feature(loader, name, dataset_cache, data_dir)
    if family == "pledge_repurchase_unlock":
        return _pledge_unlock_feature(loader, name, dataset_cache, data_dir)
    if family == "northbound":
        return _northbound_feature(loader, name, dataset_cache, data_dir)
    return raw.get(name.lower())


def _index_market_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    raw = loader.raw_data_cache
    close = raw["close"]
    index_close = _daily_series(loader, _records(dataset_cache, data_dir, "index_daily_bars"), "close")
    if index_close is None:
        index_close = close.mean(dim=0)
    index_matrix = index_close.unsqueeze(0).expand(close.shape[0], -1)
    if name == "INDEX_RETURN_1D":
        return _log_return(index_matrix, 1)
    if name == "INDEX_RETURN_5D":
        return _log_return(index_matrix, 5)
    if name == "INDEX_RETURN_20D":
        return _log_return(index_matrix, 20)
    if name == "INDEX_VOLATILITY_20D":
        return _rolling_std(_log_return(index_matrix, 1), 20)
    if name == "BENCHMARK_RELATIVE_RETURN_5D":
        return _log_return(close, 5) - _log_return(index_matrix, 5)
    if name == "BENCHMARK_RELATIVE_RETURN_20D":
        return _log_return(close, 20) - _log_return(index_matrix, 20)
    if name == "MARKET_REGIME_UP_DOWN_FLAG":
        return (_log_return(index_matrix, 20) > 0).to(dtype=torch.float32)
    basic = _records(dataset_cache, data_dir, "index_daily_basic")
    if name == "INDEX_VALUATION_PE":
        series = _daily_series(loader, basic, "pe")
        return (series if series is not None else torch.zeros(close.shape[1], device=close.device)).unsqueeze(0).expand_as(close)
    if name == "INDEX_VALUATION_PB":
        series = _daily_series(loader, basic, "pb")
        return (series if series is not None else torch.zeros(close.shape[1], device=close.device)).unsqueeze(0).expand_as(close)
    return _zeros(raw)


def _industry_feature(loader, name: str) -> torch.Tensor:
    raw = loader.raw_data_cache
    close = raw["close"]
    codes = raw.get("industry_code_matrix")
    if codes is None:
        codes = torch.zeros_like(close)
    codes = codes.to(dtype=torch.long, device=close.device)
    if name == "INDUSTRY_MEMBER_FLAG":
        return torch.ones_like(close)
    if name == "INDUSTRY_CONCENTRATION_PROXY":
        out = torch.zeros_like(close)
        for date_idx in range(close.shape[1]):
            current = codes[:, date_idx]
            for code in torch.unique(current):
                mask = current == code
                out[mask, date_idx] = float(mask.to(torch.float32).mean().item())
        return out
    base = raw.get("turnover_rate", torch.zeros_like(close)) if name == "INDUSTRY_RELATIVE_TURNOVER" else _log_return(close, 20 if name in {"INDUSTRY_RELATIVE_RETURN_20D", "INDUSTRY_MOMENTUM"} else 5)
    out = torch.zeros_like(close)
    for date_idx in range(close.shape[1]):
        current = codes[:, date_idx]
        for code in torch.unique(current):
            mask = current == code
            if mask.any():
                out[mask, date_idx] = base[mask, date_idx] - base[mask, date_idx].mean()
    return out


def _suspension_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    raw = loader.raw_data_cache
    if name == "RECENT_SUSPENSION_COUNT_20D":
        return _rolling_sum(raw.get("is_suspended", _zeros(raw)), 20)
    if name in {"NAME_CHANGE_ST_FLAG", "ST_HISTORY_FLAG"}:
        records = _records(dataset_cache, data_dir, "name_changes")
        return _event_flag(loader, records, "ann_date", predicate=lambda rec: "ST" in str(rec.get("name") or rec.get("new_name") or "").upper())
    if name == "NEW_SHARE_FLAG":
        return _event_flag(loader, _records(dataset_cache, data_dir, "new_shares"), "ipo_date")
    return _zeros(raw)


def _financial_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    income = _pit_latest(loader, _records(dataset_cache, data_dir, "income_statements"), "ann_date")
    balance = _pit_latest(loader, _records(dataset_cache, data_dir, "balance_sheets"), "ann_date")
    cashflow = _pit_latest(loader, _records(dataset_cache, data_dir, "cashflow_statements"), "ann_date")
    shape = loader.raw_data_cache["close"].shape
    out = torch.zeros(shape, dtype=torch.float32, device=loader.raw_data_cache["close"].device)
    for si, ts_code in enumerate(loader.ts_codes):
        prev_rev = 0.0
        prev_profit = 0.0
        prev_cash = 0.0
        for di, trade_date in enumerate(loader.trade_dates):
            inc = income.get((ts_code, trade_date), {})
            bal = balance.get((ts_code, trade_date), {})
            csh = cashflow.get((ts_code, trade_date), {})
            revenue = _num(inc, "revenue", "total_revenue", "oper_rev")
            profit = _num(inc, "net_profit", "n_income_attr_p", "total_profit")
            assets = _num(bal, "total_assets", "total_asset")
            debt = _num(bal, "total_liab", "total_debt")
            current_assets = _num(bal, "total_cur_assets", "current_assets")
            current_liab = _num(bal, "total_cur_liab", "current_liab")
            cogs = _num(inc, "oper_cost", "total_oper_cost")
            ocf = _num(csh, "net_cash_flows_oper_act", "n_cashflow_act")
            capex = abs(_num(csh, "c_pay_acq_const_fiolta", "capex"))
            if name == "ROA":
                value = profit / max(abs(assets), 1e-6)
            elif name == "GROSS_MARGIN":
                value = (revenue - cogs) / max(abs(revenue), 1e-6)
            elif name == "NET_MARGIN":
                value = profit / max(abs(revenue), 1e-6)
            elif name == "ASSET_TURNOVER":
                value = revenue / max(abs(assets), 1e-6)
            elif name == "DEBT_TO_ASSET":
                value = debt / max(abs(assets), 1e-6)
            elif name == "CURRENT_RATIO":
                value = current_assets / max(abs(current_liab), 1e-6)
            elif name == "OPERATING_CASHFLOW_TO_NET_INCOME":
                value = ocf / max(abs(profit), 1e-6)
            elif name == "FREE_CASHFLOW_PROXY":
                value = (ocf - capex) / max(abs(assets), 1e-6)
            elif name == "REVENUE_GROWTH_YOY":
                value = (revenue - prev_rev) / max(abs(prev_rev), 1e-6) if prev_rev else 0.0
            elif name == "NET_PROFIT_GROWTH_YOY":
                value = (profit - prev_profit) / max(abs(prev_profit), 1e-6) if prev_profit else 0.0
            elif name == "CASHFLOW_GROWTH_YOY":
                value = (ocf - prev_cash) / max(abs(prev_cash), 1e-6) if prev_cash else 0.0
            elif name == "ACCRUALS_PROXY":
                value = (profit - ocf) / max(abs(assets), 1e-6)
            else:
                value = 0.0
            out[si, di] = float(value)
            if revenue:
                prev_rev = revenue
            if profit:
                prev_profit = profit
            if ocf:
                prev_cash = ocf
    return out


def _event_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    if name.startswith("FORECAST_"):
        records = _records(dataset_cache, data_dir, "earnings_forecasts")
        if name == "FORECAST_UPWARD_REVISION_FLAG":
            return _event_flag(loader, records, "ann_date", predicate=lambda rec: _num(rec, "p_change_min", "net_profit_min") > 0)
        return _event_flag(loader, records, "ann_date", predicate=lambda rec: _num(rec, "p_change_min", "net_profit_min") < 0)
    if name == "EXPRESS_SURPRISE_PROXY":
        return _pit_numeric(loader, _records(dataset_cache, data_dir, "earnings_express"), "ann_date", ("yoy_net_profit", "net_profit_yoy", "surprise"))
    records = _records(dataset_cache, data_dir, "disclosure_calendar")
    if name == "DAYS_TO_DISCLOSURE":
        return _days_to_event(loader, records, "pre_date")
    if name == "DAYS_SINCE_DISCLOSURE":
        return _days_since_event(loader, records, "actual_date")
    if name == "DISCLOSURE_DELAY_FLAG":
        return _event_flag(loader, records, "actual_date", predicate=lambda rec: str(rec.get("actual_date") or "") > str(rec.get("pre_date") or ""))
    return _zeros(loader.raw_data_cache)


def _moneyflow_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    records = _records(dataset_cache, data_dir, "moneyflow")
    amount = torch.clamp(loader.raw_data_cache.get("amount", _zeros(loader.raw_data_cache)), min=1.0)
    net = _daily_matrix(loader, records, ("net_mf_amount", "net_amount", "moneyflow_net"))
    main = _daily_matrix(loader, records, ("buy_lg_amount", "buy_elg_amount")) - _daily_matrix(loader, records, ("sell_lg_amount", "sell_elg_amount"))
    small = _daily_matrix(loader, records, ("buy_sm_amount", "buy_md_amount")) - _daily_matrix(loader, records, ("sell_sm_amount", "sell_md_amount"))
    if name == "MONEYFLOW_NET_RATIO":
        return net / amount
    if name == "MONEYFLOW_MAIN_NET_RATIO":
        return main / amount
    if name == "MONEYFLOW_SMALL_ORDER_RATIO":
        return small / amount
    if name == "MONEYFLOW_Z20":
        return _rolling_z(net / amount, 20)
    if name == "MONEYFLOW_TREND_5D":
        return _rolling_mean(net / amount, 5)
    if name == "MONEYFLOW_TREND_20D":
        return _rolling_mean(net / amount, 20)
    return _zeros(loader.raw_data_cache)


def _margin_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    records = _records(dataset_cache, data_dir, "margin_detail") or _records(dataset_cache, data_dir, "margin_summary")
    amount = torch.clamp(loader.raw_data_cache.get("amount", _zeros(loader.raw_data_cache)), min=1.0)
    margin_balance = _daily_matrix(loader, records, ("rzye", "margin_balance", "rzrqye"))
    short_balance = _daily_matrix(loader, records, ("rqye", "short_sell_balance"))
    margin_buy = _daily_matrix(loader, records, ("rzmre", "margin_buy_amount"))
    if name == "MARGIN_BALANCE_CHANGE":
        return margin_balance - _delay(margin_balance, 1)
    if name == "MARGIN_BUY_RATIO":
        return margin_buy / amount
    if name == "SHORT_SELL_BALANCE_CHANGE":
        return short_balance - _delay(short_balance, 1)
    if name == "MARGIN_CROWDING_Z20":
        return _rolling_z(margin_balance / amount, 20)
    return _zeros(loader.raw_data_cache)


def _abnormal_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    if name == "TOP_LIST_FLAG":
        return _event_flag(loader, _records(dataset_cache, data_dir, "top_list"), "trade_date")
    if name == "TOP_INST_NET_BUY_RATIO":
        return _daily_matrix(loader, _records(dataset_cache, data_dir, "top_inst"), ("net_buy_ratio", "net_buy", "net_amount")) / torch.clamp(loader.raw_data_cache.get("amount", _zeros(loader.raw_data_cache)), min=1.0)
    records = _records(dataset_cache, data_dir, "block_trades")
    if name == "BLOCK_TRADE_DISCOUNT_PROXY":
        price = _daily_matrix(loader, records, ("price", "block_price"))
        close = torch.clamp(loader.raw_data_cache["close"], min=1e-6)
        return (price - close) / close
    if name == "BLOCK_TRADE_VALUE_RATIO":
        value = _daily_matrix(loader, records, ("amount", "vol", "value"))
        return value / torch.clamp(loader.raw_data_cache.get("amount", _zeros(loader.raw_data_cache)), min=1.0)
    return _zeros(loader.raw_data_cache)


def _holder_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    if name in {"HOLDER_NUMBER_CHANGE", "HOLDER_CONCENTRATION_PROXY"}:
        holder = _pit_numeric(loader, _records(dataset_cache, data_dir, "holder_number"), "ann_date", ("holder_num", "holder_number"))
        if name == "HOLDER_NUMBER_CHANGE":
            return holder - _delay(holder, 1)
        return 1.0 / torch.clamp(holder, min=1.0)
    if name == "TOP10_HOLDER_RATIO":
        return _pit_numeric(loader, _records(dataset_cache, data_dir, "top10_holders"), "ann_date", ("hold_ratio", "holder_ratio", "top10_holder_ratio"))
    if name == "TOP10_FLOAT_HOLDER_RATIO":
        return _pit_numeric(loader, _records(dataset_cache, data_dir, "top10_float_holders"), "ann_date", ("hold_ratio", "float_holder_ratio", "top10_float_holder_ratio"))
    return _zeros(loader.raw_data_cache)


def _pledge_unlock_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    if name in {"PLEDGE_RATIO", "PLEDGE_RISK_FLAG"}:
        pledge = _pit_numeric(loader, _records(dataset_cache, data_dir, "pledge_stat"), "ann_date", ("pledge_ratio", "pledge_rate"))
        return (pledge > 0.3).to(dtype=torch.float32) if name == "PLEDGE_RISK_FLAG" else pledge
    if name == "REPURCHASE_FLAG":
        return _event_flag(loader, _records(dataset_cache, data_dir, "repurchases"), "ann_date")
    if name == "REPURCHASE_AMOUNT_RATIO":
        return _pit_numeric(loader, _records(dataset_cache, data_dir, "repurchases"), "ann_date", ("amount", "repurchase_amount")) / torch.clamp(loader.raw_data_cache.get("total_mv", _zeros(loader.raw_data_cache)), min=1.0)
    if name in {"SHARE_UNLOCK_AMOUNT_RATIO", "UNLOCK_PRESSURE_FLAG"}:
        unlock = _pit_numeric(loader, _records(dataset_cache, data_dir, "share_unlocks"), "ann_date", ("unlock_amount", "float_amount", "amount")) / torch.clamp(loader.raw_data_cache.get("total_mv", _zeros(loader.raw_data_cache)), min=1.0)
        return (unlock > 0.05).to(dtype=torch.float32) if name == "UNLOCK_PRESSURE_FLAG" else unlock
    return _zeros(loader.raw_data_cache)


def _northbound_feature(loader, name: str, dataset_cache: dict[str, list[dict[str, Any]]], data_dir: Path) -> torch.Tensor:
    holding = _daily_matrix(loader, _records(dataset_cache, data_dir, "hk_holdings"), ("holding_ratio", "hold_ratio", "volume_ratio"))
    if name == "HK_HOLDING_RATIO":
        return holding
    if name == "HK_HOLDING_CHANGE_5D":
        return holding - _delay(holding, 5)
    if name == "HK_HOLDING_CHANGE_20D":
        return holding - _delay(holding, 20)
    if name == "HK_HOLDING_Z20":
        return _rolling_z(holding, 20)
    return _zeros(loader.raw_data_cache)


def _records(cache: dict[str, list[dict[str, Any]]], data_dir: Path, dataset: str) -> list[dict[str, Any]]:
    if dataset not in cache:
        path = data_dir / dataset / "records.jsonl"
        if not path.exists():
            cache[dataset] = []
        else:
            cache[dataset] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return cache[dataset]


def _dataset_available(dataset: str, raw: dict[str, torch.Tensor], data_dir: Path, cache: dict[str, list[dict[str, Any]]]) -> bool:
    if dataset in BASE_DATASET_KEYS and any(key in raw for key in BASE_DATASET_KEYS[dataset]):
        return True
    return bool(_records(cache, data_dir, dataset))


def _daily_matrix(loader, records: list[dict[str, Any]], fields: tuple[str, ...]) -> torch.Tensor:
    raw = loader.raw_data_cache
    if not records:
        return _zeros(raw)
    rows = []
    for record in records:
        ts_code = str(record.get("ts_code") or record.get("con_code") or "")
        trade_date = str(record.get("trade_date") or record.get("ann_date") or "")
        if not ts_code or not trade_date:
            continue
        rows.append({"ts_code": ts_code, "trade_date": trade_date, "value": sum(_num(record, field) for field in fields)})
    if not rows:
        return _zeros(raw)
    df = pd.DataFrame(rows)
    pivot = df.pivot_table(index="trade_date", columns="ts_code", values="value", aggfunc="last")
    pivot = pivot.reindex(index=loader.trade_dates, columns=loader.ts_codes).ffill().fillna(0.0)
    return torch.tensor(pivot.to_numpy(dtype="float32").T, dtype=torch.float32, device=raw["close"].device)


def _pit_numeric(loader, records: list[dict[str, Any]], availability_field: str, fields: tuple[str, ...]) -> torch.Tensor:
    latest = _pit_latest(loader, records, availability_field)
    out = _zeros(loader.raw_data_cache)
    for si, ts_code in enumerate(loader.ts_codes):
        for di, trade_date in enumerate(loader.trade_dates):
            out[si, di] = float(sum(_num(latest.get((ts_code, trade_date), {}), field) for field in fields))
    return out


def _pit_latest(loader, records: list[dict[str, Any]], availability_field: str) -> dict[tuple[str, str], dict[str, Any]]:
    by_stock: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        ts_code = str(record.get("ts_code") or record.get("con_code") or "")
        available = str(record.get(availability_field) or record.get("ann_date") or record.get("trade_date") or "")
        if ts_code and available:
            enriched = dict(record)
            enriched["_available_date"] = available
            by_stock[ts_code].append(enriched)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for ts_code in loader.ts_codes:
        records_for_stock = sorted(by_stock.get(ts_code, []), key=lambda item: str(item.get("_available_date") or ""))
        idx = 0
        current: dict[str, Any] = {}
        for trade_date in loader.trade_dates:
            while idx < len(records_for_stock) and str(records_for_stock[idx].get("_available_date") or "") <= trade_date:
                current = records_for_stock[idx]
                idx += 1
            latest[(ts_code, trade_date)] = current
    return latest


def _event_flag(
    loader,
    records: list[dict[str, Any]],
    date_field: str,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> torch.Tensor:
    out = _zeros(loader.raw_data_cache)
    stock_index = {ts_code: idx for idx, ts_code in enumerate(loader.ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(loader.trade_dates)}
    for record in records:
        if predicate is not None and not predicate(record):
            continue
        ts_code = str(record.get("ts_code") or record.get("con_code") or "")
        event_date = str(record.get(date_field) or record.get("trade_date") or "")
        si = stock_index.get(ts_code)
        di = date_index.get(event_date)
        if si is not None and di is not None:
            out[si, di] = 1.0
    return out


def _days_to_event(loader, records: list[dict[str, Any]], date_field: str) -> torch.Tensor:
    out = _zeros(loader.raw_data_cache)
    events = _events_by_stock(records, date_field)
    for si, ts_code in enumerate(loader.ts_codes):
        event_dates = events.get(ts_code, [])
        for di, trade_date in enumerate(loader.trade_dates):
            future = [item for item in event_dates if item >= trade_date]
            out[si, di] = float(_date_delta(trade_date, future[0])) if future else 0.0
    return out


def _days_since_event(loader, records: list[dict[str, Any]], date_field: str) -> torch.Tensor:
    out = _zeros(loader.raw_data_cache)
    events = _events_by_stock(records, date_field)
    for si, ts_code in enumerate(loader.ts_codes):
        event_dates = events.get(ts_code, [])
        for di, trade_date in enumerate(loader.trade_dates):
            past = [item for item in event_dates if item <= trade_date]
            out[si, di] = float(_date_delta(past[-1], trade_date)) if past else 0.0
    return out


def _daily_series(loader, records: list[dict[str, Any]], field: str) -> torch.Tensor | None:
    if not records:
        return None
    rows = []
    for record in records:
        trade_date = str(record.get("trade_date") or "")
        if trade_date:
            rows.append({"trade_date": trade_date, "value": _num(record, field)})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    series = df.groupby("trade_date")["value"].last().reindex(loader.trade_dates).ffill().fillna(0.0)
    return torch.tensor(series.to_numpy(dtype="float32"), dtype=torch.float32, device=loader.raw_data_cache["close"].device)


def _events_by_stock(records: list[dict[str, Any]], date_field: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for record in records:
        ts_code = str(record.get("ts_code") or record.get("con_code") or "")
        event_date = str(record.get(date_field) or "")
        if ts_code and event_date:
            grouped[ts_code].append(event_date)
    return {key: sorted(values) for key, values in grouped.items()}


def _date_delta(left: str, right: str) -> int:
    try:
        return int((pd.to_datetime(right) - pd.to_datetime(left)).days)
    except Exception:
        return 0


def _num(record: dict[str, Any], *fields: str) -> float:
    for field in fields:
        value = record.get(field)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _zeros(raw: dict[str, torch.Tensor]) -> torch.Tensor:
    return torch.zeros_like(raw["close"], dtype=torch.float32)


def _delay(x: torch.Tensor, periods: int) -> torch.Tensor:
    if periods <= 0 or periods >= x.shape[1]:
        return torch.zeros_like(x)
    pad = torch.zeros((x.shape[0], periods), dtype=x.dtype, device=x.device)
    return torch.cat([pad, x[:, :-periods]], dim=1)


def _log_return(x: torch.Tensor, periods: int) -> torch.Tensor:
    if periods <= 0 or periods >= x.shape[1]:
        return torch.zeros_like(x)
    delayed = _delay(x, periods)
    out = torch.log(torch.clamp(x, min=1e-6) / torch.clamp(delayed, min=1e-6))
    out[:, :periods] = 0.0
    return torch.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def _rolling_std(x: torch.Tensor, window: int) -> torch.Tensor:
    rows = []
    for idx in range(x.shape[1]):
        current = x[:, max(0, idx - window + 1) : idx + 1]
        rows.append(current.std(dim=1, unbiased=False))
    return torch.stack(rows, dim=1)


def _rolling_mean(x: torch.Tensor, window: int) -> torch.Tensor:
    rows = []
    for idx in range(x.shape[1]):
        rows.append(x[:, max(0, idx - window + 1) : idx + 1].mean(dim=1))
    return torch.stack(rows, dim=1)


def _rolling_sum(x: torch.Tensor, window: int) -> torch.Tensor:
    rows = []
    for idx in range(x.shape[1]):
        rows.append(x[:, max(0, idx - window + 1) : idx + 1].sum(dim=1))
    return torch.stack(rows, dim=1)


def _rolling_z(x: torch.Tensor, window: int) -> torch.Tensor:
    rows = []
    for idx in range(x.shape[1]):
        current = x[:, max(0, idx - window + 1) : idx + 1]
        mean = current.mean(dim=1)
        std = torch.clamp(current.std(dim=1, unbiased=False), min=1e-6)
        rows.append((x[:, idx] - mean) / std)
    return torch.stack(rows, dim=1)


def _family_row() -> dict[str, Any]:
    return {
        "family": "",
        "feature_count": 0,
        "enabled_feature_count": 0,
        "alpha_feature_count": 0,
        "weak_pit_feature_count": 0,
        "required_datasets": [],
        "optional_datasets": [],
        "missing_datasets": [],
        "zero_coverage_features": 0,
        "coverage_sum": 0.0,
        "coverage_observations": 0,
    }


def _family_summary(summary: dict[str, Any]) -> dict[str, Any]:
    families = summary.get("feature_family_readiness", [])
    return {
        "family_count": len(families),
        "ready_family_count": sum(1 for item in families if item.get("readiness") == "ready"),
        "insufficient_data_family_count": sum(1 for item in families if item.get("readiness") == "insufficient_data"),
        "warning_family_count": sum(1 for item in families if item.get("readiness") == "warning"),
        "weak_pit_feature_count": summary.get("weak_pit_feature_count", 0),
        "disabled_feature_count": summary.get("disabled_feature_count", 0),
    }


def _family_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Feature Family Readiness: {summary.get('feature_set_name')}",
        "",
        "| family | readiness | features | enabled | weak PIT | coverage | missing datasets |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary.get("feature_family_readiness", []):
        lines.append(
            f"| {row.get('family')} | {row.get('readiness')} | {row.get('feature_count', 0)} | "
            f"{row.get('enabled_feature_count', 0)} | {row.get('weak_pit_feature_count', 0)} | "
            f"{float(row.get('coverage_mean', 0.0)):.4f} | {','.join(row.get('missing_datasets', []))} |"
        )
    return "\n".join(lines) + "\n"


def _warning(feature_name: str, family: str, severity: str, code: str, message: str) -> dict[str, Any]:
    return {
        "feature_name": feature_name,
        "family": family,
        "severity": severity,
        "code": code,
        "message": message,
    }


def _empty_summary(manifest: FeatureSetManifest, warning: str | None = None) -> dict[str, Any]:
    warnings = []
    if warning:
        warnings.append(_warning("", "", "warning", "not_attached", warning))
    return {
        "feature_set_name": manifest.feature_set_name,
        "feature_set_version": manifest.feature_set_version,
        "feature_set_hash": manifest.content_hash,
        "feature_count": 0,
        "enabled_feature_count": 0,
        "weak_pit_feature_count": 0,
        "disabled_feature_count": 0,
        "feature_summaries": [],
        "feature_family_readiness": [],
        "feature_pit_alignment": [],
        "warnings": warnings,
        "feature_pit_alignment_status": "warning" if warnings else "ok",
    }
