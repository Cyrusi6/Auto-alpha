from pathlib import Path


TARGETS = [
    Path("src/research_suite/__init__.py"),
    Path("src/research_suite/models.py"),
    Path("src/research_suite/catalog.py"),
    Path("src/research_suite/walk_forward.py"),
    Path("src/research_suite/promotion.py"),
    Path("src/research_suite/workflow.py"),
    Path("src/research_suite/report.py"),
    Path("src/research_suite/run_suite.py"),
    Path("src/factor_store/storage.py"),
    Path("src/dashboard/data_service.py"),
    Path("src/dashboard/app.py"),
]

FORBIDDEN_TERMS = [
    "cryptodataloader",
    "memebacktest",
    "memeindicators",
    "best_meme_strategy",
    "liq_score",
    "fomo",
    "pressure",
    "liquidity",
    "fdv",
    "crypto_quant",
    "solana",
    "birdeye",
    "dexscreener",
    "jupiter",
]


def test_research_suite_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
