"""Optional formula vocabulary helpers for versioned feature sets."""

from __future__ import annotations

from model_core.ops import OPS_CONFIG
from model_core.vocab import FORMULA_VOCAB, FormulaVocab


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
