"""Feature readiness catalog for expanded raw datasets."""

from __future__ import annotations

from .models import DatasetPitSafety, DatasetReadinessCheck, FeatureFamilyReadiness, FeatureReadinessStatus


FEATURE_FAMILY_POLICIES: dict[str, dict[str, list[str] | str]] = {
    "price_volume": {
        "required": ["daily_bars", "adjustment_factors", "daily_limits"],
        "optional": ["suspensions", "name_changes"],
        "plan": "Feature v2 can use adjusted price, returns, limits and active masks after cutoff shifting.",
    },
    "liquidity": {
        "required": ["daily_bars", "daily_basic"],
        "optional": ["moneyflow", "margin_detail"],
        "plan": "Feature v2 can use amount, turnover and volume-derived liquidity.",
    },
    "volatility": {
        "required": ["daily_bars", "adjustment_factors"],
        "optional": ["index_daily_bars"],
        "plan": "Rolling volatility remains price-derived until market proxy features are promoted.",
    },
    "valuation": {
        "required": ["daily_basic"],
        "optional": ["index_daily_basic"],
        "plan": "Feature v2 can use PB/PE/PS and market value fields.",
    },
    "quality_growth": {
        "required": ["financial_features"],
        "optional": ["income_statements", "balance_sheets", "cashflow_statements", "earnings_express"],
        "plan": "Fundamental features require announcement-date availability and PIT joins.",
    },
    "industry_neutral": {
        "required": ["industry_members"],
        "optional": ["industry_classification"],
        "plan": "Industry neutralization needs manually reviewed weak-PIT industry membership before full use.",
    },
    "index_membership": {
        "required": ["index_members"],
        "optional": ["index_basic"],
        "plan": "Index membership can feed universes and benchmark-aware portfolios after effective-date review.",
    },
    "moneyflow": {
        "required": ["moneyflow"],
        "optional": [],
        "plan": "Moneyflow is an optional alpha family and should be shifted to next tradable session.",
    },
    "margin": {
        "required": ["margin_summary", "margin_detail"],
        "optional": [],
        "plan": "Margin features are optional and should remain separate from core alpha readiness.",
    },
    "event_driven": {
        "required": ["top_list", "top_inst", "block_trades", "hk_holdings"],
        "optional": [],
        "plan": "Event-like market datasets need event-date cutoff and liquidity review before v3 features.",
    },
    "shareholder": {
        "required": ["holder_number", "top10_holders", "top10_float_holders"],
        "optional": ["holder_trades", "pledge_detail", "pledge_stat", "repurchases", "share_unlocks"],
        "plan": "Shareholder features need announcement-date contracts; unsafe datasets remain excluded.",
    },
    "corporate_action": {
        "required": ["corporate_actions"],
        "optional": ["new_shares"],
        "plan": "Corporate action features are limited to event flags and total-return/accounting support.",
    },
}


def build_feature_readiness_catalog(checks: list[DatasetReadinessCheck]) -> list[FeatureFamilyReadiness]:
    by_dataset = {item.dataset: item for item in checks}
    rows: list[FeatureFamilyReadiness] = []
    for family, policy in FEATURE_FAMILY_POLICIES.items():
        required = [str(item) for item in policy["required"]]
        optional = [str(item) for item in policy["optional"]]
        blockers: list[str] = []
        warnings: list[str] = []
        for dataset in required:
            check = by_dataset.get(dataset)
            if check is None:
                blockers.append(f"{dataset} readiness check is missing")
                continue
            if check.status == "blocked":
                blockers.append(f"{dataset} blocked: {'; '.join(check.blockers)}")
            if check.record_count <= 0:
                blockers.append(f"{dataset} has no records")
            if check.pit_safety == DatasetPitSafety.unsafe_missing_availability:
                blockers.append(f"{dataset} is unsafe for PIT feature use")
            elif check.pit_safety == DatasetPitSafety.weak_pit:
                warnings.append(f"{dataset} is weak-PIT and requires manual review")
            elif check.pit_safety == DatasetPitSafety.event_date_only:
                warnings.append(f"{dataset} must be cutoff-shifted after market close")
        for dataset in optional:
            check = by_dataset.get(dataset)
            if check and check.pit_safety in {DatasetPitSafety.weak_pit, DatasetPitSafety.unsafe_missing_availability}:
                warnings.append(f"optional {dataset} has {check.pit_safety} PIT status")
        status = FeatureReadinessStatus.blocked if blockers else (FeatureReadinessStatus.warning if warnings else FeatureReadinessStatus.ready)
        rows.append(
            FeatureFamilyReadiness(
                feature_family=family,
                required_datasets=required,
                optional_datasets=optional,
                readiness_status=status,
                blockers=blockers,
                weak_pit_warnings=warnings,
                future_feature_plan=str(policy["plan"]),
            )
        )
    return rows
