from pathlib import Path


MODEL_CORE_FILES = [
    Path("src/auto_alpha/research/formulas/runtime/config.py"),
    Path("src/auto_alpha/research/formulas/runtime/vocab.py"),
    Path("src/auto_alpha/research/formulas/runtime/ops.py"),
    Path("src/auto_alpha/research/formulas/runtime/vm.py"),
    Path("src/auto_alpha/research/formulas/runtime/factors.py"),
    Path("src/auto_alpha/research/formulas/runtime/data_loader.py"),
    Path("src/auto_alpha/research/formulas/runtime/backtest.py"),
    Path("src/auto_alpha/research/formulas/runtime/engine.py"),
    Path("src/auto_alpha/research/formulas/runtime/alphagpt.py"),
    Path("src/auto_alpha/research/formulas/runtime/__init__.py"),
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
    "token address",
    "address",
    "ohlcv",
    "crypto_quant",
    "solana",
    "birdeye",
    "dexscreener",
    "jupiter",
]


def test_model_core_files_do_not_contain_old_business_terms():
    payload = "\n".join(path.read_text(encoding="utf-8") for path in MODEL_CORE_FILES).lower()

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
