from pathlib import Path


TARGETS = [
    Path("approval/__init__.py"),
    Path("approval/models.py"),
    Path("approval/store.py"),
    Path("approval/run_approval.py"),
    Path("paper_account/__init__.py"),
    Path("paper_account/models.py"),
    Path("paper_account/ledger.py"),
    Path("paper_account/performance.py"),
    Path("paper_account/run_account.py"),
    Path("operations/__init__.py"),
    Path("operations/models.py"),
    Path("operations/daily_runner.py"),
    Path("operations/report.py"),
    Path("operations/run_daily.py"),
    Path("monitoring/__init__.py"),
    Path("monitoring/models.py"),
    Path("monitoring/checks.py"),
    Path("monitoring/report.py"),
    Path("monitoring/run_monitor.py"),
    Path("strategy_manager/runner.py"),
    Path("execution/paper_broker.py"),
    Path("dashboard/data_service.py"),
    Path("dashboard/app.py"),
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
    "sol_mint",
    "usdc_mint",
    "best_meme_strategy",
    "cryptodataloader",
    "solanatrader",
    "token_address",
    "fdv",
    "liquidity",
]


def test_operations_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
