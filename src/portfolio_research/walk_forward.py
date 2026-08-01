"""Fail-closed contiguous portfolio walk-forward splits."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .contracts import PortfolioResearchError, PortfolioResearchPolicy


@dataclass(frozen=True)
class PortfolioWalkForwardSplit:
    split_id: str
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    embargo_indices: tuple[int, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def build_portfolio_splits(
    common_eligible_dates: np.ndarray,
    policy: PortfolioResearchPolicy,
    *,
    effective_embargo: int,
) -> list[PortfolioWalkForwardSplit]:
    eligible = np.asarray(common_eligible_dates, dtype=bool).reshape(-1)
    if effective_embargo < policy.label_horizon:
        raise PortfolioResearchError("portfolio_embargo_shorter_than_label_horizon")
    segments = _segments(eligible)
    required = policy.train_size + policy.validation_size + policy.test_size + 2 * effective_embargo
    splits: list[PortfolioWalkForwardSplit] = []
    for segment_id, (start, end) in enumerate(segments):
        if end - start < required:
            continue
        cursor = start
        ordinal = 0
        while cursor + required <= end:
            train_end = cursor + policy.train_size
            validation_start = train_end + effective_embargo
            validation_end = validation_start + policy.validation_size
            test_start = validation_end + effective_embargo
            test_end = test_start + policy.test_size
            split = PortfolioWalkForwardSplit(
                split_id=f"segment_{segment_id}_portfolio_wf_{ordinal}",
                train_indices=tuple(range(cursor, train_end)),
                validation_indices=tuple(range(validation_start, validation_end)),
                test_indices=tuple(range(test_start, test_end)),
                embargo_indices=tuple(range(train_end, validation_start))
                + tuple(range(validation_end, test_start)),
            )
            if len(split.test_indices) != policy.test_size:
                raise PortfolioResearchError("portfolio_walk_forward_test_size_mismatch")
            splits.append(split)
            cursor += policy.step_size
            ordinal += 1
    if len(splits) < policy.min_evaluable_windows:
        raise PortfolioResearchError("portfolio_walk_forward_windows_insufficient")
    return splits


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask.tolist() + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index))
            start = None
    return result
