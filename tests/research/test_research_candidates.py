import json

import pytest

from auto_alpha.research.formulas.runtime_vm import StackVM
from auto_alpha.research.discovery.studies_candidates import default_candidates
from auto_alpha.research.discovery.studies_candidates import load_candidates_json
from auto_alpha.research.discovery.studies_candidates import save_candidates_json


def test_default_candidates_are_valid_stack_formulas():
    candidates = default_candidates()
    vm = StackVM()

    assert len(candidates) >= 20
    assert {candidate.name for candidate in candidates} >= {
        "ret_1d",
        "ret_5d",
        "turnover_rate",
        "roe",
        "rank_roe",
        "corr5_ret_turnover",
        "growth_quality",
    }
    assert all(vm.validate(candidate.formula_tokens) for candidate in candidates)
    assert all(candidate.complexity is not None and candidate.lookback is not None for candidate in candidates)


def test_save_and_load_candidates_json_round_trip(tmp_path):
    path = tmp_path / "candidates.json"
    candidates = default_candidates()[:3]

    save_candidates_json(candidates, path)
    loaded = load_candidates_json(path)

    assert loaded == candidates


def test_load_candidates_json_rejects_invalid_token(tmp_path):
    path = tmp_path / "bad_candidates.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "bad",
                    "formula_tokens": [9999],
                    "description": "invalid",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid token"):
        load_candidates_json(path)
