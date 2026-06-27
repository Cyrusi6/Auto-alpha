from pathlib import Path


TARGETS = [
    Path("formula_search/__init__.py"),
    Path("formula_search/models.py"),
    Path("formula_search/generator.py"),
    Path("formula_search/mutation.py"),
    Path("formula_search/search.py"),
    Path("formula_search/report.py"),
    Path("formula_search/run_search.py"),
    Path("model_core/ops.py"),
    Path("model_core/vm.py"),
    Path("research/candidates.py"),
    Path("research/batch_runner.py"),
    Path("research/report.py"),
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


def test_formula_search_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
