from pathlib import Path


TARGETS = [
    Path("compute_cluster"),
    Path("experiment_orchestrator"),
    Path("formula_batch_eval/sharding.py"),
    Path("formula_batch_eval/merge.py"),
    Path("performance_benchmark/runner.py"),
]

FORBIDDEN = [
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
    "token_address",
    "fdv",
]


def test_compute_experiment_code_excludes_old_terms():
    chunks = []
    for target in TARGETS:
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        chunks.extend(path.read_text(encoding="utf-8").lower() for path in files)
    payload = "\n".join(chunks)
    for term in FORBIDDEN:
        assert term not in payload
