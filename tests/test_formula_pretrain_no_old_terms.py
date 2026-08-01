from pathlib import Path


OLD_TERMS = [
    "solana",
    "jupiter",
    "meme",
    "crypto",
    "birdeye",
    "dexscreener",
    "wallet",
    "private_key",
    "best_meme_strategy",
    "cryptodataloader",
    "solanatrader",
    "token_address",
    "fdv",
    "liquidity",
]


def test_formula_corpus_batch_eval_and_pretrain_no_old_terms():
    paths = [
        Path("src/formula_corpus"),
        Path("src/formula_batch_eval"),
        Path("src/neural_search/pretrain.py"),
        Path("src/neural_search/run_pretrain.py"),
    ]
    for path in paths:
        files = [path] if path.is_file() else sorted(path.glob("*.py"))
        for file_path in files:
            text = file_path.read_text(encoding="utf-8").lower()
            for term in OLD_TERMS:
                assert term not in text, f"{term} found in {file_path}"
