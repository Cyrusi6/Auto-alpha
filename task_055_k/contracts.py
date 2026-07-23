from __future__ import annotations

from data_pipeline.ashare.request_normalization import tushare_request_fingerprint


BASELINE_COMMIT = "cc44926dda583652c0dad260bacb62a75550cdda"
TASK055J_FINAL_SEAL_HASH = "ecb95537625014a0e98e34ffc8e15a30c36c537db511c7e2d5444ce3322e2aee"
TASK055J_RUNTIME_HASH = "4681efc9e58d0e2db5458f7a62d284355ebe94d588d337df0eb69825d4263cf9"
TASK055J_AUTHORIZATION_HASH = "d88936e5b14685052d9cb7d78770b039be2c896199b3012aada58f48cc40261b"
TASK055J_REHEARSAL_HASH = "e6059d433853984b87404badaec41878b17b1f789dacec31cbeb9f581a3a3645"
TASK055J_REHEARSAL_VERIFICATION_HASH = "1e34d24915ad454e055b619f0d0ad4b9253d54fc79b3ed46d81279d4d117971c"
TASK055J_REPORT_HASH = "3c04689265d3c8ef0e300901c1ea8c78596a57742a6dbcf16711f7a5e34ad650"
TASK055J_FINAL_VERIFICATION_HASH = "f8468d6d9a12e7c0552d1ec1f4adfa690fe74fd7acc75f20c9d0c0f289d74501"
TASK055J_RELATIVE_ROOT = "validation_runs/task_055_j_20260718"
TASK055J_AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055j_single_canary_v1"

# Task 055-KR is an in-place Task 055-K correction, but it must never mutate the
# historical 2026-07-19 generations or authority journals.
HISTORICAL_TASK055K_RELATIVE_ROOT = "validation_runs/task_055_k_20260719"
HISTORICAL_TASK055K_AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055k_single_canary_v1"
TASK055K_RELATIVE_ROOT = "validation_runs/task_055_k_kr_20260723"
TASK055K_AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055k_single_canary_v2"
MAX_DATE = "20260630"

HISTORICAL_READY_EVIDENCE = {
    "report_content_hash": "26c651354a536386b59b87e652f5d02cb5c8df1ffb241f1daef4eb855d2afa88",
    "final_verification_content_hash": "bc625a1dfa5ea49ddf28095c20971f471006d7b796a7852afebe6e446f096fad",
    "candidate_checkpoint_content_hash": "0c7d0d3a7e79d669bb6a4067bb74fc521a403fda1974356a22a44cc0908471e5",
    "scrubbed_evidence_content_hash": "9efe85ef534ab9ec6cc7a946b0e2e0603805b8777dd4afc27df1c74aec331807",
}

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
MAX_CREDENTIAL_READS = 1
EXPECTED_ORDERED_KEY_ROOT = "5aa5ebbe225c4093ce6b76f8359c34e3cde4a6e3d3fd88ba3ee1f53ebfd92e6f"

READY_STATUS = "task055k_single_canary_engineering_ready_waiting_operator_authorization_no_network_executed"
BLOCKED_STATUS = "task055k_single_canary_correctness_closure_blocked"
CHECKPOINT_STATUS = "sealed_candidate_waiting_operator_authorization"
FINAL_SEAL_STATUS = "engineering_candidate_waiting_operator_authorization"

SOURCE_SCHEMA = "task055kr_git_index_source_seal_v2"
PARENT_VERIFICATION_SCHEMA = "task055kr_task055j_parent_verification_v2"
SUPERSESSION_SCHEMA = "task055kr_historical_ready_supersession_v1"
CANDIDATE_AUTHORITY_SCHEMA = "task055kr_candidate_runtime_authority_v2"
CANDIDATE_SEAL_SCHEMA = "task055kr_candidate_checkpoint_v2"
FINAL_CANDIDATE_SEAL_SCHEMA = "task055kr_final_candidate_seal_v1"
OPERATOR_AUTHORIZATION_SCHEMA = "task055kr_operator_single_canary_authorization_v1"
ATTEMPT_RESERVATION_SCHEMA = "task055kr_single_attempt_reservation_v2"
TRANSPORT_RECEIPT_SCHEMA = "task055kr_signed_transport_receipt_v2"
ACCEPTANCE_SCHEMA = "task055kr_canary_acceptance_v2"
APPLICATION_STAGE_SCHEMA = "task055kr_application_stage_v2"
APPLICATION_JOURNAL_SCHEMA = "task055kr_application_stage_journal_v2"
APPLICATION_SCHEMA = "task055kr_staged_response_application_v2"
FEE_REPLAY_SCHEMA = "task055kr_fee_aware_replay_v1"
REHEARSAL_SCHEMA = "task055kr_native_rehearsal_v2"
REHEARSAL_VERIFICATION_SCHEMA = "task055kr_native_rehearsal_verification_v2"
FINAL_REPORT_SCHEMA = "task055kr_engineering_report_v2"
FINAL_VERIFICATION_SCHEMA = "task055kr_independent_final_verification_v2"
SCRUBBED_SCHEMA = "task055kr_scrubbed_candidate_evidence_v2"
GIT_ATTESTATION_SCHEMA = "task055kr_git_evidence_attestation_v1"

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

EXECUTION_LINEAGE_ROLES = (
    "source_seal",
    "parent_verification",
    "candidate_authority",
    "candidate_checkpoint",
    "final_report",
    "final_verification",
    "final_candidate_seal",
)

ENGINEERING_VALIDATION_ROLES = (
    "native_rehearsal",
    "rehearsal_independent_verification",
    "positive_primary_application",
    "positive_sibling_application",
    "positive_independent_verification",
    "empty_primary_application",
    "empty_sibling_application",
    "empty_independent_verification",
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

ENGINEERING_WARNINGS = (
    "external_worm_or_monotonic_counter_unavailable",
    "operational_state_unproven",
)
