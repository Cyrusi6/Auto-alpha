import json

import torch

from formula_corpus import FormulaCorpusConfig, build_formula_corpus
from model_core.alphagpt import AlphaGPT
from neural_search import AlphaGPTPretrainConfig, AlphaGPTPretrainer
from neural_search import run_pretrain


def test_alphagpt_pretrain_writes_checkpoint_and_history(tmp_path):
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(tmp_path / "corpus"), max_records=8))
    result = AlphaGPTPretrainer(
        AlphaGPTPretrainConfig(
            sequence_path=corpus.paths["formula_sequences_path"],
            preference_path=corpus.paths["formula_preferences_path"],
            output_dir=str(tmp_path / "pretrain"),
            epochs=1,
            batch_size=4,
            max_sequences=8,
            device="cpu",
        )
    ).train()

    latest = tmp_path / "pretrain" / "checkpoints" / "latest.pt"
    assert result.status == "success"
    assert latest.exists()
    assert (tmp_path / "pretrain" / "alphagpt_pretrain_history.jsonl").exists()
    model, metadata = AlphaGPT.load_checkpoint(latest, device="cpu")
    logits, value, _task_probs = model(torch.tensor([[0]], dtype=torch.long))
    assert logits.shape[-1] == model.vocab_size
    assert metadata["vocab_size"] == model.vocab_size
    assert value.shape[0] == 1


def test_alphagpt_pretrain_cli(tmp_path, capsys):
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(tmp_path / "corpus"), max_records=8))
    exit_code = run_pretrain.main(
        [
            "--sequence-path",
            corpus.paths["formula_sequences_path"],
            "--output-dir",
            str(tmp_path / "pretrain_cli"),
            "--epochs",
            "1",
            "--batch-size",
            "4",
            "--max-sequences",
            "8",
            "--device",
            "cpu",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "success"
    assert (tmp_path / "pretrain_cli" / "checkpoint_manifest.json").exists()


def test_alphagpt_pretrain_distributed_cpu_fallback_metadata(tmp_path, capsys):
    corpus = build_formula_corpus(FormulaCorpusConfig(output_dir=str(tmp_path / "corpus"), max_records=8))
    exit_code = run_pretrain.main(
        [
            "--sequence-path",
            corpus.paths["formula_sequences_path"],
            "--output-dir",
            str(tmp_path / "pretrain_ddp"),
            "--epochs",
            "1",
            "--batch-size",
            "4",
            "--max-sequences",
            "8",
            "--device",
            "cpu",
            "--distributed",
            "--world-size",
            "1",
            "--rank",
            "0",
            "--local-rank",
            "0",
            "--resource-report-path",
            str(tmp_path / "resource_usage.json"),
            "--save-rank0-only",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    report = json.loads((tmp_path / "pretrain_ddp" / "distributed_training_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "pretrain_ddp" / "checkpoint_manifest.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["summary"]["distributed"] is True
    assert report["distributed"] is True
    assert report["world_size"] == 1
    assert report["fallback_to_cpu"] is True
    assert manifest["rank0_only"] is True
    assert (tmp_path / "resource_usage.json").exists()
