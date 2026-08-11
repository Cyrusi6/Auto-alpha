"""Formula generation, diversity, novelty, and static-admission logic."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact


def select_shortlist(candidates, *, top_k: int, max_per_family: int, min_novelty_score: float) -> tuple[list, list, dict]:
    ranked = sorted(
        [item for item in candidates if item.status == "validation_candidate" and item.novelty_score >= min_novelty_score],
        key=lambda item: item.final_score,
        reverse=True,
    )
    family_counts: dict[str, int] = {}
    shortlist = []
    rejected = []
    for candidate in ranked:
        family = (candidate.family_tags or ["general"])[0]
        if len(shortlist) >= top_k:
            rejected.append(replace(candidate, status="rejected", reject_reason="outside_top_k"))
            continue
        if family_counts.get(family, 0) >= max_per_family:
            rejected.append(replace(candidate, status="rejected", reject_reason="family_cap"))
            continue
        family_counts[family] = family_counts.get(family, 0) + 1
        shortlist.append(replace(candidate, status="shortlisted", diversity_group=family))
    selected_ids = {item.alpha_candidate_id for item in shortlist}
    for candidate in candidates:
        if candidate.alpha_candidate_id not in selected_ids and candidate.status != "validation_candidate":
            rejected.append(candidate)
    report = {
        "shortlist_count": len(shortlist),
        "rejected_count": len(rejected),
        "family_counts": family_counts,
        "max_per_family": max_per_family,
        "min_novelty_score": min_novelty_score,
        "top_k": top_k,
        "warning_count": 0,
    }
    return shortlist, rejected, report


def write_diversity_outputs(shortlist, rejected, report: dict, output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    short_path = write_jsonl_artifact(target / "alpha_shortlist.jsonl", [item.to_dict() for item in shortlist], "alpha_shortlist", "alpha_factory")
    rej_path = write_jsonl_artifact(target / "alpha_rejected.jsonl", [item.to_dict() for item in rejected], "alpha_rejected", "alpha_factory")
    div_path = write_json_artifact(target / "alpha_diversity_report.json", report, "alpha_diversity_report", "alpha_factory")
    md_path = target / "alpha_diversity_report.md"
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "alpha_shortlist_path": str(short_path),
        "alpha_rejected_path": str(rej_path),
        "alpha_diversity_report_path": str(div_path),
        "alpha_diversity_report_md_path": str(md_path),
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Alpha Diversity Report",
        "",
        f"- shortlist_count: {report.get('shortlist_count')}",
        f"- rejected_count: {report.get('rejected_count')}",
        "",
        "| family | count |",
        "| --- | ---: |",
    ]
    for family, count in sorted((report.get("family_counts") or {}).items()):
        lines.append(f"| {family} | {count} |")
    return "\n".join(lines) + "\n"

def score_novelty(candidates, existing_factors) -> dict[str, float]:
    existing_hashes = {record.formula_hash for record in existing_factors}
    existing_names = [set(record.formula or []) for record in existing_factors]
    existing_families = {
        str(family)
        for record in existing_factors
        for family in ((record.metadata or {}).get("alpha_family_tags") or [])
    }
    candidate_family_counts: dict[str, int] = {}
    for candidate in candidates:
        for family in candidate.family_tags or ["general"]:
            candidate_family_counts[str(family)] = candidate_family_counts.get(str(family), 0) + 1
    max_family_count = max(candidate_family_counts.values(), default=1)
    scores: dict[str, float] = {}
    for candidate in candidates:
        base = 1.0 if candidate.formula_hash not in existing_hashes else 0.0
        candidate_names = set(candidate.formula_names)
        max_overlap = 0.0
        for names in existing_names:
            union = candidate_names | names
            if union:
                max_overlap = max(max_overlap, len(candidate_names & names) / len(union))
        families = [str(value) for value in (candidate.family_tags or ["general"])]
        cohort_novelty = max(
            (1.0 - (candidate_family_counts.get(family, 1) - 1) / max(max_family_count, 1))
            for family in families
        )
        historical_novelty = 1.0 if any(family not in existing_families for family in families) else 0.0
        score = 0.35 * base + 0.25 * (1.0 - max_overlap) + 0.20 * cohort_novelty + 0.20 * historical_novelty
        scores[candidate.alpha_candidate_id] = float(max(0.0, min(1.0, score)))
    return scores

from dataclasses import replace

from auto_alpha.research.features.factory import build_feature_semantics_map, feature_semantics_contract_hash
from auto_alpha.research.formulas.vm import StackVM


FORBIDDEN_NAMES = {"TARGET_RET", "target_ret", "FUTURE_RETURN", "NEXT_RET"}


def run_static_checks(
    candidates,
    *,
    max_complexity: int,
    max_lookback: int,
    vocab=None,
    promotion_gate=None,
    feature_meta: dict[str, dict] | None = None,
    feature_semantics: dict[str, object] | None = None,
) -> tuple[list, list[dict]]:
    if feature_semantics is None and feature_meta:
        feature_semantics = build_feature_semantics_map(feature_meta.values())
    if not feature_semantics:
        raise ValueError("static admission requires canonical feature semantics")
    vm = StackVM(vocab)
    contract_hash = feature_semantics_contract_hash(feature_semantics)
    seen: set[str] = set()
    updated = []
    rows: list[dict] = []
    for candidate in candidates:
        errors: list[str] = []
        warnings: list[str] = []
        valid, reason = vm.validate_with_reason(candidate.formula_tokens)
        if not valid:
            errors.append(reason)
        if candidate.formula_hash in seen:
            errors.append("duplicate_formula_hash")
        seen.add(candidate.formula_hash)
        if candidate.complexity > max_complexity:
            errors.append("complexity_exceeds_limit")
        formula_semantics = None
        if valid:
            try:
                formula_semantics = vm.formula_semantics(candidate.formula_tokens, feature_semantics)
            except ValueError as exc:
                errors.append(str(exc))
        canonical_lookback = formula_semantics.max_raw_lag if formula_semantics is not None else None
        if canonical_lookback is not None and canonical_lookback > max_lookback:
            errors.append("lookback_exceeds_limit")
        forbidden = sorted(set(candidate.formula_names) & FORBIDDEN_NAMES)
        if forbidden:
            errors.append(f"forbidden_token:{','.join(forbidden)}")
        promotion_metadata = {}
        if promotion_gate is not None:
            gate_errors, gate_warnings, promotion_metadata = promotion_gate.check_formula_names(candidate.formula_names, feature_meta or {})
            errors.extend(gate_errors)
            warnings.extend(gate_warnings)
        status = "passed" if not errors else "failed"
        updated_candidate = replace(
            candidate,
            lookback=int(canonical_lookback if canonical_lookback is not None else candidate.lookback),
            static_check_status=status,
            status="static_passed" if status == "passed" and candidate.status != "rejected" else "rejected",
            reject_reason="; ".join(errors) if errors else candidate.reject_reason,
            metadata=dict(candidate.metadata) | {
                "canonical_semantics_hash": formula_semantics.semantics_hash if formula_semantics is not None else None,
                "feature_semantics_contract_hash": contract_hash,
                "canonical_max_raw_lag": formula_semantics.max_raw_lag if formula_semantics is not None else None,
                "required_observations": formula_semantics.required_observations if formula_semantics is not None else None,
            },
        )
        updated.append(updated_candidate)
        rows.append(
            {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "status": status,
                "errors": errors,
                "warnings": warnings,
                "complexity": candidate.complexity,
                "lookback": candidate.lookback,
                "canonical_max_raw_lag": formula_semantics.max_raw_lag if formula_semantics is not None else None,
                "required_observations": formula_semantics.required_observations if formula_semantics is not None else None,
                "canonical_semantics_hash": formula_semantics.semantics_hash if formula_semantics is not None else None,
                "feature_semantics_contract_hash": contract_hash,
                "feature_promotion": promotion_metadata,
            }
        )
    return updated, rows

import hashlib
import json
import random
from pathlib import Path
from typing import Iterable

from auto_alpha.research.factors.store import stable_formula_hash
from auto_alpha.research.features.factory import build_feature_semantics_map, feature_semantics_contract_hash, make_formula_vocab_from_manifest
from auto_alpha.research.features.promotion import load_promotion_gate
from auto_alpha.research.search.formulas import generate_initial_population
from auto_alpha.research.search.formulas import FormulaSearchConfig
from auto_alpha.research.search.formulas import crossover_formula
from auto_alpha.research.search.formulas import mutate_formula
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB
from auto_alpha.research.formulas.candidates import default_candidates
from auto_alpha.research.formulas.candidates import load_candidates_json

from auto_alpha.research.search.models import AlphaCandidateRecord
from auto_alpha.research.search.models import AlphaCandidateSource
from auto_alpha.research.search.models import template_formulas


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
