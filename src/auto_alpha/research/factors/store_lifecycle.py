"""Canonical factor research lifecycle and admission checks; consolidated from auto_alpha.research.factors.store."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class FactorLifecycleStatus(StrEnum):
    generated = "generated"
    research_evaluated = "research_evaluated"
    research_rejected = "research_rejected"
    validation_candidate = "validation_candidate"
    validation_data_blocked = "validation_data_blocked"
    statistically_rejected = "statistically_rejected"
    historical_replay_passed = "historical_replay_passed"
    clean_holdout_passed = "clean_holdout_passed"
    factor_certified = "factor_certified"
    composite_unvalidated = "composite_unvalidated"


VALIDATION_ADMISSION_STATUS = FactorLifecycleStatus.validation_candidate.value


def has_positive_oos_evidence(payload: Any) -> bool:
    """Return true only for an explicit positive, evaluable test-split decision."""

    status = str(_value(payload, "status") or "")
    if status != VALIDATION_ADMISSION_STATUS:
        return False
    metadata = _mapping(_value(payload, "metadata"))
    decision = _mapping(metadata.get("gate_decision"))
    checks = _mapping(decision.get("checks"))
    return bool(
        decision.get("passed") is True
        and checks.get("oos_evidence_positive") is True
        and _finite_positive(checks.get("test_evaluable_date_count"))
        and _finite_positive(checks.get("test_valid_observation_count"))
        and _finite_positive(checks.get("test_rank_ic_mean"))
    )


def validation_admission_reason(payload: Any) -> str:
    if str(_value(payload, "status") or "") != VALIDATION_ADMISSION_STATUS:
        return "factor_status_not_validation_candidate"
    if not has_positive_oos_evidence(payload):
        return "positive_oos_evidence_missing"
    return "validation_candidate_admitted"


def _value(payload: Any, key: str) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite_positive(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return numeric > 0.0 and numeric < float("inf")
