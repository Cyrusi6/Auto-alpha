from pathlib import Path


CORE_FILES = [
    Path("data_pipeline/config.py"),
    Path("data_pipeline/data_manager.py"),
    Path("data_pipeline/db_manager.py"),
    Path("data_pipeline/run_pipeline.py"),
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
    Path("data_pipeline/providers/base.py"),
    Path("data_pipeline/providers/birdeye.py"),
    Path("data_pipeline/providers/dexscreener.py"),
]


def test_core_data_pipeline_files_do_not_reference_old_business_terms():
    payload = "\n".join(path.read_text(encoding="utf-8") for path in CORE_FILES)

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload


def test_old_provider_files_are_removed():
    for path in REMOVED_PROVIDER_FILES:
        assert not path.exists()
