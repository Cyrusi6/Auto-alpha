from pathlib import Path


MODEL_CORE_FILES = [
    Path("model_core/config.py"),
    Path("model_core/vocab.py"),
    Path("model_core/ops.py"),
    Path("model_core/vm.py"),
    Path("model_core/factors.py"),
    Path("model_core/data_loader.py"),
    Path("model_core/backtest.py"),
    Path("model_core/engine.py"),
    Path("model_core/alphagpt.py"),
    Path("model_core/__init__.py"),
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
