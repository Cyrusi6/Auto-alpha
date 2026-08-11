from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/formulas/candidates.py"),
    Path("src/auto_alpha/research/formulas/evaluator.py"),
    Path("src/auto_alpha/research/factors/composite.py"),
    Path("src/auto_alpha/portfolio/simulator/backtest.py"),
    Path("src/auto_alpha/execution/trading/strategy.py"),
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


def test_legacy_research_batch_modules_are_deleted():
    legacy = Path("src/auto_alpha/research/search")
    assert not list(legacy.glob("studies*.py"))
