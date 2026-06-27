import json
import math

import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from factor_store import LocalFactorStore
from model_core.alphagpt import AlphaGPT, count_parameters
from model_core.vocab import FORMULA_VOCAB
from neural_search.action_mask import build_action_mask, explain_available_actions
from neural_search.dataset import FormulaSequenceDataset
from neural_search.models import NeuralSearchConfig
from neural_search.sampler import NeuralFormulaSampler
from neural_search.trainer import NeuralFormulaTrainer
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def _prepare_sample_universe(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    build_universe_from_storage(
        LocalAshareStorage(data_dir),
        UniverseBuildConfig(
            universe_name="csi300_sample",
            as_of_date="20240104",
            min_listed_days=0,
            min_amount=0,
            use_index_members=True,
            index_code="000300.SH",
        ),
    )
    return data_dir


def test_alphagpt_checkpoint_roundtrip(tmp_path):
    model = AlphaGPT()
    assert count_parameters(model) > 0

    checkpoint = model.save_checkpoint(tmp_path / "checkpoint.pt", metadata={"run": "unit"})
    loaded, metadata = AlphaGPT.load_checkpoint(checkpoint, device="cpu")
    logits, value, task_probs = loaded(torch.tensor([[FORMULA_VOCAB.encode_name("RET_1D")]], dtype=torch.long))

    assert metadata["run"] == "unit"
    assert logits.shape[-1] == FORMULA_VOCAB.size
    assert value.shape == (1, 1)
    assert task_probs.shape[0] == 1
    json.dumps(metadata)


def test_action_mask_respects_stack_rules():
    feature = FORMULA_VOCAB.encode_name("RET_1D")
    add = FORMULA_VOCAB.encode_name("ADD")
    zscore = FORMULA_VOCAB.encode_name("CS_ZSCORE")

    start_mask = build_action_mask([], max_formula_len=4, min_formula_len=2)
    assert bool(start_mask[feature])
    assert not bool(start_mask[add])

    one_feature_mask = build_action_mask([feature], max_formula_len=4, min_formula_len=2)
    assert bool(one_feature_mask[zscore])
    assert not bool(one_feature_mask[add])

    two_feature_mask = build_action_mask([feature, feature], max_formula_len=4, min_formula_len=2)
    assert bool(two_feature_mask[add])
    assert "CS_ZSCORE" in explain_available_actions([feature])


def test_neural_sampler_is_reproducible_and_valid():
    torch.manual_seed(7)
    model = AlphaGPT()
    model.eval()
    sampler_a = NeuralFormulaSampler(model, seed=11, max_formula_len=6, max_complexity=24, max_lookback=10)
    sampler_b = NeuralFormulaSampler(model, seed=11, max_formula_len=6, max_complexity=24, max_lookback=10)

    samples_a = [sample.to_dict() for sample in sampler_a.sample_batch(3)]
    samples_b = [sample.to_dict() for sample in sampler_b.sample_batch(3)]

    assert samples_a == samples_b
    assert all("tokens" in sample for sample in samples_a)
    assert any(sample["valid"] for sample in samples_a)


def test_supervised_warmup_updates_parameters(tmp_path):
    trainer = NeuralFormulaTrainer(
        config=NeuralSearchConfig(warmup_steps=1, policy_steps=0, batch_size=2),
        data_dir=str(tmp_path / "data"),
        universe_name=None,
        universe_file=None,
        factor_store_dir=str(tmp_path / "store"),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "neural"),
    )
    before = next(trainer.model.parameters()).detach().clone()
    history = trainer.supervised_warmup()
    after = next(trainer.model.parameters()).detach()

    assert history
    assert math.isfinite(history[-1].loss)
    assert not torch.equal(before, after)
    assert len(FormulaSequenceDataset.from_defaults(LocalFactorStore(tmp_path / "store"))) > 0


def test_neural_trainer_policy_search_writes_artifacts(tmp_path):
    data_dir = _prepare_sample_universe(tmp_path)
    trainer = NeuralFormulaTrainer(
        config=NeuralSearchConfig(
            seed=42,
            warmup_steps=1,
            policy_steps=1,
            batch_size=2,
            samples_per_step=2,
            max_formula_len=6,
            factor_transform="winsorize_zscore",
            enable_gate=True,
            top_k=2,
        ),
        data_dir=str(data_dir),
        universe_name="csi300_sample",
        universe_file=None,
        factor_store_dir=str(tmp_path / "store"),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "neural"),
        correlation_threshold=0.99,
        min_coverage=0.5,
    )
    result = trainer.train()

    assert result.candidates_evaluated > 0
    assert result.training_history
    assert all(math.isfinite(row["loss"]) for row in result.training_history)
    assert (tmp_path / "neural" / "neural_search_result.json").exists()
    assert (tmp_path / "neural" / "neural_training_history.jsonl").exists()
    assert (tmp_path / "neural" / "neural_search_report.md").exists()
    assert list((tmp_path / "neural" / "checkpoints").glob("*.pt"))
