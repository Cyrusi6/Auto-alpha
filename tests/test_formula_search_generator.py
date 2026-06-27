from formula_search.generator import generate_initial_population
from formula_search.models import FormulaSearchConfig
from model_core.vm import StackVM


def test_initial_population_is_reproducible_and_valid():
    config = FormulaSearchConfig(seed=7, population_size=10, max_formula_len=8, max_complexity=24, max_lookback=10)
    first = generate_initial_population(config)
    second = generate_initial_population(config)
    vm = StackVM()

    assert [item.formula_hash for item in first] == [item.formula_hash for item in second]
    assert len(first) == 10
    assert all(vm.validate(item.formula_tokens) for item in first)
    assert all(len(item.formula_tokens) <= config.max_formula_len for item in first)
    assert all(item.complexity <= config.max_complexity for item in first)
    assert all(item.lookback <= config.max_lookback for item in first)
    assert len({tuple(item.formula_names) for item in first}) == len(first)
    assert any(len(item.formula_tokens) > 1 for item in first)


def test_population_respects_tight_lookback_limit():
    config = FormulaSearchConfig(seed=3, population_size=5, max_formula_len=5, max_complexity=12, max_lookback=5)
    population = generate_initial_population(config)

    assert population
    assert all(item.lookback <= 5 for item in population)
