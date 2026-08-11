from pathlib import Path


def test_alpha_feature_factory_no_old_business_terms():
    paths = [
        Path("src/auto_alpha/research/search"),
        Path("src/auto_alpha/research/features"),
        Path("src/auto_alpha/research/formulas/data_loader.py"),
        Path("src/auto_alpha/research/formulas/factors.py"),
        Path("src/auto_alpha/research/formulas/evaluator.py"),
        Path("src/auto_alpha/research/search/formulas.py"),
        Path("src/auto_alpha/platform/observability/monitoring/checks.py"),
        Path("src/auto_alpha/platform/observability/dashboard/data_service.py"),
    ]
    banned = [
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
    ]

    hits = []
    for path in paths:
        files = path.rglob("*.py") if path.is_dir() else [path]
        for file_path in files:
            text = file_path.read_text(encoding="utf-8").lower()
            for term in banned:
                if term in text:
                    hits.append(f"{file_path}:{term}")

    assert hits == []
