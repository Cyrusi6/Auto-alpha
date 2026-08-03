"""Versioned feature set factory for A-share auto_alpha.research.discovery.studies."""

from auto_alpha.research.features.factory_builder import build_feature_tensor
from auto_alpha.research.features.factory_builder import build_feature_tensor_artifacts
from auto_alpha.research.features.factory_builder import load_feature_manifest
from auto_alpha.research.features.factory_catalog import FEATURE_SET_V1
from auto_alpha.research.features.factory_catalog import FEATURE_SET_V2
from auto_alpha.research.features.factory_catalog import FEATURE_SET_V3
from auto_alpha.research.features.factory_catalog import build_feature_set_manifest
from auto_alpha.research.features.factory_catalog import get_feature_definitions
from auto_alpha.research.features.factory_contracts import build_feature_contract
from auto_alpha.research.features.factory_contracts import build_tensor_content_fingerprint
from auto_alpha.research.features.factory_contracts import contract_from_definition
from auto_alpha.research.features.factory_contracts import feature_semantic_source_hash
from auto_alpha.research.features.factory_contracts import intersect_candidate_feature_blockers
from auto_alpha.research.features.factory_models import FeatureDefinition
from auto_alpha.research.features.factory_models import FeatureFamily
from auto_alpha.research.features.factory_models import FeatureSetManifest
from auto_alpha.research.features.factory_models import FeatureTensorBuildResult
from auto_alpha.research.features.factory_readiness import FEATURE_FAMILY_POLICIES
from auto_alpha.research.features.factory_readiness import build_feature_readiness_catalog
from auto_alpha.research.features.factory_semantics import FeatureSemantics
from auto_alpha.research.features.factory_semantics import FormulaSemantics
from auto_alpha.research.features.factory_semantics import build_feature_semantics
from auto_alpha.research.features.factory_semantics import build_feature_semantics_map
from auto_alpha.research.features.factory_semantics import calculate_formula_semantics
from auto_alpha.research.features.factory_semantics import feature_semantics_contract_hash
from auto_alpha.research.features.factory_vocab_adapter import FeatureSetFormulaVocab
from auto_alpha.research.features.factory_vocab_adapter import make_formula_vocab
from auto_alpha.research.features.factory_vocab_adapter import make_formula_vocab_from_manifest
from auto_alpha.research.features.factory_validity import build_feature_values_and_validity

__all__ = [
    "FEATURE_SET_V1",
    "FEATURE_SET_V2",
    "FEATURE_SET_V3",
    "FeatureDefinition",
    "FeatureFamily",
    "FeatureSetFormulaVocab",
    "FeatureSetManifest",
    "FeatureSemantics",
    "FormulaSemantics",
    "FeatureTensorBuildResult",
    "FEATURE_FAMILY_POLICIES",
    "build_feature_readiness_catalog",
    "build_feature_contract",
    "build_feature_set_manifest",
    "build_feature_semantics",
    "build_feature_semantics_map",
    "build_feature_tensor",
    "build_feature_tensor_artifacts",
    "build_feature_values_and_validity",
    "build_tensor_content_fingerprint",
    "contract_from_definition",
    "feature_semantic_source_hash",
    "feature_semantics_contract_hash",
    "get_feature_definitions",
    "load_feature_manifest",
    "intersect_candidate_feature_blockers",
    "calculate_formula_semantics",
    "make_formula_vocab",
    "make_formula_vocab_from_manifest",
]
