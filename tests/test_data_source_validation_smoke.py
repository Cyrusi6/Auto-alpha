import json
from pathlib import Path

from data_source_validation.run_smoke import main


ALL_DATASETS = "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions"


def test_sample_smoke_cli_writes_reports(tmp_path, capsys):
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "smoke"

    rc = main(
        [
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(out_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            ALL_DATASETS,
            "--index-codes",
            "000300.SH",
            "--chunk-days",
            "2",
            "--cache",
            "--audit",
            "--validate",
            "--stats",
            "--snapshot",
            "--compact",
            "--run-incremental-recovery",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "OK"
    assert (out_dir / "data_source_smoke_report.json").exists()
    assert (out_dir / "provider_probe.json").exists()
    assert (out_dir / "field_coverage.json").exists()
    assert (out_dir / "dataset_contracts.json").exists()
    assert payload["incremental_recovery"]["ok"] is True
    assert payload["audit_summary"]["total_requests"] > 0


def test_fake_tushare_success_smoke_is_offline_and_uses_cache(tmp_path, capsys):
    rc = main(
        [
            "--provider",
            "tushare",
            "--fake-tushare-scenario",
            "success",
            "--data-dir",
            str(tmp_path / "fake_data"),
            "--output-dir",
            str(tmp_path / "fake_smoke"),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            ALL_DATASETS,
            "--cache",
            "--audit",
            "--validate",
            "--stats",
            "--run-incremental-recovery",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["provider"] == "tushare"
    assert payload["status"] == "OK"
    assert payload["audit_summary"]["cache_hit_count"] > 0
    assert "fake-token-redacted" not in json.dumps(payload, ensure_ascii=False)


def test_fake_tushare_error_scenario_returns_zero_unless_fail_on_error(tmp_path, capsys):
    base_args = [
        "--provider",
        "tushare",
        "--fake-tushare-scenario",
        "permission_denied",
        "--data-dir",
        str(tmp_path / "denied_data"),
        "--output-dir",
        str(tmp_path / "denied_smoke"),
        "--start-date",
        "20240102",
        "--end-date",
        "20240104",
        "--datasets",
        "securities,trade_calendar",
        "--audit",
    ]
    assert main(base_args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ERROR"
    assert payload["diagnostic_counts"]["permission_denied"] == 2

    fail_args = [
        "--provider",
        "tushare",
        "--fake-tushare-scenario",
        "permission_denied",
        "--data-dir",
        str(tmp_path / "denied_data_fail"),
        "--output-dir",
        str(tmp_path / "denied_smoke_fail"),
        "--start-date",
        "20240102",
        "--datasets",
        "securities",
        "--fail-on-error",
    ]
    assert main(fail_args) == 1
    capsys.readouterr()


def test_tushare_without_allow_network_does_not_sync(tmp_path, capsys):
    rc = main(
        [
            "--provider",
            "tushare",
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "smoke"),
            "--start-date",
            "20240102",
            "--datasets",
            "securities",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "SKIPPED"
    assert payload["diagnostic_counts"]["network_disabled"] == 1


def test_tushare_allow_network_without_token_reports_missing_token(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    rc = main(
        [
            "--provider",
            "tushare",
            "--allow-network",
            "--require-token",
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "smoke"),
            "--start-date",
            "20240102",
            "--datasets",
            "securities",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "ERROR"
    assert payload["diagnostic_counts"]["missing_token"] == 1


def test_smoke_baseline_compare_reports_diff_without_failing(tmp_path, capsys):
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline_out = tmp_path / "baseline_out"
    current_out = tmp_path / "current_out"
    args = [
        "--provider",
        "sample",
        "--start-date",
        "20240102",
        "--end-date",
        "20240104",
        "--datasets",
        "securities,daily_bars",
        "--validate",
        "--stats",
    ]
    assert main([*args, "--data-dir", str(baseline), "--output-dir", str(baseline_out)]) == 0
    capsys.readouterr()
    assert main([*args, "--data-dir", str(current), "--output-dir", str(current_out), "--baseline-data-dir", str(baseline), "--compare-baseline"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["baseline_compare"]["compared"] is True
    assert Path(payload["paths"]["baseline_compare_summary_path"]).exists()
