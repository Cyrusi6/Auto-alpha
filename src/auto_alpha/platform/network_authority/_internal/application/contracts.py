from __future__ import annotations

BASELINE_COMMIT = "0b9cbf6a9ff49061361fb2e7b1aaa19045610ffd"
PARENT_AUTHORIZATION_SEAL_HASH = "6c32e777374319026c1db23b10686bf9c245595b170a76f8e29e2f8259ca9b72"
PARENT_GIT_EVIDENCE_HASH = "2ef732ecb20eebcbf0dede46a058cb5e1730ea2bea94a98f02afac9d09b2fa20"
PARENT_CANARY_PLAN_HASH = "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae"
PARENT_TASK055H_RELATIVE_ROOT = "validation_runs/task_055_h_20260717"
TASK055I_RELATIVE_ROOT = "validation_runs/task_055_i_20260717"
GLOBAL_AUTHORITY_RELATIVE_ROOT = "governance/network_authority/task055h_single_canary_v1"
MAX_DATE = "20260630"

CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
    "transport_hash": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_hash": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}

MAX_UNIQUE_SECURITY_DATES = 64
MAX_LOGICAL_REQUESTS = 128
MAX_PHYSICAL_ATTEMPTS = 160

RUNTIME_AUTHORITY_SCHEMA = "task055i_global_network_authority_v1"
EXECUTION_AUTHORIZATION_SCHEMA = "task055i_single_canary_execution_authorization_v1"
CANARY_EXECUTION_SCHEMA = "task055i_single_canary_execution_v1"
CANARY_ACCEPTANCE_SCHEMA = "task055i_single_canary_acceptance_v1"
RESPONSE_APPLICATION_SCHEMA = "task055i_native_response_application_v1"
REHEARSAL_SCHEMA = "task055i_native_application_rehearsal_v1"
SCRUBBED_EVIDENCE_SCHEMA = "task055i_scrubbed_execution_authorization_v1"
FINAL_REPORT_SCHEMA = "task055i_engineering_report_v1"
FINAL_VERIFICATION_SCHEMA = "task055i_independent_final_verification_v1"

READY_STATUS = "single_canary_execution_ready_no_network_executed"
BLOCKED_STATUS = "task055i_single_canary_execution_blocked_no_network_executed"

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

SEMANTIC_SOURCE_PATHS = (
    "src/data_pipeline/ashare/cache.py",
    "src/data_pipeline/ashare/config.py",
    "src/data_pipeline/ashare/providers/tushare_client.py",
    "src/data_pipeline/ashare/request_normalization.py",
    "src/data_pipeline/ashare/security.py",
    "src/data_lake/task052_freeze.py",
    "src/matrix_store/strict_engineering.py",
    "src/feature_factory/engineering_replay/orchestrator.py",
    "src/research_firewall/production_sentinel/sentinel.py",
    "src/auto_alpha/platform/network_authority/_internal/simulation/policy.py",
    "src/auto_alpha/platform/network_authority/_internal/simulation/simulator.py",
    "src/auto_alpha/platform/network_authority/_internal/evidence/transport.py",
    "src/auto_alpha/platform/network_authority/_internal/validation/causal.py",
    "src/auto_alpha/platform/network_authority/_internal/validation/fees.py",
    "src/auto_alpha/platform/network_authority/_internal/validation/network_state.py",
    "src/auto_alpha/platform/network_authority/_internal/validation/truth.py",
    "src/auto_alpha/platform/network_authority/_internal/authorization/application.py",
    "src/auto_alpha/platform/network_authority/_internal/authorization/authorization.py",
    "src/auto_alpha/platform/network_authority/_internal/authorization/fee.py",
    "src/auto_alpha/platform/network_authority/_internal/authorization/independent.py",
    "src/auto_alpha/platform/network_authority/_internal/authorization/network.py",
    "src/auto_alpha/platform/network_authority/_internal/application/application.py",
    "src/auto_alpha/platform/network_authority/_internal/application/authority.py",
    "src/auto_alpha/platform/network_authority/_internal/application/contracts.py",
    "src/auto_alpha/platform/network_authority/_internal/application/executor.py",
    "src/auto_alpha/platform/network_authority/_internal/application/ledger.py",
    "src/auto_alpha/platform/network_authority/_internal/application/network_cli.py",
    "src/auto_alpha/platform/network_authority/_internal/application/rehearsal.py",
    "src/auto_alpha/platform/network_authority/_internal/application/run.py",
    "src/auto_alpha/platform/network_authority/_internal/application/verifier.py",
)
