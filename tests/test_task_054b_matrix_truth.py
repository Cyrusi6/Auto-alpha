from __future__ import annotations

import torch

from matrix_store.strict_engineering import StrictEngineeringPITMatrixBuilder, StrictEngineeringPITMatrixConfig
from tests.test_task_053a_matrix import _fixture
from validation_lab.metrics import evaluate_factor_splits
from validation_lab.models import ValidationSplit
from validation_lab.policy import EngineeringRobustnessPolicy


def test_strict_matrix_preserves_diagnostic_target_but_separates_research_common_cells(tmp_path):
    dates, codes, universe, freeze, _, _ = _fixture(tmp_path)
    result = StrictEngineeringPITMatrixBuilder(
        StrictEngineeringPITMatrixConfig(
            min_cross_section_breadth=30,
            research_observable_cutoff=dates[2],
        )
    ).build(
        governed_freeze_dir=freeze.generation_dir,
        historical_universe_dir=universe.generation_dir,
        output_root=tmp_path / "matrix",
    )
    import json
    from pathlib import Path
    import numpy as np

    root = Path(result.generation_dir)
    manifest = json.loads((root / "task_052a_strict_matrix_manifest.json").read_text())
    target_available = np.load(root / "target_available.npy", allow_pickle=False)
    signal_cells = np.load(root / "signal_candidate_cells.npy", allow_pickle=False)
    common_cells = np.load(root / "validation_common_cells.npy", allow_pickle=False)
    stock = codes.index("S001")
    assert target_available[stock, 2]
    assert signal_cells[stock, 2]
    assert not common_cells[stock, 2]
    assert manifest["research_eligibility_contract_applied"] is True
    assert manifest["research_firewall_attestation_required"] is True
    assert "research_holdout_firewall_enabled" not in manifest
    assert "firewall_out_of_bounds_access_count" not in manifest


def test_validation_coverage_uses_common_cell_denominator_not_full_stock_axis():
    factors = torch.tensor([[1.0], [2.0], [999.0], [999.0]])
    target = torch.tensor([[0.1], [0.2], [0.0], [0.0]])
    validity = torch.tensor([[True], [True], [False], [False]])
    common = torch.tensor([[True], [True], [False], [False]])
    split = ValidationSplit("s", "rolling", [], [], ["20240102"])
    policy = EngineeringRobustnessPolicy(
        policy_id="coverage-test",
        min_cross_section_breadth=2,
        min_oos_dates=0,
        min_valid_oos_ratio=0,
        min_valid_oos_dates=0,
        min_evaluable_windows=0,
        min_cumulative_oos_dates=0,
        min_standard_deviation=-1,
        min_mean_rank_ic=-1,
        min_mean_icir=-1e9,
        min_window_pass_ratio=0,
        max_train_test_decay=1e9,
    )
    results, _, _ = evaluate_factor_splits(
        factors,
        target,
        ["20240102"],
        [split],
        "factor",
        validity=validity,
        validation_common_mask=common,
        eligible_date_mask=torch.tensor([True]),
        policy=policy,
    )
    assert results[0].test_metrics["coverage_mean"] == 1.0
    assert results[0].test_metrics["n_observations"] == 2.0
