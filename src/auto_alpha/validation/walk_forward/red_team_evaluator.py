"""Production one-shot sealed-holdout evaluator for frozen factor candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import uuid
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np
import torch
from auto_alpha.platform.artifacts.schema.writer import attach_artifact_metadata

from auto_alpha.research.factors.engine import preprocess_factor_with_validity
from auto_alpha.research.factors.store import make_factor_id, stable_formula_hash
from auto_alpha.research.features.builder import load_feature_manifest
from auto_alpha.research.features.vocab import make_formula_vocab_from_manifest
from auto_alpha.research.formulas.vm import StackVM
from auto_alpha.validation.walk_forward.engine_metrics import evaluate_factor_dates

from auto_alpha.validation.walk_forward.red_team_candidate_pool import validate_candidate_pool_manifest
from auto_alpha.validation.walk_forward.red_team_capability import HoldoutCapabilityRegistry
from auto_alpha.validation.walk_forward.red_team_contracts import SealedHoldoutPolicy
from auto_alpha.validation.walk_forward.red_team_contracts import validate_holdout_policy
from auto_alpha.validation.walk_forward.red_team_io import HoldoutContractError
from auto_alpha.validation.walk_forward.red_team_io import atomic_json
from auto_alpha.validation.walk_forward.red_team_io import atomic_jsonl
from auto_alpha.validation.walk_forward.red_team_io import read_json
from auto_alpha.validation.walk_forward.red_team_io import sha256_file
from auto_alpha.validation.walk_forward.red_team_io import stable_hash
from auto_alpha.validation.walk_forward.red_team_view import resolve_view_artifact
from auto_alpha.validation.walk_forward.red_team_view import validate_sealed_holdout_view


class ValidationRedTeamAgent:
    """Consumes one reviewed capability and never writes back to Alpha Factory."""

    def __init__(self, capability_path: str | Path, reviewed_capability_hash: str, *, device: str = "cpu"):
        raw = read_json(capability_path, artifact_type="sealed_holdout_capability")
        self.registry = HoldoutCapabilityRegistry(str(raw.get("registry_root") or ""))
        self.capability_path = Path(capability_path).resolve()
        self.capability = self.registry.validate(self.capability_path, reviewed_capability_hash)
        self.device = torch.device(device)

    def evaluate(self) -> tuple[Path, dict[str, Any]]:
        self.registry.begin(self.capability)
        result_path: Path | None = None
        try:
            output_root = Path(self.capability["red_team_output_root"]).resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            atomic_json(
                output_root / "holdout_feedback_forbidden.json",
                attach_artifact_metadata(
                    {
                        "status": "enforced",
                        "candidate_pool_root": self.capability["candidate_pool_root"],
                        "holdout_view_root": self.capability["holdout_view_root"],
                        "feedback_to_search_forbidden": True,
                        "search_agent_readable": False,
                    },
                    "holdout_feedback_firewall",
                    "validation_red_team",
                ),
            )
            candidate_pool = validate_candidate_pool_manifest(
                self.capability["candidate_pool_manifest_path"],
                revalidate_sources=True,
            )
            view = validate_sealed_holdout_view(
                self.capability["holdout_view_manifest_path"],
                open_payloads=True,
            )
            policy, _ = validate_holdout_policy(self.capability["holdout_policy_path"])
            if candidate_pool["content_hash"] != self.capability["candidate_pool_root"]:
                raise HoldoutContractError("capability_candidate_pool_drift")
            if view["content_hash"] != self.capability["holdout_view_root"]:
                raise HoldoutContractError("capability_holdout_view_drift")
            if policy.policy_hash != self.capability["holdout_policy_hash"]:
                raise HoldoutContractError("capability_holdout_policy_drift")
            data = _load_view(self.capability["holdout_view_manifest_path"], view, self.device)
            _validate_window_policy(view, data, policy)
            rows = []
            for candidate in candidate_pool["candidates"]:
                rows.append(_evaluate_candidate(candidate, data, view, policy, candidate_pool["content_hash"]))
            result_path, result = _publish_result(
                output_root,
                capability=self.capability,
                candidate_pool=candidate_pool,
                view=view,
                policy=policy,
                rows=rows,
                ledger_root_at_start=self.registry.ledger_root(),
            )
            self.registry.finish(self.capability, status="completed", result_manifest_path=result_path)
            return result_path, result
        except Exception as exc:
            self.registry.finish(
                self.capability,
                status="blocked",
                result_manifest_path=result_path,
                blocker=f"{type(exc).__name__}:{exc}",
            )
            raise


def validate_sealed_holdout_view(path: str | Path, *, open_payloads: bool = False) -> dict[str, Any]:
    from auto_alpha.validation.walk_forward.red_team_view import validate_sealed_holdout_view as validate

    return validate(path, open_payloads=open_payloads)


def _load_view(manifest_path: str | Path, view: dict[str, Any], device: torch.device) -> dict[str, Any]:
    def tensor(role: str, dtype=None):
        array = np.array(np.load(resolve_view_artifact(manifest_path, view, role), mmap_mode="r"), copy=True)
        result = torch.from_numpy(array).to(device)
        return result.to(dtype=dtype) if dtype is not None else result

    trade_dates = json.loads(resolve_view_artifact(manifest_path, view, "trade_dates").read_text(encoding="utf-8"))
    ts_codes = json.loads(resolve_view_artifact(manifest_path, view, "ts_codes").read_text(encoding="utf-8"))
    feature_manifest_path = resolve_view_artifact(manifest_path, view, "feature_manifest")
    feature_manifest = load_feature_manifest(feature_manifest_path)
    signal = tensor("signal_candidate_cells", torch.bool)
    membership = tensor("membership", torch.bool)
    active = tensor("active", torch.bool)
    evaluation_dates = tensor("evaluation_date_mask", torch.bool)
    target_available = tensor("target_available", torch.bool)
    common = signal & membership & active & evaluation_dates.unsqueeze(0) & target_available
    artifact_roles = {row["role"] for row in view.get("artifact_catalog") or []}
    raw: dict[str, Any] = {"amount": tensor("amount", torch.float32)}
    raw_validity: dict[str, torch.Tensor] = {}
    for role in ("log_mkt_cap", "industry_codes"):
        if role in artifact_roles:
            raw[role] = tensor(role)
        validity_role = f"{role}_validity"
        if validity_role in artifact_roles:
            raw_validity[role] = tensor(validity_role, torch.bool)
    certified_count = int(view.get("certified_factor_count") or 0)
    certified_values = tensor("certified_factor_values", torch.float32) if certified_count else None
    certified_validity = tensor("certified_factor_validity", torch.bool) if certified_count else None
    return {
        "trade_dates": [str(value) for value in trade_dates],
        "ts_codes": [str(value) for value in ts_codes],
        "feature_manifest": feature_manifest,
        "vocab": make_formula_vocab_from_manifest(feature_manifest),
        "features": tensor("feature_tensor", torch.float32),
        "feature_validity": tensor("feature_validity", torch.bool),
        "target": tensor("target_return", torch.float32),
        "target_available": target_available,
        "signal": signal,
        "membership": membership,
        "active": active,
        "evaluation_dates": evaluation_dates,
        "common": common,
        "raw": raw,
        "raw_validity": raw_validity,
        "regime_masks": tensor("regime_date_masks", torch.bool),
        "regime_names": json.loads(resolve_view_artifact(manifest_path, view, "regime_names").read_text(encoding="utf-8")),
        "universe_masks": tensor("universe_masks", torch.bool),
        "universe_names": json.loads(resolve_view_artifact(manifest_path, view, "universe_names").read_text(encoding="utf-8")),
        "certified_values": certified_values,
        "certified_validity": certified_validity,
    }


def _evaluate_candidate(
    candidate: dict[str, Any],
    data: dict[str, Any],
    view: dict[str, Any],
    policy: SealedHoldoutPolicy,
    candidate_pool_root: str,
) -> dict[str, Any]:
    base = {
        "selection_rank": candidate["selection_rank"],
        "alpha_candidate_id": candidate["alpha_candidate_id"],
        "factor_id": candidate["factor_id"],
        "formula_hash": candidate["formula_hash"],
        "formula_tokens": candidate["formula_tokens"],
        "formula_names": candidate["formula_names"],
        "holdout_policy_hash": policy.policy_hash,
        "candidate_pool_root": candidate_pool_root,
        "holdout_view_root": view["content_hash"],
        "evidence_level": "single_future_untouched_holdout",
        "certification_supported": False,
    }
    try:
        profile = policy.profile
        if candidate.get("transform_method") != profile.neutralization_method:
            raise HoldoutContractError("candidate_neutralization_profile_mismatch")
        vocab = data["vocab"]
        canonical_tokens = [int(vocab.encode_name(str(name))) for name in candidate["formula_names"]]
        stored_tokens = [int(value) for value in candidate["formula_tokens"]]
        if canonical_tokens != stored_tokens or vocab.decode_tokens(stored_tokens) != candidate["formula_names"]:
            raise HoldoutContractError("holdout_formula_vocab_identity_mismatch")
        formula_hash = stable_formula_hash(
            stored_tokens,
            candidate["formula_names"],
            candidate["feature_version"],
            candidate["operator_version"],
        )
        if formula_hash != candidate["formula_hash"] or make_factor_id(formula_hash) != candidate["factor_id"]:
            raise HoldoutContractError("holdout_formula_identity_mismatch")
        vm = StackVM(vocab)
        executed = vm.execute_with_validity(stored_tokens, data["features"], data["feature_validity"])
        if executed is None:
            raise HoldoutContractError("holdout_stackvm_no_output")
        factor, validity = executed
        required_transform_inputs = {
            "neutralize_market_cap": ("log_mkt_cap",),
            "neutralize_industry": ("industry_codes",),
            "neutralize_industry_size": ("industry_codes", "log_mkt_cap"),
        }.get(candidate.get("transform_method"), ())
        for name in required_transform_inputs:
            if name not in data["raw"] or name not in data["raw_validity"]:
                raise HoldoutContractError(f"holdout_transform_source_or_validity_missing:{name}")
            validity &= data["raw_validity"][name]
        factor, validity = preprocess_factor_with_validity(
            factor,
            validity,
            data["raw"],
            candidate.get("transform_method") or "raw",
            data["signal"] & data["membership"] & data["active"],
        )
        factor_validity = validity & data["signal"] & data["membership"] & data["active"] & data["evaluation_dates"].unsqueeze(0)
        valid_values = factor[factor_validity & torch.isfinite(factor)]
        if valid_values.numel() == 0 or float(valid_values.std(unbiased=False).item()) <= 1e-8:
            raise HoldoutContractError("holdout_factor_no_valid_variation")
        windows = _window_metrics(factor, factor_validity, data, view, profile.min_cross_section_breadth)
        evaluable = [row for row in windows if row.get("evaluable")]
        if not evaluable:
            raise HoldoutContractError("holdout_no_evaluable_windows")
        rank_ics = [float(row["rank_ic_mean"]) for row in evaluable]
        gross_spreads = [float(row["quantile_spread"]) for row in evaluable]
        turnovers = [float(row["turnover_mean"]) for row in evaluable]
        median_rank_ic = float(median(rank_ics))
        positive_ratio = float(sum(value > 0 for value in rank_ics) / len(rank_ics))
        window_pass_ratio = float(
            sum(float(row["rank_ic_mean"]) > 0 and float(row["quantile_spread"]) > 0 and float(row["rank_ic_hit_rate"]) >= 0.5 for row in evaluable)
            / len(evaluable)
        )
        gross_spread = float(mean(gross_spreads))
        turnover = float(mean(turnovers))
        modeled_cost = turnover * float(profile.modeled_cost_bps) / 10_000.0
        net_spread = gross_spread - modeled_cost
        double_cost_spread = gross_spread - 2.0 * modeled_cost
        max_correlation = _max_certified_correlation(factor, factor_validity, data)
        regime = _direction_diagnostics(factor, factor_validity, data, view, profile.min_cross_section_breadth, kind="regime")
        universe = _direction_diagnostics(factor, factor_validity, data, view, profile.min_cross_section_breadth, kind="universe")
        all_dates = [date for index, date in enumerate(data["trade_dates"]) if bool(data["evaluation_dates"][index].item())]
        overall_metrics = evaluate_factor_dates(
            factor,
            data["target"],
            data["trade_dates"],
            all_dates,
            validity=factor_validity,
            target_available_mask=data["target_available"],
            validation_common_mask=data["common"],
            min_breadth=profile.min_cross_section_breadth,
        )
        placebo = _placebo(
            factor,
            factor_validity,
            data,
            view,
            policy,
            formula_hash,
            float(overall_metrics.get("rank_ic_mean") or 0.0),
        )
        checks = {
            "sufficient_evaluable_windows": len(evaluable) >= profile.min_evaluable_windows,
            "median_rank_ic_positive": median_rank_ic > profile.min_median_rank_ic,
            "positive_rank_ic_window_ratio": positive_ratio >= profile.min_positive_rank_ic_window_ratio,
            "walk_forward_window_pass_ratio": window_pass_ratio >= profile.min_walk_forward_pass_ratio,
            "cost_after_spread_positive": net_spread > profile.min_net_top_bottom_spread,
            "double_modeled_cost_no_reversal": double_cost_spread > 0.0,
            "existing_factor_correlation": max_correlation <= profile.max_existing_factor_correlation,
            "pit_and_leakage_clear": view.get("pit_validation_status") == "passed" and int(view.get("leakage_blocker_count") or 0) == 0,
            "placebo_clear": placebo["percentile"] >= profile.min_placebo_percentile,
            "regime_direction_consistent": regime["positive_ratio"] >= profile.min_regime_direction_ratio,
            "universe_direction_consistent": universe["positive_ratio"] >= profile.min_universe_direction_ratio,
        }
        reasons = [name for name, passed in checks.items() if not passed]
        return {
            **base,
            "status": "sealed_holdout_passed" if not reasons else "sealed_holdout_rejected",
            "gate_passed": not reasons,
            "gate_reasons": reasons,
            "metrics": {
                "median_rank_ic": median_rank_ic,
                "positive_rank_ic_window_ratio": positive_ratio,
                "walk_forward_window_pass_ratio": window_pass_ratio,
                "gross_top_bottom_spread": gross_spread,
                "turnover": turnover,
                "modeled_cost": modeled_cost,
                "net_top_bottom_spread": net_spread,
                "double_modeled_cost_net_spread": double_cost_spread,
                "max_existing_factor_correlation": max_correlation,
                "placebo_percentile": placebo["percentile"],
                "regime_direction_ratio": regime["positive_ratio"],
                "universe_direction_ratio": universe["positive_ratio"],
                "evaluable_window_count": len(evaluable),
            },
            "windows": windows,
            "regime_diagnostics": regime,
            "universe_diagnostics": universe,
            "placebo": placebo,
            "gate_checks": checks,
            "failed_formula_reuse_with_same_holdout_forbidden": bool(reasons),
            "next_eligible_evidence": "next_generation_holdout_or_shadow_observation" if reasons else None,
        }
    except Exception as exc:
        return {
            **base,
            "status": "data_blocked",
            "gate_passed": False,
            "gate_reasons": [f"{type(exc).__name__}:{exc}"],
            "metrics": {},
            "failed_formula_reuse_with_same_holdout_forbidden": True,
            "next_eligible_evidence": "next_generation_holdout_or_shadow_observation",
        }


def _validate_window_policy(view: dict[str, Any], data: dict[str, Any], policy: SealedHoldoutPolicy) -> None:
    evaluation_dates = [
        date
        for index, date in enumerate(data["trade_dates"])
        if bool(data["evaluation_dates"][index].item())
    ]
    windows = view.get("windows") or []
    if len(windows) < policy.profile.min_evaluable_windows:
        raise HoldoutContractError("holdout_window_count_below_locked_policy")
    covered: list[str] = []
    previous_end = ""
    for window in windows:
        start = str(window.get("start_date") or "")
        end = str(window.get("end_date") or "")
        if not start or not end or start > end or (previous_end and start <= previous_end):
            raise HoldoutContractError("holdout_windows_not_strictly_ordered")
        selected = [date for date in evaluation_dates if start <= date <= end]
        if len(selected) != policy.profile.window_size:
            raise HoldoutContractError("holdout_window_size_mismatch")
        covered.extend(selected)
        previous_end = end
    if covered != evaluation_dates:
        raise HoldoutContractError("holdout_windows_do_not_cover_evaluation_axis_exactly")


def _window_metrics(factor, validity, data, view, min_breadth):
    rows = []
    for ordinal, window in enumerate(view.get("windows") or [], start=1):
        dates = [
            date
            for index, date in enumerate(data["trade_dates"])
            if window["start_date"] <= date <= window["end_date"] and bool(data["evaluation_dates"][index].item())
        ]
        metrics = evaluate_factor_dates(
            factor,
            data["target"],
            data["trade_dates"],
            dates,
            validity=validity,
            active_mask=data["active"],
            target_available_mask=data["target_available"],
            index_member_mask=data["membership"],
            validation_common_mask=data["common"],
            min_breadth=min_breadth,
        )
        rows.append({"window_id": window.get("window_id") or f"holdout_{ordinal:03d}", "start_date": window["start_date"], "end_date": window["end_date"], **metrics})
    return rows


def _direction_diagnostics(factor, validity, data, view, min_breadth, *, kind):
    if kind == "regime":
        names, masks = data["regime_names"], data["regime_masks"]
    else:
        names, masks = data["universe_names"], data["universe_masks"]
    results = {}
    positives = 0
    for index, name in enumerate(names):
        if kind == "regime":
            date_mask = masks[index]
            common = data["common"] & date_mask.unsqueeze(0)
            dates = [date for idx, date in enumerate(data["trade_dates"]) if bool(date_mask[idx].item()) and bool(data["evaluation_dates"][idx].item())]
        else:
            common = data["common"] & masks[index]
            dates = [date for idx, date in enumerate(data["trade_dates"]) if bool(data["evaluation_dates"][idx].item())]
        metrics = evaluate_factor_dates(
            factor,
            data["target"],
            data["trade_dates"],
            dates,
            validity=validity,
            target_available_mask=data["target_available"],
            validation_common_mask=common,
            min_breadth=min_breadth,
        )
        passed = bool(metrics.get("evaluable")) and float(metrics.get("rank_ic_mean") or 0.0) > 0.0
        positives += int(passed)
        results[str(name)] = {"passed": passed, "metrics": metrics}
    return {"positive_ratio": positives / max(len(names), 1), "results": results}


def _max_certified_correlation(factor, validity, data):
    references = data.get("certified_values")
    reference_validity = data.get("certified_validity")
    if references is None:
        return 0.0
    correlations = []
    for index in range(references.shape[0]):
        mask = validity & reference_validity[index] & data["common"] & torch.isfinite(factor) & torch.isfinite(references[index])
        if int(mask.sum().item()) < 2:
            continue
        left = factor[mask].float() - factor[mask].float().mean()
        right = references[index][mask].float() - references[index][mask].float().mean()
        denominator = left.norm() * right.norm()
        if float(denominator.item()) > 1e-12:
            correlations.append(abs(float((left * right).sum().item() / denominator.item())))
    return max(correlations, default=0.0)


def _placebo(factor, validity, data, view, policy, formula_hash, actual):
    seed = int(hashlib.sha256(f"{formula_hash}|{view['content_hash']}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    scores = []
    dates = [date for index, date in enumerate(data["trade_dates"]) if bool(data["evaluation_dates"][index].item())]
    for _ in range(max(1, policy.profile.placebo_trials)):
        target = data["target"].clone()
        generator = torch.Generator(device="cpu").manual_seed(rng.randrange(2**31))
        for date_index in torch.where(data["evaluation_dates"])[0].tolist():
            mask = data["common"][:, date_index] & validity[:, date_index]
            positions = torch.where(mask)[0]
            if positions.numel() < 2:
                continue
            order = torch.randperm(positions.numel(), generator=generator, device="cpu").to(positions.device)
            target[positions, date_index] = target[positions[order], date_index]
        metrics = evaluate_factor_dates(
            factor,
            target,
            data["trade_dates"],
            dates,
            validity=validity,
            target_available_mask=data["target_available"],
            validation_common_mask=data["common"],
            min_breadth=policy.profile.min_cross_section_breadth,
        )
        scores.append(float(metrics.get("rank_ic_mean") or 0.0))
    return {
        "trial_count": len(scores),
        "percentile": sum(value <= actual for value in scores) / len(scores),
        "null_score_root": stable_hash(scores),
        "seed_commitment": hashlib.sha256(str(seed).encode()).hexdigest(),
    }


def _publish_result(output_root, *, capability, candidate_pool, view, policy, rows, ledger_root_at_start):
    archive = [
        {
            "alpha_candidate_id": row["alpha_candidate_id"],
            "factor_id": row["factor_id"],
            "formula_hash": row["formula_hash"],
            "status": row["status"],
            "reason_codes": row.get("gate_reasons") or [],
            "same_holdout_formula_reuse_forbidden": True,
            "next_eligible_evidence": "next_generation_holdout_or_shadow_observation",
        }
        for row in rows
        if row["status"] != "sealed_holdout_passed"
    ]
    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    semantic = {
        "candidate_pool_root": candidate_pool["content_hash"],
        "holdout_view_root": view["content_hash"],
        "holdout_policy_hash": policy.policy_hash,
        "capability_id": capability["capability_id"],
        "capability_registry_root": capability["registry_root"],
        "capability_manifest_sha256": sha256_file(
            Path(capability["registry_root"]) / "capabilities" / capability["capability_id"] / "holdout_capability.json"
        ),
        "candidate_results": rows,
        "archive": archive,
    }
    result_root = stable_hash(semantic)
    generation_name = f"sealed_holdout_{result_root[:24]}"
    generations = output_root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    generation = generations / generation_name
    if generation.exists():
        raise HoldoutContractError("sealed_holdout_generation_already_exists")
    temporary = generations / f".{generation_name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(parents=True, exist_ok=False)
    result_rows = [attach_artifact_metadata(row, "sealed_holdout_candidate_result", "validation_red_team") for row in rows]
    archive_rows = [attach_artifact_metadata(row, "sealed_holdout_candidate_archive", "validation_red_team") for row in archive]
    results_path = atomic_jsonl(temporary / "sealed_holdout_candidate_results.jsonl", result_rows)
    archive_path = atomic_jsonl(temporary / "candidate_holdout_archive.jsonl", archive_rows)
    core = {
        "status": "completed",
        "result_root": result_root,
        "candidate_pool_root": candidate_pool["content_hash"],
        "candidate_identity_root": candidate_pool["candidate_identity_root"],
        "formula_hashes": candidate_pool["formula_hashes"],
        "holdout_view_root": view["content_hash"],
        "holdout_policy_hash": policy.policy_hash,
        "capability_id": capability["capability_id"],
        "capability_registry_root": capability["registry_root"],
        "capability_manifest_sha256": sha256_file(
            Path(capability["registry_root"])
            / "capabilities"
            / capability["capability_id"]
            / "holdout_capability.json"
        ),
        "capability_ledger_root_at_start": ledger_root_at_start,
        "candidate_count": len(rows),
        "terminal_count": len(rows),
        "status_counts": status_counts,
        "candidate_results_sha256": sha256_file(results_path),
        "candidate_archive_sha256": sha256_file(archive_path),
        "candidate_results_semantic_root": stable_hash(rows),
        "candidate_archive_semantic_root": stable_hash(archive),
        "result_visibility": "validation_red_team_only",
        "feedback_to_search_forbidden": True,
        "search_feedback_artifact_count": 0,
        "selection_order_immutable": True,
        "formula_mutation_count": 0,
        "holdout_consumption_count": 1,
        "certification_ready": False,
        "portfolio_ready": False,
    }
    manifest_payload = attach_artifact_metadata({**core, "content_hash": stable_hash(core)}, "sealed_holdout_result_manifest", "validation_red_team")
    manifest_path = atomic_json(temporary / "sealed_holdout_result_manifest.json", manifest_payload)
    os.replace(temporary, generation)
    pointer = {
        "generation": generation_name,
        "generation_path": str(Path("generations") / generation_name),
        "manifest_sha256": sha256_file(generation / manifest_path.name),
        "result_root": result_root,
    }
    atomic_json(output_root / "current_sealed_holdout_result.json", pointer)
    return generation / manifest_path.name, read_json(generation / manifest_path.name, artifact_type="sealed_holdout_result_manifest")
