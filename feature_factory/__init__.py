"""Versioned feature set factory for A-share research."""

from .builder import build_feature_tensor, build_feature_tensor_artifacts, load_feature_manifest
from .catalog import FEATURE_SET_V1, FEATURE_SET_V2, FEATURE_SET_V3, build_feature_set_manifest, get_feature_definitions
from .models import FeatureDefinition, FeatureFamily, FeatureSetManifest, FeatureTensorBuildResult
from .readiness import FEATURE_FAMILY_POLICIES, build_feature_readiness_catalog
from .vocab_adapter import FeatureSetFormulaVocab, make_formula_vocab, make_formula_vocab_from_manifest
from .validity import build_feature_values_and_validity

__all__ = [
    "FEATURE_SET_V1",
    "FEATURE_SET_V2",
    "FEATURE_SET_V3",
    "FeatureDefinition",
    "FeatureFamily",
    "FeatureSetFormulaVocab",
    "FeatureSetManifest",
    "FeatureTensorBuildResult",
    "FEATURE_FAMILY_POLICIES",
    "build_feature_readiness_catalog",
    "build_feature_set_manifest",
    "build_feature_tensor",
    "build_feature_tensor_artifacts",
    "build_feature_values_and_validity",
    "get_feature_definitions",
    "load_feature_manifest",
    "make_formula_vocab",
    "make_formula_vocab_from_manifest",
]
