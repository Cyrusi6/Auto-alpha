from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/factors/engine.py"),
    Path("src/auto_alpha/research/factors/engine_transforms.py"),
    Path("src/auto_alpha/research/factors/engine_correlation.py"),
    Path("src/auto_alpha/research/factors/engine_gate.py"),
    Path("src/auto_alpha/research/factors/engine_pipeline.py"),
    Path("src/auto_alpha/research/formulas/runtime_data_loader.py"),
    Path("src/auto_alpha/research/formulas/runtime_backtest.py"),
    Path("src/auto_alpha/research/formulas/runtime_engine.py"),
    Path("src/auto_alpha/research/discovery/evaluation_report.py"),
    Path("src/auto_alpha/research/factors/store_models.py"),
    Path("src/auto_alpha/research/factors/store_storage.py"),
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


def test_factor_research_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
