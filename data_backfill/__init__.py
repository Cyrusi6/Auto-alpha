"""Production-style local backfill utilities for A-share datasets."""

from .coverage import analyze_backfill_coverage
from .executor import execute_backfill_plan
from .models import (
    BackfillCoverageRecord,
    BackfillJob,
    BackfillJobStatus,
    BackfillPlan,
    BackfillQuotaSummary,
    BackfillReadinessReport,
    BackfillRunReport,
    BackfillRunState,
    BackfillScope,
    DatasetCoverageMatrix,
)
from .planner import build_backfill_plan, write_backfill_plan
from .quota import evaluate_backfill_quota

__all__ = [
    "BackfillCoverageRecord",
    "BackfillJob",
    "BackfillJobStatus",
    "BackfillPlan",
    "BackfillQuotaSummary",
    "BackfillReadinessReport",
    "BackfillRunReport",
    "BackfillRunState",
    "BackfillScope",
    "DatasetCoverageMatrix",
    "analyze_backfill_coverage",
    "build_backfill_plan",
    "execute_backfill_plan",
    "evaluate_backfill_quota",
    "write_backfill_plan",
]
