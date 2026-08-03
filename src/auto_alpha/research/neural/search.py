"""Neural-guided A-share formula search."""

from auto_alpha.research.neural.search_action_mask import build_action_mask
from auto_alpha.research.neural.search_action_mask import explain_available_actions
from auto_alpha.research.neural.search_action_mask import masked_sample
from auto_alpha.research.neural.search_dataset import FormulaSequenceDataset
from auto_alpha.research.neural.search_dataset import build_supervised_sequences
from auto_alpha.research.neural.search_dataset import load_formula_records_from_store
from auto_alpha.research.neural.search_models import AlphaGPTCheckpointManifest
from auto_alpha.research.neural.search_models import AlphaGPTPretrainConfig
from auto_alpha.research.neural.search_models import AlphaGPTPretrainEpoch
from auto_alpha.research.neural.search_models import AlphaGPTPretrainResult
from auto_alpha.research.neural.search_models import NeuralSearchCheckpointInfo
from auto_alpha.research.neural.search_models import NeuralSearchConfig
from auto_alpha.research.neural.search_models import NeuralSearchResult
from auto_alpha.research.neural.search_models import NeuralTrainingStep
from auto_alpha.research.neural.search_models import PolicySample
from auto_alpha.research.neural.search_models import PreferenceTrainingStep
from auto_alpha.research.neural.search_pretrain import AlphaGPTPretrainer
from auto_alpha.research.neural.search_reward import formula_reward_from_research_result
from auto_alpha.research.neural.search_sampler import NeuralFormulaSampler
from auto_alpha.research.neural.search_trainer import NeuralFormulaTrainer

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
