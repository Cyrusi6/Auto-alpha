from __future__ import annotations

from task_055_f.truth_v2 import _classify_cell


def _cell(**overrides):
    base = {
        "ts_code": "600000.SH",
        "trade_date": "20240102",
        "bar_observed": True,
        "lifecycle": {"listed": True, "active": True},
        "corporate_action_validity": True,
        "valuation_closure_domain": True,
    }
    base.update(overrides)
    return base


def _bar():
    return {"complete": True, "values": {}, "validity": {}, "axis_present": True, "row_hash": "x"}


def test_complete_bar_plus_resume_is_normal_trading():
    row = _classify_cell(
        _cell(),
        _bar(),
        [{"suspend_type": "R", "suspend_timing": None, "row_hash": "r"}],
        [],
        [],
    )
    assert row["state"] == "TRADED_PRIMARY_BAR"


def test_complete_bar_plus_suspend_is_conflict():
    row = _classify_cell(
        _cell(),
        _bar(),
        [{"suspend_type": "S", "suspend_timing": None, "row_hash": "s"}],
        [],
        [],
    )
    assert row["state"] == "MATRIX_SOURCE_CONFLICT"


def test_complete_bar_lifecycle_conflict_is_not_swallowed_as_terminated():
    row = _classify_cell(
        _cell(lifecycle={"listed": False, "active": False}),
        _bar(),
        [],
        [],
        [],
    )
    assert row["state"] == "MATRIX_SOURCE_CONFLICT"
