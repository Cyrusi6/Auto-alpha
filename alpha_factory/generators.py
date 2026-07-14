"""Candidate generation ensemble for Alpha Factory."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Iterable

from factor_store import stable_formula_hash
from feature_factory import build_feature_semantics_map, feature_semantics_contract_hash, make_formula_vocab_from_manifest
from feature_promotion import load_promotion_gate
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
    vocab = make_formula_vocab_from_manifest(manifest)
    vm = StackVM(vocab)
    candidates: list[AlphaCandidateRecord] = []
    warnings: list[str] = []
    feature_meta = _feature_meta(manifest)
    feature_semantics = build_feature_semantics_map(manifest)
    semantics_contract_hash = feature_semantics_contract_hash(feature_semantics)
    required_families = _parse_csv_set(config.require_feature_family_ready)
    family_budget = _parse_family_budget(config.feature_family_budget)
    promotion_gate = load_promotion_gate(
        policy_path=config.feature_promotion_policy_path,
        allowlist_path=config.feature_promotion_allowlist_path,
        denylist_path=config.feature_promotion_denylist_path,
        require_promotion=config.require_feature_promotion,
        allow_risk_filter_features=config.allow_risk_filter_features,
    )

    def add(name: str, formula_tokens: list[int], formula_names: list[str], source: str, tags: list[str], refs: list[str] | None = None, metadata=None):
        nonlocal candidates
        formula_names = list(formula_names or _decode(formula_tokens, FORMULA_VOCAB))
        if _uses_disallowed_feature(formula_names, feature_meta, config.exclude_weak_pit_features):
            warnings.append(f"candidate skipped because feature is disabled or weak PIT: {name}")
            return
        if promotion_gate is not None:
            errors, gate_warnings, metadata = promotion_gate.check_formula_names(formula_names, feature_meta)
            if errors:
                warnings.append(f"candidate skipped by feature promotion gate: {name}: {'; '.join(errors)}")
                return
            if gate_warnings:
                warnings.append(f"candidate feature promotion warning: {name}: {'; '.join(gate_warnings)}")
        try:
            formula_tokens = [vocab.encode_name(item) for item in formula_names]
        except ValueError as exc:
            warnings.append(f"candidate skipped because token is not in feature set vocab: {name}: {exc}")
            return
        try:
            valid, reason = vm.validate_with_reason(formula_tokens)
            complexity = vm.formula_complexity(formula_tokens)
            formula_semantics = vm.formula_semantics(formula_tokens, feature_semantics)
            lookback = formula_semantics.max_raw_lag
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
                family_tags=_manifest_family_tags(formula_names, feature_meta, tags),
                validation_status="valid" if valid else "invalid",
                status="generated" if valid else "rejected",
                reject_reason=None if valid else reason,
                metadata=(metadata or {"name": name}) | {
                    "canonical_semantics_hash": formula_semantics.semantics_hash if valid else None,
                    "feature_semantics_contract_hash": semantics_contract_hash,
                    "canonical_max_raw_lag": formula_semantics.max_raw_lag if valid else None,
                    "required_observations": formula_semantics.required_observations if valid else None,
                },
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

    for spec in template_formulas(
        config.feature_set_name,
        manifest,
        exclude_weak_pit_features=config.exclude_weak_pit_features,
        required_feature_families=required_families,
        feature_family_budget=family_budget,
    )[: max(0, config.template_budget)]:
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
        generated = generate_initial_population(search_config, feature_semantics=feature_semantics)
        for candidate in generated[: max(0, config.random_budget)]:
            add(candidate.formula_hash, candidate.formula_tokens, candidate.formula_names, AlphaCandidateSource.random, _family_tags(candidate.formula_names), metadata={"source_generation": candidate.generation})
        parents = generated or []
        for parent in parents[: max(0, config.mutation_budget)]:
            child = mutate_formula(parent, rng, search_config, feature_semantics=feature_semantics)
            add(child.formula_hash, child.formula_tokens, child.formula_names, AlphaCandidateSource.mutation, _family_tags(child.formula_names), refs=[parent.formula_hash])
        for _idx in range(max(0, min(config.crossover_budget, len(parents) // 2))):
            left, right = rng.sample(parents, 2)
            child = crossover_formula(left, right, rng, search_config, feature_semantics=feature_semantics)
            add(child.formula_hash, child.formula_tokens, child.formula_names, AlphaCandidateSource.crossover, _family_tags(child.formula_names), refs=[left.formula_hash, right.formula_hash])
    except Exception as exc:
        warnings.append(f"random/mutation/crossover generation failed: {exc}")

    if config.formula_corpus_path:
        for record in _load_jsonl(config.formula_corpus_path)[: max(0, config.corpus_budget)]:
            tokens = [int(item) for item in record.get("formula_tokens", [])]
            names = list(record.get("formula_names") or _decode(tokens, FORMULA_VOCAB))
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


def _decode(tokens: Iterable[int], vocab=FORMULA_VOCAB) -> list[str]:
    names = []
    for token in tokens:
        try:
            names.append(vocab.token_name(int(token)))
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


def _feature_meta(manifest) -> dict[str, dict]:
    payload = manifest.to_dict() if hasattr(manifest, "to_dict") else dict(manifest)
    return {
        str(item.get("feature_name")): dict(item)
        for item in payload.get("feature_definitions", [])
        if isinstance(item, dict) and item.get("feature_name")
    }


def _uses_disallowed_feature(names: list[str], meta: dict[str, dict], exclude_weak_pit: bool) -> bool:
    for name in names:
        item = meta.get(name)
        if item is None:
            continue
        if not item.get("default_enabled", True) or not item.get("used_for_alpha", True):
            return True
        if exclude_weak_pit and item.get("pit_safety") != "pit_safe":
            return True
    return False


def _manifest_family_tags(names: list[str], meta: dict[str, dict], fallback: list[str]) -> list[str]:
    tags = list(fallback)
    for name in names:
        item = meta.get(name)
        if item and item.get("family"):
            tags.append(str(item["family"]))
    return sorted(set(tags or ["general"]))


def _parse_csv_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def _parse_family_budget(value: str | None) -> dict[str, int] | None:
    if not value:
        return None
    result: dict[str, int] = {}
    for part in value.split(","):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        try:
            result[key.strip()] = int(raw_value)
        except ValueError:
            continue
    return result or None


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
