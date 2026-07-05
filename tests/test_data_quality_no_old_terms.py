from pathlib import Path


FORBIDDEN_TERMS = {
    "solana",
    "meme",
    "crypto",
    "jupiter",
    "dexscreener",
    "birdeye",
    "best_meme_strategy",
    "cryptodataloader",
}


def test_data_quality_lab_does_not_reintroduce_old_terms() -> None:
    for path in Path("data_quality_lab").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN_TERMS:
            assert term not in text, f"{term} found in {path}"
