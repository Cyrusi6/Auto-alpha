"""Candidate formula sources for batch factor research."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from factor_store import stable_formula_hash
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB

from .models import FactorCandidate


FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


def default_candidates() -> list[FactorCandidate]:
    specs = [
        ("ret_1d", ["RET_1D"], "One-day return signal."),
        ("ret_5d", ["RET_5D"], "Five-day return signal."),
        ("turnover_rate", ["TURNOVER_RATE"], "Turnover signal."),
        ("volume_ratio", ["VOLUME_RATIO"], "Volume ratio signal."),
        ("log_amount", ["LOG_AMOUNT"], "Trading amount signal."),
        ("log_mkt_cap", ["LOG_MKT_CAP"], "Market capitalization signal."),
        ("pb", ["PB"], "Book-to-price valuation signal."),
        ("pe_ttm", ["PE_TTM"], "Trailing earnings valuation signal."),
        ("roe", ["ROE"], "Return on equity signal."),
        ("revenue_yoy", ["REVENUE_YOY"], "Revenue growth signal."),
        ("rank_roe", ["ROE", "CS_RANK"], "Cross-sectional rank of profitability."),
        ("zscore_ret_1d", ["RET_1D", "CS_ZSCORE"], "Cross-sectional return z-score."),
        ("mean5_ret_1d", ["RET_1D", "TS_MEAN5"], "Five-day mean return."),
        ("rank5_ret_1d", ["RET_1D", "TS_RANK5"], "Five-day time-series return rank."),
        ("delta5_amount", ["LOG_AMOUNT", "DELTA5"], "Five-day amount change."),
        ("corr5_ret_turnover", ["RET_1D", "TURNOVER_RATE", "TS_CORR5"], "Return and turnover rolling correlation."),
        ("ret_1d_plus_roe", ["RET_1D", "ROE", "ADD"], "Return plus profitability."),
        ("ret_5d_minus_pb", ["RET_5D", "PB", "SUB"], "Momentum minus valuation."),
        ("roe_minus_pb", ["ROE", "PB", "SUB", "CS_RANK"], "Ranked profitability minus valuation."),
        ("growth_quality", ["REVENUE_YOY", "ROE", "ADD", "CS_ZSCORE"], "Growth plus profitability quality."),
    ]
    candidates = [_make_candidate(name, names, description, source="default", generation=0) for name, names, description in specs]
    _validate_candidates(candidates)
    return candidates


def from_formula_search_candidates(candidates: Iterable[object]) -> list[FactorCandidate]:
    converted: list[FactorCandidate] = []
    for idx, candidate in enumerate(candidates):
        formula_tokens = [int(token) for token in getattr(candidate, "formula_tokens")]
        formula_names = list(getattr(candidate, "formula_names"))
        converted.append(
            _make_candidate(
                name=f"search_{idx}_{getattr(candidate, 'formula_hash', '')[:8]}",
                formula_names=formula_names,
                description=f"Search generated formula {idx}",
                formula_tokens=formula_tokens,
                formula_hash=getattr(candidate, "formula_hash", None),
                complexity=getattr(candidate, "complexity", None),
                lookback=getattr(candidate, "lookback", None),
                source=getattr(candidate, "source", "search"),
                parent_hashes=getattr(candidate, "parent_hashes", None),
                generation=getattr(candidate, "generation", None),
                validation_reason=getattr(candidate, "validation_reason", None),
            )
        )
    _validate_candidates(converted)
    return converted


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

    return _make_candidate(
        name=name,
        formula_names=formula_names,
        description=payload.get("description"),
        formula_tokens=formula_tokens,
        formula_hash=payload.get("formula_hash"),
        complexity=payload.get("complexity"),
        lookback=payload.get("lookback"),
        source=payload.get("source"),
        parent_hashes=payload.get("parent_hashes"),
        generation=payload.get("generation"),
        validation_reason=payload.get("validation_reason"),
    )


def _make_candidate(
    name: str,
    formula_names: list[str],
    description: str | None = None,
    formula_tokens: list[int] | None = None,
    formula_hash: str | None = None,
    complexity: int | None = None,
    lookback: int | None = None,
    source: str | None = None,
    parent_hashes: list[str] | None = None,
    generation: int | None = None,
    validation_reason: str | None = None,
) -> FactorCandidate:
    vm = StackVM()
    tokens = formula_tokens or [FORMULA_VOCAB.encode_name(name) for name in formula_names]
    names = FORMULA_VOCAB.decode_tokens(tokens)
    valid, reason = vm.validate_with_reason(tokens)
    if not valid:
        validation_reason = validation_reason or reason
    return FactorCandidate(
        name=name,
        formula_tokens=[int(token) for token in tokens],
        formula_names=names,
        description=description,
        formula_hash=formula_hash
        or stable_formula_hash([int(token) for token in tokens], names, FEATURE_VERSION, OPERATOR_VERSION),
        complexity=int(complexity) if complexity is not None else vm.formula_complexity(tokens),
        lookback=int(lookback) if lookback is not None else vm.formula_lookback(tokens),
        source=source,
        parent_hashes=parent_hashes,
        generation=generation,
        validation_reason=validation_reason or reason,
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
        valid, reason = vm.validate_with_reason(candidate.formula_tokens)
        if not valid:
            raise ValueError(f"candidate {candidate.name} has invalid formula arity: {reason}")
