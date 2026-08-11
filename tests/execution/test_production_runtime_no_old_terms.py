from __future__ import annotations

from pathlib import Path


ROOTS = [
    Path("src/auto_alpha/execution/trading"),
    Path("src/auto_alpha/execution/broker"),
    Path("src/auto_alpha/execution/settlement"),
    Path("src/auto_alpha/platform/governance/network"),
]

BANNED_TERMS = [
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
    "sol_mint",
    "usdc_mint",
    "best_meme_strategy",
    "cryptodataloader",
    "solanatrader",
    "token_address",
    "fdv",
    "liquidity",
]


def test_production_runtime_packages_do_not_reintroduce_old_terms():
    offenders: list[str] = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for term in BANNED_TERMS:
                if term in text:
                    offenders.append(f"{path}:{term}")
    assert offenders == []
