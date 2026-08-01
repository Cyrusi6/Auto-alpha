"""Static formula checks for Alpha Factory candidates."""

from __future__ import annotations

from dataclasses import replace

from feature_factory import build_feature_semantics_map, feature_semantics_contract_hash
from model_core.vm import StackVM


FORBIDDEN_NAMES = {"TARGET_RET", "target_ret", "FUTURE_RETURN", "NEXT_RET"}


def run_static_checks(
    candidates,
    *,
    max_complexity: int,
    max_lookback: int,
    vocab=None,
    promotion_gate=None,
    feature_meta: dict[str, dict] | None = None,
    feature_semantics: dict[str, object] | None = None,
) -> tuple[list, list[dict]]:
    if feature_semantics is None and feature_meta:
        feature_semantics = build_feature_semantics_map(feature_meta.values())
    if not feature_semantics:
        raise ValueError("static admission requires canonical feature semantics")
    vm = StackVM(vocab)
    contract_hash = feature_semantics_contract_hash(feature_semantics)
    seen: set[str] = set()
    updated = []
    rows: list[dict] = []
    for candidate in candidates:
        errors: list[str] = []
        warnings: list[str] = []
        valid, reason = vm.validate_with_reason(candidate.formula_tokens)
        if not valid:
            errors.append(reason)
        if candidate.formula_hash in seen:
            errors.append("duplicate_formula_hash")
        seen.add(candidate.formula_hash)
        if candidate.complexity > max_complexity:
            errors.append("complexity_exceeds_limit")
        formula_semantics = None
        if valid:
            try:
                formula_semantics = vm.formula_semantics(candidate.formula_tokens, feature_semantics)
            except ValueError as exc:
                errors.append(str(exc))
        canonical_lookback = formula_semantics.max_raw_lag if formula_semantics is not None else None
        if canonical_lookback is not None and canonical_lookback > max_lookback:
            errors.append("lookback_exceeds_limit")
        forbidden = sorted(set(candidate.formula_names) & FORBIDDEN_NAMES)
        if forbidden:
            errors.append(f"forbidden_token:{','.join(forbidden)}")
        promotion_metadata = {}
        if promotion_gate is not None:
            gate_errors, gate_warnings, promotion_metadata = promotion_gate.check_formula_names(candidate.formula_names, feature_meta or {})
            errors.extend(gate_errors)
            warnings.extend(gate_warnings)
        status = "passed" if not errors else "failed"
        updated_candidate = replace(
            candidate,
            lookback=int(canonical_lookback if canonical_lookback is not None else candidate.lookback),
            static_check_status=status,
            status="static_passed" if status == "passed" and candidate.status != "rejected" else "rejected",
            reject_reason="; ".join(errors) if errors else candidate.reject_reason,
            metadata=dict(candidate.metadata) | {
                "canonical_semantics_hash": formula_semantics.semantics_hash if formula_semantics is not None else None,
                "feature_semantics_contract_hash": contract_hash,
                "canonical_max_raw_lag": formula_semantics.max_raw_lag if formula_semantics is not None else None,
                "required_observations": formula_semantics.required_observations if formula_semantics is not None else None,
            },
        )
        updated.append(updated_candidate)
        rows.append(
            {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "status": status,
                "errors": errors,
                "warnings": warnings,
                "complexity": candidate.complexity,
                "lookback": candidate.lookback,
                "canonical_max_raw_lag": formula_semantics.max_raw_lag if formula_semantics is not None else None,
                "required_observations": formula_semantics.required_observations if formula_semantics is not None else None,
                "canonical_semantics_hash": formula_semantics.semantics_hash if formula_semantics is not None else None,
                "feature_semantics_contract_hash": contract_hash,
                "feature_promotion": promotion_metadata,
            }
        )
    return updated, rows
