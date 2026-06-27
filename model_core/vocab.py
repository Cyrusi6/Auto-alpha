"""Formula vocabulary for A-share factor research."""

from __future__ import annotations

from dataclasses import dataclass

from .ops import OPS_CONFIG


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
