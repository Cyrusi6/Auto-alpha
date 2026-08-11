from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/portfolio/risk"),
    Path("src/auto_alpha/portfolio/construction/optimizer.py"),
    Path("src/auto_alpha/portfolio/simulator/backtest.py"),
    Path("src/auto_alpha/execution/trading/strategy.py"),
]

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
    "fdv",
]


def test_risk_and_optimizer_code_excludes_old_terms():
    chunks = []
    for target in TARGETS:
        if target.is_dir():
            chunks.extend(path.read_text(encoding="utf-8").lower() for path in target.rglob("*.py"))
        else:
            chunks.append(target.read_text(encoding="utf-8").lower())
    payload = "\n".join(chunks)
    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
