from __future__ import annotations

from data_pipeline.ashare.request_normalization import tushare_request_fingerprint


BASELINE_COMMIT = "a2a352d6e980cee286e8f64203045c8f12611180"
TASK055J_FINAL_SEAL_HASH = "ecb95537625014a0e98e34ffc8e15a30c36c537db511c7e2d5444ce3322e2aee"
TASK055J_RUNTIME_HASH = "4681efc9e58d0e2db5458f7a62d284355ebe94d588d337df0eb69825d4263cf9"
TASK055J_AUTHORIZATION_HASH = "d88936e5b14685052d9cb7d78770b039be2c896199b3012aada58f48cc40261b"
TASK055J_REHEARSAL_HASH = "e6059d433853984b87404badaec41878b17b1f789dacec31cbeb9f581a3a3645"
TASK055J_REHEARSAL_VERIFICATION_HASH = "1e34d24915ad454e055b619f0d0ad4b9253d54fc79b3ed46d81279d4d117971c"
TASK055J_REPORT_HASH = "3c04689265d3c8ef0e300901c1ea8c78596a57742a6dbcf16711f7a5e34ad650"
TASK055J_FINAL_VERIFICATION_HASH = "f8468d6d9a12e7c0552d1ec1f4adfa690fe74fd7acc75f20c9d0c0f289d74501"
TASK055J_RELATIVE_ROOT = "validation_runs/task_055_j_20260718"
TASK055J_AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055j_single_canary_v1"
TASK055K_RELATIVE_ROOT = "validation_runs/task_055_k_20260719"
TASK055K_AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055k_single_canary_v1"
MAX_DATE = "20260630"

CANARY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
]
CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": CANARY_FIELDS,
    "request_fingerprint": "8cec7ae0957a9d54afb1f08736db3f1c12b402554f5e1c3cc2e007658b8af869",
    "transport_identity": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_identity": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}

if tushare_request_fingerprint(
    CANARY["api_name"],
    params={"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"]},
    fields=CANARY_FIELDS,
) != CANARY["request_fingerprint"]:
    raise RuntimeError("task055k_fixed_canary_request_fingerprint_drift")

MAX_UNIQUE_SECURITY_DATES = 64
MAX_LOGICAL_REQUESTS = 128
MAX_PHYSICAL_ATTEMPTS = 160

READY_STATUS = "task055k_single_canary_engineering_ready_waiting_operator_authorization_no_network_executed"
BLOCKED_STATUS = "task055k_single_canary_correctness_closure_blocked"

SOURCE_SCHEMA = "task055k_git_index_source_seal_v1"
PARENT_VERIFICATION_SCHEMA = "task055k_task055j_parent_verification_v1"
CANDIDATE_AUTHORITY_SCHEMA = "task055k_candidate_runtime_authority_v1"
CANDIDATE_SEAL_SCHEMA = "task055k_candidate_execution_checkpoint_v1"
ATTEMPT_RESERVATION_SCHEMA = "task055k_single_attempt_reservation_v1"
TRANSPORT_RECEIPT_SCHEMA = "task055k_signed_transport_receipt_v1"
REHEARSAL_SCHEMA = "task055k_native_rehearsal_v1"
REHEARSAL_VERIFICATION_SCHEMA = "task055k_native_rehearsal_verification_v1"
FINAL_REPORT_SCHEMA = "task055k_engineering_report_v1"
FINAL_VERIFICATION_SCHEMA = "task055k_independent_final_verification_v1"
SCRUBBED_SCHEMA = "task055k_scrubbed_candidate_evidence_v1"

APPLICATION_STAGES = (
    "response_acceptance",
    "raw_repair",
    "truth_successor",
    "freeze",
    "strict_matrix",
    "v3_tensor",
    "exact20_materialization",
    "firewall_sentinel",
    "valuation",
    "net_replay",
    "all_in_replay",
    "final_publication",
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
