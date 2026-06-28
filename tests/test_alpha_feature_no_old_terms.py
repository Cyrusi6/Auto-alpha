from pathlib import Path


def test_alpha_feature_factory_no_old_business_terms():
    paths = [
        Path("alpha_factory"),
        Path("feature_factory"),
        Path("model_core/data_loader.py"),
        Path("model_core/factors.py"),
        Path("formula_batch_eval"),
        Path("formula_search/search.py"),
        Path("formula_search/run_search.py"),
        Path("research_suite/workflow.py"),
        Path("monitoring/checks.py"),
        Path("dashboard/data_service.py"),
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
