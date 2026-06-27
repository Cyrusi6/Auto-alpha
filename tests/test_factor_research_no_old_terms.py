from pathlib import Path


TARGETS = [
    Path("factor_engine/__init__.py"),
    Path("factor_engine/transforms.py"),
    Path("factor_engine/correlation.py"),
    Path("factor_engine/gate.py"),
    Path("factor_engine/pipeline.py"),
    Path("model_core/data_loader.py"),
    Path("model_core/backtest.py"),
    Path("model_core/engine.py"),
    Path("evaluation/report.py"),
    Path("factor_store/models.py"),
    Path("factor_store/storage.py"),
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


def test_factor_research_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
