import json
from pathlib import Path

from data_pipeline import run_pipeline


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


def test_run_pipeline_sync_returns_not_implemented(capsys):
    result = run_pipeline.main(["--sync"])
    captured = capsys.readouterr()

    assert result != 0
    assert "not implemented yet" in captured.err


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
