"""Canonical Task 054-C production evidence protocol."""
from __future__ import annotations
from dataclasses import dataclass

TASK_ID = "task_054_c"
EVIDENCE_SCOPE = "real_production"
PROTOCOL_VERSION = "task054c_production_protocol_v1"
READ_LEDGER_SCHEMA = "task054c_supervisor_read_ledger_v1"
RECEIPT_SCHEMA = "task054c_component_receipt_v1"
SENTINEL_SCHEMA = "task054c_production_sentinel_v1"
RUN_MUTATIONS = ("baseline", "post_cutoff", "inside_cutoff")
RUN_PATHS = ("raw_local", "raw_scheduler", "matrix_local", "matrix_scheduler")
RAW_COMPONENTS = (
    "strict_matrix_builder", "v3_tensor_builder", "research_projection_publisher",
    "ashare_data_loader", "stackvm", "proxy_evaluator", "formula_batch_evaluator",
    "factor_materializer", "validation_service", "campaign_store", "consolidation",
)
MATRIX_COMPONENTS = (
    "strict_matrix_validator", "v3_tensor_validator", "research_projection_publisher",
    "ashare_data_loader", "stackvm", "proxy_evaluator", "formula_batch_evaluator",
    "factor_materializer", "validation_service", "campaign_store", "consolidation",
)
TERMINAL_STATUS = "task054c_engineering_baseline_completed_historical_selection_contaminated_certification_blocked"
BLOCKED_STATUS = "task054c_engineering_baseline_blocked"

@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    manifest_path: str
    content_hash: str
    manifest_sha256: str

@dataclass(frozen=True)
class RunIdentity:
    mutation: str
    source_kind: str
    execution_kind: str
    invocation_id: str

    @property
    def path_name(self) -> str:
        return f"{self.source_kind}_{self.execution_kind}"
