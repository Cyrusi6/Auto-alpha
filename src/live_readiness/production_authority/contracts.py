from __future__ import annotations

BASELINE_COMMIT = "8690dd749ddcf1f40ec2c745bca1b6968af2b3e8"
PARENT_TASK055I_RELATIVE_ROOT = "validation_runs/task_055_i_20260717"
PARENT_RUNTIME_AUTHORITY_HASH = "faa134dd6527321ca33d872abc5821c1b648f77963f16b5ec9e448dd65accb57"
PARENT_EXECUTION_AUTHORIZATION_HASH = "5ff8226d9fcbc475c0c6970d7d1d94cd16bfac3f27ee02ea47009b2666e1d5bb"
PARENT_REPORT_HASH = "a0ee66a4bd78b067c65e5ec078525919132bd0fd69af2b9da6f1e767ee25fc5d"
PARENT_FINAL_VERIFICATION_HASH = "e2e4eccdaea442c4f60138f2ac00bdc7cb4256b92061984d889a0469e6906f24"
PARENT_AUTHORIZATION_SEAL_HASH = "6c32e777374319026c1db23b10686bf9c245595b170a76f8e29e2f8259ca9b72"
PARENT_CANARY_PLAN_HASH = "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae"

TASK055J_RELATIVE_ROOT = "validation_runs/task_055_j_20260718"
AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055j_single_canary_v1"
MAX_DATE = "20260630"

CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vol",
        "amount",
    ],
    "transport_hash": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_hash": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}

MAX_UNIQUE_SECURITY_DATES = 64
MAX_LOGICAL_REQUESTS = 128
MAX_PHYSICAL_ATTEMPTS = 160

READY_STATUS = "task055j_single_canary_production_closure_ready_no_network_executed"
BLOCKED_STATUS = "task055j_single_canary_production_closure_blocked_no_network_executed"

SOURCE_TREE_SCHEMA = "task055j_source_tree_seal_v1"
APPLICATION_TREE_SCHEMA = "task055j_application_artifact_tree_seal_v1"
APPLICATION_PREFLIGHT_SCHEMA = "task055j_production_application_preflight_v1"
AUTHORITY_SCHEMA = "task055j_runtime_network_authority_v1"
AUTHORIZATION_SCHEMA = "task055j_execution_authorization_v1"
FINAL_EXECUTION_SEAL_SCHEMA = "task055j_final_execution_seal_v1"
TRANSPORT_RECEIPT_SCHEMA = "task055j_executor_transport_receipt_v1"
EXECUTION_SCHEMA = "task055j_single_canary_execution_v1"
ACCEPTANCE_SCHEMA = "task055j_single_canary_acceptance_v1"
TRUTH_SUCCESSOR_SCHEMA = "task055j_truth_successor_v1"
CAUSAL_REPLAY_SCHEMA = "task055j_fee_aware_exact20_x5_replay_v1"
APPLICATION_SCHEMA = "task055j_native_response_application_v1"
REHEARSAL_SCHEMA = "task055j_native_application_rehearsal_v1"
REHEARSAL_VERIFICATION_SCHEMA = "task055j_rehearsal_independent_verification_v1"
FINAL_REPORT_SCHEMA = "task055j_engineering_report_v1"
FINAL_VERIFICATION_SCHEMA = "task055j_independent_final_verification_v1"
SCRUBBED_EVIDENCE_SCHEMA = "task055j_scrubbed_execution_evidence_v1"

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

RUNTIME_SOURCE_SUFFIXES = (".py", ".toml", ".lock", ".yml", ".yaml")
RUNTIME_SOURCE_FILES = {
    "requirements.txt",
    "requirements-optional.txt",
    "environment.yml",
    ".env.example",
}
EVIDENCE_ONLY_PATHS = (
    "README.md",
    "CATREADME.md",
    "FRAMEWORK_UPDATE.md",
    "evidence/task_055_j/",
)
