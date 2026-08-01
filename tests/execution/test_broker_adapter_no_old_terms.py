from pathlib import Path


FORBIDDEN = [
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


def test_broker_adapter_and_modified_execution_paths_have_no_old_terms():
    roots = [
        Path("src/auto_alpha/execution/broker/adapter"),
        Path("src/auto_alpha/execution/operations/daily"),
        Path("src/auto_alpha/execution/trading/paper"),
        Path("src/auto_alpha/platform/observability/monitoring"),
        Path("src/auto_alpha/platform/observability/dashboard"),
    ]
    files = []
    for root in roots:
        files.extend(path for path in root.rglob("*.py") if path.is_file())
    for path in files:
        content = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN:
            assert term not in content, f"{term} found in {path}"
