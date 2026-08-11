from pathlib import Path


OLD_TERMS = [
    "crypto",
    "solana",
    "meme",
    "birdeye",
    "dexscreener",
    "wallet",
    "lamports",
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


SCAN_PATHS = [
    Path("src/auto_alpha/research/factors"),
    Path("src/auto_alpha/execution/trading/daily.py"),
    Path("src/auto_alpha/platform/observability/monitoring/checks.py"),
    Path("src/auto_alpha/platform/observability/monitoring/run_monitor.py"),
]


def test_model_lifecycle_modules_do_not_reintroduce_old_business_terms():
    hits = []
    for path in SCAN_PATHS:
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for file_path in files:
            text = file_path.read_text(encoding="utf-8").lower()
            for term in OLD_TERMS:
                if term in text:
                    hits.append((str(file_path), term))

    assert hits == []
