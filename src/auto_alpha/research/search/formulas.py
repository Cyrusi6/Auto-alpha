"""Formula generation, mutation, deduplication, search, reporting, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FormulaCandidate:
    formula_tokens: list[int]
    formula_names: list[str]
    formula_hash: str
    complexity: int
    lookback: int
    source: str
    parent_hashes: list[str]
    generation: int
    validation_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaSearchConfig:
    seed: int = 42
    population_size: int = 20
    generations: int = 3
    max_formula_len: int = 8
    max_complexity: int = 20
    max_lookback: int = 10
    mutation_rate: float = 0.7
    crossover_rate: float = 0.3
    elite_size: int = 5
    top_k: int = 5
    candidate_batch_size: int | None = None
    search_mode: str = "random"
    neural_warmup_steps: int = 1
    neural_policy_steps: int = 1
    neural_checkpoint: str | None = None
    hybrid_neural_ratio: float = 0.5
    feature_promotion_policy_path: str | None = None
    feature_promotion_allowlist_path: str | None = None
    feature_promotion_denylist_path: str | None = None
    require_feature_promotion: bool = False
    allow_risk_filter_features: bool = False


@dataclass(frozen=True)
class FormulaSearchResult:
    search_id: str
    generations: list[dict[str, Any]]
    candidates_generated: int
    candidates_valid: int
    candidates_evaluated: int
    approved_factor_ids: list[str]
    composite_factor_id: str | None
    best_candidates: list[dict[str, Any]]
    paths: dict[str, str]
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import random
from collections.abc import Sequence

from auto_alpha.research.factors.store import stable_formula_hash
from auto_alpha.research.features.semantics import FeatureSemantics
from auto_alpha.research.formulas.operators import get_operator_spec
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB



FEATURE_VERSION = "ashare_features_v1"
OPERATOR_VERSION = "ashare_ops_v1"


def generate_seed_formulas(feature_semantics: dict[str, FeatureSemantics] | None = None) -> list[FormulaCandidate]:
    names = [
        ["RET_1D", "CS_ZSCORE"],
        ["RET_5D", "TS_RANK5"],
        ["ROE", "CS_RANK"],
        ["REVENUE_YOY", "ROE", "ADD", "CS_ZSCORE"],
        ["RET_1D", "TURNOVER_RATE", "TS_CORR5"],
        ["LOG_AMOUNT", "DELTA5", "CS_ZSCORE"],
        ["PB", "NEG", "CS_RANK"],
        ["RET_5D", "PB", "SUB"],
    ]
    return [_make_candidate([FORMULA_VOCAB.encode_name(name) for name in formula], "seed", [], 0, feature_semantics=feature_semantics) for formula in names]


def generate_initial_population(
    config: FormulaSearchConfig,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> list[FormulaCandidate]:
    rng = random.Random(config.seed)
    population: list[FormulaCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in generate_seed_formulas(feature_semantics):
        if _within_limits(candidate, config):
            _append_unique(population, seen, candidate)
        if len(population) >= config.population_size:
            return population
    attempts = 0
    while len(population) < config.population_size and attempts < config.population_size * 100:
        attempts += 1
        candidate = generate_random_formula(
            FORMULA_VOCAB,
            rng,
            config.max_formula_len,
            config.max_complexity,
            config.max_lookback,
            feature_semantics=feature_semantics,
        )
        _append_unique(population, seen, candidate)
    return population


def generate_random_formula(
    vocab,
    rng: random.Random,
    max_len: int,
    max_complexity: int,
    max_lookback: int,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> FormulaCandidate:
    vm = StackVM()
    features = list(range(vocab.feature_count))
    unary_ops = _search_generator_operator_tokens(arity=1, max_lookback=max_lookback)
    binary_ops = _search_generator_operator_tokens(arity=2, max_lookback=max_lookback)

    for _ in range(200):
        tokens = [rng.choice(features)]
        target_ops = 1 if max_len <= 3 else rng.randint(1, max(1, min(4, max_len // 2)))
        for _op_idx in range(target_ops):
            if rng.random() < 0.6 and len(tokens) + 1 <= max_len:
                tokens.append(rng.choice(unary_ops))
            elif len(tokens) + 2 <= max_len:
                tokens.extend([rng.choice(features), rng.choice(binary_ops)])
        valid, reason = vm.validate_with_reason(tokens)
        if not valid:
            continue
        candidate = _make_candidate(tokens, "random", [], 0, feature_semantics=feature_semantics)
        if len(candidate.formula_tokens) <= max_len and candidate.complexity <= max_complexity and candidate.lookback <= max_lookback:
            return candidate
    return _make_candidate([rng.choice(features), rng.choice(unary_ops)], "random", [], 0, feature_semantics=feature_semantics)


def _make_candidate(
    tokens: Sequence[int],
    source: str,
    parent_hashes: list[str],
    generation: int,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> FormulaCandidate:
    vm = StackVM()
    formula_tokens = [int(token) for token in tokens]
    names = vm.canonical_formula(formula_tokens)
    valid, reason = vm.validate_with_reason(formula_tokens)
    formula_hash = stable_formula_hash(formula_tokens, names, FEATURE_VERSION, OPERATOR_VERSION)
    lookback = vm.formula_semantics(formula_tokens, feature_semantics).max_raw_lag if feature_semantics else vm.formula_lookback(formula_tokens)
    return FormulaCandidate(
        formula_tokens=formula_tokens,
        formula_names=names,
        formula_hash=formula_hash,
        complexity=vm.formula_complexity(formula_tokens),
        lookback=lookback,
        source=source,
        parent_hashes=list(parent_hashes),
        generation=int(generation),
        validation_reason=reason if valid else reason,
    )


def _search_generator_operator_tokens(arity: int, max_lookback: int | None = None) -> list[int]:
    tokens: list[int] = []
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        spec = get_operator_spec(token, FORMULA_VOCAB.operator_offset)
        if spec.arity == arity and (max_lookback is None or spec.lookback <= max_lookback):
            tokens.append(token)
    return tokens


def _append_unique(population: list[FormulaCandidate], seen: set[tuple[str, ...]], candidate: FormulaCandidate) -> None:
    key = tuple(candidate.formula_names)
    if key in seen:
        return
    seen.add(key)
    population.append(candidate)


def _within_limits(candidate: FormulaCandidate, config: FormulaSearchConfig) -> bool:
    return (
        len(candidate.formula_tokens) <= config.max_formula_len
        and candidate.complexity <= config.max_complexity
        and candidate.lookback <= config.max_lookback
    )

import random

from auto_alpha.research.formulas.operators import get_operator_spec
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB
from auto_alpha.research.features.semantics import FeatureSemantics



def mutate_formula(
    candidate: FormulaCandidate,
    rng: random.Random,
    config: FormulaSearchConfig,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> FormulaCandidate:
    strategies = [_replace_feature, _replace_operator_same_arity, _insert_unary, _combine_with_feature]
    for _ in range(100):
        tokens = rng.choice(strategies)(candidate.formula_tokens, rng)
        tokens = simplify_formula(tokens)
        result = _make_candidate(tokens, "mutation", [candidate.formula_hash], candidate.generation + 1, feature_semantics=feature_semantics)
        if _valid_candidate(result, config):
            return result
    return candidate


def crossover_formula(
    parent_a: FormulaCandidate,
    parent_b: FormulaCandidate,
    rng: random.Random,
    config: FormulaSearchConfig,
    *,
    feature_semantics: dict[str, FeatureSemantics] | None = None,
) -> FormulaCandidate:
    binary_ops = _search_mutation_operator_tokens(arity=2, max_lookback=config.max_lookback)
    for _ in range(100):
        tokens = list(parent_a.formula_tokens) + list(parent_b.formula_tokens) + [rng.choice(binary_ops)]
        tokens = simplify_formula(tokens)
        result = _make_candidate(
            tokens,
            "crossover",
            [parent_a.formula_hash, parent_b.formula_hash],
            max(parent_a.generation, parent_b.generation) + 1,
            feature_semantics=feature_semantics,
        )
        if _valid_candidate(result, config):
            return result
    return parent_a


def simplify_formula(tokens: list[int]) -> list[int]:
    vm = StackVM()
    simplified = [int(token) for token in tokens]
    while len(simplified) >= 2 and simplified[-1] == simplified[-2] == FORMULA_VOCAB.encode_name("WINSORIZE"):
        simplified.pop()
    valid, _reason = vm.validate_with_reason(simplified)
    return simplified if valid else [FORMULA_VOCAB.encode_name("RET_1D"), FORMULA_VOCAB.encode_name("CS_ZSCORE")]


def _replace_feature(tokens: list[int], rng: random.Random) -> list[int]:
    result = list(tokens)
    feature_positions = [idx for idx, token in enumerate(result) if 0 <= int(token) < FORMULA_VOCAB.feature_count]
    if not feature_positions:
        return result
    result[rng.choice(feature_positions)] = rng.randrange(FORMULA_VOCAB.feature_count)
    return result


def _replace_operator_same_arity(tokens: list[int], rng: random.Random) -> list[int]:
    result = list(tokens)
    op_positions = [idx for idx, token in enumerate(result) if int(token) >= FORMULA_VOCAB.operator_offset]
    if not op_positions:
        return result
    position = rng.choice(op_positions)
    spec = get_operator_spec(result[position], FORMULA_VOCAB.operator_offset)
    result[position] = rng.choice(_search_mutation_operator_tokens(spec.arity))
    return result


def _insert_unary(tokens: list[int], rng: random.Random) -> list[int]:
    return list(tokens) + [rng.choice(_search_mutation_operator_tokens(arity=1))]


def _combine_with_feature(tokens: list[int], rng: random.Random) -> list[int]:
    return list(tokens) + [rng.randrange(FORMULA_VOCAB.feature_count), rng.choice(_search_mutation_operator_tokens(arity=2))]


def _search_mutation_operator_tokens(arity: int, max_lookback: int | None = None) -> list[int]:
    candidates: list[int] = []
    for token in range(FORMULA_VOCAB.operator_offset, FORMULA_VOCAB.size):
        spec = get_operator_spec(token, FORMULA_VOCAB.operator_offset)
        if spec.arity == arity and (max_lookback is None or spec.lookback <= max_lookback):
            candidates.append(token)
    return candidates


def _valid_candidate(candidate: FormulaCandidate, config: FormulaSearchConfig) -> bool:
    valid, _reason = StackVM().validate_with_reason(candidate.formula_tokens)
    return (
        valid
        and len(candidate.formula_tokens) <= config.max_formula_len
        and candidate.complexity <= config.max_complexity
        and candidate.lookback <= config.max_lookback
    )

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact


@dataclass(frozen=True)
class ExperimentMergeReport:
    status: str
    shard_count: int
    merged_records: int
    duplicate_formula_hash_count: int
    missing_shard_count: int
    warnings: list[str]
    paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def merge_formula_batch_eval_results(shard_dirs: list[str | Path], output_dir: str | Path) -> ExperimentMergeReport:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    missing = 0
    warnings: list[str] = []
    for shard_dir in shard_dirs:
        path = Path(shard_dir) / "formula_eval_results.jsonl"
        if not path.exists():
            alt = Path(shard_dir) / "formula_eval_results_shard_0.jsonl"
            path = alt if alt.exists() else path
        if not path.exists():
            missing += 1
            warnings.append(f"missing_shard:{shard_dir}")
            continue
        for row in _read_jsonl(path):
            request = row.get("request") if isinstance(row.get("request"), dict) else {}
            formula_hash = str(request.get("formula_hash") or row.get("formula_hash") or json.dumps(row, sort_keys=True))
            if formula_hash in seen:
                duplicates += 1
                continue
            seen.add(formula_hash)
            rows.append(row)
    result_payload = {
        "status": "success" if missing == 0 else "warning",
        "results": rows,
        "summary": {
            "merged_records": len(rows),
            "duplicate_formula_hash_count": duplicates,
            "missing_shard_count": missing,
        },
    }
    write_jsonl_artifact(output / "merged_formula_eval_results.jsonl", rows, "formula_eval_results", "experiment_orchestrator")
    write_json_artifact(output / "merged_formula_batch_eval_result.json", result_payload, "formula_batch_eval_result", "experiment_orchestrator")
    report = ExperimentMergeReport(
        status=result_payload["status"],
        shard_count=len(shard_dirs),
        merged_records=len(rows),
        duplicate_formula_hash_count=duplicates,
        missing_shard_count=missing,
        warnings=warnings,
        paths={
            "merged_formula_eval_results": str(output / "merged_formula_eval_results.jsonl"),
            "merged_formula_batch_eval_result": str(output / "merged_formula_batch_eval_result.json"),
            "experiment_merge_report": str(output / "experiment_merge_report.json"),
            "experiment_merge_report_md": str(output / "experiment_merge_report.md"),
        },
    )
    write_json_artifact(output / "experiment_merge_report.json", report.to_dict(), "experiment_merge_report", "experiment_orchestrator")
    (output / "experiment_merge_report.md").write_text(_render_merge_report(report), encoding="utf-8")
    return report


def merge_formula_search_results(shard_dirs: list[str | Path], output_dir: str | Path) -> ExperimentMergeReport:
    rows = []
    for shard_dir in shard_dirs:
        payload = _read_json(Path(shard_dir) / "search_result.json") or _read_json(Path(shard_dir) / "formula_search_result.json")
        if payload:
            rows.extend(payload.get("best_candidates", []) or payload.get("results", []))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_json_artifact(output / "merged_search_result.json", {"best_candidates": rows}, "experiment_search_merge_result", "experiment_orchestrator")
    return ExperimentMergeReport(
        status="success",
        shard_count=len(shard_dirs),
        merged_records=len(rows),
        duplicate_formula_hash_count=0,
        missing_shard_count=0,
        warnings=[],
        paths={"merged_search_result": str(output / "merged_search_result.json")},
    )


def merge_factor_store_shards(shard_factor_store_dirs: list[str | Path], output_store_dir: str | Path) -> dict:
    output = Path(output_store_dir)
    output.mkdir(parents=True, exist_ok=True)
    copied = 0
    seen: set[str] = set()
    target = output / "factors.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        for directory in shard_factor_store_dirs:
            for row in _read_jsonl(Path(directory) / "factors.jsonl"):
                factor_id = str(row.get("factor_id") or "")
                if factor_id in seen:
                    continue
                seen.add(factor_id)
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                copied += 1
    return {"output_store_dir": str(output), "factors": copied}


def merge_benchmark_results(shard_dirs: list[str | Path], output_dir: str | Path) -> dict:
    rows = [_read_json(Path(path) / "benchmark_result.json") for path in shard_dirs]
    rows = [row for row in rows if row]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = {"status": "success", "shards": rows}
    write_json_artifact(output / "merged_benchmark_result.json", payload, "gpu_benchmark_report", "experiment_orchestrator")
    return payload


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _render_merge_report(report: ExperimentMergeReport) -> str:
    return "\n".join(
        [
            "# Experiment Merge Report",
            "",
            f"- status: `{report.status}`",
            f"- shard_count: {report.shard_count}",
            f"- merged_records: {report.merged_records}",
            f"- missing_shard_count: {report.missing_shard_count}",
        ]
    ) + "\n"

import json
from pathlib import Path



def write_search_report(result: FormulaSearchResult, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "search_report.json"
    md_path = output_path / "search_report.md"
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    return json_path, md_path


def _render_markdown(result: FormulaSearchResult) -> str:
    lines = [
        "# Formula Search Report",
        "",
        f"- search_id: `{result.search_id}`",
        f"- composite_factor_id: `{result.composite_factor_id or ''}`",
        f"- candidates_generated: `{result.candidates_generated}`",
        f"- candidates_valid: `{result.candidates_valid}`",
        f"- candidates_evaluated: `{result.candidates_evaluated}`",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(result.config, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Generations",
        "",
        "| generation | candidates | approved | rejected | skipped | errors |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in result.generations:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("generation", "")),
                    str(item.get("candidates", 0)),
                    str(item.get("approved", 0)),
                    str(item.get("rejected", 0)),
                    str(item.get("skipped", 0)),
                    str(item.get("errors", 0)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Best Candidates",
            "",
            "| rank | formula | factor_id | status | score | source | generation | complexity | lookback |",
            "| ---: | --- | --- | --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for rank, item in enumerate(result.best_candidates, start=1):
        candidate = item.get("candidate", {})
        formula = " ".join(candidate.get("formula_names", [])) if isinstance(candidate, dict) else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    formula,
                    str(item.get("factor_id") or ""),
                    str(item.get("status") or ""),
                    f"{float(item.get('score', 0.0) or 0.0):.6f}",
                    str(candidate.get("source", "")) if isinstance(candidate, dict) else "",
                    str(candidate.get("generation", "")) if isinstance(candidate, dict) else "",
                    str(candidate.get("complexity", "")) if isinstance(candidate, dict) else "",
                    str(candidate.get("lookback", "")) if isinstance(candidate, dict) else "",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Approved Factors",
            "",
            "```json",
            json.dumps(result.approved_factor_ids, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)

import json
import random
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.formulas.data_loader import AShareDataLoader
from auto_alpha.research.factors.composite import build_composite_factor_matrix
from auto_alpha.research.factors.composite import register_composite_factor
from auto_alpha.research.factors.composite import select_approved_factors
from auto_alpha.research.formulas.evaluator import FormulaBatchEvalConfig, FormulaBatchEvaluator
from auto_alpha.research.formulas.candidates import from_formula_search_candidates



class FormulaSearchRunner:
    def __init__(
        self,
        search_config: FormulaSearchConfig,
        data_dir: str,
        universe_name: str | None,
        universe_file: str | None,
        factor_store_dir: str,
        report_dir: str,
        output_dir: str,
        factor_transform: str = "raw",
        enable_gate: bool = True,
        correlation_threshold: float = 0.95,
        min_coverage: float = 0.8,
        composite_method: str = "rank_average",
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
        continue_on_error: bool = True,
        matrix_cache_dir: str | None = None,
        use_matrix_cache: bool = False,
        evaluation_output_dir: str | None = None,
        evaluation_chunk_size: int = 32,
        evaluation_device: str = "auto",
        use_eval_cache: bool = False,
        eval_cache_dir: str | None = None,
        point_in_time: bool = False,
        feature_cutoff_mode: str = "same_day_after_close",
        min_listing_days: int = 0,
        exclude_st: bool = False,
        run_leakage_audit: bool = False,
        leakage_audit_dir: str | None = None,
        fail_on_leakage_blocker: bool = False,
        corporate_action_aware: bool = False,
        target_return_mode: str = "adjusted_close",
        corporate_action_dir: str | None = None,
        corporate_action_cash_field: str = "cash_div",
        data_freeze_dir: str | None = None,
        data_freeze_id: str | None = None,
        data_version_manifest_path: str | None = None,
        require_data_freeze: bool = False,
        freeze_validation_report_path: str | None = None,
        compute_state_dir: str | None = None,
        compute_output_dir: str | None = None,
        use_compute_scheduler: bool = False,
        formula_shard_count: int = 1,
        formula_shard_id: int | None = None,
        resource_report_path: str | None = None,
        experiment_id: str | None = None,
        alpha_candidates_path: str | None = None,
        alpha_seed_top_k: int | None = None,
        alpha_campaign_manifest_path: str | None = None,
        feature_set_name: str = "ashare_features_v1",
        feature_set_manifest_path: str | None = None,
        feature_promotion_policy_path: str | None = None,
        feature_promotion_allowlist_path: str | None = None,
        feature_promotion_denylist_path: str | None = None,
        require_feature_promotion: bool = False,
        allow_risk_filter_features: bool = False,
    ):
        self.search_config = search_config
        self.data_dir = data_dir
        self.universe_name = universe_name
        self.universe_file = universe_file
        self.factor_store_dir = factor_store_dir
        self.report_dir = report_dir
        self.output_dir = Path(output_dir)
        self.factor_transform = factor_transform
        self.enable_gate = enable_gate
        self.correlation_threshold = correlation_threshold
        self.min_coverage = min_coverage
        self.composite_method = composite_method
        self.train_ratio = train_ratio
        self.valid_ratio = valid_ratio
        self.continue_on_error = continue_on_error
        self.matrix_cache_dir = matrix_cache_dir
        self.use_matrix_cache = bool(use_matrix_cache)
        self.evaluation_output_dir = evaluation_output_dir
        self.evaluation_chunk_size = int(evaluation_chunk_size)
        self.evaluation_device = evaluation_device
        self.use_eval_cache = bool(use_eval_cache)
        self.eval_cache_dir = eval_cache_dir
        self.point_in_time = bool(point_in_time)
        self.feature_cutoff_mode = feature_cutoff_mode
        self.min_listing_days = int(min_listing_days)
        self.exclude_st = bool(exclude_st)
        self.run_leakage_audit = bool(run_leakage_audit)
        self.leakage_audit_dir = leakage_audit_dir
        self.fail_on_leakage_blocker = bool(fail_on_leakage_blocker)
        self.corporate_action_aware = bool(corporate_action_aware)
        self.target_return_mode = target_return_mode
        self.corporate_action_dir = corporate_action_dir
        self.corporate_action_cash_field = corporate_action_cash_field
        self.data_freeze_dir = data_freeze_dir
        self.data_freeze_id = data_freeze_id
        self.data_version_manifest_path = data_version_manifest_path
        self.require_data_freeze = bool(require_data_freeze)
        self.freeze_validation_report_path = freeze_validation_report_path
        self.compute_state_dir = compute_state_dir
        self.compute_output_dir = compute_output_dir
        self.use_compute_scheduler = bool(use_compute_scheduler)
        self.formula_shard_count = int(formula_shard_count)
        self.formula_shard_id = formula_shard_id
        self.resource_report_path = resource_report_path
        self.experiment_id = experiment_id
        self.alpha_candidates_path = alpha_candidates_path
        self.alpha_seed_top_k = alpha_seed_top_k
        self.alpha_campaign_manifest_path = alpha_campaign_manifest_path
        self.feature_set_name = feature_set_name
        self.feature_set_manifest_path = feature_set_manifest_path
        self.feature_promotion_policy_path = feature_promotion_policy_path
        self.feature_promotion_allowlist_path = feature_promotion_allowlist_path
        self.feature_promotion_denylist_path = feature_promotion_denylist_path
        self.require_feature_promotion = bool(require_feature_promotion)
        self.allow_risk_filter_features = bool(allow_risk_filter_features)
        self.rng = random.Random(search_config.seed)

    def run(self) -> FormulaSearchResult:
        created_at = _utc_now()
        search_id = _make_search_id(created_at, self.search_config.seed)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        population = self._initial_population()
        generated: dict[str, FormulaCandidate] = {candidate.formula_hash: candidate for candidate in population}
        evaluated_hashes: set[str] = set()
        generation_summaries: list[dict[str, Any]] = []
        all_results: list[dict[str, Any]] = []

        for generation in range(max(self.search_config.generations, 0)):
            batch_candidates = [candidate for candidate in population if candidate.formula_hash not in evaluated_hashes]
            batch_size = self.search_config.candidate_batch_size or self.search_config.population_size
            batch_candidates = batch_candidates[: max(batch_size, 0)]
            for candidate in batch_candidates:
                evaluated_hashes.add(candidate.formula_hash)
            batch_result = self._run_generation_batch(search_id, generation, batch_candidates)
            result_payloads = [result.to_dict() for result in batch_result.results]
            all_results.extend(result_payloads)
            generation_summaries.append(
                {
                    "generation": generation,
                    "candidates": len(batch_candidates),
                    "approved": sum(1 for item in batch_result.results if item.status == "validation_candidate"),
                    "rejected": sum(1 for item in batch_result.results if item.status == "research_rejected"),
                    "skipped": sum(1 for item in batch_result.results if item.status == "skipped_existing"),
                    "errors": sum(1 for item in batch_result.results if item.status == "error"),
                    "batch_id": batch_result.batch_id,
                }
            )

            elites = self._select_elites(batch_result.results, generated)
            population = self._next_population(elites or population, generated)

        composite_info = self._register_composite(search_id, created_at)
        approved_factor_ids = _search_search_unique(
            str(item.get("factor_id"))
            for item in all_results
            if item.get("factor_id") and item.get("status") == "validation_candidate"
        )
        best = sorted(all_results, key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)[: self.search_config.top_k]
        paths = {
            "search_result_path": str(self.output_dir / "search_result.json"),
            "search_candidates_path": str(self.output_dir / "search_candidates.jsonl"),
            "search_report_json_path": str(self.output_dir / "search_report.json"),
            "search_report_md_path": str(self.output_dir / "search_report.md"),
        }
        result = FormulaSearchResult(
            search_id=search_id,
            generations=generation_summaries,
            candidates_generated=len(generated),
            candidates_valid=sum(1 for candidate in generated.values() if candidate.validation_reason == "ok"),
            candidates_evaluated=len(all_results),
            approved_factor_ids=approved_factor_ids,
            composite_factor_id=composite_info.get("factor_id") if composite_info else None,
            best_candidates=best,
            paths=paths,
            config=asdict(self.search_config)
            | {
                "data_dir": self.data_dir,
                "universe_name": self.universe_name,
                "universe_file": self.universe_file,
                "factor_store_dir": self.factor_store_dir,
                "report_dir": self.report_dir,
                "output_dir": str(self.output_dir),
                "factor_transform": self.factor_transform,
                "enable_gate": self.enable_gate,
                "correlation_threshold": self.correlation_threshold,
                "min_coverage": self.min_coverage,
                "composite_method": self.composite_method,
                "matrix_cache_dir": self.matrix_cache_dir,
                "use_matrix_cache": self.use_matrix_cache,
                "evaluation_engine": "FormulaBatchEvaluator",
                "point_in_time": self.point_in_time,
                "feature_cutoff_mode": self.feature_cutoff_mode,
                "min_listing_days": self.min_listing_days,
                "exclude_st": self.exclude_st,
                "run_leakage_audit": self.run_leakage_audit,
                "corporate_action_aware": self.corporate_action_aware,
                "target_return_mode": self.target_return_mode,
                "corporate_action_dir": self.corporate_action_dir,
                "data_freeze_dir": self.data_freeze_dir,
                "data_freeze_id": self.data_freeze_id,
                "data_version_manifest_path": self.data_version_manifest_path,
                "require_data_freeze": self.require_data_freeze,
                "use_compute_scheduler": self.use_compute_scheduler,
                "formula_shard_count": self.formula_shard_count,
                "formula_shard_id": self.formula_shard_id,
                "resource_report_path": self.resource_report_path,
                "experiment_id": self.experiment_id,
                "alpha_candidates_path": self.alpha_candidates_path,
                "alpha_campaign_manifest_path": self.alpha_campaign_manifest_path,
                "feature_set_name": self.feature_set_name,
                "feature_set_manifest_path": self.feature_set_manifest_path,
                "feature_promotion_policy_path": self.feature_promotion_policy_path,
                "feature_promotion_allowlist_path": self.feature_promotion_allowlist_path,
                "feature_promotion_denylist_path": self.feature_promotion_denylist_path,
                "require_feature_promotion": self.require_feature_promotion,
                "allow_risk_filter_features": self.allow_risk_filter_features,
            },
        )
        self._write_outputs(result, generated)
        write_search_report(result, self.output_dir)
        return result

    def _run_generation_batch(self, search_id: str, generation: int, candidates: list[FormulaCandidate]):
        requests = [
            replace(
                request,
                metadata=dict(request.metadata or {})
                | {"search_id": search_id, "generation": generation},
            )
            for request in from_formula_search_candidates(candidates)
        ]
        evaluation_output = (
            Path(self.evaluation_output_dir) / f"generation_{generation}"
            if self.evaluation_output_dir
            else self.output_dir / f"generation_{generation}"
        )
        config = FormulaBatchEvalConfig(
            data_dir=self.data_dir,
            factor_store_dir=self.factor_store_dir,
            report_dir=self.report_dir,
            output_dir=str(evaluation_output),
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            matrix_cache_dir=self.matrix_cache_dir,
            use_matrix_cache=self.use_matrix_cache,
            device=self.evaluation_device,
            factor_transform=self.factor_transform,
            enable_gate=self.enable_gate,
            correlation_threshold=self.correlation_threshold,
            min_coverage=self.min_coverage,
            train_ratio=self.train_ratio,
            valid_ratio=self.valid_ratio,
            chunk_size=self.evaluation_chunk_size,
            use_eval_cache=self.use_eval_cache,
            eval_cache_dir=self.eval_cache_dir,
            skip_existing=True,
            register_approved=True,
            continue_on_error=self.continue_on_error,
            batch_id=f"{search_id}_gen_{generation}",
            shard_id=self.formula_shard_id,
            shard_count=self.formula_shard_count,
            resource_report_path=self.resource_report_path,
            feature_set_name=self.feature_set_name,
            feature_set_manifest_path=self.feature_set_manifest_path,
            alpha_campaign_id=_alpha_campaign_id(self.alpha_campaign_manifest_path),
        )
        return FormulaBatchEvaluator(config).run(requests)

    def _initial_population(self) -> list[FormulaCandidate]:
        population = generate_initial_population(self.search_config)
        if not self.alpha_candidates_path:
            return population
        alpha = _load_alpha_candidates(self.alpha_candidates_path, self.alpha_seed_top_k)
        merged: dict[str, FormulaCandidate] = {candidate.formula_hash: candidate for candidate in alpha}
        for candidate in population:
            merged.setdefault(candidate.formula_hash, candidate)
        return list(merged.values())[: max(self.search_config.population_size, len(alpha))]

    def _select_elites(self, results, generated: dict[str, FormulaCandidate]) -> list[FormulaCandidate]:
        ranked = sorted(results, key=lambda item: item.score, reverse=True)
        elites: list[FormulaCandidate] = []
        for result in ranked:
            candidate_hash = result.request.formula_hash
            if candidate_hash and candidate_hash in generated:
                elites.append(generated[candidate_hash])
            if len(elites) >= max(self.search_config.elite_size, 1):
                break
        return elites

    def _next_population(
        self,
        elites: list[FormulaCandidate],
        generated: dict[str, FormulaCandidate],
    ) -> list[FormulaCandidate]:
        next_population = list(elites[: max(self.search_config.elite_size, 1)])
        attempts = 0
        while len(next_population) < self.search_config.population_size and attempts < self.search_config.population_size * 100:
            attempts += 1
            if len(elites) >= 2 and self.rng.random() < self.search_config.crossover_rate:
                left, right = self.rng.sample(elites, 2)
                child = crossover_formula(left, right, self.rng, self.search_config)
            else:
                parent = self.rng.choice(elites)
                child = mutate_formula(parent, self.rng, self.search_config)
            if child.formula_hash in generated:
                continue
            generated[child.formula_hash] = child
            next_population.append(child)
        return next_population

    def _register_composite(self, search_id: str, created_at: str) -> dict[str, Any] | None:
        store = LocalFactorStore(self.factor_store_dir)
        factor_ids = select_approved_factors(
            store,
            max_factors=max(self.search_config.top_k, 0),
            max_pairwise_corr=self.correlation_threshold,
        )
        if not factor_ids:
            return None
        loader = AShareDataLoader(
            data_dir=self.data_dir,
            device="cpu",
            universe_name=self.universe_name,
            universe_file=self.universe_file,
            matrix_cache_dir=self.matrix_cache_dir,
            use_matrix_cache=self.use_matrix_cache,
            point_in_time=self.point_in_time,
            feature_cutoff_mode=self.feature_cutoff_mode,
            min_listing_days=self.min_listing_days,
            exclude_st=self.exclude_st,
            corporate_action_aware=self.corporate_action_aware,
            target_return_mode=self.target_return_mode,
            corporate_action_dir=self.corporate_action_dir,
        ).load_data()
        values = build_composite_factor_matrix(
            store,
            factor_ids,
            loader.ts_codes,
            loader.trade_dates,
            method=self.composite_method,
        )
        return register_composite_factor(
            store,
            factor_ids,
            loader.ts_codes,
            loader.trade_dates,
            values,
            method=self.composite_method,
            batch_id=search_id,
            created_at=created_at,
        )

    def _write_outputs(self, result: FormulaSearchResult, generated: dict[str, FormulaCandidate]) -> None:
        (self.output_dir / "search_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (self.output_dir / "search_candidates.jsonl").open("w", encoding="utf-8") as handle:
            for candidate in generated.values():
                handle.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _make_search_id(created_at: str, seed: int) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    return f"search_{seed}_{safe}"


def _search_search_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _load_alpha_candidates(path: str, top_k: int | None) -> list[FormulaCandidate]:
    target = Path(path)
    if not target.exists():
        return []
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = sorted(rows, key=lambda item: float(item.get("final_score", 0.0) or 0.0), reverse=True)
    if top_k is not None:
        rows = rows[: max(top_k, 0)]
    candidates: list[FormulaCandidate] = []
    for idx, row in enumerate(rows):
        tokens = [int(item) for item in row.get("formula_tokens", [])]
        names = [str(item) for item in row.get("formula_names", [])]
        formula_hash = str(row.get("formula_hash") or f"alpha_{idx}")
        candidates.append(
            FormulaCandidate(
                formula_tokens=tokens,
                formula_names=names,
                formula_hash=formula_hash,
                complexity=int(row.get("complexity", len(tokens)) or len(tokens)),
                lookback=int(row.get("lookback", 0) or 0),
                source=f"alpha_factory:{row.get('source', 'shortlist')}",
                parent_hashes=[str(row.get("alpha_candidate_id", ""))],
                generation=0,
                validation_reason="ok",
            )
        )
    return candidates


def _alpha_campaign_id(path: str | None) -> str | None:
    if not path or not Path(path).exists():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload.get("campaign_id")
    except Exception:
        return None

import argparse
import contextlib
import io
import json
from pathlib import Path

from auto_alpha.research.factors.engine import SUPPORTED_TRANSFORMS
from auto_alpha.data.lake.store import validate_research_input
from auto_alpha.research.factors.composite import COMPOSITE_METHODS
from auto_alpha.research.factors.store import LocalFactorStore, has_positive_oos_evidence
from auto_alpha.validation.walk_forward.engine_run_validation import main as run_validation_main
from auto_alpha.validation.certification.factors_run_certify import main as run_certify_main



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local formula search for A-share factors.")
    parser.add_argument("--search-mode", choices=["random", "neural", "hybrid"], default="random")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--population-size", type=int, default=20)
    parser.add_argument("--generations", type=int, default=3)
    parser.add_argument("--max-formula-len", type=int, default=8)
    parser.add_argument("--max-complexity", type=int, default=20)
    parser.add_argument("--max-lookback", type=int, default=10)
    parser.add_argument("--mutation-rate", type=float, default=0.7)
    parser.add_argument("--crossover-rate", type=float, default=0.3)
    parser.add_argument("--elite-size", type=int, default=5)
    parser.add_argument("--candidate-batch-size", type=int)
    parser.add_argument("--neural-warmup-steps", type=int, default=1)
    parser.add_argument("--neural-policy-steps", type=int, default=1)
    parser.add_argument("--neural-checkpoint")
    parser.add_argument("--hybrid-neural-ratio", type=float, default=0.5)
    parser.add_argument("--corpus-sequence-path")
    parser.add_argument("--corpus-path")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--use-matrix-cache", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--run-leakage-audit", action="store_true")
    parser.add_argument("--leakage-audit-dir")
    parser.add_argument("--fail-on-leakage-blocker", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument(
        "--target-return-mode",
        choices=("adjusted_close", "raw_close", "corporate_action_total_return"),
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-dir")
    parser.add_argument("--corporate-action-cash-field", choices=("cash_div", "cash_div_tax"), default="cash_div")
    parser.add_argument("--evaluation-output-dir")
    parser.add_argument("--evaluation-chunk-size", type=int, default=32)
    parser.add_argument("--evaluation-device", default="auto")
    parser.add_argument("--use-eval-cache", action="store_true")
    parser.add_argument("--eval-cache-dir")
    parser.add_argument("--factor-transform", default="raw", choices=sorted(SUPPORTED_TRANSFORMS))
    parser.add_argument("--enable-gate", action="store_true")
    parser.add_argument("--disable-gate", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--composite-method", default="rank_average", choices=sorted(COMPOSITE_METHODS))
    parser.add_argument("--correlation-threshold", type=float, default=0.95)
    parser.add_argument("--min-coverage", type=float, default=0.8)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--valid-ratio", type=float, default=0.2)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--compute-output-dir")
    parser.add_argument("--formula-shard-count", type=int, default=1)
    parser.add_argument("--formula-shard-id", type=int)
    parser.add_argument("--resource-report-path")
    parser.add_argument("--experiment-id")
    parser.add_argument("--distributed-search", action="store_true")
    parser.add_argument("--merge-search-shards", action="store_true")
    parser.add_argument("--search-shard-dir", action="append", default=[])
    parser.add_argument("--alpha-candidates-path")
    parser.add_argument("--alpha-campaign-manifest-path")
    parser.add_argument("--feature-set-name", default="ashare_features_v1")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--feature-promotion-policy-path")
    parser.add_argument("--feature-promotion-allowlist-path")
    parser.add_argument("--feature-promotion-denylist-path")
    parser.add_argument("--require-feature-promotion", action="store_true")
    parser.add_argument("--allow-risk-filter-features", action="store_true")
    parser.add_argument("--use-alpha-shortlist-as-seed", action="store_true")
    parser.add_argument("--alpha-seed-top-k", type=int)
    parser.add_argument("--run-validation-lab", action="store_true")
    parser.add_argument("--validation-output-dir")
    parser.add_argument("--run-certification", action="store_true")
    parser.add_argument("--certification-output-dir")
    parser.add_argument("--certification-policy-path")
    parser.add_argument("--certification-policy-profile", default="sample_lenient_certification")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    freeze_payload = _apply_data_freeze_args(args)
    if args.merge_search_shards:
        report = merge_formula_search_results(args.search_shard_dir, args.output_dir)
        payload = report.to_dict()
        payload.update(freeze_payload)
        payload["search_mode"] = "merge"
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if report.status in {"success", "warning"} else 1
    search_config = FormulaSearchConfig(
        seed=args.seed,
        population_size=args.population_size,
        generations=args.generations,
        max_formula_len=args.max_formula_len,
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        mutation_rate=args.mutation_rate,
        crossover_rate=args.crossover_rate,
        elite_size=args.elite_size,
        top_k=args.top_k,
        candidate_batch_size=args.candidate_batch_size,
        search_mode=args.search_mode,
        neural_warmup_steps=args.neural_warmup_steps,
        neural_policy_steps=args.neural_policy_steps,
        neural_checkpoint=args.neural_checkpoint,
        hybrid_neural_ratio=args.hybrid_neural_ratio,
        feature_promotion_policy_path=args.feature_promotion_policy_path,
        feature_promotion_allowlist_path=args.feature_promotion_allowlist_path,
        feature_promotion_denylist_path=args.feature_promotion_denylist_path,
        require_feature_promotion=args.require_feature_promotion,
        allow_risk_filter_features=args.allow_risk_filter_features,
    )
    if args.search_mode == "neural":
        result = _run_neural(args)
        result.update(freeze_payload)
        _attach_pit_metadata(result, args)
        _attach_search_trial_summary(result)
        _maybe_run_validation_and_certification(result, args)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.search_mode == "hybrid":
        result = _run_hybrid(args, search_config)
        result.update(freeze_payload)
        _attach_pit_metadata(result, args)
        _attach_search_trial_summary(result)
        _maybe_run_validation_and_certification(result, args)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    result = FormulaSearchRunner(
        search_config=search_config,
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        composite_method=args.composite_method,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        continue_on_error=args.continue_on_error,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        evaluation_output_dir=args.evaluation_output_dir,
        evaluation_chunk_size=args.evaluation_chunk_size,
        evaluation_device=args.evaluation_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        fail_on_leakage_blocker=args.fail_on_leakage_blocker,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_cash_field=args.corporate_action_cash_field,
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        freeze_validation_report_path=args.freeze_validation_report_path,
        compute_state_dir=args.compute_state_dir,
        compute_output_dir=args.compute_output_dir,
        use_compute_scheduler=args.use_compute_scheduler,
        formula_shard_count=args.formula_shard_count,
        formula_shard_id=args.formula_shard_id,
        resource_report_path=args.resource_report_path,
        experiment_id=args.experiment_id,
        alpha_candidates_path=args.alpha_candidates_path if args.use_alpha_shortlist_as_seed else None,
        alpha_seed_top_k=args.alpha_seed_top_k,
        alpha_campaign_manifest_path=args.alpha_campaign_manifest_path,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
        feature_promotion_policy_path=args.feature_promotion_policy_path,
        feature_promotion_allowlist_path=args.feature_promotion_allowlist_path,
        feature_promotion_denylist_path=args.feature_promotion_denylist_path,
        require_feature_promotion=args.require_feature_promotion,
        allow_risk_filter_features=args.allow_risk_filter_features,
    ).run()
    payload = result.to_dict()
    payload.update(freeze_payload)
    _attach_pit_metadata(payload, args)
    _attach_search_trial_summary(payload)
    _maybe_run_validation_and_certification(payload, args)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run_neural(args: argparse.Namespace) -> dict[str, object]:
    from auto_alpha.research.search.neural import NeuralFormulaTrainer

    result = NeuralFormulaTrainer(
        config=_neural_config_from_args(args),
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
    ).train()
    payload = result.to_dict()
    payload["search_mode"] = "neural"
    return payload


def _run_hybrid(args: argparse.Namespace, search_config: FormulaSearchConfig) -> dict[str, object]:
    from auto_alpha.research.search.neural import NeuralFormulaTrainer

    output_dir = Path(args.output_dir)
    neural_output = output_dir / "neural"
    neural_result = NeuralFormulaTrainer(
        config=_neural_config_from_args(args),
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=str(neural_output),
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
    ).train()
    random_result = FormulaSearchRunner(
        search_config=search_config,
        data_dir=args.data_dir,
        universe_name=args.universe_name,
        universe_file=args.universe_file,
        factor_store_dir=args.factor_store_dir,
        report_dir=args.report_dir,
        output_dir=args.output_dir,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        correlation_threshold=args.correlation_threshold,
        min_coverage=args.min_coverage,
        composite_method=args.composite_method,
        train_ratio=args.train_ratio,
        valid_ratio=args.valid_ratio,
        continue_on_error=args.continue_on_error,
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        evaluation_output_dir=args.evaluation_output_dir,
        evaluation_chunk_size=args.evaluation_chunk_size,
        evaluation_device=args.evaluation_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        run_leakage_audit=args.run_leakage_audit,
        leakage_audit_dir=args.leakage_audit_dir,
        fail_on_leakage_blocker=args.fail_on_leakage_blocker,
        corporate_action_aware=args.corporate_action_aware,
        target_return_mode=args.target_return_mode,
        corporate_action_dir=args.corporate_action_dir,
        corporate_action_cash_field=args.corporate_action_cash_field,
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        freeze_validation_report_path=args.freeze_validation_report_path,
        alpha_candidates_path=args.alpha_candidates_path if args.use_alpha_shortlist_as_seed else None,
        alpha_seed_top_k=args.alpha_seed_top_k,
        alpha_campaign_manifest_path=args.alpha_campaign_manifest_path,
        feature_set_name=args.feature_set_name,
        feature_set_manifest_path=args.feature_set_manifest_path,
        feature_promotion_policy_path=args.feature_promotion_policy_path,
        feature_promotion_allowlist_path=args.feature_promotion_allowlist_path,
        feature_promotion_denylist_path=args.feature_promotion_denylist_path,
        require_feature_promotion=args.require_feature_promotion,
        allow_risk_filter_features=args.allow_risk_filter_features,
    ).run()
    payload = random_result.to_dict()
    neural_payload = neural_result.to_dict()
    payload["search_mode"] = "hybrid"
    payload["neural_metadata"] = {
        "search_id": neural_payload["search_id"],
        "approved_factor_ids": neural_payload["approved_factor_ids"],
        "composite_factor_id": neural_payload["composite_factor_id"],
        "checkpoint_paths": neural_payload["checkpoint_paths"],
        "paths": neural_payload["paths"],
        "hybrid_neural_ratio": args.hybrid_neural_ratio,
    }
    payload["approved_factor_ids"] = _search_run_search_unique(payload.get("approved_factor_ids", []) + neural_payload["approved_factor_ids"])
    if neural_payload.get("composite_factor_id"):
        payload["composite_factor_id"] = neural_payload["composite_factor_id"]
    (output_dir / "search_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _neural_config_from_args(args: argparse.Namespace) -> NeuralSearchConfig:
    from auto_alpha.research.search.neural import NeuralSearchConfig

    return NeuralSearchConfig(
        seed=args.seed,
        max_formula_len=args.max_formula_len,
        warmup_steps=args.neural_warmup_steps,
        policy_steps=args.neural_policy_steps,
        batch_size=max(1, min(args.population_size, 8)),
        samples_per_step=max(1, int(args.population_size * max(0.0, min(args.hybrid_neural_ratio, 1.0)))),
        max_complexity=args.max_complexity,
        max_lookback=args.max_lookback,
        resume_checkpoint=args.neural_checkpoint,
        factor_transform=args.factor_transform,
        enable_gate=args.enable_gate and not args.disable_gate,
        top_k=args.top_k,
        composite_method=args.composite_method,
        corpus_sequence_path=args.corpus_sequence_path or _sequence_path_from_corpus(args.corpus_path),
        matrix_cache_dir=args.matrix_cache_dir,
        use_matrix_cache=args.use_matrix_cache,
        evaluation_output_dir=args.evaluation_output_dir,
        evaluation_chunk_size=args.evaluation_chunk_size,
        evaluation_device=args.evaluation_device,
        use_eval_cache=args.use_eval_cache,
        eval_cache_dir=args.eval_cache_dir,
    )


def _search_run_search_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sequence_path_from_corpus(corpus_path: str | None) -> str | None:
    if not corpus_path:
        return None
    path = Path(corpus_path)
    sibling = path.parent / "formula_sequences.jsonl"
    return str(sibling if sibling.exists() else path)


def _attach_pit_metadata(payload: dict[str, object], args: argparse.Namespace) -> None:
    payload["point_in_time"] = bool(args.point_in_time)
    payload["feature_cutoff_mode"] = args.feature_cutoff_mode
    payload["min_listing_days"] = int(args.min_listing_days)
    payload["exclude_st"] = bool(args.exclude_st)
    payload["leakage_audit_requested"] = bool(args.run_leakage_audit)
    payload["corporate_action_aware"] = bool(args.corporate_action_aware)
    payload["target_return_mode"] = args.target_return_mode
    if args.corporate_action_dir:
        payload["corporate_action_dir"] = args.corporate_action_dir
    if args.leakage_audit_dir:
        payload["leakage_audit_dir"] = args.leakage_audit_dir
    if args.data_freeze_dir:
        payload["data_freeze_dir"] = args.data_freeze_dir
    if args.data_freeze_id:
        payload["data_freeze_id"] = args.data_freeze_id
    if args.data_version_manifest_path:
        payload["data_version_manifest_path"] = args.data_version_manifest_path


def _maybe_run_validation_and_certification(payload: dict[str, object], args: argparse.Namespace) -> None:
    if not (args.run_validation_lab or args.run_certification):
        payload["total_search_trials"] = int(payload.get("candidates_generated", 0) or 0)
        payload["selected_factor_id"] = payload.get("composite_factor_id")
        payload["validation_target_count"] = 1 if payload.get("composite_factor_id") else 0
        return
    factor_id = str(payload.get("composite_factor_id") or _latest_composite(args.factor_store_dir) or "")
    if not factor_id:
        return
    validation_dir = Path(args.validation_output_dir) if args.validation_output_dir else Path(args.output_dir) / "validation_lab"
    certification_dir = Path(args.certification_output_dir) if args.certification_output_dir else Path(args.output_dir) / "factor_certification"
    if args.run_validation_lab:
        validation_argv = [
            "run-suite",
            "--data-dir",
            args.data_dir,
            "--factor-store-dir",
            args.factor_store_dir,
            "--factor-id",
            factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(validation_dir),
            "--run-multiple-testing",
            "--run-overfit-risk",
            "--run-placebo",
            "--run-regime",
            "--run-sensitivity",
            "--run-stress-backtest",
            "--formula-search-result-path",
            str(Path(args.output_dir) / "search_result.json"),
        ]
        if args.data_freeze_dir:
            validation_argv.extend(["--data-freeze-dir", args.data_freeze_dir])
        if args.universe_name:
            validation_argv.extend(["--universe-name", args.universe_name])
        payload["validation_summary"] = _run_child_json(run_validation_main, validation_argv)
        payload["validation_output_dir"] = str(validation_dir)
    if args.run_certification:
        cert_argv = [
            "run",
            "--factor-store-dir",
            args.factor_store_dir,
            "--factor-id",
            factor_id,
            "--factor-type",
            "composite",
            "--output-dir",
            str(certification_dir),
            "--policy-profile",
            args.certification_policy_profile,
            "--validation-lab-report-path",
            str(validation_dir / "validation_lab_report.json"),
            "--factor-validation-summary-path",
            str(validation_dir / "factor_validation_summary.json"),
            "--multiple-testing-report-path",
            str(validation_dir / "multiple_testing_report.json"),
            "--overfit-risk-report-path",
            str(validation_dir / "overfit_risk_report.json"),
            "--placebo-test-report-path",
            str(validation_dir / "placebo_test_report.json"),
            "--regime-validation-report-path",
            str(validation_dir / "regime_validation_report.json"),
            "--sensitivity-report-path",
            str(validation_dir / "sensitivity_report.json"),
            "--stress-backtest-report-path",
            str(validation_dir / "stress_backtest_report.json"),
        ]
        if args.certification_policy_path:
            cert_argv.extend(["--policy-path", args.certification_policy_path])
        payload["certification_summary"] = _run_child_json(run_certify_main, cert_argv)
        payload["certification_output_dir"] = str(certification_dir)
    payload["selected_factor_id"] = factor_id
    payload["validation_target_count"] = 1


def _attach_search_trial_summary(payload: dict[str, object]) -> None:
    candidates_generated = int(payload.get("candidates_generated", 0) or 0)
    candidates_valid = int(payload.get("candidates_valid", 0) or 0)
    candidates_evaluated = int(payload.get("candidates_evaluated", 0) or 0)
    hashes = set()
    for item in payload.get("best_candidates", []) if isinstance(payload.get("best_candidates"), list) else []:
        if isinstance(item, dict) and item.get("formula_hash"):
            hashes.add(str(item["formula_hash"]))
    payload.setdefault("alpha_seed_count", 0)
    payload["total_search_trials"] = candidates_generated
    payload["valid_search_trials"] = candidates_valid
    payload["evaluated_search_trials"] = candidates_evaluated
    payload["unique_formula_hash_count"] = max(len(hashes), candidates_valid if candidates_valid else 0)
    payload["selected_factor_id"] = payload.get("composite_factor_id")
    payload["validation_target_count"] = 1 if payload.get("composite_factor_id") else 0


def _latest_composite(factor_store_dir: str) -> str | None:
    records = LocalFactorStore(factor_store_dir).load_factors()
    record = next(
        (
            item
            for item in reversed(records)
            if item.factor_type == "composite" and has_positive_oos_evidence(item)
        ),
        None,
    )
    return record.factor_id if record is not None else None


def _run_child_json(main_func, argv: list[str]) -> dict[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = main_func(argv)
    if exit_code != 0:
        raise RuntimeError(f"child command failed: {argv}")
    output = buffer.getvalue().strip()
    return json.loads(output) if output else {}


def _apply_data_freeze_args(args: argparse.Namespace) -> dict[str, object]:
    report = validate_research_input(
        data_dir=args.data_dir,
        data_freeze_dir=args.data_freeze_dir,
        require_freeze=args.require_data_freeze,
    )
    if report.error_count > 0:
        raise RuntimeError(f"data freeze validation failed: {report.status}")
    if args.data_freeze_dir:
        args.data_dir = str(Path(args.data_freeze_dir) / "data")
    return {
        "data_freeze_id": args.data_freeze_id or report.freeze_id,
        "data_freeze_hash": report.content_hash,
        "freeze_validation_status": report.status,
        "freeze_validation_report_path": args.freeze_validation_report_path,
        "data_version_manifest_path": args.data_version_manifest_path,
    }


if __name__ == "__main__":
    raise SystemExit(main())
