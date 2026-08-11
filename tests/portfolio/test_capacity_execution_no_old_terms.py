from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/portfolio/simulator/capacity.py"),
    Path("src/auto_alpha/execution/trading/plan.py"),
    Path("src/auto_alpha/portfolio/simulator/backtest.py"),
    Path("src/auto_alpha/execution/trading/strategy.py"),
    Path("src/auto_alpha/execution/trading"),
    Path("src/auto_alpha/platform/observability/monitoring"),
    Path("src/auto_alpha/platform/observability/dashboard"),
]

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
]


def test_capacity_execution_code_excludes_old_terms():
    chunks = []
    for target in TARGETS:
        if target.is_dir():
            chunks.extend(path.read_text(encoding="utf-8").lower() for path in target.rglob("*.py"))
        else:
            chunks.append(target.read_text(encoding="utf-8").lower())
    payload = "\n".join(chunks)
    for term in FORBIDDEN:
        assert term not in payload
