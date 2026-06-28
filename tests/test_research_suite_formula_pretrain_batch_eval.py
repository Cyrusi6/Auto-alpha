import json

from research_suite.run_suite import main as run_suite_main


def test_research_suite_formula_corpus_pretrain_batch_eval(tmp_path, capsys):
    exit_code = run_suite_main(
        [
            "--suite-name",
            "formula_pretrain_suite",
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path / "data"),
            "--universe-name",
            "csi300_sample",
            "--index-code",
            "000300.SH",
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "suite"),
            "--backtest-dir",
            str(tmp_path / "backtest"),
            "--orders-dir",
            str(tmp_path / "orders"),
            "--as-of-date",
            "20240104",
            "--search-mode",
            "random",
            "--search-population-size",
            "4",
            "--search-generations",
            "1",
            "--search-max-candidates",
            "2",
            "--top-k",
            "2",
            "--build-formula-corpus",
            "--pretrain-alphagpt",
            "--pretrain-epochs",
            "1",
            "--pretrain-batch-size",
            "4",
            "--pretrain-max-sequences",
            "8",
            "--pretrain-device",
            "cpu",
            "--use-batch-eval",
            "--batch-eval-device",
            "cpu",
            "--batch-eval-chunk-size",
            "2",
            "--walk-forward-train-size",
            "1",
            "--walk-forward-test-size",
            "1",
            "--walk-forward-step-size",
            "1",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "success"
    stage_names = [stage["name"] for stage in payload["stages"]]
    assert "formula_corpus" in stage_names
    assert "alphagpt_pretrain" in stage_names
    assert "formula_batch_eval" in stage_names
    assert (tmp_path / "suite" / "formula_corpus" / "formula_corpus.jsonl").exists()
    assert (tmp_path / "suite" / "alphagpt_pretrain" / "checkpoint_manifest.json").exists()
    assert (tmp_path / "suite" / "formula_batch_eval" / "formula_batch_eval_result.json").exists()
    catalog = json.loads((tmp_path / "suite" / "artifact_catalog.json").read_text())
    names = {entry["name"] for entry in catalog["entries"]}
    assert "formula_corpus" in names
    assert "alphagpt_pretrain_result" in names
    assert "formula_batch_eval_result" in names
