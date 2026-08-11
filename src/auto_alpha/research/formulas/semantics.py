"""Formula runtime configuration, vocabulary, and validity semantics."""

from __future__ import annotations

from dataclasses import dataclass

from auto_alpha.research.formulas.operators import OPS_CONFIG


FEATURE_NAMES = (
    "RET_1D",
    "RET_5D",
    "AMPLITUDE",
    "TURNOVER_RATE",
    "VOLUME_RATIO",
    "LOG_AMOUNT",
    "LOG_MKT_CAP",
    "PB",
    "PE_TTM",
    "ROE",
    "REVENUE_YOY",
)


@dataclass(frozen=True)
class FormulaVocab:
    feature_names: tuple[str, ...]
    operator_names: tuple[str, ...]

    @property
    def feature_count(self) -> int:
        return len(self.feature_names)

    @property
    def operator_offset(self) -> int:
        return self.feature_count

    @property
    def token_names(self) -> tuple[str, ...]:
        return self.feature_names + self.operator_names

    @property
    def size(self) -> int:
        return len(self.token_names)

    def token_name(self, token_id: int) -> str:
        return self.token_names[int(token_id)]

    def encode_name(self, name: str) -> int:
        return self.token_names.index(name)

    def decode_tokens(self, tokens: list[int]) -> list[str]:
        return [self.token_name(token) for token in tokens]


FORMULA_VOCAB = FormulaVocab(
    feature_names=FEATURE_NAMES,
    operator_names=tuple(cfg[0] for cfg in OPS_CONFIG),
)


def make_formula_vocab(
    feature_names: list[str] | tuple[str, ...] | None = None,
    operator_names: list[str] | tuple[str, ...] | None = None,
) -> FormulaVocab:
    return FormulaVocab(
        feature_names=tuple(feature_names or FEATURE_NAMES),
        operator_names=tuple(operator_names or tuple(cfg[0] for cfg in OPS_CONFIG)),
    )

import os
from pathlib import Path

import torch

from auto_alpha.research.formulas.semantics import FORMULA_VOCAB


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

import torch

from auto_alpha.research.formulas.operators import get_operator_spec


def propagate_operator_validity(name: str, args: list[torch.Tensor], values: list[torch.Tensor]) -> torch.Tensor:
    if not args:
        raise ValueError("validity propagation requires inputs")
    name = str(name).upper()
    if name in {"ADD", "SUB", "MUL"}:
        return args[0] & args[1]
    if name == "DIV":
        return args[0] & args[1] & torch.isfinite(values[1]) & (torch.abs(values[1]) >= 1e-6)
    if name in {"NEG", "ABS", "SIGN", "WINSORIZE"}:
        return args[0]
    if name.startswith("DELAY"):
        return _delay(args[0], int(name.removeprefix("DELAY")))
    if name.startswith("DELTA"):
        periods = int(name.removeprefix("DELTA"))
        return args[0] & _delay(args[0], periods)
    if name.startswith("TS_CORR"):
        window = int(name.removeprefix("TS_CORR"))
        return _rolling_all(args[0] & args[1], window)
    if name.startswith("TS_"):
        window = int(name.rsplit("_", 1)[-1].removeprefix("ZSCORE").removeprefix("RANK").removeprefix("MEAN").removeprefix("STD").removeprefix("MIN").removeprefix("MAX"))
        return _rolling_all(args[0], window)
    if name in {"CS_RANK", "CS_ZSCORE"}:
        breadth_ok = args[0].sum(dim=0, keepdim=True) >= 2
        return args[0] & breadth_ok
    raise KeyError(f"missing validity rule for operator: {name}")


def execute_operator_with_validity(
    token: int,
    operator_offset: int,
    values: list[torch.Tensor],
    masks: list[torch.Tensor],
) -> tuple[torch.Tensor, torch.Tensor]:
    """Execute an operator without allowing invalid inputs into its statistics."""
    spec = get_operator_spec(token, operator_offset)
    name = spec.name.upper()
    valid = propagate_operator_validity(name, masks, values)
    if name == "CS_RANK":
        result = _masked_cs_rank(values[0], valid)
    elif name == "CS_ZSCORE":
        result = _masked_cs_zscore(values[0], valid)
    else:
        clean_values = [torch.where(mask, value, torch.zeros_like(value)) for value, mask in zip(values, masks, strict=True)]
        result = spec.func(*clean_values)
    valid = valid & torch.isfinite(result)
    return torch.where(valid, result, torch.zeros_like(result)), valid


def _masked_cs_rank(values: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    result = torch.zeros_like(values)
    for date_index in range(values.shape[1]):
        date_mask = mask[:, date_index]
        count = int(date_mask.sum().item())
        if count < 2:
            continue
        eligible = values[date_mask, date_index]
        order = torch.argsort(eligible, stable=True)
        sorted_values = eligible[order]
        sorted_ranks = torch.empty_like(sorted_values)
        start = 0
        while start < count:
            end = start + 1
            while end < count and bool(sorted_values[end] == sorted_values[start]):
                end += 1
            sorted_ranks[start:end] = (start + end - 1) / 2.0
            start = end
        ranks = torch.empty_like(sorted_ranks)
        ranks[order] = sorted_ranks / max(count - 1, 1)
        result[date_mask, date_index] = ranks
    return result


def _masked_cs_zscore(values: torch.Tensor, mask: torch.Tensor, limit: float = 5.0) -> torch.Tensor:
    masked = torch.where(mask, values, torch.zeros_like(values))
    count = mask.sum(dim=0, keepdim=True).clamp_min(1).to(values.dtype)
    mean = masked.sum(dim=0, keepdim=True) / count
    centered = torch.where(mask, values - mean, torch.zeros_like(values))
    variance = centered.square().sum(dim=0, keepdim=True) / count
    scale = torch.sqrt(variance).clamp_min(1e-6)
    return torch.clamp(centered / scale, -limit, limit)


def _delay(mask: torch.Tensor, periods: int) -> torch.Tensor:
    result = torch.zeros_like(mask, dtype=torch.bool)
    if periods <= 0:
        return mask.bool()
    if mask.shape[1] > periods:
        result[:, periods:] = mask[:, :-periods]
    return result


def _rolling_all(mask: torch.Tensor, window: int) -> torch.Tensor:
    result = torch.zeros_like(mask, dtype=torch.bool)
    if window <= 1:
        return mask.bool()
    if mask.shape[1] >= window:
        windows = mask.to(torch.int16).unfold(1, window, 1)
        result[:, window - 1 :] = windows.sum(dim=-1) == window
    return result
