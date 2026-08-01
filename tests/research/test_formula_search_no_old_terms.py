from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/formulas/search/__init__.py"),
    Path("src/auto_alpha/research/formulas/search/models.py"),
    Path("src/auto_alpha/research/formulas/search/generator.py"),
    Path("src/auto_alpha/research/formulas/search/mutation.py"),
    Path("src/auto_alpha/research/formulas/search/search.py"),
    Path("src/auto_alpha/research/formulas/search/report.py"),
    Path("src/auto_alpha/research/formulas/search/run_search.py"),
    Path("src/auto_alpha/research/formulas/runtime/ops.py"),
    Path("src/auto_alpha/research/formulas/runtime/vm.py"),
    Path("src/auto_alpha/research/discovery/studies/candidates.py"),
    Path("src/auto_alpha/research/discovery/studies/batch_runner.py"),
    Path("src/auto_alpha/research/discovery/studies/report.py"),
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


def test_formula_search_files_exclude_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
