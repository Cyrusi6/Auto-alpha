from pathlib import Path


TARGETS = [
    Path("src/auto_alpha/data/ingestion/pipeline/ashare/quality.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/ashare/state.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/ashare/storage.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/ashare/manager.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/run_pipeline.py"),
    Path("src/auto_alpha/data/universe/__init__.py"),
    Path("src/auto_alpha/data/universe/models.py"),
    Path("src/auto_alpha/data/universe/builder.py"),
    Path("src/auto_alpha/data/universe/run_universe.py"),
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
