from pathlib import Path


def test_validation_and_certification_modules_do_not_reintroduce_old_business_terms():
    roots = [
        Path("src/validation_lab"),
        Path("src/factor_certification"),
        Path("src/research_suite"),
        Path("src/factor_lifecycle"),
        Path("src/monitoring"),
    ]
    terms = [
        "crypto",
        "solana",
        "meme",
        "birdeye",
        "dexscreener",
        "jupiter",
        "wallet",
        "lamports",
        "private_key",
        "token_address",
        "sol_mint",
        "usdc_mint",
    ]
    scanned_suffixes = {".py"}

    offenders: list[tuple[str, str]] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in scanned_suffixes or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for term in terms:
                if term in text:
                    offenders.append((str(path), term))

    assert offenders == []
