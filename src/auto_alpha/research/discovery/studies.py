"""Batch A-share factor research orchestration."""

from auto_alpha.research.discovery.studies_batch_runner import BatchFactorResearchRunner
from auto_alpha.research.discovery.studies_candidates import default_candidates
from auto_alpha.research.discovery.studies_candidates import from_formula_search_candidates
from auto_alpha.research.discovery.studies_candidates import load_candidates_json
from auto_alpha.research.discovery.studies_candidates import save_candidates_json
from auto_alpha.research.discovery.studies_composite import build_composite_factor_matrix
from auto_alpha.research.discovery.studies_composite import register_composite_factor
from auto_alpha.research.discovery.studies_composite import select_approved_factors
from auto_alpha.research.discovery.studies_models import BatchResearchConfig
from auto_alpha.research.discovery.studies_models import BatchResearchResult
from auto_alpha.research.discovery.studies_models import CandidateRunResult
from auto_alpha.research.discovery.studies_models import FactorCandidate
from auto_alpha.research.discovery.studies_report import write_batch_report

__all__ = [
    "BatchFactorResearchRunner",
    "BatchResearchConfig",
    "BatchResearchResult",
    "CandidateRunResult",
    "FactorCandidate",
    "build_composite_factor_matrix",
    "default_candidates",
    "from_formula_search_candidates",
    "load_candidates_json",
    "register_composite_factor",
    "save_candidates_json",
    "select_approved_factors",
    "write_batch_report",
]
