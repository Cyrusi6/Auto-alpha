from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/platform/governance/approval/__init__.py"),
    Path("src/auto_alpha/platform/governance/approval/models.py"),
    Path("src/auto_alpha/platform/governance/approval/store.py"),
    Path("src/auto_alpha/platform/governance/approval/run_approval.py"),
    Path("src/auto_alpha/execution/trading/paper.py"),
    Path("src/auto_alpha/execution/trading/paper_models.py"),
    Path("src/auto_alpha/execution/trading/paper_ledger.py"),
    Path("src/auto_alpha/execution/trading/paper_performance.py"),
    Path("src/auto_alpha/execution/trading/paper_run_account.py"),
    Path("src/auto_alpha/execution/operations/daily.py"),
    Path("src/auto_alpha/execution/operations/daily_models.py"),
    Path("src/auto_alpha/execution/operations/daily_daily_runner.py"),
    Path("src/auto_alpha/execution/operations/daily_report.py"),
    Path("src/auto_alpha/execution/operations/daily_run_daily.py"),
    Path("src/auto_alpha/platform/observability/monitoring/__init__.py"),
    Path("src/auto_alpha/platform/observability/monitoring/models.py"),
    Path("src/auto_alpha/platform/observability/monitoring/checks.py"),
    Path("src/auto_alpha/platform/observability/monitoring/report.py"),
    Path("src/auto_alpha/platform/observability/monitoring/run_monitor.py"),
    Path("src/auto_alpha/execution/trading/strategy_runner.py"),
    Path("src/auto_alpha/execution/trading/engine_paper_broker.py"),
    Path("src/auto_alpha/platform/observability/dashboard/data_service.py"),
    Path("src/auto_alpha/platform/observability/dashboard/app.py"),
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
