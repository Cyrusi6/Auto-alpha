"""Interpretable formula templates for Alpha Factory."""

from __future__ import annotations

from model_core.vocab import FORMULA_VOCAB


def template_formulas(feature_set_name: str = "ashare_features_v1") -> list[dict[str, object]]:
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
    result = []
    for name, formula_names, tags in specs:
        tokens = [FORMULA_VOCAB.encode_name(item) for item in formula_names]
        result.append({"name": name, "formula_names": formula_names, "formula_tokens": tokens, "family_tags": tags})
    return result
