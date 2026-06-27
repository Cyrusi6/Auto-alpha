from pathlib import Path


TARGETS = [
    Path("research/__init__.py"),
    Path("research/models.py"),
    Path("research/candidates.py"),
    Path("research/batch_runner.py"),
    Path("research/composite.py"),
    Path("research/report.py"),
    Path("research/run_batch.py"),
    Path("backtest/io.py"),
    Path("backtest/run_backtest.py"),
    Path("strategy_manager/runner.py"),
    Path("dashboard/data_service.py"),
    Path("dashboard/app.py"),
]

FORBIDDEN_TERMS = [
    "cryptodataloader",
    "memebacktest",
    "memeindicators",
    "best_meme_strategy",
    "liq_score",
    "fomo",
    "pressure",
    "liquidity",
    "fdv",
    "crypto_quant",
    "solana",
    "birdeye",
    "dexscreener",
    "jupiter",
]


def test_research_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
