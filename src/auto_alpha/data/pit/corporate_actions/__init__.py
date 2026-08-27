"""Corporate action normalization, total-return, and paper-account accounting."""

from .models import (
    CorporateActionApplication,
    CorporateActionEvent,
    CorporateActionLedgerEntry,
    CorporateActionReport,
    CorporateActionType,
    TotalReturnSeriesRecord,
)
from .normalizer import (
    bind_cninfo_documents_to_security_identity_intervals,
    build_cninfo_text_extractor_contract,
    extract_cninfo_document_text,
    normalize_corporate_action_records,
    parse_cninfo_corporate_action_documents,
    project_cninfo_corporate_action_event_versions,
    publish_cninfo_corporate_action_semantics,
    validate_cninfo_corporate_action_semantics,
    validate_cninfo_text_extractor_contract,
)
from .schedule import build_action_schedule, eligible_events_for_account, filter_events_available_as_of

__all__ = [
    "bind_cninfo_documents_to_security_identity_intervals",
    "CorporateActionApplication",
    "CorporateActionEvent",
    "CorporateActionLedgerEntry",
    "CorporateActionReport",
    "CorporateActionType",
    "TotalReturnSeriesRecord",
    "normalize_corporate_action_records",
    "build_cninfo_text_extractor_contract",
    "extract_cninfo_document_text",
    "parse_cninfo_corporate_action_documents",
    "project_cninfo_corporate_action_event_versions",
    "publish_cninfo_corporate_action_semantics",
    "validate_cninfo_corporate_action_semantics",
    "validate_cninfo_text_extractor_contract",
    "build_action_schedule",
    "eligible_events_for_account",
    "filter_events_available_as_of",
]
