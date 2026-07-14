"""Historical selection-impact forensic using frozen campaign artifacts only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from factor_store.hash import make_factor_id, stable_formula_hash
from factor_store.normalized_overlay import publish_normalized_factor_overlay
from feature_factory.builder import load_feature_manifest
from feature_factory.semantics import build_feature_semantics_map, feature_semantics_contract_hash
from feature_factory.vocab_adapter import make_formula_vocab_from_manifest
from model_core.ops import operator_lookback
from model_core.vm import StackVM


FORENSIC_VERSION = "task054b_selection_impact_forensic_v1"


@dataclass(frozen=True)
class ForensicConfig:
    campaign_root: str
    feature_manifest_path: str
    output_root: str
    fixed_probe_factor_ids: tuple[str, ...] = ()
    expected_unique_candidates: int | None = None


def run_selection_impact_forensic(config: ForensicConfig) -> dict[str, Any]:
    campaign_root = Path(config.campaign_root).resolve()
    output_root = Path(config.output_root)
    paths = _resolve_campaign_artifacts(campaign_root)
    manifest = _read_json(paths["campaign_manifest"])
    generation_stats = _read_json(paths["generation_stats"])
    candidates, duplicate_count = _load_unique_candidates(paths["candidates"])
    expected_count = config.expected_unique_candidates
    if expected_count is None:
        expected_count = int(generation_stats.get("generated", len(candidates)))
    if len(candidates) != expected_count:
        raise RuntimeError(f"unique_candidate_count_mismatch:{len(candidates)}!={expected_count}")

    scored = _by_key(_read_jsonl(paths["scored"]), "alpha_candidate_id")
    static = _by_key(_read_jsonl(paths["static_checks"]), "alpha_candidate_id")
    historical_shortlist = {
        str(row["alpha_candidate_id"]) for row in _read_jsonl(paths["shortlist"])
    }
    factor_records = _factor_records_by_hash(paths.get("factor_store"))
    fixed_probes = _resolve_fixed_probes(config.fixed_probe_factor_ids, factor_records)

    feature_manifest = load_feature_manifest(config.feature_manifest_path)
    vocab = make_formula_vocab_from_manifest(feature_manifest)
    semantics = build_feature_semantics_map(feature_manifest)
    semantics_contract_hash = feature_semantics_contract_hash(semantics)
    campaign_config = dict(manifest.get("config_snapshot", {}))
    max_lookback = int(campaign_config.get("max_lookback", 0))
    max_complexity = int(campaign_config.get("max_complexity", 0))
    top_k = int(campaign_config.get("top_k", len(historical_shortlist)))
    max_per_family = max(1, int(campaign_config.get("max_per_family", top_k)))
    min_novelty = float(campaign_config.get("min_novelty_score", 0.0))

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate["alpha_candidate_id"])
        score_row = scored.get(candidate_id)
        static_row = static.get(candidate_id)
        if score_row is None or static_row is None:
            raise RuntimeError(f"missing_frozen_selection_artifact:{candidate_id}")
        trace = trace_formula_dependencies(candidate["formula_tokens"], vocab, semantics)
        identity = _identity(candidate, vocab)
        source_factor = factor_records.get(str(candidate["formula_hash"]))
        old_components = dict(score_row.get("score_components") or {})
        old_penalty = float(old_components.get("lookback_penalty", 0.002 * int(candidate["lookback"])))
        new_penalty = 0.002 * float(trace["max_raw_lag"])
        old_score = _score_from_components(old_components, old_penalty)
        new_score = _score_from_components(old_components, new_penalty)
        old_errors = [str(item) for item in static_row.get("errors", [])]
        non_lookback_errors = [item for item in old_errors if item != "lookback_exceeds_limit"]
        new_errors = list(non_lookback_errors)
        if int(candidate.get("complexity", 0)) > max_complexity:
            new_errors.append("complexity_exceeds_limit")
        if int(trace["max_raw_lag"]) > max_lookback:
            new_errors.append("lookback_exceeds_limit")
        formula_hash = str(candidate["formula_hash"])
        factor_id = make_factor_id(formula_hash)
        row = {
            "alpha_candidate_id": candidate_id,
            "factor_id": factor_id,
            "formula_hash": formula_hash,
            "source_candidate_record_sha256": _sha_json(candidate),
            "source_factor_record_sha256": _sha_json(source_factor) if source_factor else None,
            "formula_names": list(candidate["formula_names"]),
            "stored_tokens": [int(item) for item in candidate["formula_tokens"]],
            "manifest_reencoded_tokens": identity["manifest_reencoded_tokens"],
            "stored_formula_hash": formula_hash,
            "recomputed_formula_hash": identity["recomputed_formula_hash"],
            "stored_factor_id": source_factor.get("factor_id") if source_factor else factor_id,
            "recomputed_factor_id": make_factor_id(identity["recomputed_formula_hash"]),
            "feature_version": str(candidate["feature_version"]),
            "operator_version": str(candidate["operator_version"]),
            "complexity": int(candidate["complexity"]),
            "stored_lookback": int(candidate["lookback"]),
            "canonical_max_raw_lag": int(trace["max_raw_lag"]),
            "canonical_required_observations": int(trace["required_observations"]),
            "longest_dependency_path": trace["longest_dependency_path"],
            "dependency_nodes": trace["nodes"],
            "original_max_lookback": max_lookback,
            "selection_policy_violation": int(trace["max_raw_lag"]) > max_lookback,
            "old_lookback_penalty": old_penalty,
            "new_lookback_penalty": new_penalty,
            "frozen_final_score": float(score_row.get("final_score", 0.0)),
            "recomputed_old_score": old_score,
            "recomputed_new_score": new_score,
            "old_static_eligible": str(static_row.get("status")) == "passed",
            "new_static_eligible": not new_errors,
            "old_static_errors": old_errors,
            "new_static_errors": sorted(set(new_errors)),
            "historical_shortlist_member": candidate_id in historical_shortlist,
            "formula_identity_preserved": identity["formula_identity_preserved"],
            "selection_identity_preserved": False,
            "historical_selection_reconstructed": False,
            "fixed_engineering_probe": factor_id in fixed_probes,
            "policy_terminal_state": (
                "selection_policy_violation"
                if factor_id in fixed_probes and int(trace["max_raw_lag"]) > max_lookback
                else "eligible_for_conditional_engineering_replay"
            ),
            "family_tags": list(candidate.get("family_tags") or ["general"]),
            "novelty_score": float(score_row.get("novelty_score", 0.0)),
            "historical_status": str(score_row.get("status", "")),
        }
        rows.append(row)

    old_rank = _rank(rows, "recomputed_old_score")
    new_rank = _rank(rows, "recomputed_new_score")
    reconstructed_old = _select_shortlist(
        rows, "recomputed_old_score", "old_static_eligible", top_k, max_per_family, min_novelty
    )
    reconstructed_new = _select_shortlist(
        rows, "recomputed_new_score", "new_static_eligible", top_k, max_per_family, min_novelty
    )
    historical_reconstructed = reconstructed_old == historical_shortlist and all(
        abs(row["recomputed_old_score"] - row["frozen_final_score"]) <= 1e-12 for row in rows
    )
    for row in rows:
        candidate_id = row["alpha_candidate_id"]
        row["old_rank"] = old_rank[candidate_id]
        row["new_rank"] = new_rank[candidate_id]
        row["reconstructed_old_shortlist_member"] = candidate_id in reconstructed_old
        row["reconstructed_new_shortlist_member"] = candidate_id in reconstructed_new
        row["shortlist_membership_changed"] = (candidate_id in reconstructed_old) != (candidate_id in reconstructed_new)
        row["historical_selection_reconstructed"] = historical_reconstructed
        row["selection_identity_preserved"] = bool(
            row["formula_identity_preserved"]
            and row["old_static_eligible"] == row["new_static_eligible"]
            and row["old_rank"] == row["new_rank"]
            and not row["shortlist_membership_changed"]
            and abs(row["recomputed_old_score"] - row["recomputed_new_score"]) <= 1e-12
        )

    source_lineage = {
        key: _sha_file(path)
        for key, path in paths.items()
        if path is not None and path.is_file()
    }
    overlay_records = [_normalized_overlay_record(row, semantics_contract_hash) for row in rows]
    overlay = publish_normalized_factor_overlay(
        output_root / "normalized_factor_overlay",
        overlay_records,
        source_lineage=source_lineage,
        semantics_contract_hash=semantics_contract_hash,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    rows_path = write_jsonl_artifact(
        output_root / "selection_impact_candidates.jsonl",
        rows,
        "task054b_selection_impact_candidates",
        "task_054_b",
    )
    identity_count = sum(bool(row["formula_identity_preserved"]) for row in rows)
    blockers = []
    if not historical_reconstructed:
        blockers.append("historical_selection_not_reconstructed")
    if identity_count != len(rows):
        blockers.append("formula_identity_mismatch")
    summary = {
        "status": "completed" if not blockers else "blocked",
        "forensic_version": FORENSIC_VERSION,
        "campaign_id": manifest.get("campaign_id"),
        "unique_candidate_count": len(rows),
        "expected_unique_candidate_count": expected_count,
        "duplicate_candidate_count": duplicate_count,
        "fixed_probe_count": sum(bool(row["fixed_engineering_probe"]) for row in rows),
        "formula_identity_preserved_count": identity_count,
        "selection_identity_preserved_count": sum(bool(row["selection_identity_preserved"]) for row in rows),
        "historical_selection_reconstructed": historical_reconstructed,
        "selection_policy_violation_count": sum(bool(row["selection_policy_violation"]) for row in rows),
        "fixed_probe_selection_policy_violation_count": sum(
            bool(row["fixed_engineering_probe"] and row["selection_policy_violation"]) for row in rows
        ),
        "shortlist_membership_change_count": sum(bool(row["shortlist_membership_changed"]) for row in rows),
        "canonical_lookback_changed_count": sum(
            int(row["stored_lookback"]) != int(row["canonical_max_raw_lag"]) for row in rows
        ),
        "score_changed_count": sum(
            abs(row["recomputed_old_score"] - row["recomputed_new_score"]) > 1e-12 for row in rows
        ),
        "rank_changed_count": sum(row["old_rank"] != row["new_rank"] for row in rows),
        "static_eligibility_changed_count": sum(
            row["old_static_eligible"] != row["new_static_eligible"] for row in rows
        ),
        "historical_shortlist_count": len(historical_shortlist),
        "reconstructed_new_shortlist_count": len(reconstructed_new),
        "feature_contract_count": len(semantics),
        "semantics_contract_hash": semantics_contract_hash,
        "source_lineage": source_lineage,
        "normalized_overlay": {
            key: overlay[key]
            for key in ("generation_id", "content_hash", "records_sha256", "record_count")
        },
        "selection_metrics_source": "frozen_campaign_artifacts_only",
        "target_or_outcome_read": False,
        "blockers": blockers,
        "certification_ready": False,
        "portfolio_ready": False,
    }
    report_path = write_json_artifact(
        output_root / "selection_impact_forensic_report.json",
        summary,
        "task054b_selection_impact_report",
        "task_054_b",
    )
    return summary | {
        "candidate_artifact_path": str(rows_path),
        "report_path": str(report_path),
        "normalized_overlay_dir": overlay["generation_dir"],
    }


def trace_formula_dependencies(
    formula_tokens: Iterable[int],
    vocab,
    feature_semantics: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    tokens = [int(item) for item in formula_tokens]
    vm = StackVM(vocab)
    stack: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for node_index, raw_token in enumerate(tokens):
        token = int(raw_token)
        name = vocab.token_name(token)
        if token < vocab.operator_offset:
            semantics = feature_semantics.get(name)
            if semantics is None:
                raise RuntimeError(f"missing_feature_semantics:{name}")
            max_raw_lag = int(_semantic_value(semantics, "max_raw_lag"))
            node = {
                "node_index": node_index,
                "name": name,
                "kind": "feature",
                "input_node_indices": [],
                "incremental_raw_lag": max_raw_lag,
                "max_raw_lag": max_raw_lag,
                "required_observations": max_raw_lag + 1,
            }
            path = [name]
        else:
            arity = vm.arity_map.get(token)
            if arity is None or len(stack) < arity:
                raise RuntimeError(f"invalid_formula_syntax:{name}")
            inputs = stack[-arity:]
            del stack[-arity:]
            lookback = int(operator_lookback(token, vocab.operator_offset))
            increment = lookback if name.startswith(("DELAY", "DELTA")) else max(0, lookback - 1)
            longest = max(inputs, key=lambda item: (item["max_raw_lag"], len(item["path"])))
            max_raw_lag = int(longest["max_raw_lag"]) + increment
            node = {
                "node_index": node_index,
                "name": name,
                "kind": "operator",
                "input_node_indices": [int(item["node_index"]) for item in inputs],
                "incremental_raw_lag": increment,
                "max_raw_lag": max_raw_lag,
                "required_observations": max_raw_lag + 1,
            }
            path = list(longest["path"]) + [name]
        nodes.append(node)
        stack.append({"node_index": node_index, "max_raw_lag": max_raw_lag, "path": path})
    if len(stack) != 1:
        raise RuntimeError("invalid_formula_stack_terminal")
    canonical = vm.formula_semantics(tokens, dict(feature_semantics))
    if int(canonical.max_raw_lag) != int(stack[0]["max_raw_lag"]):
        raise RuntimeError("canonical_formula_semantics_trace_mismatch")
    return {
        "max_raw_lag": int(canonical.max_raw_lag),
        "required_observations": int(canonical.required_observations),
        "longest_dependency_path": [item.to_dict() for item in canonical.longest_dependency_path],
        "nodes": nodes,
    }


def _resolve_campaign_artifacts(root: Path) -> dict[str, Path | None]:
    catalogs = sorted(root.rglob("alpha_campaign_artifact_catalog.json"))
    if len(catalogs) != 1:
        raise RuntimeError(f"campaign_artifact_catalog_count:{len(catalogs)}")
    catalog = _read_json(catalogs[0])
    entries = {str(item.get("name")): Path(str(item.get("path"))) for item in catalog.get("entries", [])}

    def required(name: str) -> Path:
        path = entries.get(name)
        if path is None or not path.is_file():
            raise RuntimeError(f"missing_campaign_artifact:{name}")
        return path

    factor_stores = sorted(root.rglob("consolidated_factor_store/factors.jsonl"))
    return {
        "artifact_catalog": catalogs[0],
        "campaign_manifest": required("alpha_campaign_manifest_path"),
        "generation_stats": required("alpha_generation_stats_path"),
        "candidates": required("alpha_candidates_path"),
        "scored": required("alpha_scored_candidates_path"),
        "static_checks": required("alpha_static_checks_path"),
        "shortlist": required("alpha_shortlist_path"),
        "factor_store": factor_stores[0] if len(factor_stores) == 1 else None,
    }


def _identity(candidate: Mapping[str, Any], vocab) -> dict[str, Any]:
    names = [str(item) for item in candidate["formula_names"]]
    try:
        tokens = [int(vocab.encode_name(name)) for name in names]
    except ValueError as exc:
        raise RuntimeError(f"formula_name_not_in_manifest_vocab:{exc}") from exc
    formula_hash = stable_formula_hash(
        tokens,
        names,
        str(candidate["feature_version"]),
        str(candidate["operator_version"]),
    )
    return {
        "manifest_reencoded_tokens": tokens,
        "recomputed_formula_hash": formula_hash,
        "formula_identity_preserved": (
            tokens == [int(item) for item in candidate["formula_tokens"]]
            and formula_hash == str(candidate["formula_hash"])
            and str(candidate["alpha_candidate_id"]) == f"alpha_{formula_hash[:16]}"
        ),
    }


def _score_from_components(components: Mapping[str, Any], lookback_penalty: float) -> float:
    return float(
        float(components.get("full_eval_score", 0.0))
        + 0.5 * float(components.get("proxy_percentile", 0.0))
        + 0.2 * float(components.get("novelty_score", 0.0))
        - float(components.get("complexity_penalty", 0.0))
        - lookback_penalty
    )


def _rank(rows: Iterable[Mapping[str, Any]], score_key: str) -> dict[str, int]:
    ordered = sorted(
        rows,
        key=lambda row: (-float(row[score_key]), str(row["formula_hash"]), str(row["alpha_candidate_id"])),
    )
    return {str(row["alpha_candidate_id"]): index for index, row in enumerate(ordered, 1)}


def _select_shortlist(
    rows: Iterable[Mapping[str, Any]],
    score_key: str,
    eligibility_key: str,
    top_k: int,
    max_per_family: int,
    min_novelty: float,
) -> set[str]:
    eligible = [
        row
        for row in rows
        if bool(row[eligibility_key])
        and str(row["historical_status"]) != "rejected"
        and float(row["novelty_score"]) >= min_novelty
    ]
    ranked = sorted(eligible, key=lambda row: (-float(row[score_key]), str(row["formula_hash"])))
    selected: set[str] = set()
    family_counts: dict[str, int] = {}
    for row in ranked:
        if len(selected) >= top_k:
            break
        family = str((row.get("family_tags") or ["general"])[0])
        if family_counts.get(family, 0) >= max_per_family:
            continue
        selected.add(str(row["alpha_candidate_id"]))
        family_counts[family] = family_counts.get(family, 0) + 1
    return selected


def _normalized_overlay_record(row: Mapping[str, Any], contract_hash: str) -> dict[str, Any]:
    return {
        "factor_id": row["factor_id"],
        "formula": row["formula_names"],
        "formula_tokens": row["stored_tokens"],
        "formula_hash": row["formula_hash"],
        "feature_version": row["feature_version"],
        "operator_version": row["operator_version"],
        "lookback_days": row["canonical_max_raw_lag"],
        "status": "normalized_historical_candidate",
        "source_candidate_record_sha256": row["source_candidate_record_sha256"],
        "source_factor_record_sha256": row["source_factor_record_sha256"],
        "stored_lookback_days": row["stored_lookback"],
        "canonical_required_observations": row["canonical_required_observations"],
        "semantics_contract_hash": contract_hash,
        "migration_reason": "canonical_recursive_dependency_semantics",
        "formula_identity_preserved": row["formula_identity_preserved"],
        "selection_identity_preserved": row["selection_identity_preserved"],
        "historical_selection_reconstructed": row["historical_selection_reconstructed"],
        "selection_policy_violation": row["selection_policy_violation"],
        "policy_terminal_state": row["policy_terminal_state"],
    }


def _load_unique_candidates(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows = _read_jsonl(path)
    by_hash: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        formula_hash = str(row.get("formula_hash", ""))
        if not formula_hash:
            raise RuntimeError("candidate_missing_formula_hash")
        previous = by_hash.get(formula_hash)
        if previous is None:
            by_hash[formula_hash] = row
        elif _candidate_identity(previous) == _candidate_identity(row):
            duplicates += 1
        else:
            raise RuntimeError(f"duplicate_formula_hash_identity_conflict:{formula_hash}")
    return sorted(by_hash.values(), key=lambda row: str(row["alpha_candidate_id"])), duplicates


def _candidate_identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(row.get("formula_names") or ()),
        tuple(row.get("formula_tokens") or ()),
        row.get("feature_version"),
        row.get("operator_version"),
    )


def _factor_records_by_hash(path: Path | None) -> dict[str, dict[str, Any]]:
    return {str(row["formula_hash"]): row for row in _read_jsonl(path)} if path else {}


def _resolve_fixed_probes(requested: Iterable[str], factor_records: Mapping[str, Mapping[str, Any]]) -> set[str]:
    probes = {str(item) for item in requested}
    if not probes:
        probes = {str(row["factor_id"]) for row in factor_records.values()}
    missing = probes - {str(row["factor_id"]) for row in factor_records.values()}
    if missing:
        raise RuntimeError(f"fixed_probe_identity_missing:{','.join(sorted(missing))}")
    return probes


def _by_key(rows: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        value = str(row[key])
        if value in result:
            raise RuntimeError(f"duplicate_artifact_key:{key}:{value}")
        result[value] = row
    return result


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path.name}")
    return payload


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise RuntimeError(f"jsonl_object_required:{path.name}")
            rows.append(payload)
    return rows


def _sha_json(payload: Mapping[str, Any] | None) -> str | None:
    if payload is None:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_value(semantics: Any, field: str) -> Any:
    if isinstance(semantics, Mapping):
        return semantics[field]
    return getattr(semantics, field)
