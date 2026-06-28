import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import LocalFactorStore
from leakage_audit.factor_audit import audit_factor_values
from leakage_audit.run_audit import main as leakage_main
from leakage_audit.static_analysis import scan_formula_leakage
from model_core import engine


def _prepare_factor_store(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    return data_dir, store_dir


def test_static_scan_blocks_forward_looking_formula_name():
    result = scan_formula_leakage(
        formulas=[
            {
                "name": "bad",
                "formula_tokens": [0],
                "formula_names": ["TARGET_RET"],
            }
        ]
    )

    assert result.blocked_formula_count == 1
    assert result.issues[0].severity == "blocker"


def test_factor_audit_detects_future_factor_values(tmp_path, capsys):
    data_dir, store_dir = _prepare_factor_store(tmp_path)
    capsys.readouterr()
    store = LocalFactorStore(store_dir)
    factor = store.load_latest_factor()
    values_path = store_dir / "factor_values" / f"{factor.factor_id}.jsonl"
    with values_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"factor_id": factor.factor_id, "trade_date": "20991231", "ts_code": "000001.SZ", "value": 1.0}) + "\n")

    result = audit_factor_values(store_dir, factor.factor_id, as_of_date="20240104")

    assert result.future_date_count == 1
    assert any(issue.code == "factor_value_after_as_of_date" for issue in result.issues)


def test_leakage_audit_cli_writes_reports(tmp_path, capsys):
    data_dir, store_dir = _prepare_factor_store(tmp_path)
    capsys.readouterr()
    output_dir = tmp_path / "leakage"

    rc = leakage_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(output_dir),
            "--as-of-date",
            "20240104",
            "--cutoff-date",
            "20240104",
            "--point-in-time",
            "--feature-cutoff-mode",
            "next_trade_day_open",
            "--run-static-scan",
            "--run-truncation-test",
            "--max-formulas",
            "3",
            "--pretty",
        ]
    )
    capsys.readouterr()
    report = json.loads((output_dir / "leakage_audit_report.json").read_text())

    assert rc == 0
    assert report["truncation_consistency"]["passed"] is True
    assert (output_dir / "leakage_issues.jsonl").exists()
    assert (output_dir / "active_security_mask.jsonl.schema.json").exists()
