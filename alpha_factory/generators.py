"""Candidate generation ensemble for Alpha Factory."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Iterable

from factor_store import stable_formula_hash
from formula_search.generator import generate_initial_population
from formula_search.models import FormulaSearchConfig
from formula_search.mutation import crossover_formula, mutate_formula
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from research.candidates import default_candidates, load_candidates_json

from .models import AlphaCandidateRecord, AlphaCandidateSource
from .templates import template_formulas


OPERATOR_VERSION = "ashare_ops_v1"


def generate_alpha_candidates(config, manifest) -> tuple[list[AlphaCandidateRecord], list[str]]:
    rng = random.Random(config.seed)
    vm = StackVM()
    candidates: list[AlphaCandidateRecord] = []
    warnings: list[str] = []

    def add(name: str, formula_tokens: list[int], formula_names: list[str], source: str, tags: list[str], refs: list[str] | None = None, metadata=None):
        nonlocal candidates
        try:
            valid, reason = vm.validate_with_reason(formula_tokens)
            complexity = vm.formula_complexity(formula_tokens)
            lookback = vm.formula_lookback(formula_tokens)
        except Exception as exc:
            valid, reason, complexity, lookback = False, str(exc), len(formula_tokens), 0
        formula_hash = stable_formula_hash(formula_tokens, formula_names, manifest.feature_version, manifest.operator_version)
        alpha_id = f"alpha_{formula_hash[:16]}"
        candidates.append(
            AlphaCandidateRecord(
                alpha_candidate_id=alpha_id,
                formula_hash=formula_hash,
                formula_tokens=list(formula_tokens),
                formula_names=list(formula_names),
                source=source,
                source_refs=refs or [],
                feature_set_name=manifest.feature_set_name,
                feature_version=manifest.feature_version,
                operator_version=manifest.operator_version,
                complexity=int(complexity),
                lookback=int(lookback),
                family_tags=tags,
                validation_status="valid" if valid else "invalid",
                status="generated" if valid else "rejected",
                reject_reason=None if valid else reason,
                metadata=metadata or {"name": name},
            )
        )

    try:
        for candidate in default_candidates()[: max(0, config.candidate_budget)]:
            add(
                candidate.name,
                candidate.formula_tokens,
                candidate.formula_names,
                AlphaCandidateSource.default_candidates,
                _family_tags(candidate.formula_names),
                metadata={"description": candidate.description},
            )
    except Exception as exc:
        warnings.append(f"default_candidates failed: {exc}")

    for spec in template_formulas(config.feature_set_name)[: max(0, config.template_budget)]:
        add(str(spec["name"]), list(spec["formula_tokens"]), list(spec["formula_names"]), AlphaCandidateSource.template, list(spec["family_tags"]))

    try:
        search_config = FormulaSearchConfig(
            seed=config.seed,
            population_size=max(config.random_budget, 1),
            generations=1,
            max_formula_len=config.max_formula_len,
            max_complexity=config.max_complexity,
            max_lookback=config.max_lookback,
        )
        generated = generate_initial_population(search_config)
        for candidate in generated[: max(0, config.random_budget)]:
            add(candidate.formula_hash, candidate.formula_tokens, candidate.formula_names, AlphaCandidateSource.random, _family_tags(candidate.formula_names), metadata={"source_generation": candidate.generation})
        parents = generated or []
        for parent in parents[: max(0, config.mutation_budget)]:
            child = mutate_formula(parent, rng, search_config)
            add(child.formula_hash, child.formula_tokens, child.formula_names, AlphaCandidateSource.mutation, _family_tags(child.formula_names), refs=[parent.formula_hash])
        for _idx in range(max(0, min(config.crossover_budget, len(parents) // 2))):
            left, right = rng.sample(parents, 2)
            child = crossover_formula(left, right, rng, search_config)
            add(child.formula_hash, child.formula_tokens, child.formula_names, AlphaCandidateSource.crossover, _family_tags(child.formula_names), refs=[left.formula_hash, right.formula_hash])
    except Exception as exc:
        warnings.append(f"random/mutation/crossover generation failed: {exc}")

    if config.formula_corpus_path:
        for record in _load_jsonl(config.formula_corpus_path)[: max(0, config.corpus_budget)]:
            tokens = [int(item) for item in record.get("formula_tokens", [])]
            names = list(record.get("formula_names") or _decode(tokens))
            add(str(record.get("formula_hash", "corpus")), tokens, names, AlphaCandidateSource.formula_corpus, _family_tags(names), refs=[str(record.get("formula_hash", ""))], metadata={"corpus_record": record.get("formula_hash")})

    if config.candidates_json:
        try:
            for candidate in load_candidates_json(config.candidates_json):
                add(candidate.name, candidate.formula_tokens, candidate.formula_names, AlphaCandidateSource.imported, _family_tags(candidate.formula_names), metadata={"description": candidate.description})
        except Exception as exc:
            warnings.append(f"imported candidates failed: {exc}")

    deduped: dict[tuple[str, str], AlphaCandidateRecord] = {}
    for candidate in candidates:
        key = (candidate.formula_hash, candidate.feature_version)
        if key not in deduped:
            deduped[key] = candidate
    return _round_robin_by_source(list(deduped.values()), max(config.candidate_budget, 0)), warnings


def _load_jsonl(path: str) -> list[dict]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _decode(tokens: Iterable[int]) -> list[str]:
    names = []
    for token in tokens:
        try:
            names.append(FORMULA_VOCAB.token_name(int(token)))
        except Exception:
            names.append(str(token))
    return names


def _family_tags(names: list[str]) -> list[str]:
    tags: list[str] = []
    for name in names:
        if "RET" in name:
            tags.append("price_return")
        elif name in {"LOG_AMOUNT", "TURNOVER_RATE", "VOLUME_RATIO"}:
            tags.append("liquidity")
        elif name in {"PB", "PE_TTM", "PS_TTM"}:
            tags.append("valuation")
        elif name in {"ROE"}:
            tags.append("quality")
        elif name in {"REVENUE_YOY"}:
            tags.append("growth")
        elif "VOL" in name or name == "AMPLITUDE":
            tags.append("volatility")
        elif "MKT_CAP" in name:
            tags.append("size")
    return sorted(set(tags or ["general"]))


def _round_robin_by_source(candidates: list[AlphaCandidateRecord], budget: int) -> list[AlphaCandidateRecord]:
    if budget <= 0:
        return []
    buckets: dict[str, list[AlphaCandidateRecord]] = {}
    for candidate in candidates:
        buckets.setdefault(candidate.source, []).append(candidate)
    selected: list[AlphaCandidateRecord] = []
    sources = list(buckets)
    while len(selected) < budget and any(buckets.values()):
        for source in sources:
            if not buckets.get(source):
                continue
            selected.append(buckets[source].pop(0))
            if len(selected) >= budget:
                break
    return selected
