from pathlib import Path


def test_settlement_modules_do_not_reintroduce_old_business_terms():
    roots = [
        Path("src/auto_alpha/execution/settlement/engine"),
        Path("src/auto_alpha/execution/trading/paper_ledger.py"),
        Path("src/auto_alpha/execution/trading/paper_models.py"),
        Path("src/auto_alpha/portfolio/simulation/backtest_run_backtest.py"),
        Path("src/auto_alpha/execution/trading/strategy_runner.py"),
        Path("src/auto_alpha/execution/operations/daily_daily_runner.py"),
        Path("src/auto_alpha/execution/operations/daily_run_daily.py"),
    ]
    old_terms = [
        "solana",
        "jupiter",
        "meme",
        "crypto",
        "birdeye",
        "dexscreener",
        "wallet",
        "lamports",
        "token_address",
        "best_meme_strategy",
    ]
    offenders = []
    for root in roots:
        paths = [root] if root.is_file() else [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]
        for path in paths:
            text = path.read_text(encoding="utf-8").lower()
            for term in old_terms:
                if term in text:
                    offenders.append((str(path), term))

    assert offenders == []
