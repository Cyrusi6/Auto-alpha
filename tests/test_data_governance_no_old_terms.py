from pathlib import Path


TARGETS = [
    Path("src/data_pipeline/ashare/quality.py"),
    Path("src/data_pipeline/ashare/state.py"),
    Path("src/data_pipeline/ashare/storage.py"),
    Path("src/data_pipeline/ashare/manager.py"),
    Path("src/data_pipeline/run_pipeline.py"),
    Path("src/universe/__init__.py"),
    Path("src/universe/models.py"),
    Path("src/universe/builder.py"),
    Path("src/universe/run_universe.py"),
]

FORBIDDEN_TERMS = [
    "solana",
    "jupiter",
    "meme",
    "crypto",
    "birdeye",
    "dexscreener",
    "wallet",
    "swap",
    "private_key",
    "fdv",
    "liquidity",
]


def test_data_governance_and_universe_code_excludes_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
