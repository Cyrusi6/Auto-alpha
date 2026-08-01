from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/discovery/suite/__init__.py"),
    Path("src/auto_alpha/research/discovery/suite/models.py"),
    Path("src/auto_alpha/research/discovery/suite/catalog.py"),
    Path("src/auto_alpha/research/discovery/suite/walk_forward.py"),
    Path("src/auto_alpha/research/discovery/suite/promotion.py"),
    Path("src/auto_alpha/research/discovery/suite/workflow.py"),
    Path("src/auto_alpha/research/discovery/suite/report.py"),
    Path("src/auto_alpha/research/discovery/suite/run_suite.py"),
    Path("src/auto_alpha/research/factors/store/storage.py"),
    Path("src/auto_alpha/platform/observability/dashboard/data_service.py"),
    Path("src/auto_alpha/platform/observability/dashboard/app.py"),
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
