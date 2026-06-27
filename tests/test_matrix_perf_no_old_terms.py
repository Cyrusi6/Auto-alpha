from pathlib import Path


TARGETS = [
    Path("matrix_store"),
    Path("performance_benchmark"),
    Path("cross_source_checks"),
    Path("model_core/data_loader.py"),
    Path("research_suite/models.py"),
    Path("research_suite/workflow.py"),
    Path("dashboard/config.py"),
    Path("dashboard/data_service.py"),
    Path("dashboard/app.py"),
]

OLD_TERMS = [
    "solana",
    "jupiter",
    "meme",
    "crypto",
    "birdeye",
    "dexscreener",
    "wallet",
    "swap",
    "private_key",
    "token_address",
    "fdv",
    "liquidity",
]


def test_matrix_perf_modules_do_not_reintroduce_old_terms():
    for target in TARGETS:
        paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in paths:
            payload = path.read_text(encoding="utf-8").lower()
            for term in OLD_TERMS:
                assert term not in payload, f"{term} found in {path}"
