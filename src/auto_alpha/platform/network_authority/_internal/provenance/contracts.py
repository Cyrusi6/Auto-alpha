from __future__ import annotations

EXPECTED_BASELINE = "67571b7c4adb439744e29c61ee258763aa2f2f79"
MAX_DATE = "20260630"
SIMULATION_START_FLOOR = "20160104"
SIMULATION_END = "20240530"
MAX_STALE_AGE_TRADE_DAYS = 250

OFFLINE_STAGE_STATUS = "offline_source_salvage_completed"
OFFLINE_BLOCKED_STATUS = "task055e_governed_acquisition_or_dynamic_simulation_closure_blocked"

PROVENANCE_SCHEMA = "task055e_row_provenance_index_v1"
RECONCILIATION_SCHEMA = "task055e_offline_reconciliation_v1"
ANCHOR_SCHEMA = "task055e_anchor_reprojection_v1"
DOMAIN_SCHEMA = "task055e_valuation_domains_v1"
NETWORK_PLAN_SCHEMA = "task055e_minimal_network_plan_v1"
FINAL_REPORT_SCHEMA = "task055e_offline_source_salvage_report_v1"

DAILY_API_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
)
DAILY_NORMALIZED_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
)
SUSPEND_FIELDS = (
    "ts_code",
    "trade_date",
    "suspend_timing",
    "suspend_type",
)

OFFLINE_CLASSIFICATIONS = {
    "existing_valid_daily_bar",
    "existing_positive_suspend_event",
    "complete_range_response_without_row",
    "lake_missing_but_raw_cache_contains_bar",
    "raw/lake/matrix_conflict",
    "genuinely_not_found_offline",
}

PROBE_KEYS = (
    ("600170.SH", "20160323"),
    ("601018.SH", "20160517"),
    ("600019.SH", "20160823"),
)
