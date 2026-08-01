from pathlib import Path


TARGETS = [
    Path("src/research/__init__.py"),
    Path("src/research/models.py"),
    Path("src/research/candidates.py"),
    Path("src/research/batch_runner.py"),
    Path("src/research/composite.py"),
    Path("src/research/report.py"),
    Path("src/research/run_batch.py"),
    Path("src/backtest/io.py"),
    Path("src/backtest/run_backtest.py"),
    Path("src/strategy_manager/runner.py"),
    Path("src/dashboard/data_service.py"),
    Path("src/dashboard/app.py"),
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
