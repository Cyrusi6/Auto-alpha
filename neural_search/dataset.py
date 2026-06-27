"""Warm-start formula sequence dataset for AlphaGPT."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import torch

from factor_store import LocalFactorStore
from formula_search.generator import generate_seed_formulas
from research.candidates import default_candidates, load_candidates_json


def load_formula_records_from_store(store: LocalFactorStore) -> list[list[int]]:
    return [record.formula_tokens for record in store.load_factors()]


def load_candidates_from_json(path: str | Path) -> list[list[int]]:
    return [candidate.formula_tokens for candidate in load_candidates_json(path)]


def build_supervised_sequences(formulas: Iterable[list[int]]) -> list[tuple[list[int], int]]:
    sequences: list[tuple[list[int], int]] = []
    for formula in formulas:
        tokens = [int(token) for token in formula]
        for index in range(1, len(tokens)):
            sequences.append((tokens[:index], tokens[index]))
    return sequences


class FormulaSequenceDataset:
    def __init__(self, formulas: Iterable[list[int]]):
        self.samples = build_supervised_sequences(formulas)

    @classmethod
    def from_defaults(cls, store: LocalFactorStore | None = None, candidates_json: str | Path | None = None) -> "FormulaSequenceDataset":
        formulas: list[list[int]] = []
        formulas.extend(candidate.formula_tokens for candidate in default_candidates())
        formulas.extend(candidate.formula_tokens for candidate in generate_seed_formulas())
        if store is not None:
            formulas.extend(load_formula_records_from_store(store))
        if candidates_json is not None:
            formulas.extend(load_candidates_from_json(candidates_json))
        return cls(formulas)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        prefix, target = self.samples[index]
        return torch.tensor(prefix, dtype=torch.long), torch.tensor(target, dtype=torch.long)

    def to_jsonl(self, path: str | Path) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for prefix, target in self.samples:
                handle.write(json.dumps({"prefix": prefix, "target": target}, ensure_ascii=False))
                handle.write("\n")
        return output_path
