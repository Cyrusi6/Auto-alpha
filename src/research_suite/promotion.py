"""Promotion gate for production candidate factors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from factor_store import LocalFactorStore

from .models import PromotionConfig, PromotionDecision, WalkForwardResult


def promote_factor_if_eligible(
    store: LocalFactorStore,
    factor_id: str,
    walk_forward_result: WalkForwardResult,
    backtest_metrics: dict[str, float],
    config: PromotionConfig,
) -> PromotionDecision:
    record = next((item for item in store.load_factors() if item.factor_id == factor_id), None)
    factor_type = (record.factor_type if record is not None else None) or "single"
    checks: dict[str, Any] = {
        "mean_test_score": float(walk_forward_result.summary.get("mean_test_score", 0.0)),
        "positive_test_score_ratio": float(walk_forward_result.summary.get("positive_test_score_ratio", 0.0)),
        "fill_rate": float(backtest_metrics.get("fill_rate", 0.0)),
        "constraint_reject_rate": float(backtest_metrics.get("constraint_reject_rate", 0.0)),
        "tracking_error": float(backtest_metrics.get("tracking_error", 0.0)),
        "risk_constraint_violations": float(backtest_metrics.get("risk_constraint_violations", 0.0)),
        "max_active_style_exposure_abs": float(backtest_metrics.get("max_active_style_exposure_abs", 0.0)),
        "max_factor_risk_share": float(backtest_metrics.get("max_factor_risk_share", 0.0)),
        "max_specific_risk_share": float(backtest_metrics.get("max_specific_risk_share", 0.0)),
        "factor_type": factor_type,
        "require_composite": bool(config.require_composite),
    }
    reasons: list[str] = []
    if checks["mean_test_score"] < config.min_mean_test_score:
        reasons.append("mean_test_score_below_threshold")
    if checks["positive_test_score_ratio"] < config.min_positive_test_score_ratio:
        reasons.append("positive_test_score_ratio_below_threshold")
    if checks["fill_rate"] < config.min_fill_rate:
        reasons.append("fill_rate_below_threshold")
    if checks["constraint_reject_rate"] > config.max_constraint_reject_rate:
        reasons.append("constraint_reject_rate_above_threshold")
    if checks["tracking_error"] > config.max_tracking_error:
        reasons.append("tracking_error_above_threshold")
    if checks["risk_constraint_violations"] > config.max_constraint_violations:
        reasons.append("risk_constraint_violations_above_threshold")
    if checks["max_active_style_exposure_abs"] > config.max_active_style_exposure_abs:
        reasons.append("active_style_exposure_above_threshold")
    if checks["max_factor_risk_share"] > config.max_factor_risk_share:
        reasons.append("factor_risk_share_above_threshold")
    if checks["max_specific_risk_share"] > config.max_specific_risk_share:
        reasons.append("specific_risk_share_above_threshold")
    if config.require_composite and factor_type != "composite":
        reasons.append("factor_is_not_composite")

    passed = not reasons
    decision = PromotionDecision(
        factor_id=factor_id,
        passed=passed,
        new_status="production_candidate" if passed else (record.status if record is not None else "unknown"),
        reasons=reasons,
        checks=checks,
        created_at=_utc_now(),
    )
    if passed:
        store.update_factor_status(
            factor_id,
            "production_candidate",
            reason="promotion_passed",
            promotion_decision=decision.to_dict(),
        )
    return decision


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
