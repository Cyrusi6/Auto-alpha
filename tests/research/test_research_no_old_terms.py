from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/discovery/studies/__init__.py"),
    Path("src/auto_alpha/research/discovery/studies/models.py"),
    Path("src/auto_alpha/research/discovery/studies/candidates.py"),
    Path("src/auto_alpha/research/discovery/studies/batch_runner.py"),
    Path("src/auto_alpha/research/discovery/studies/composite.py"),
    Path("src/auto_alpha/research/discovery/studies/report.py"),
    Path("src/auto_alpha/research/discovery/studies/run_batch.py"),
    Path("src/auto_alpha/portfolio/simulation/backtest/io.py"),
    Path("src/auto_alpha/portfolio/simulation/backtest/run_backtest.py"),
    Path("src/auto_alpha/execution/trading/strategy/runner.py"),
    Path("src/auto_alpha/platform/observability/dashboard/data_service.py"),
    Path("src/auto_alpha/platform/observability/dashboard/app.py"),
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
