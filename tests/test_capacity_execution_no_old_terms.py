from pathlib import Path


TARGETS = [
    Path("capacity_model"),
    Path("execution_plan"),
    Path("backtest/run_backtest.py"),
    Path("backtest/simulator.py"),
    Path("strategy_manager/runner.py"),
    Path("operations"),
    Path("monitoring"),
    Path("dashboard"),
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
