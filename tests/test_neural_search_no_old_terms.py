from pathlib import Path


TARGETS = [
    Path("src/neural_search/__init__.py"),
    Path("src/neural_search/models.py"),
    Path("src/neural_search/action_mask.py"),
    Path("src/neural_search/dataset.py"),
    Path("src/neural_search/trainer.py"),
    Path("src/neural_search/sampler.py"),
    Path("src/neural_search/reward.py"),
    Path("src/neural_search/report.py"),
    Path("src/neural_search/run_neural_search.py"),
    Path("src/formula_search/run_search.py"),
    Path("src/research_suite/models.py"),
    Path("src/research_suite/workflow.py"),
    Path("src/model_core/engine.py"),
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


def test_neural_search_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
