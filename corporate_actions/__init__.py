"""Corporate action normalization, total-return, and paper-account accounting."""

from .models import (
    CorporateActionApplication,
    CorporateActionEvent,
    CorporateActionLedgerEntry,
    CorporateActionReport,
    CorporateActionType,
    TotalReturnSeriesRecord,
)
from .normalizer import normalize_corporate_action_records
from .schedule import build_action_schedule, eligible_events_for_account, filter_events_available_as_of

__all__ = [
    "CorporateActionApplication",
    "CorporateActionEvent",
    "CorporateActionLedgerEntry",
    "CorporateActionReport",
    "CorporateActionType",
    "TotalReturnSeriesRecord",
    "normalize_corporate_action_records",
    "build_action_schedule",
    "eligible_events_for_account",
    "filter_events_available_as_of",
]
