from __future__ import annotations

EXPECTED_BASELINE = "5bc179de10a921e9547d63c393643d4438b126f3"
MAX_DATE = "20260630"
TASK055G_RELATIVE_ROOT = "validation_runs/task_055_g_20260716_v3"
TASK055H_RELATIVE_ROOT = "validation_runs/task_055_h_20260717"

AUTHORIZATION_SEAL_SCHEMA = "task055h_network_authorization_seal_v1"
SCRUBBED_EVIDENCE_SCHEMA = "task055h_scrubbed_authorization_evidence_v1"
SCRUBBED_VERIFICATION_SCHEMA = "task055h_scrubbed_evidence_verification_v1"
ACCESS_JOURNAL_SCHEMA = "task055h_durable_access_journal_v1"
OPERATIONAL_SEAL_SCHEMA = "task055h_authoritative_operational_seal_v1"
FEE_ATTESTATION_SCHEMA = "task055h_fee_schedule_attestation_v1"
CANARY_ACCEPTANCE_SCHEMA = "task055h_canary_acceptance_v1"
RESUME_AUTHORIZATION_SCHEMA = "task055h_resume_authorization_v1"
RESPONSE_APPLY_SCHEMA = "task055h_native_response_apply_v1"
FINAL_REPORT_SCHEMA = "task055h_engineering_report_v1"
FINAL_VERIFICATION_SCHEMA = "task055h_independent_final_verification_v1"

READY_STATUS = "canary_authorization_ready_no_network_executed"
BLOCKED_STATUS = "task055h_canary_authorization_blocked_no_network_executed"

MAX_UNIQUE_SECURITY_DATES = 64
MAX_LOGICAL_REQUESTS = 128
MAX_PHYSICAL_ATTEMPTS = 160

OFFICIAL_DOCUMENT_IDS = (
    "fee_reform_2015",
    "handling_fee_2023",
    "management_fee_2012",
    "stamp_half_2023",
    "stamp_history_context",
    "stamp_tax_law",
    "transfer_fee_2022",
)

CERTIFICATION_BLOCKERS = (
    "historical_selection_contamination",
    "selection_data_reused",
    "execution_modeled",
    "suspension_timing_semantics_uncertified",
    "constituent_publication_timing_unknown",
    "vendor_historical_revision_risk",
    "prospective_holdout_not_arrived",
    "broker_specific_commission_unavailable",
)
