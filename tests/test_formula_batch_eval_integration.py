import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from formula_corpus import FormulaCorpusConfig, build_formula_corpus
from formula_search import run_search
from matrix_store import build_matrix_cache


def test_formula_search_uses_batch_eval_and_matrix_cache(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    matrix_dir = tmp_path / "matrix"
    build_matrix_cache(data_dir, matrix_dir)
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(tmp_path / "corpus"), max_records=8))
    exit_code = run_search.main(
        [
            "--search-mode",
            "random",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "search"),
            "--seed",
            "42",
            "--population-size",
            "4",
            "--generations",
            "1",
            "--candidate-batch-size",
            "2",
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--min-coverage",
            "0.5",
            "--correlation-threshold",
            "0.99",
            "--use-matrix-cache",
            "--matrix-cache-dir",
            str(matrix_dir),
            "--use-batch-eval",
            "--batch-eval-output-dir",
            str(tmp_path / "batch_eval"),
            "--batch-eval-device",
            "cpu",
            "--corpus-path",
            corpus.paths["formula_corpus_path"],
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["candidates_evaluated"] > 0
    assert (tmp_path / "batch_eval" / "generation_0" / "formula_batch_eval_result.json").exists()
