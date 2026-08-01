"""Neural-guided A-share formula search."""

from .action_mask import build_action_mask, explain_available_actions, masked_sample
from .dataset import FormulaSequenceDataset, build_supervised_sequences, load_formula_records_from_store
from .models import (
    AlphaGPTCheckpointManifest,
    AlphaGPTPretrainConfig,
    AlphaGPTPretrainEpoch,
    AlphaGPTPretrainResult,
    NeuralSearchCheckpointInfo,
    NeuralSearchConfig,
    NeuralSearchResult,
    NeuralTrainingStep,
    PolicySample,
    PreferenceTrainingStep,
)
from .pretrain import AlphaGPTPretrainer
from .reward import formula_reward_from_research_result
from .sampler import NeuralFormulaSampler
from .trainer import NeuralFormulaTrainer

__all__ = [
    "FormulaSequenceDataset",
    "AlphaGPTCheckpointManifest",
    "AlphaGPTPretrainConfig",
    "AlphaGPTPretrainEpoch",
    "AlphaGPTPretrainResult",
    "AlphaGPTPretrainer",
    "NeuralFormulaSampler",
    "NeuralFormulaTrainer",
    "NeuralSearchCheckpointInfo",
    "NeuralSearchConfig",
    "NeuralSearchResult",
    "NeuralTrainingStep",
    "PolicySample",
    "PreferenceTrainingStep",
    "build_action_mask",
    "build_supervised_sequences",
    "explain_available_actions",
    "formula_reward_from_research_result",
    "load_formula_records_from_store",
    "masked_sample",
]
