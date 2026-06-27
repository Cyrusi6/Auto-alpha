"""Batch A-share factor research orchestration."""

from .batch_runner import BatchFactorResearchRunner
from .candidates import default_candidates, load_candidates_json, save_candidates_json
from .composite import (
    build_composite_factor_matrix,
    register_composite_factor,
    select_approved_factors,
)
from .models import BatchResearchConfig, BatchResearchResult, CandidateRunResult, FactorCandidate
from .report import write_batch_report

__all__ = [
    "BatchFactorResearchRunner",
    "BatchResearchConfig",
    "BatchResearchResult",
    "CandidateRunResult",
    "FactorCandidate",
    "build_composite_factor_matrix",
    "default_candidates",
    "load_candidates_json",
    "register_composite_factor",
    "save_candidates_json",
    "select_approved_factors",
    "write_batch_report",
]
