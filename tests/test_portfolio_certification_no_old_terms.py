from pathlib import Path


def test_portfolio_governance_modules_do_not_reintroduce_old_business_terms():
    terms = [
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
    roots = [Path("portfolio_lab"), Path("portfolio_certification")]
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for term in terms:
                if term in text:
                    offenders.append((str(path), term))
    assert offenders == []
