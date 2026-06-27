"""Random formula generation for A-share formula search."""

from __future__ import annotations

import random
from collections.abc import Sequence

from factor_store import stable_formula_hash
from model_core.ops import get_operator_spec
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB

from .models import FormulaCandidate, FormulaSearchConfig


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


def generate_seed_formulas() -> list[FormulaCandidate]:
    names = [
        ["RET_1D", "CS_ZSCORE"],
        ["RET_5D", "TS_RANK5"],
        ["ROE", "CS_RANK"],
        ["REVENUE_YOY", "ROE", "ADD", "CS_ZSCORE"],
        ["RET_1D", "TURNOVER_RATE", "TS_CORR5"],
        ["LOG_AMOUNT", "DELTA5", "CS_ZSCORE"],
        ["PB", "NEG", "CS_RANK"],
        ["RET_5D", "PB", "SUB"],
    ]
    return [_make_candidate([FORMULA_VOCAB.encode_name(name) for name in formula], "seed", [], 0) for formula in names]


def generate_initial_population(config: FormulaSearchConfig) -> list[FormulaCandidate]:
    rng = random.Random(config.seed)
    population: list[FormulaCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in generate_seed_formulas():
        if _within_limits(candidate, config):
            _append_unique(population, seen, candidate)
        if len(population) >= config.population_size:
            return population
    attempts = 0
    while len(population) < config.population_size and attempts < config.population_size * 100:
        attempts += 1
        candidate = generate_random_formula(
            FORMULA_VOCAB,
            rng,
            config.max_formula_len,
            config.max_complexity,
            config.max_lookback,
        )
        _append_unique(population, seen, candidate)
    return population


def generate_random_formula(
    vocab,
    rng: random.Random,
    max_len: int,
    max_complexity: int,
    max_lookback: int,
) -> FormulaCandidate:
    vm = StackVM()
    features = list(range(vocab.feature_count))
    unary_ops = _operator_tokens(arity=1, max_lookback=max_lookback)
    binary_ops = _operator_tokens(arity=2, max_lookback=max_lookback)

    for _ in range(200):
        tokens = [rng.choice(features)]
        target_ops = 1 if max_len <= 3 else rng.randint(1, max(1, min(4, max_len // 2)))
        for _op_idx in range(target_ops):
            if rng.random() < 0.6 and len(tokens) + 1 <= max_len:
                tokens.append(rng.choice(unary_ops))
            elif len(tokens) + 2 <= max_len:
                tokens.extend([rng.choice(features), rng.choice(binary_ops)])
        valid, reason = vm.validate_with_reason(tokens)
        if not valid:
            continue
        candidate = _make_candidate(tokens, "random", [], 0)
        if len(candidate.formula_tokens) <= max_len and candidate.complexity <= max_complexity and candidate.lookback <= max_lookback:
            return candidate
    return _make_candidate([rng.choice(features), rng.choice(unary_ops)], "random", [], 0)


def _make_candidate(tokens: Sequence[int], source: str, parent_hashes: list[str], generation: int) -> FormulaCandidate:
    vm = StackVM()
    formula_tokens = [int(token) for token in tokens]
    names = vm.canonical_formula(formula_tokens)
    valid, reason = vm.validate_with_reason(formula_tokens)
    formula_hash = stable_formula_hash(formula_tokens, names, FEATURE_VERSION, OPERATOR_VERSION)
    return FormulaCandidate(
        formula_tokens=formula_tokens,
        formula_names=names,
        formula_hash=formula_hash,
        complexity=vm.formula_complexity(formula_tokens),
        lookback=vm.formula_lookback(formula_tokens),
        source=source,
        parent_hashes=list(parent_hashes),
        generation=int(generation),
        validation_reason=reason if valid else reason,
    )


def _operator_tokens(arity: int, max_lookback: int | None = None) -> list[int]:
    tokens: list[int] = []
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        spec = get_operator_spec(token, FORMULA_VOCAB.operator_offset)
        if spec.arity == arity and (max_lookback is None or spec.lookback <= max_lookback):
            tokens.append(token)
    return tokens


def _append_unique(population: list[FormulaCandidate], seen: set[tuple[str, ...]], candidate: FormulaCandidate) -> None:
    key = tuple(candidate.formula_names)
    if key in seen:
        return
    seen.add(key)
    population.append(candidate)


def _within_limits(candidate: FormulaCandidate, config: FormulaSearchConfig) -> bool:
    return (
        len(candidate.formula_tokens) <= config.max_formula_len
        and candidate.complexity <= config.max_complexity
        and candidate.lookback <= config.max_lookback
    )
