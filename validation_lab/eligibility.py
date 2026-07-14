"""Campaign-level validation date eligibility and contiguous segments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from research_firewall import ResearchEligibilityContract


@dataclass(frozen=True)
class EligibilityResult:
    eligible_mask: np.ndarray
    reasons_by_date: tuple[tuple[str, ...], ...]
    eligible_date_hash: str
    segments: tuple[tuple[int, int], ...]
    research_contract: dict[str, object] | None = None


def build_common_eligibility(
    trade_dates: list[str],
    *,
    membership_known: np.ndarray,
    snapshot_valid: np.ndarray,
    target_data_valid: np.ndarray,
    structural_gap_free: np.ndarray,
    research_contract: ResearchEligibilityContract | None = None,
) -> EligibilityResult:
    arrays = [np.asarray(value, dtype=np.bool_).reshape(-1) for value in (membership_known, snapshot_valid, target_data_valid, structural_gap_free)]
    if any(value.shape != (len(trade_dates),) for value in arrays):
        raise ValueError("eligibility masks must align to trade_dates")
    names = ["membership_unknown", "snapshot_proof_invalid", "target_data_insufficient", "structural_data_gap"]
    if research_contract is not None:
        arrays.append(np.asarray(research_contract.eligible_mask(trade_dates), dtype=np.bool_))
        names.append("research_target_endpoint_unobservable")
    eligible = np.logical_and.reduce(arrays)
    reasons = []
    for index in range(len(trade_dates)):
        reasons.append(tuple(names[position] for position, value in enumerate(arrays) if not value[index]))
    if research_contract is not None:
        contract_lineage = research_contract.lineage(trade_dates)
        eligible_hash = hashlib.sha256(
            (
                contract_lineage["eligible_date_hash"]
                + "\n"
                + "\n".join(date for date, allowed in zip(trade_dates, eligible) if allowed)
            ).encode("utf-8")
        ).hexdigest()
    else:
        selected = [date for date, allowed in zip(trade_dates, eligible) if allowed]
        eligible_hash = hashlib.sha256("\n".join(selected).encode()).hexdigest()
        contract_lineage = None
    return EligibilityResult(
        eligible_mask=eligible,
        reasons_by_date=tuple(reasons),
        eligible_date_hash=eligible_hash,
        segments=tuple(_segments(eligible)),
        research_contract=contract_lineage,
    )


def eligible_date_segments(trade_dates: list[str], result: EligibilityResult) -> list[list[str]]:
    return [trade_dates[start:end] for start, end in result.segments]


def _segments(mask: np.ndarray):
    start = None
    for index, value in enumerate(mask.tolist() + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            yield start, index
            start = None
