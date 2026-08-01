"""Formula mutation and crossover utilities."""

from __future__ import annotations

import random

from model_core.ops import get_operator_spec
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from feature_factory.semantics import FeatureSemantics

from .generator import _make_candidate
from .models import FormulaCandidate, FormulaSearchConfig


def mutate_formula(
    candidate: FormulaCandidate,
    rng: random.Random,
    config: FormulaSearchConfig,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> FormulaCandidate:
    strategies = [_replace_feature, _replace_operator_same_arity, _insert_unary, _combine_with_feature]
    for _ in range(100):
        tokens = rng.choice(strategies)(candidate.formula_tokens, rng)
        tokens = simplify_formula(tokens)
        result = _make_candidate(tokens, "mutation", [candidate.formula_hash], candidate.generation + 1, feature_semantics=feature_semantics)
        if _valid_candidate(result, config):
            return result
    return candidate


def crossover_formula(
    parent_a: FormulaCandidate,
    parent_b: FormulaCandidate,
    rng: random.Random,
    config: FormulaSearchConfig,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> FormulaCandidate:
    binary_ops = _operator_tokens(arity=2, max_lookback=config.max_lookback)
    for _ in range(100):
        tokens = list(parent_a.formula_tokens) + list(parent_b.formula_tokens) + [rng.choice(binary_ops)]
        tokens = simplify_formula(tokens)
        result = _make_candidate(
            tokens,
            "crossover",
            [parent_a.formula_hash, parent_b.formula_hash],
            max(parent_a.generation, parent_b.generation) + 1,
            feature_semantics=feature_semantics,
        )
        if _valid_candidate(result, config):
            return result
    return parent_a


def simplify_formula(tokens: list[int]) -> list[int]:
    vm = StackVM()
    simplified = [int(token) for token in tokens]
    while len(simplified) >= 2 and simplified[-1] == simplified[-2] == FORMULA_VOCAB.encode_name("WINSORIZE"):
        simplified.pop()
    valid, _reason = vm.validate_with_reason(simplified)
    return simplified if valid else [FORMULA_VOCAB.encode_name("RET_1D"), FORMULA_VOCAB.encode_name("CS_ZSCORE")]


def _replace_feature(tokens: list[int], rng: random.Random) -> list[int]:
    result = list(tokens)
    feature_positions = [idx for idx, token in enumerate(result) if 0 <= int(token) < FORMULA_VOCAB.feature_count]
    if not feature_positions:
        return result
    result[rng.choice(feature_positions)] = rng.randrange(FORMULA_VOCAB.feature_count)
    return result


def _replace_operator_same_arity(tokens: list[int], rng: random.Random) -> list[int]:
    result = list(tokens)
    op_positions = [idx for idx, token in enumerate(result) if int(token) >= FORMULA_VOCAB.operator_offset]
    if not op_positions:
        return result
    position = rng.choice(op_positions)
    spec = get_operator_spec(result[position], FORMULA_VOCAB.operator_offset)
    result[position] = rng.choice(_operator_tokens(spec.arity))
    return result


def _insert_unary(tokens: list[int], rng: random.Random) -> list[int]:
    return list(tokens) + [rng.choice(_operator_tokens(arity=1))]


def _combine_with_feature(tokens: list[int], rng: random.Random) -> list[int]:
    return list(tokens) + [rng.randrange(FORMULA_VOCAB.feature_count), rng.choice(_operator_tokens(arity=2))]


def _operator_tokens(arity: int, max_lookback: int | None = None) -> list[int]:
    candidates: list[int] = []
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        spec = get_operator_spec(token, FORMULA_VOCAB.operator_offset)
        if spec.arity == arity and (max_lookback is None or spec.lookback <= max_lookback):
            candidates.append(token)
    return candidates


def _valid_candidate(candidate: FormulaCandidate, config: FormulaSearchConfig) -> bool:
    valid, _reason = StackVM().validate_with_reason(candidate.formula_tokens)
    return (
        valid
        and len(candidate.formula_tokens) <= config.max_formula_len
        and candidate.complexity <= config.max_complexity
        and candidate.lookback <= config.max_lookback
    )
