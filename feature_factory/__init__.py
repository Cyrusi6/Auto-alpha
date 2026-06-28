"""Versioned feature set factory for A-share research."""

from .builder import build_feature_tensor, build_feature_tensor_artifacts, load_feature_manifest
from .catalog import FEATURE_SET_V1, FEATURE_SET_V2, build_feature_set_manifest, get_feature_definitions
from .models import FeatureDefinition, FeatureFamily, FeatureSetManifest, FeatureTensorBuildResult
from .vocab_adapter import FeatureSetFormulaVocab, make_formula_vocab

__all__ = [
    "FEATURE_SET_V1",
    "FEATURE_SET_V2",
    "FeatureDefinition",
    "FeatureFamily",
    "FeatureSetFormulaVocab",
    "FeatureSetManifest",
    "FeatureTensorBuildResult",
    "build_feature_set_manifest",
    "build_feature_tensor",
    "build_feature_tensor_artifacts",
    "get_feature_definitions",
    "load_feature_manifest",
    "make_formula_vocab",
]
