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
    "token_address",
    "fdv",
    "liquidity",
]


def test_data_backfill_lake_no_old_terms():
    paths = [
        *Path("data_backfill").glob("*.py"),
        *Path("data_lake").glob("*.py"),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN:
            assert term not in text, f"{term} found in {path}"
