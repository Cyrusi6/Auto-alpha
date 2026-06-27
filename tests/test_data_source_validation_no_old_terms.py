from pathlib import Path


def test_data_source_validation_no_old_terms():
    forbidden = [
        "solana",
        "jupiter",
        "meme",
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
    root = Path("data_source_validation")
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in root.rglob("*.py"))
    for term in forbidden:
        assert term not in text
