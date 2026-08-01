"""Configuration for A-share factor research."""

from __future__ import annotations

import os
from pathlib import Path

import torch

from .vocab import FORMULA_VOCAB


class ModelConfig:
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    BATCH_SIZE = int(os.getenv("ALPHA_BATCH_SIZE", "128"))
    TRAIN_STEPS = int(os.getenv("ALPHA_TRAIN_STEPS", "10"))
    MAX_FORMULA_LEN = int(os.getenv("ALPHA_MAX_FORMULA_LEN", "8"))
    DATA_DIR = Path(os.getenv("ASHARE_MODEL_DATA_DIR") or os.getenv("ASHARE_DATA_DIR") or "data/ashare")
    OUTPUT_DIR = Path(os.getenv("ALPHA_OUTPUT_DIR") or "artifacts/factors")
    MIN_COVERAGE = float(os.getenv("ALPHA_MIN_COVERAGE", "0.5"))
    TOP_BOTTOM_QUANTILE = float(os.getenv("ALPHA_TOP_BOTTOM_QUANTILE", "0.33"))
    INPUT_DIM = FORMULA_VOCAB.feature_count
