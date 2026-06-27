import json
from pathlib import Path

from data_pipeline import run_pipeline
from data_pipeline.ashare.providers.sample import SampleAShareDataProvider


def test_run_pipeline_main_outputs_default_plan(capsys):
    result = run_pipeline.main([])
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["provider"] == "tushare"


def test_run_pipeline_pretty_dry_run_outputs_json(capsys):
    result = run_pipeline.main(["--dry-run", "--pretty"])
    captured = capsys.readouterr()

    assert result == 0
    assert "\n  " in captured.out
    assert json.loads(captured.out)["provider"] == "tushare"


def test_run_pipeline_sync_tushare_without_token_returns_error(monkeypatch, capsys):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    result = run_pipeline.main(["--sync"])
    captured = capsys.readouterr()

    assert result != 0
    assert "TUSHARE_TOKEN is required" in captured.err


def test_run_pipeline_sync_sample_writes_local_files(tmp_path, capsys):
    result = run_pipeline.main(
        [
            "--sync",
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path),
            "--pretty",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["provider"] == "sample"
    assert [dataset["dataset"] for dataset in payload["datasets"]] == [
        "securities",
        "trade_calendar",
        "daily_bars",
        "daily_basic",
        "financial_features",
    ]
    for dataset in payload["datasets"]:
        assert Path(dataset["path"]).is_relative_to(tmp_path)
        assert Path(dataset["path"]).exists()


def test_run_pipeline_sync_tushare_with_fake_provider_writes_local_files(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(
        "data_pipeline.ashare.manager.create_ashare_provider",
        lambda config: SampleAShareDataProvider(),
    )

    result = run_pipeline.main(
        [
            "--sync",
            "--provider",
            "tushare",
            "--data-dir",
            str(tmp_path),
            "--pretty",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["provider"] == "tushare"
    assert [dataset["dataset"] for dataset in payload["datasets"]] == [
        "securities",
        "trade_calendar",
        "daily_bars",
        "daily_basic",
        "financial_features",
    ]
    for dataset in payload["datasets"]:
        assert Path(dataset["path"]).is_relative_to(tmp_path)
        assert Path(dataset["path"]).exists()


def test_run_pipeline_sync_tushare_without_token_returns_nonzero(monkeypatch, capsys):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    result = run_pipeline.main(["--sync", "--provider", "tushare"])
    captured = capsys.readouterr()

    assert result != 0
    assert "TUSHARE_TOKEN is required" in captured.err


def test_run_pipeline_source_excludes_old_entrypoint_terms():
    source = Path("data_pipeline/run_pipeline.py").read_text(encoding="utf-8")

    for forbidden in [
        "BIRDEYE_API_KEY",
        "BirdeyeProvider",
        "DexScreenerProvider",
        "solana",
        "crypto_quant",
        "DataManager",
    ]:
        assert forbidden not in source
