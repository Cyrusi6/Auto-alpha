from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/data/matrix/store"),
    Path("src/auto_alpha/data/lake/catalog"),
    Path("src/auto_alpha/data/quality/cross_source"),
    Path("src/auto_alpha/research/formulas/data_loader.py"),
    Path("src/auto_alpha/platform/observability/dashboard/config.py"),
    Path("src/auto_alpha/platform/observability/dashboard/data_service.py"),
    Path("src/auto_alpha/platform/observability/dashboard/app.py"),
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
