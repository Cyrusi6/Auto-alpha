from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/research/formulas/search_models.py"),
    Path("src/auto_alpha/research/formulas/search_generator.py"),
    Path("src/auto_alpha/research/formulas/search_mutation.py"),
    Path("src/auto_alpha/research/formulas/search_search.py"),
    Path("src/auto_alpha/research/formulas/search_report.py"),
    Path("src/auto_alpha/research/formulas/search_run_search.py"),
    Path("src/auto_alpha/research/formulas/runtime_ops.py"),
    Path("src/auto_alpha/research/formulas/runtime_vm.py"),
    Path("src/auto_alpha/research/formulas/candidates.py"),
    Path("src/auto_alpha/research/formulas/batch_evaluator.py"),
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
