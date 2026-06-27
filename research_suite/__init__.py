"""One-click A-share research suite orchestration."""

from .catalog import load_artifact_catalog, register_artifact, write_artifact_catalog
from .models import (
    ArtifactCatalog,
    ArtifactEntry,
    PromotionConfig,
    PromotionDecision,
    ResearchSuiteConfig,
    ResearchSuiteResult,
    SuiteStageResult,
    WalkForwardResult,
    WalkForwardWindow,
)
from .promotion import promote_factor_if_eligible
from .walk_forward import build_walk_forward_windows, evaluate_factor_walk_forward, summarize_walk_forward
from .workflow import ResearchSuiteRunner

__all__ = [
    "ArtifactCatalog",
    "ArtifactEntry",
    "PromotionConfig",
    "PromotionDecision",
    "ResearchSuiteConfig",
    "ResearchSuiteResult",
    "ResearchSuiteRunner",
    "SuiteStageResult",
    "WalkForwardResult",
    "WalkForwardWindow",
    "build_walk_forward_windows",
    "evaluate_factor_walk_forward",
    "load_artifact_catalog",
    "promote_factor_if_eligible",
    "register_artifact",
    "summarize_walk_forward",
    "write_artifact_catalog",
]
