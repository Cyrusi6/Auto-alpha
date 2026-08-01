from pathlib import Path


def test_alpha_feature_factory_no_old_business_terms():
    paths = [
        Path("src/alpha_factory"),
        Path("src/feature_factory"),
        Path("src/model_core/data_loader.py"),
        Path("src/model_core/factors.py"),
        Path("src/formula_batch_eval"),
        Path("src/formula_search/search.py"),
        Path("src/formula_search/run_search.py"),
        Path("src/research_suite/workflow.py"),
        Path("src/monitoring/checks.py"),
        Path("src/dashboard/data_service.py"),
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
