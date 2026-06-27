from pathlib import Path


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
    "crypto_quant",
    "solana",
    "birdeye",
    "dexscreener",
    "jupiter",
]


def test_factor_platform_files_do_not_contain_old_business_terms():
    files = [
        *Path("factor_store").glob("*.py"),
        *Path("evaluation").glob("*.py"),
        Path("model_core/engine.py"),
        Path("model_core/backtest.py"),
    ]
    payload = "\n".join(path.read_text(encoding="utf-8") for path in files).lower()

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
