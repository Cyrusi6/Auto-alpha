"""Strict factor-certified admission for portfolio auto_alpha.research.discovery.studies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from auto_alpha.portfolio.construction.research_contracts import FACTOR_CERTIFIED_STATUS
from auto_alpha.portfolio.construction.research_contracts import PortfolioResearchError


def validate_factor_certified_records(
    records: Sequence[Mapping[str, Any]],
    *,
    min_factor_count: int,
    min_family_count: int,
) -> list[dict[str, Any]]:
    if len(records) < min_factor_count:
        raise PortfolioResearchError("certified_factor_count_below_policy")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for raw in records:
        row = dict(raw)
        factor_id = str(row.get("factor_id") or "")
        formula_hash = str(row.get("formula_hash") or "")
        status = str(row.get("status") or row.get("certification_status") or "")
        family = str(row.get("family") or "")
        if not factor_id or not formula_hash or len(formula_hash) != 64:
            raise PortfolioResearchError("factor_certified_identity_invalid")
        if status != FACTOR_CERTIFIED_STATUS:
            raise PortfolioResearchError(f"factor_not_factor_certified:{factor_id}:{status or 'missing'}")
        if factor_id in seen_ids or formula_hash in seen_hashes:
            raise PortfolioResearchError("factor_certified_pool_duplicate_identity")
        if not family:
            raise PortfolioResearchError(f"factor_certified_family_missing:{factor_id}")
        if str(row.get("sealed_holdout_status") or "") != "sealed_holdout_passed":
            raise PortfolioResearchError(f"factor_certified_holdout_evidence_missing:{factor_id}")
        if row.get("independent_audit_passed") is not True:
            raise PortfolioResearchError(f"factor_certified_independent_audit_missing:{factor_id}")
        evidence_hash = str(row.get("certification_evidence_hash") or "")
        if len(evidence_hash) != 64:
            raise PortfolioResearchError(f"factor_certified_evidence_hash_invalid:{factor_id}")
        lookback = int(row.get("effective_lookback") or row.get("lookback_days") or 0)
        if lookback < 0:
            raise PortfolioResearchError(f"factor_certified_lookback_invalid:{factor_id}")
        row["factor_id"] = factor_id
        row["formula_hash"] = formula_hash
        row["status"] = FACTOR_CERTIFIED_STATUS
        row["family"] = family
        row["effective_lookback"] = lookback
        normalized.append(row)
        seen_ids.add(factor_id)
        seen_hashes.add(formula_hash)
    families = {row["family"] for row in normalized}
    if len(families) < min_family_count:
        raise PortfolioResearchError("certified_factor_family_count_below_policy")
    return sorted(normalized, key=lambda row: row["factor_id"])
