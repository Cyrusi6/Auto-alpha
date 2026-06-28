import json

from formula_corpus import FormulaCorpusConfig, build_formula_corpus
from model_core.vm import StackVM


def test_formula_corpus_builds_sequences_preferences_and_schema_artifacts(tmp_path):
    result = build_formula_corpus(
        FormulaCorpusConfig(
            output_dir=str(tmp_path / "corpus"),
            max_records=12,
            max_preference_pairs=20,
        )
    )
    payload = result.to_dict()
    corpus_path = tmp_path / "corpus" / "formula_corpus.jsonl"
    sequences_path = tmp_path / "corpus" / "formula_sequences.jsonl"
    preferences_path = tmp_path / "corpus" / "formula_preferences.jsonl"

    assert payload["stats"]["valid_records"] > 0
    assert payload["stats"]["sequence_records"] > 0
    assert corpus_path.exists()
    assert sequences_path.exists()
    assert preferences_path.exists()
    assert (tmp_path / "corpus" / "formula_corpus.jsonl.schema.json").exists()

    records = [json.loads(line) for line in corpus_path.read_text().splitlines() if line.strip()]
    vm = StackVM()
    assert len(records) == len({row["formula_hash"] for row in records})
    assert all(vm.validate(row["formula_tokens"]) for row in records if row["valid"])
    sequences = [json.loads(line) for line in sequences_path.read_text().splitlines() if line.strip()]
    assert all("prefix_tokens" in row and "target_token" in row for row in sequences)
    assert all(row["prefix_tokens"] for row in sequences)


def test_formula_corpus_merges_factor_store_records(tmp_path):
    from factor_store import FactorRecord, LocalFactorStore, make_factor_id, stable_formula_hash
    from model_core.vocab import FORMULA_VOCAB

    tokens = [FORMULA_VOCAB.encode_name("ROE")]
    names = ["ROE"]
    formula_hash = stable_formula_hash(tokens, names, "ashare_features_v1", "ashare_ops_v1")
    store = LocalFactorStore(tmp_path / "store")
    store.save_factor(
        FactorRecord(
            factor_id=make_factor_id(formula_hash),
            formula=names,
            formula_tokens=tokens,
            formula_hash=formula_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="test",
            status="approved",
            metrics={"score": 1.23},
        )
    )
    result = build_formula_corpus(
        FormulaCorpusConfig(
            factor_store_dir=str(tmp_path / "store"),
            output_dir=str(tmp_path / "corpus"),
            include_defaults=False,
            include_seed=False,
        )
    )
    assert result.stats["valid_records"] == 1
    row = json.loads((tmp_path / "corpus" / "formula_corpus.jsonl").read_text().splitlines()[0])
    assert row["status"] == "approved"
    assert row["score"] == 1.23
