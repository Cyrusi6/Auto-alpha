from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/neural/search.py"),
    Path("src/auto_alpha/research/neural/search_models.py"),
    Path("src/auto_alpha/research/neural/search_action_mask.py"),
    Path("src/auto_alpha/research/neural/search_dataset.py"),
    Path("src/auto_alpha/research/neural/search_trainer.py"),
    Path("src/auto_alpha/research/neural/search_sampler.py"),
    Path("src/auto_alpha/research/neural/search_reward.py"),
    Path("src/auto_alpha/research/neural/search_report.py"),
    Path("src/auto_alpha/research/neural/search_run_neural_search.py"),
    Path("src/auto_alpha/research/formulas/search_run_search.py"),
    Path("src/auto_alpha/research/discovery/factory_models.py"),
    Path("src/auto_alpha/research/discovery/factory_runner.py"),
    Path("src/auto_alpha/research/formulas/runtime_engine.py"),
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
