from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/search/neural.py"),
    Path("src/auto_alpha/research/search/neural_cli.py"),
    Path("src/auto_alpha/research/search/pretrain_cli.py"),
    Path("src/auto_alpha/research/search/formulas.py"),
    Path("src/auto_alpha/research/search/models.py"),
    Path("src/auto_alpha/research/search/workflow.py"),
    Path("src/auto_alpha/research/formulas/engine.py"),
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
