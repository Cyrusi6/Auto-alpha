import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from formula_batch_eval import FormulaBatchEvalConfig, FormulaBatchEvaluator, requests_from_candidates
from research.candidates import default_candidates


def test_formula_batch_eval_runs_and_writes_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    result = FormulaBatchEvaluator(
        FormulaBatchEvalConfig(
            data_dir=str(data_dir),
            factor_store_dir=str(tmp_path / "store"),
            report_dir=str(tmp_path / "reports"),
            output_dir=str(tmp_path / "batch_eval"),
            factor_transform="winsorize_zscore",
            enable_gate=True,
            min_coverage=0.5,
            correlation_threshold=0.99,
            register_approved=True,
            chunk_size=2,
            device="cpu",
            use_eval_cache=True,
            eval_cache_dir=str(tmp_path / "cache"),
        )
    ).run(requests_from_candidates(default_candidates()[:3]))

    assert result.summary["total"] == 3
    assert (tmp_path / "batch_eval" / "formula_batch_eval_result.json").exists()
    assert (tmp_path / "batch_eval" / "formula_eval_results.jsonl").exists()
    assert (tmp_path / "batch_eval" / "formula_eval_cache_manifest.json").exists()
    assert (tmp_path / "batch_eval" / "formula_batch_eval_benchmark.json").exists()
    payload = json.loads((tmp_path / "batch_eval" / "formula_batch_eval_result.json").read_text())
    assert payload["artifact_type"] == "formula_batch_eval_result"
    assert payload["benchmark"]["device"] == "cpu"


def test_formula_batch_eval_cli_from_corpus(tmp_path, capsys):
    from formula_corpus import FormulaCorpusConfig, build_formula_corpus
    from formula_batch_eval import run_batch_eval

    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(tmp_path / "corpus"), max_records=4))
    exit_code = run_batch_eval.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "eval"),
            "--corpus-path",
            corpus.paths["formula_corpus_path"],
            "--max-formulas",
            "2",
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--min-coverage",
            "0.5",
            "--device",
            "cpu",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["summary"]["total"] == 2


def test_formula_batch_eval_shards_and_merge(tmp_path, capsys):
    from formula_corpus import FormulaCorpusConfig, build_formula_corpus
    from formula_batch_eval import run_batch_eval

    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(tmp_path / "corpus"), max_records=6))
    shard_dirs = []
    for shard_id in range(2):
        shard_dir = tmp_path / f"eval_shard_{shard_id}"
        shard_dirs.append(shard_dir)
        exit_code = run_batch_eval.main(
            [
                "--data-dir",
                str(data_dir),
                "--factor-store-dir",
                str(tmp_path / "store"),
                "--report-dir",
                str(tmp_path / "reports"),
                "--output-dir",
                str(shard_dir),
                "--corpus-path",
                corpus.paths["formula_corpus_path"],
                "--max-formulas",
                "4",
                "--device",
                "cpu",
                "--continue-on-error",
                "--shard-id",
                str(shard_id),
                "--shard-count",
                "2",
                "--write-shard-manifest",
                "--resource-report-path",
                str(shard_dir / "resource_usage.json"),
                "--pretty",
            ]
        )
        assert exit_code == 0
        capsys.readouterr()
        assert (shard_dir / "shard_manifest.json").exists()
        assert (shard_dir / "resource_usage.json").exists()

    exit_code = run_batch_eval.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "merged"),
            "--merge-shards",
            "--shard-dir",
            str(shard_dirs[0]),
            "--shard-dir",
            str(shard_dirs[1]),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["summary"]["total"] > 0
    assert (tmp_path / "merged" / "formula_batch_eval_result.json").exists()
    assert (tmp_path / "merged" / "formula_eval_results.jsonl").exists()
