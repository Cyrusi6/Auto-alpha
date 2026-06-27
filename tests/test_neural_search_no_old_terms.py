from pathlib import Path


TARGETS = [
    Path("neural_search/__init__.py"),
    Path("neural_search/models.py"),
    Path("neural_search/action_mask.py"),
    Path("neural_search/dataset.py"),
    Path("neural_search/trainer.py"),
    Path("neural_search/sampler.py"),
    Path("neural_search/reward.py"),
    Path("neural_search/report.py"),
    Path("neural_search/run_neural_search.py"),
    Path("formula_search/run_search.py"),
    Path("research_suite/models.py"),
    Path("research_suite/workflow.py"),
    Path("model_core/engine.py"),
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


def test_neural_search_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
