from pathlib import Path


def test_statement_reconciliation_modules_do_not_reintroduce_old_terms():
    forbidden = [
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
        "best_meme_strategy",
        "cryptodataloader",
        "solanatrader",
        "token_address",
        "fdv",
        "liquidity",
    ]
    roots = [
        Path("src/auto_alpha/execution/broker/statements.py"),
        Path("src/auto_alpha/execution/settlement/reconciliation.py"),
    ]
    for root in roots:
        for path in ([root] if root.is_file() else root.rglob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            for word in forbidden:
                assert word not in text, f"{word} found in {path}"
