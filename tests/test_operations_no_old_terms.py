from pathlib import Path


TARGETS = [
    Path("src/approval/__init__.py"),
    Path("src/approval/models.py"),
    Path("src/approval/store.py"),
    Path("src/approval/run_approval.py"),
    Path("src/paper_account/__init__.py"),
    Path("src/paper_account/models.py"),
    Path("src/paper_account/ledger.py"),
    Path("src/paper_account/performance.py"),
    Path("src/paper_account/run_account.py"),
    Path("src/operations/__init__.py"),
    Path("src/operations/models.py"),
    Path("src/operations/daily_runner.py"),
    Path("src/operations/report.py"),
    Path("src/operations/run_daily.py"),
    Path("src/monitoring/__init__.py"),
    Path("src/monitoring/models.py"),
    Path("src/monitoring/checks.py"),
    Path("src/monitoring/report.py"),
    Path("src/monitoring/run_monitor.py"),
    Path("src/strategy_manager/runner.py"),
    Path("src/execution/paper_broker.py"),
    Path("src/dashboard/data_service.py"),
    Path("src/dashboard/app.py"),
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
