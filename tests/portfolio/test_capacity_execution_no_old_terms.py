from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/portfolio/simulation/capacity"),
    Path("src/auto_alpha/execution/trading/plan"),
    Path("src/auto_alpha/portfolio/simulation/backtest_run_backtest.py"),
    Path("src/auto_alpha/portfolio/simulation/backtest_simulator.py"),
    Path("src/auto_alpha/execution/trading/strategy_runner.py"),
    Path("src/auto_alpha/execution/operations/daily"),
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
