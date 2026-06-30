from pathlib import Path


OLD_TERMS = [
    "solana",
    "jupiter",
    "meme",
    "crypto",
    "birdeye",
    "dexscreener",
    "wallet",
    "lamports",
    "mint",
    "swap",
    "private_key",
    "sol_mint",
    "usdc_mint",
    "best_meme_strategy",
    "cryptodataloader",
    "solanatrader",
]


def test_broker_connectivity_modules_do_not_reintroduce_old_business_terms():
    for root in [Path("broker_connectivity"), Path("broker_readonly_mirror")]:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for term in OLD_TERMS:
                assert term not in text, f"{term} found in {path}"
