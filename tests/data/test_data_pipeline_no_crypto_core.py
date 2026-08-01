from pathlib import Path


CORE_FILES = [
    Path("src/auto_alpha/data/ingestion/pipeline/config.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/data_manager.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/db_manager.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/run_pipeline.py"),
]

FORBIDDEN_TERMS = [
    "BIRDEYE",
    "Birdeye",
    "DexScreener",
    "crypto_quant",
    "solana",
    "liquidity",
    "fdv",
    "pair_address",
    "mint",
]

REMOVED_PROVIDER_FILES = [
    Path("src/auto_alpha/data/ingestion/pipeline/providers/base.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/providers/birdeye.py"),
    Path("src/auto_alpha/data/ingestion/pipeline/providers/dexscreener.py"),
]


def test_core_data_pipeline_files_do_not_reference_old_business_terms():
    payload = "\n".join(path.read_text(encoding="utf-8") for path in CORE_FILES)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload


def test_old_provider_files_are_removed():
    for path in REMOVED_PROVIDER_FILES:
        assert not path.exists()
