import json

import pytest

from model_core.vm import StackVM
from research.candidates import default_candidates, load_candidates_json, save_candidates_json


def test_default_candidates_are_valid_stack_formulas():
    candidates = default_candidates()
    vm = StackVM()

    assert len(candidates) >= 12
    assert {candidate.name for candidate in candidates} >= {
        "ret_1d",
        "ret_5d",
        "turnover_rate",
        "roe",
        "rank_roe",
    }
    assert all(vm.validate(candidate.formula_tokens) for candidate in candidates)


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
