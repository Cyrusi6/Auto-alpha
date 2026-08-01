from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/neural/search/__init__.py"),
    Path("src/auto_alpha/research/neural/search/models.py"),
    Path("src/auto_alpha/research/neural/search/action_mask.py"),
    Path("src/auto_alpha/research/neural/search/dataset.py"),
    Path("src/auto_alpha/research/neural/search/trainer.py"),
    Path("src/auto_alpha/research/neural/search/sampler.py"),
    Path("src/auto_alpha/research/neural/search/reward.py"),
    Path("src/auto_alpha/research/neural/search/report.py"),
    Path("src/auto_alpha/research/neural/search/run_neural_search.py"),
    Path("src/auto_alpha/research/formulas/search/run_search.py"),
    Path("src/auto_alpha/research/discovery/suite/models.py"),
    Path("src/auto_alpha/research/discovery/suite/workflow.py"),
    Path("src/auto_alpha/research/formulas/runtime/engine.py"),
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


def test_neural_search_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
