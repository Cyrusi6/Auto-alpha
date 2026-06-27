from pathlib import Path


TARGETS = [
    Path("research_suite/__init__.py"),
    Path("research_suite/models.py"),
    Path("research_suite/catalog.py"),
    Path("research_suite/walk_forward.py"),
    Path("research_suite/promotion.py"),
    Path("research_suite/workflow.py"),
    Path("research_suite/report.py"),
    Path("research_suite/run_suite.py"),
    Path("factor_store/storage.py"),
    Path("dashboard/data_service.py"),
    Path("dashboard/app.py"),
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
