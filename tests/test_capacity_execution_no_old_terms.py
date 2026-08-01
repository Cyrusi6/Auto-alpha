from pathlib import Path


TARGETS = [
    Path("src/capacity_model"),
    Path("src/execution_plan"),
    Path("src/backtest/run_backtest.py"),
    Path("src/backtest/simulator.py"),
    Path("src/strategy_manager/runner.py"),
    Path("src/operations"),
    Path("src/monitoring"),
    Path("src/dashboard"),
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
    "liquidity",
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
