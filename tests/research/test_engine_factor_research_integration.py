import json

import torch

from auto_alpha.data.ingestion.pipeline.ashare import AShareDataConfig, AShareDataManager, LocalAshareStorage
from auto_alpha.research.factors.engine_correlation import factor_values_to_matrix
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.formulas import runtime_engine as engine
from auto_alpha.data.pit.universe import UniverseBuildConfig, build_universe_from_storage


def prepare_data_with_universe(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    build_universe_from_storage(
        LocalAshareStorage(data_dir),
        UniverseBuildConfig(
            universe_name="all_a_sample",
            as_of_date="20240104",
            min_listed_days=0,
            min_amount=0,
        ),
    )
    return data_dir


def test_engine_dry_run_register_with_transform_gate_and_universe(tmp_path, capsys):
    data_dir = prepare_data_with_universe(tmp_path)
    store_dir = tmp_path / "store"
    report_dir = tmp_path / "reports"

    result = engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--universe-name",
            "all_a_sample",
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(report_dir),
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--min-coverage",
            "0.5",
            "--correlation-threshold",
            "0.99",
            "--pretty",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    factor_record = LocalFactorStore(store_dir).load_factors()[-1]

    assert result == 0
    assert payload["n_stocks"] == 3
    assert payload["universe_name"] == "all_a_sample"
    assert payload["transform_method"] == "winsorize_zscore"
    assert payload["gate_decision"]["status"] == "research_rejected"
    assert payload["status"] == "research_rejected"
    assert factor_record.transform_method == "winsorize_zscore"
    assert factor_record.gate_status == "research_rejected"
    assert factor_record.metadata["max_abs_correlation"] == payload["max_abs_correlation"]
    assert json.loads((report_dir / "factor_report.json").read_text(encoding="utf-8"))["status"] == "research_rejected"

    values = LocalFactorStore(store_dir).load_factor_values(payload["factor_id"])
    matrix = factor_values_to_matrix(values, ["000001.SZ", "600000.SH", "830000.BJ"], ["20240102", "20240103", "20240104"])
    assert torch.isfinite(matrix).all()
    assert torch.allclose(matrix.mean(dim=0), torch.zeros(3), atol=1e-5)


def test_engine_disable_gate_keeps_candidate_status(tmp_path, capsys):
    data_dir = prepare_data_with_universe(tmp_path)
    store_dir = tmp_path / "store"

    result = engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
            "--factor-transform",
            "zscore",
            "--disable-gate",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    factor_record = LocalFactorStore(store_dir).load_factors()[-1]

    assert result == 0
    assert payload["status"] == "research_evaluated"
    assert payload["gate_decision"] is None
    assert factor_record.status == "research_evaluated"


def test_engine_training_registers_with_transform_and_gate(tmp_path, capsys):
    data_dir = prepare_data_with_universe(tmp_path)
    store_dir = tmp_path / "store"

    result = engine.main(
        [
            "--steps",
            "2",
            "--batch-size",
            "3",
            "--data-dir",
            str(data_dir),
            "--universe-name",
            "all_a_sample",
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--min-coverage",
            "0.5",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["factor_id"].startswith("factor_")
    assert payload["transform_method"] == "winsorize_zscore"
    assert payload["gate_decision"]["passed"] is False
    assert payload["status"] == "research_rejected"
    assert (tmp_path / "out" / "best_factor_formula.json").exists()
