from pathlib import Path


def test_settlement_modules_do_not_reintroduce_old_business_terms():
    roots = [
        Path("settlement_engine"),
        Path("paper_account/ledger.py"),
        Path("paper_account/models.py"),
        Path("backtest/run_backtest.py"),
        Path("strategy_manager/runner.py"),
        Path("operations/daily_runner.py"),
        Path("operations/run_daily.py"),
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
