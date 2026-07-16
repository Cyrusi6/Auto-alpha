from __future__ import annotations

EXPECTED_BASELINE = "c9e551e9e8e81f447bed9d0ca5cf97f69af1dd29"
EXPECTED_TASK055E_REPORT_HASH = "202d84acc3ad245ad2b7c0b24e3e5eedafa5138a2e2f9b3d296086ea1f03b676"
MAX_DATE = "20260630"
SIMULATION_START = "20160104"
SIMULATION_END = "20240530"
MAX_STALE_AGE_TRADE_DAYS = 250

TRUTH_SCHEMA = "task055f_security_date_truth_v2"
READ_LEDGER_SCHEMA = "task055f_append_only_read_ledger_v1"
CAUSAL_SCHEMA = "task055f_causal_frontier_v1"
NETWORK_PLAN_SCHEMA = "task055f_exact_frontier_network_plan_v1"
CANARY_SCHEMA = "task055f_network_canary_v1"
CANARY_ACCEPTANCE_SCHEMA = "task055f_canary_acceptance_v1"
FINAL_REPORT_SCHEMA = "task055f_engineering_report_v1"
SEMANTIC_VERIFICATION_SCHEMA = "task055f_independent_semantic_verification_v1"
FEE_SCHEDULE_SCHEMA = "task055f_fee_schedule_v2"
FEE_DOCUMENT_ACQUISITION_SCHEMA = "task055f_official_fee_document_acquisition_v1"

BLOCKED_STATUS = "task055f_governed_evidence_or_fee_or_dynamic_simulation_closure_blocked"
COMPLETED_STATUS = (
    "task055f_native_simulator_engineering_completed_future_research_data_blocked_"
    "historical_selection_contaminated_execution_modeled_certification_blocked"
)

DAILY_FIELDS = (
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
MATRIX_DAILY_FIELDS = ("open", "high", "low", "close", "pre_close", "volume", "amount")
SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")

TRUTH_STATES = {
    "TRADED_PRIMARY_BAR",
    "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE",
    "RESUME_EVENT_WITHOUT_SUSPENSION_EVIDENCE",
    "SUSPENSION_EVENT_CONFLICT",
    "SUSPENSION_INTRADAY_UNSUPPORTED",
    "SUSPENSION_TIMING_UNPARSED",
    "MATRIX_SOURCE_CONFLICT",
    "LIFECYCLE_OR_CORPORATE_ACTION_CONFLICT",
    "LIFECYCLE_TERMINATED",
    "DATA_SOURCE_GAP",
}

MODELED_STALE_METHOD = "STALE_VENDOR_DAILY_NON_TRADING_MODELED"
OFFICIAL_OPEN_METHOD = "OFFICIAL_OPEN"
OFFICIAL_CLOSE_METHOD = "OFFICIAL_CLOSE"

EXPLICIT_FULL_DAY_TIMINGS = {
    "09:30-15:00",
    "09:30-11:30,13:00-15:00",
    "全天",
    "全日",
    "FULL_DAY",
}

MAX_UNIQUE_SECURITY_DATES = 64
MAX_LOGICAL_REQUESTS = 128
MAX_PHYSICAL_ATTEMPTS = 160

PROBE_KEYS = (
    ("600170.SH", "20160323"),
    ("601018.SH", "20160517"),
    ("600019.SH", "20160823"),
)
