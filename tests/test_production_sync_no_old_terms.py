from pathlib import Path


TARGETS = [
    Path("data_pipeline/ashare/sync_plan.py"),
    Path("data_pipeline/ashare/cache.py"),
    Path("data_pipeline/ashare/audit.py"),
    Path("data_pipeline/ashare/compaction.py"),
    Path("data_pipeline/ashare/stats.py"),
    Path("data_pipeline/ashare/manager.py"),
    Path("data_pipeline/run_pipeline.py"),
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


def test_production_sync_code_excludes_old_terms():
    payload = "\n".join(path.read_text(encoding="utf-8").lower() for path in TARGETS)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload
