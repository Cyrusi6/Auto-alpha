from pathlib import Path


TARGETS = [
    Path("src/factor_engine/__init__.py"),
    Path("src/factor_engine/transforms.py"),
    Path("src/factor_engine/correlation.py"),
    Path("src/factor_engine/gate.py"),
    Path("src/factor_engine/pipeline.py"),
    Path("src/model_core/data_loader.py"),
    Path("src/model_core/backtest.py"),
    Path("src/model_core/engine.py"),
    Path("src/evaluation/report.py"),
    Path("src/factor_store/models.py"),
    Path("src/factor_store/storage.py"),
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


def test_factor_research_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
