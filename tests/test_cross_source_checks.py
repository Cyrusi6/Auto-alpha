import json
import shutil

from cross_source_checks import compare_data_dirs
from cross_source_checks.run_compare import main as compare_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager


def test_cross_source_compare_identical_data_dirs(tmp_path, capsys):
    left = tmp_path / "left"
    right = tmp_path / "right"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=left)).sync(validate=True)
    shutil.copytree(left, right)

    report = compare_data_dirs(left, right, ["daily_bars", "daily_basic", "daily_limits"])
    assert not report.has_differences
    assert all(item.numeric_field_max_abs_diff == 0 for item in report.datasets)

    exit_code = compare_main(
        [
            "--left-data-dir",
            str(left),
            "--right-data-dir",
            str(right),
            "--output-dir",
            str(tmp_path / "cross"),
            "--datasets",
            "daily_bars,daily_basic",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["has_differences"] is False
    assert (tmp_path / "cross" / "cross_source_report.json").exists()


def test_cross_source_compare_detects_numeric_and_missing_diffs(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=left)).sync(validate=True)
    shutil.copytree(left, right)

    path = right / "daily_bars" / "records.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records[0]["close"] = float(records[0]["close"]) + 1.23
    records = records[:-1]
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n", encoding="utf-8")

    report = compare_data_dirs(left, right, ["daily_bars"])
    diff = report.datasets[0]
    assert report.has_differences
    assert diff.numeric_field_max_abs_diff > 0
    assert diff.missing_keys_right == 1
