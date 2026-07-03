"""Optional formula vocabulary helpers for versioned feature sets."""

from __future__ import annotations

from model_core.ops import OPS_CONFIG
from model_core.vocab import FORMULA_VOCAB, FormulaVocab

from .models import FeatureSetManifest


def make_formula_vocab(
    feature_names: list[str] | tuple[str, ...] | None = None,
    operator_names: list[str] | tuple[str, ...] | None = None,
) -> FormulaVocab:
    return FormulaVocab(
        feature_names=tuple(feature_names or FORMULA_VOCAB.feature_names),
        operator_names=tuple(operator_names or tuple(cfg[0] for cfg in OPS_CONFIG)),
    )


class FeatureSetFormulaVocab(FormulaVocab):
    pass


def make_formula_vocab_from_manifest(manifest: FeatureSetManifest | dict) -> FormulaVocab:
    payload = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest)
    feature_definitions = payload.get("feature_definitions", [])
    feature_names = [
        str(item.get("feature_name"))
        for item in feature_definitions
        if isinstance(item, dict) and item.get("feature_name")
    ]
    return make_formula_vocab(feature_names=feature_names)
