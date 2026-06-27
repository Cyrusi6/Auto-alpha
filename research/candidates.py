"""Candidate formula sources for batch factor research."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB

from .models import FactorCandidate


def default_candidates() -> list[FactorCandidate]:
    enc = FORMULA_VOCAB.encode_name
    specs = [
        ("ret_1d", ["RET_1D"], "One-day return signal."),
        ("ret_5d", ["RET_5D"], "Five-day return signal."),
        ("turnover_rate", ["TURNOVER_RATE"], "Turnover signal."),
        ("log_amount", ["LOG_AMOUNT"], "Trading amount signal."),
        ("log_mkt_cap", ["LOG_MKT_CAP"], "Market capitalization signal."),
        ("pb", ["PB"], "Book-to-price valuation signal."),
        ("pe_ttm", ["PE_TTM"], "Trailing earnings valuation signal."),
        ("roe", ["ROE"], "Return on equity signal."),
        ("revenue_yoy", ["REVENUE_YOY"], "Revenue growth signal."),
        ("ret_1d_plus_roe", ["RET_1D", "ROE", "ADD"], "Return plus profitability."),
        ("ret_5d_minus_pb", ["RET_5D", "PB", "SUB"], "Momentum minus valuation."),
        ("rank_roe", ["ROE", "CS_RANK"], "Cross-sectional rank of profitability."),
    ]
    candidates = [
        FactorCandidate(
            name=name,
            formula_tokens=[enc(token_name) for token_name in formula_names],
            formula_names=formula_names,
            description=description,
        )
        for name, formula_names, description in specs
    ]
    _validate_candidates(candidates)
    return candidates


def load_candidates_json(path: str | Path) -> list[FactorCandidate]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("candidate JSON must contain a list")
    candidates = [_candidate_from_payload(item) for item in payload]
    _validate_candidates(candidates)
    return candidates


def save_candidates_json(candidates: list[FactorCandidate], path: str | Path) -> Path:
    _validate_candidates(candidates)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _candidate_from_payload(payload: Any) -> FactorCandidate:
    if not isinstance(payload, dict):
        raise ValueError("each candidate must be an object")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("candidate name is required")

    names_payload = payload.get("formula_names")
    tokens_payload = payload.get("formula_tokens")
    if names_payload is None and tokens_payload is None:
        raise ValueError(f"candidate {name} must define formula_names or formula_tokens")

    if names_payload is not None:
        if not isinstance(names_payload, list) or not all(isinstance(item, str) for item in names_payload):
            raise ValueError(f"candidate {name} formula_names must be a list of strings")
        formula_names = [str(item) for item in names_payload]
        try:
            formula_tokens = [FORMULA_VOCAB.encode_name(item) for item in formula_names]
        except ValueError as exc:
            raise ValueError(f"candidate {name} references an unknown formula name") from exc
    else:
        if not isinstance(tokens_payload, list):
            raise ValueError(f"candidate {name} formula_tokens must be a list")
        formula_tokens = [int(item) for item in tokens_payload]
        try:
            formula_names = FORMULA_VOCAB.decode_tokens(formula_tokens)
        except (IndexError, ValueError) as exc:
            raise ValueError(f"candidate {name} has invalid token id") from exc

    return FactorCandidate(
        name=name,
        formula_tokens=formula_tokens,
        formula_names=formula_names,
        description=payload.get("description"),
    )


def _validate_candidates(candidates: list[FactorCandidate]) -> None:
    vm = StackVM()
    for candidate in candidates:
        for token in candidate.formula_tokens:
            if int(token) < 0 or int(token) >= FORMULA_VOCAB.size:
                raise ValueError(f"candidate {candidate.name} has invalid token id: {token}")
        decoded = FORMULA_VOCAB.decode_tokens([int(token) for token in candidate.formula_tokens])
        if decoded != candidate.formula_names:
            raise ValueError(f"candidate {candidate.name} formula_names do not match formula_tokens")
        if not vm.validate(candidate.formula_tokens):
            raise ValueError(f"candidate {candidate.name} has invalid formula arity")
