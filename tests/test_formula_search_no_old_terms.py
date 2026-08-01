from pathlib import Path


TARGETS = [
    Path("src/formula_search/__init__.py"),
    Path("src/formula_search/models.py"),
    Path("src/formula_search/generator.py"),
    Path("src/formula_search/mutation.py"),
    Path("src/formula_search/search.py"),
    Path("src/formula_search/report.py"),
    Path("src/formula_search/run_search.py"),
    Path("src/model_core/ops.py"),
    Path("src/model_core/vm.py"),
    Path("src/research/candidates.py"),
    Path("src/research/batch_runner.py"),
    Path("src/research/report.py"),
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


def test_formula_search_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
