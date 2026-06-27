from pathlib import Path


TARGETS = [
    Path("risk_model"),
    Path("portfolio_optimizer"),
    Path("backtest/run_backtest.py"),
    Path("backtest/simulator.py"),
    Path("strategy_manager/runner.py"),
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
    "liquidity",
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
