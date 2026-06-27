import random

from formula_search.generator import generate_initial_population
from formula_search.models import FormulaSearchConfig
from formula_search.mutation import crossover_formula, mutate_formula, simplify_formula
from model_core.vm import StackVM


def test_mutation_produces_valid_formula_with_parent_hash():
    config = FormulaSearchConfig(seed=11, population_size=4, max_formula_len=8, max_complexity=24, max_lookback=10)
    parent = generate_initial_population(config)[0]
    mutated = mutate_formula(parent, random.Random(11), config)

    assert StackVM().validate(mutated.formula_tokens)
    assert parent.formula_hash in mutated.parent_hashes
    assert mutated.generation == parent.generation + 1


def test_crossover_produces_valid_formula_with_two_parents():
    config = FormulaSearchConfig(seed=12, population_size=4, max_formula_len=8, max_complexity=24, max_lookback=10)
    left, right = generate_initial_population(config)[:2]
    child = crossover_formula(left, right, random.Random(12), config)

    assert StackVM().validate(child.formula_tokens)
    assert child.parent_hashes == [left.formula_hash, right.formula_hash]


def test_simplify_preserves_legal_formula():
    formula = generate_initial_population(FormulaSearchConfig(seed=13, population_size=1))[0]
    simplified = simplify_formula(formula.formula_tokens)

    assert StackVM().validate(simplified)
