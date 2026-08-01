from pathlib import Path


FORBIDDEN_TERMS = [
    "solana",
    "jupiter",
    "meme",
    "crypto",
    "birdeye",
    "dexscreener",
    "wallet",
    "lamports",
    "mint",
    "swap",
    "private_key",
    "sol_mint",
    "usdc_mint",
    "best_meme_strategy",
    "cryptodataloader",
    "solanatrader",
    "token_address",
    "fdv",
    "liquidity",
]


def test_execution_strategy_and_backtest_do_not_contain_old_terms():
    files = [
        *Path("src/auto_alpha/execution/trading/engine").glob("*.py"),
        *Path("src/auto_alpha/execution/trading/strategy").glob("*.py"),
        *Path("src/auto_alpha/portfolio/simulation/backtest").glob("*.py"),
    ]
    payload = "\n".join(path.read_text(encoding="utf-8") for path in files).lower()

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
