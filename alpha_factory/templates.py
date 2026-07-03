"""Interpretable formula templates for Alpha Factory."""

from __future__ import annotations

from model_core.vocab import FORMULA_VOCAB
from feature_factory import FEATURE_SET_V3, make_formula_vocab_from_manifest


def template_formulas(
    feature_set_name: str = "ashare_features_v1",
    feature_set_manifest=None,
    *,
    exclude_weak_pit_features: bool = True,
    required_feature_families: set[str] | None = None,
    feature_family_budget: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    vocab = make_formula_vocab_from_manifest(feature_set_manifest) if feature_set_manifest is not None else FORMULA_VOCAB
    feature_meta = _feature_meta(feature_set_manifest)
    specs = [
        ("reversal_template", ["RET_1D"], ["reversal", "price_return"]),
        ("momentum_template", ["RET_5D"], ["momentum", "price_return"]),
        ("volatility_template", ["AMPLITUDE"], ["volatility"]),
        ("liquidity_template", ["LOG_AMOUNT"], ["liquidity"]),
        ("valuation_template", ["PB"], ["valuation"]),
        ("quality_growth_template", ["ROE", "REVENUE_YOY", "ADD"], ["quality", "growth"]),
        ("size_neutral_template", ["LOG_MKT_CAP"], ["size"]),
        ("price_volume_interaction_template", ["RET_1D", "TURNOVER_RATE", "MUL"], ["price_return", "liquidity"]),
        ("corporate_action_template", ["RET_5D"], ["corporate_action"]),
        ("index_membership_template", ["VOLUME_RATIO"], ["index_membership"]),
    ]
    if feature_set_name == FEATURE_SET_V3:
        v3_specs = [
            ("industry_relative_template", ["INDUSTRY_RELATIVE_RETURN_20D"], ["industry"]),
            ("moneyflow_reversal_template", ["MONEYFLOW_NET_RATIO", "RET_1D", "SUB"], ["moneyflow", "price_return"]),
            ("margin_crowding_template", ["MARGIN_CROWDING_Z20"], ["margin"]),
            ("financial_quality_template", ["ROA", "GROSS_MARGIN", "ADD"], ["financial_statement", "quality"]),
            ("cashflow_quality_template", ["OPERATING_CASHFLOW_TO_NET_INCOME", "FREE_CASHFLOW_PROXY", "ADD"], ["financial_statement", "cashflow"]),
            ("earnings_event_template", ["EXPRESS_SURPRISE_PROXY"], ["earnings_event"]),
            ("block_trade_discount_template", ["BLOCK_TRADE_DISCOUNT_PROXY"], ["abnormal_trading"]),
            ("holder_concentration_template", ["HOLDER_CONCENTRATION_PROXY"], ["holder_structure"]),
            ("pledge_risk_template", ["PLEDGE_RATIO"], ["pledge_repurchase_unlock"]),
            ("hk_holding_trend_template", ["HK_HOLDING_CHANGE_20D"], ["northbound"]),
        ]
        specs = v3_specs + specs
    result = []
    family_counts: dict[str, int] = {}
    for name, formula_names, tags in specs:
        if not _formula_allowed(formula_names, feature_meta, exclude_weak_pit_features):
            continue
        if required_feature_families and not (set(tags) & required_feature_families):
            continue
        if feature_family_budget and not _budget_available(tags, feature_family_budget, family_counts):
            continue
        try:
            tokens = [vocab.encode_name(item) for item in formula_names]
        except ValueError:
            continue
        result.append({"name": name, "formula_names": formula_names, "formula_tokens": tokens, "family_tags": tags})
        for tag in tags:
            family_counts[tag] = family_counts.get(tag, 0) + 1
    return result


def _feature_meta(manifest) -> dict[str, dict]:
    if manifest is None:
        return {}
    payload = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest)
    return {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_definitions", [])
        if isinstance(item, dict) and item.get("feature_name")
    }


def _formula_allowed(formula_names: list[str], meta: dict[str, dict], exclude_weak_pit: bool) -> bool:
    if not meta:
        return True
    for name in formula_names:
        if name not in meta:
            continue
        info = meta[name]
        if not info.get("default_enabled", True):
            return False
        if not info.get("used_for_alpha", True):
            return False
        if exclude_weak_pit and info.get("pit_safety") != "pit_safe":
            return False
    return True


def _budget_available(tags: list[str], budgets: dict[str, int], counts: dict[str, int]) -> bool:
    matching = [tag for tag in tags if tag in budgets]
    if not matching:
        return True
    return all(counts.get(tag, 0) < budgets[tag] for tag in matching)
