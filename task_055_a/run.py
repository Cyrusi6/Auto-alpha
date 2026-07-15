"""Unique production orchestrator for Task 055-A.

The runner accepts only the sealed observation boundary and the authoritative
Task 055-A simulation bundle.  It publishes the preregistered policy before
loading factor/target arrays, executes exact-20 by five independent scenarios
twice, verifies deterministic truth hashes, then proves immutable resume.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import publish_blocked_simulation_run, publish_simulation_run, resume_simulation_run
from .bundle import load_simulation_bundle, validate_simulation_bundle
from .observation import canonical_hash, sha256_file, validate_observation_boundary_seal
from .policy import PREREGISTERED_SCENARIOS
from .simulator import SimulationDataBlocker, simulate_event_ledger
from .verifier import verify_simulation_run


CONFIG_SCHEMA = "task055a_orchestrator_config_v1"
POLICY_SEAL_SCHEMA = "task055a_portfolio_diagnostic_policy_seal_v1"
RUN_GENERATION_SCHEMA = "task055a_simulation_run_generation_v1"
FINAL_SCHEMA = "task055a_orchestration_result_v1"
SUCCESS_STATUS = (
    "task055a_retrospective_pit_portfolio_simulator_completed_"
    "historical_selection_contaminated_execution_modeled_"
    "future_holdout_sealed_certification_blocked"
)
BLOCKED_STATUS = "task055a_simulator_engineering_baseline_blocked"
SCENARIO_NAMES = tuple(PREREGISTERED_SCENARIOS)
PHYSICAL_STATE_NAMES = (
    "certification_queue",
    "certified_pool",
    "portfolio_campaign",
    "production_candidate",
    "optimizer_activation",
    "paper_registry",
    "live_registry",
)
PHYSICAL_STATE_TOKENS = {
    "certification_queue": ("certification", "queue"),
    "certified_pool": ("certified", "pool"),
    "portfolio_campaign": ("portfolio", "campaign"),
    "production_candidate": ("production", "candidate"),
    "optimizer_activation": ("optimizer", "activation"),
    "paper_registry": ("paper", "registry"),
    "live_registry": ("live", "registry"),
}
FORBIDDEN_CONFIG_KEYS = {
    "data_dir",
    "data_root",
    "factor_store",
    "factor_store_dir",
    "latest_approved",
    "readiness",
    "ready",
    "validation_bundle",
}
REQUIRED_SIGNAL_MASKS = (
    "signal_candidate_cells",
    "membership",
    "membership_known",
    "active",
    "listed",
    "st_effective",
    "st_status_known",
    "st_information_available",
    "signal_eligible_at_close",
    "unexplained_data_gap",
)
REQUIRED_EXECUTION_MASKS = (
    "membership",
    "membership_known",
    "active",
    "listed",
    "open_execution_known",
    "open_execution_value",
    "buyable_at_open",
    "sellable_at_open",
    "suspension_source_covered",
    "suspension_event_present",
    "suspension_associated_bar_absence",
    "conservative_open_excluded",
    "unexplained_data_gap",
    "corporate_action_validity",
)


class Task055AOrchestrationError(RuntimeError):
    """Raised when production evidence or replay invariants fail closed."""


def run_task055a(
    config: Mapping[str, Any] | str | Path,
    *,
    bundle_validator: Callable[..., Mapping[str, Any]] = validate_simulation_bundle,
    bundle_loader: Callable[[str | Path], Mapping[str, Any]] = load_simulation_bundle,
    seal_validator: Callable[..., Mapping[str, Any]] = validate_observation_boundary_seal,
    simulator: Callable[..., Any] = simulate_event_ledger,
) -> dict[str, Any]:
    """Run the complete Task 055-A retrospective diagnostic orchestration.

    Injectable callables exist for focused producer tests.  The CLI never
    accepts entrypoints, commands, readiness booleans, or arbitrary data roots.
    """

    payload = _load_config(config)
    output_root = Path(payload["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    spec = _semantic_spec(payload)
    spec_hash = canonical_hash(spec)

    existing = _load_existing_final(output_root, spec_hash)
    if existing is not None:
        seal_validator(payload["observation_seal"], rescan=True)
        bundle_validator(payload["simulation_bundle"], require_ready=True)
        current_inventory = inspect_physical_states(payload["physical_state_roots"])
        if current_inventory != existing.get("physical_state_inventory"):
            raise Task055AOrchestrationError("task055a_final_resume_physical_state_drift")
        _validate_final_result(existing, output_root)
        return existing | {"orchestrator_resume_hit": True}

    blockers: list[dict[str, str]] = []
    policy_seal: dict[str, Any] | None = None
    queue_inventory: dict[str, Any] = {}
    primary: dict[str, Any] | None = None
    sibling: dict[str, Any] | None = None
    immutable_resume: dict[str, Any] | None = None
    try:
        observation = dict(seal_validator(payload["observation_seal"], rescan=True))
        bundle_manifest = dict(bundle_validator(payload["simulation_bundle"], require_ready=True))
        exact_ids = _validate_exact20(bundle_manifest.get("exact20_ids"))
        queue_inventory = inspect_physical_states(payload["physical_state_roots"])
        nonempty = [name for name, item in queue_inventory.items() if item["record_count"] != 0]
        if nonempty:
            raise Task055AOrchestrationError(f"task055a_downstream_physical_state_nonempty:{nonempty}")

        policy_seal = publish_policy_seal(
            output_root=output_root / "policy_seal",
            observation=observation,
            bundle_manifest=bundle_manifest,
            exact_ids=exact_ids,
            queue_inventory=queue_inventory,
        )
        # This is intentionally the first call that may map factor, target, or
        # execution arrays.  The immutable policy seal already exists.
        loaded_bundle = dict(bundle_loader(payload["simulation_bundle"]))
        _validate_loaded_identity(loaded_bundle, exact_ids, bundle_manifest)
        prepared = _prepare_simulation_inputs(loaded_bundle)

        primary = _execute_generation(
            root=output_root / "primary",
            role="primary_uncached",
            spec_hash=spec_hash,
            policy_seal=policy_seal,
            exact_ids=exact_ids,
            prepared=prepared,
            simulator=simulator,
            require_uncached=True,
        )
        sibling = _execute_generation(
            root=output_root / "sibling",
            role="sibling_uncached",
            spec_hash=spec_hash,
            policy_seal=policy_seal,
            exact_ids=exact_ids,
            prepared=prepared,
            simulator=simulator,
            require_uncached=True,
        )
        if primary["truth_hash"] != sibling["truth_hash"]:
            raise Task055AOrchestrationError("task055a_uncached_ab_truth_hash_mismatch")
        immutable_resume = _execute_generation(
            root=output_root / "primary",
            role="primary_uncached",
            spec_hash=spec_hash,
            policy_seal=policy_seal,
            exact_ids=exact_ids,
            prepared=prepared,
            simulator=simulator,
            require_uncached=False,
        )
        if not immutable_resume.get("resume_hit") or immutable_resume["truth_hash"] != primary["truth_hash"]:
            raise Task055AOrchestrationError("task055a_immutable_resume_validation_failed")
        blocked_runs = [row for row in primary.get("runs", []) if row.get("terminal_state") == "data_blocked"]
        if blocked_runs:
            blockers.append({
                "code": "task055a_data_blocked_runs",
                "detail": f"count={len(blocked_runs)};reason_codes={sorted({row.get('reason_code') for row in blocked_runs})}",
            })
    except Exception as error:
        blockers.append({"code": "task055a_engineering_blocker", "detail": str(error)})

    status = SUCCESS_STATUS if not blockers else BLOCKED_STATUS
    result = {
        "schema_version": FINAL_SCHEMA,
        "status": status,
        "spec_hash": spec_hash,
        "observation_seal": str(Path(payload["observation_seal"])),
        "simulation_bundle": str(Path(payload["simulation_bundle"])),
        "policy_seal_hash": None if policy_seal is None else policy_seal["content_hash"],
        "primary_truth_hash": None if primary is None else primary["truth_hash"],
        "sibling_truth_hash": None if sibling is None else sibling["truth_hash"],
        "resume_truth_hash": None if immutable_resume is None else immutable_resume["truth_hash"],
        "immutable_resume_hit": bool(immutable_resume and immutable_resume.get("resume_hit")),
        "terminal_count": 0 if primary is None else primary["terminal_count"],
        "expected_terminal_count": 20 * len(SCENARIO_NAMES),
        "physical_state_inventory": queue_inventory,
        "readiness": {
            "prospective_holdout_data_opened": False,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "queues": {name: int(queue_inventory.get(name, {}).get("record_count", 0)) for name in PHYSICAL_STATE_NAMES},
        "blockers": blockers,
    }
    if status == SUCCESS_STATUS:
        if result["terminal_count"] != result["expected_terminal_count"]:
            raise Task055AOrchestrationError("task055a_terminal_count_invalid")
        if any(item["record_count"] for item in queue_inventory.values()):
            raise Task055AOrchestrationError("task055a_physical_queue_not_empty")
    return _publish_final(output_root, spec_hash, result)


def publish_policy_seal(
    *,
    output_root: str | Path,
    observation: Mapping[str, Any],
    bundle_manifest: Mapping[str, Any],
    exact_ids: Sequence[str],
    queue_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish the immutable exact-20 × five-scenario policy before data reads."""

    semantic = {
        "schema_version": POLICY_SEAL_SCHEMA,
        "observation_boundary_hash": observation.get("content_hash"),
        "simulation_bundle_hash": bundle_manifest.get("content_hash"),
        "exact20_ids": list(exact_ids),
        "candidate_identity_root": canonical_hash(list(exact_ids)),
        "signal_cutoff": "20240528",
        "execution_endpoint": "20240530",
        "evidence_level": "retrospective_modeled_daily_bar_proxy",
        "selection_data_reused": True,
        "untouched_holdout": False,
        "portfolio_construction": {
            "independent_factor_runs": True,
            "long_only": True,
            "rebalance": "daily",
            "top_n": 20,
            "weighting": "equal_weight",
            "tie_break": "stable_ts_code",
            "combination_or_selection": False,
        },
        "scenarios": {name: PREREGISTERED_SCENARIOS[name].to_dict() for name in SCENARIO_NAMES},
        "physical_state_evidence": dict(queue_inventory),
        "code_semantic_hash": _code_semantic_hash(),
        "immutable": True,
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"policy_seal_{content_hash[:24]}"
    target = Path(output_root) / "generations" / generation_id
    manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    _publish_generation(target, "policy_seal.json", manifest)
    _atomic_json(
        Path(output_root) / "current.json",
        {"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/policy_seal.json"},
    )
    return manifest | {"manifest_path": str(target / "policy_seal.json")}


def inspect_physical_states(state_roots: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect actual queue/store/registry roots instead of trusting booleans."""

    if set(state_roots) != set(PHYSICAL_STATE_NAMES):
        missing = sorted(set(PHYSICAL_STATE_NAMES) - set(state_roots))
        extra = sorted(set(state_roots) - set(PHYSICAL_STATE_NAMES))
        raise Task055AOrchestrationError(f"task055a_physical_state_roots_invalid:missing={missing}:extra={extra}")
    result: dict[str, Any] = {}
    for name in PHYSICAL_STATE_NAMES:
        root = Path(str(state_roots[name]))
        if not root.exists():
            raise Task055AOrchestrationError(f"task055a_physical_state_root_missing:{name}")
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        tokens = PHYSICAL_STATE_TOKENS[name]
        files = [
            path for path in candidates
            if (
                all(token in path.name.lower() for token in tokens)
                or all(token in root.name.lower() for token in tokens)
            )
            and not path.name.endswith(".schema.json")
        ]
        record_count = sum(_physical_file_records(path) for path in files)
        result[name] = {
            "root": str(root),
            "file_count": len(files),
            "record_count": record_count,
            "content_root": canonical_hash([{"name": str(path.relative_to(root)) if root.is_dir() else path.name, "sha256": sha256_file(path)} for path in files]),
        }
    return result


def _prepare_simulation_inputs(bundle: Mapping[str, Any]) -> dict[str, Any]:
    dates = [str(value) for value in bundle["execution_dates"]]
    signal_dates = [str(value) for value in bundle["trade_dates"]]
    assets = [str(value) for value in bundle["ts_codes"]]
    if not dates or not signal_dates or dates[: len(signal_dates)] != signal_dates:
        raise Task055AOrchestrationError("task055a_signal_execution_axis_mismatch")
    shape_source = (len(assets), len(dates))
    raw = bundle.get("raw") or {}
    validity = bundle.get("raw_validity") or {}
    for field in ("open", "close", "vol", "amount"):
        if field not in raw or field not in validity:
            raise Task055AOrchestrationError(f"task055a_required_raw_field_missing:{field}")
        if np.asarray(raw[field]).shape != shape_source or np.asarray(validity[field]).shape != shape_source:
            raise Task055AOrchestrationError(f"task055a_required_raw_shape_mismatch:{field}")
    signal_masks = dict(bundle.get("strict_masks") or {})
    execution_masks = dict(bundle.get("execution_masks") or {})
    missing_signal = [name for name in REQUIRED_SIGNAL_MASKS if name not in signal_masks]
    missing_execution = [name for name in REQUIRED_EXECUTION_MASKS if name not in execution_masks]
    if missing_signal or missing_execution:
        raise Task055AOrchestrationError(
            f"task055a_required_strict_mask_missing:signal={missing_signal}:execution={missing_execution}"
        )
    open_valid = np.asarray(validity["open"], dtype=bool).T
    close_valid = np.asarray(validity["close"], dtype=bool).T
    open_values = np.asarray(raw["open"], dtype=float).T
    close_values = np.asarray(raw["close"], dtype=float).T
    vol_values = np.asarray(raw["vol"], dtype=float).T
    vol_valid = np.asarray(validity["vol"], dtype=bool).T
    adv = _lagged_adv_source(vol_values, vol_valid)
    buy = _execution_mask(execution_masks, "buyable_at_open", len(dates), len(assets))
    sell = _execution_mask(execution_masks, "sellable_at_open", len(dates), len(assets))
    common_execution = np.ones((len(dates), len(assets)), dtype=bool)
    for name in (
        "active", "listed", "open_execution_known",
        "open_execution_value", "suspension_source_covered", "corporate_action_validity",
    ):
        common_execution &= _execution_mask(execution_masks, name, len(dates), len(assets))
    buy_membership = (
        _execution_mask(execution_masks, "membership", len(dates), len(assets))
        & _execution_mask(execution_masks, "membership_known", len(dates), len(assets))
    )
    excluded = _execution_mask(execution_masks, "conservative_open_excluded", len(dates), len(assets))
    gaps = _execution_mask(execution_masks, "unexplained_data_gap", len(dates), len(assets))
    buy &= common_execution & buy_membership & ~excluded & ~gaps & open_valid
    sell &= common_execution & ~excluded & ~gaps & open_valid
    signal_common = np.ones((len(signal_dates), len(assets)), dtype=bool)
    for name in (
        "signal_candidate_cells", "membership", "membership_known", "active", "listed",
        "st_status_known", "st_information_available", "signal_eligible_at_close",
    ):
        signal_common &= _signal_mask(signal_masks, name, len(signal_dates), len(assets))
    signal_common &= ~_signal_mask(signal_masks, "st_effective", len(signal_dates), len(assets))
    signal_common &= ~_signal_mask(signal_masks, "unexplained_data_gap", len(signal_dates), len(assets))
    signal_common &= close_valid[: len(signal_dates)]
    valuation_open = np.where(open_valid, open_values, np.nan)
    valuation_close = np.where(close_valid, close_values, np.nan)
    eligible_indices = np.flatnonzero(signal_common.any(axis=1))
    if eligible_indices.size == 0:
        raise Task055AOrchestrationError("task055a_no_signal_eligible_dates")
    start = max(0, int(eligible_indices[0]) - 20)
    dates = dates[start:]
    signal_dates = signal_dates[start:]
    signal_common = signal_common[start:]
    open_values = open_values[start:]
    close_values = close_values[start:]
    valuation_open = valuation_open[start:]
    valuation_close = valuation_close[start:]
    adv = adv[start:]
    buy = buy[start:]
    sell = sell[start:]
    corporate_actions = [
        row for row in (bundle.get("corporate_actions") or [])
        if dates[0] <= str(row.get("ex_date") or row.get("effective_date") or "").replace("-", "") <= dates[-1]
    ]
    return {
        "market": {
            "dates": dates,
            "assets": assets,
            "open": open_values,
            "close": close_values,
            "valuation_open": valuation_open,
            "valuation_close": valuation_close,
            "adv": adv,
        },
        "buy": buy,
        "sell": sell,
        "signal_common": signal_common,
        "factor_values": {key: np.asarray(value)[:, start:] for key, value in bundle["factor_values"].items()},
        "factor_validity": {key: np.asarray(value)[:, start:] for key, value in bundle["factor_validity"].items()},
        "corporate_actions": corporate_actions,
        "benchmark": _benchmark_view(bundle.get("benchmark_index_bars") or [], dates),
        "bundle_manifest": dict(bundle["manifest"]),
        "signal_count": len(signal_dates),
        "simulation_start_date": dates[0],
    }


def _execute_generation(
    *,
    root: Path,
    role: str,
    spec_hash: str,
    policy_seal: Mapping[str, Any],
    exact_ids: Sequence[str],
    prepared: Mapping[str, Any],
    simulator: Callable[..., Any],
    require_uncached: bool,
) -> dict[str, Any]:
    generation_identity = canonical_hash({"role": role, "spec_hash": spec_hash, "policy_seal": policy_seal["content_hash"]})
    generation_id = f"simulation_{generation_identity[:24]}"
    target = root / "generations" / generation_id
    manifest_path = target / "simulation_generation_manifest.json"
    if manifest_path.exists():
        if require_uncached:
            raise Task055AOrchestrationError(f"task055a_uncached_generation_already_exists:{role}")
        validated = _validate_run_generation(manifest_path, resume=True)
        return validated | {"resume_hit": True, "manifest_path": str(manifest_path)}
    if not require_uncached:
        raise Task055AOrchestrationError("task055a_resume_generation_missing")

    staging = Path(tempfile.mkdtemp(prefix=".task055a.simulation.", dir=root.parent if root.parent.exists() else None))
    rows: list[dict[str, Any]] = []
    try:
        signal_count = int(prepared["signal_count"])
        date_count = len(prepared["market"]["dates"])
        asset_count = len(prepared["market"]["assets"])
        for factor_id in exact_ids:
            values = np.asarray(prepared["factor_values"][factor_id])
            valid = np.asarray(prepared["factor_validity"][factor_id], dtype=bool)
            expected = (asset_count, signal_count)
            if values.shape != expected or valid.shape != expected:
                raise Task055AOrchestrationError(f"task055a_factor_shape_mismatch:{factor_id}")
            scores = np.full((date_count, asset_count), np.nan, dtype=float)
            scores[:signal_count] = values.T
            selection = np.zeros((date_count, asset_count), dtype=bool)
            selection[:signal_count] = valid.T & prepared["signal_common"]
            for scenario_name in SCENARIO_NAMES:
                terminal = "retrospective_modeled_completed" if any(selection[:signal_count].ravel()) else "data_blocked"
                run_spec = {
                    "factor_id": factor_id,
                    "scenario": scenario_name,
                    "terminal_state": terminal,
                    "policy": PREREGISTERED_SCENARIOS[scenario_name].to_dict(),
                    "policy_seal_hash": policy_seal["content_hash"],
                }
                factor_entries = prepared["bundle_manifest"].get("artifacts") or {}
                input_lineage = {
                    "bundle_content_hash": prepared["bundle_manifest"].get("content_hash"),
                    "factor_values_sha256": (factor_entries.get(f"factor:{factor_id}:values") or {}).get("sha256"),
                    "factor_validity_sha256": (factor_entries.get(f"factor:{factor_id}:validity") or {}).get("sha256"),
                    "policy_seal_hash": policy_seal["content_hash"],
                }
                if not input_lineage["factor_values_sha256"] or not input_lineage["factor_validity_sha256"]:
                    raise Task055AOrchestrationError(f"task055a_factor_lineage_missing:{factor_id}")
                relative_root = Path("runs") / factor_id / scenario_name
                native_root = root / relative_root
                if (native_root / "current.json").exists():
                    raise Task055AOrchestrationError(f"task055a_uncached_native_run_exists:{factor_id}:{scenario_name}")
                try:
                    result = simulator(
                        prepared["market"],
                        scores,
                        masks={"select": selection, "buy": prepared["buy"], "sell": prepared["sell"]},
                        corporate_actions=prepared["corporate_actions"],
                        policy=PREREGISTERED_SCENARIOS[scenario_name],
                    )
                    published = publish_simulation_run(
                        output_root=native_root,
                        result=result,
                        spec=run_spec,
                        input_lineage=input_lineage,
                        market=prepared["market"],
                        benchmark=prepared["benchmark"],
                        allow_resume=False,
                    )
                except SimulationDataBlocker as error:
                    terminal = "data_blocked"
                    run_spec["terminal_state"] = terminal
                    published = publish_blocked_simulation_run(
                        output_root=native_root,
                        spec=run_spec,
                        input_lineage=input_lineage,
                        blocker={"code": "security_date_evidence_insufficient", "detail": str(error)},
                    )
                verified = verify_simulation_run(native_root)
                if published["truth_hash"] != verified["truth_hash"]:
                    raise Task055AOrchestrationError("task055a_native_artifact_verification_mismatch")
                rows.append({
                    "factor_id": factor_id,
                    "scenario": scenario_name,
                    "terminal_state": terminal,
                    "run_hash": verified["truth_hash"],
                    "content_hash": verified["content_hash"],
                    "spec_hash": verified["spec_hash"],
                    "input_lineage_hash": verified["input_lineage_hash"],
                    "path": str(relative_root),
                    "reason_code": None if terminal != "data_blocked" else (verified.get("blocker") or {}).get("code"),
                })
        truth_hash = canonical_hash([row["run_hash"] for row in rows])
        manifest = {
            "schema_version": RUN_GENERATION_SCHEMA,
            "role": role,
            "generation_id": generation_id,
            "generation_identity": generation_identity,
            "spec_hash": spec_hash,
            "policy_seal_hash": policy_seal["content_hash"],
            "candidate_ids": list(exact_ids),
            "scenarios": list(SCENARIO_NAMES),
            "terminal_count": len(rows),
            "truth_hash": truth_hash,
            "runs": rows,
        }
        manifest["content_hash"] = canonical_hash({key: value for key, value in manifest.items() if key != "content_hash"})
        _write_json(staging / "simulation_generation_manifest.json", manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _atomic_json(root / "current.json", {"generation_id": generation_id, "manifest": f"generations/{generation_id}/simulation_generation_manifest.json", "truth_hash": truth_hash})
    return _validate_run_generation(manifest_path) | {"resume_hit": False, "manifest_path": str(manifest_path)}


def _validate_run_generation(manifest_path: Path, *, resume: bool = False) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != RUN_GENERATION_SCHEMA:
        raise Task055AOrchestrationError("task055a_run_generation_schema_invalid")
    semantic = {key: value for key, value in manifest.items() if key != "content_hash"}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise Task055AOrchestrationError("task055a_run_generation_hash_invalid")
    rows = manifest.get("runs") or []
    if len(rows) != 20 * len(SCENARIO_NAMES) or manifest.get("terminal_count") != len(rows):
        raise Task055AOrchestrationError("task055a_run_generation_terminal_count_invalid")
    if sorted({row["factor_id"] for row in rows}) != sorted(manifest.get("candidate_ids") or []):
        raise Task055AOrchestrationError("task055a_run_generation_candidate_set_invalid")
    if sorted({row["scenario"] for row in rows}) != sorted(SCENARIO_NAMES):
        raise Task055AOrchestrationError("task055a_run_generation_scenario_set_invalid")
    for row in rows:
        run_root = manifest_path.parents[2] / row["path"]
        verified = (
            resume_simulation_run(
                run_root,
                expected_spec_hash=row["spec_hash"],
                expected_input_lineage_hash=row["input_lineage_hash"],
            )
            if resume
            else verify_simulation_run(
                run_root,
                expected_spec_hash=row["spec_hash"],
                expected_input_lineage_hash=row["input_lineage_hash"],
            )
        )
        if verified["truth_hash"] != row["run_hash"] or verified["content_hash"] != row["content_hash"]:
            raise Task055AOrchestrationError("task055a_run_artifact_hash_invalid")
    if canonical_hash([row["run_hash"] for row in rows]) != manifest.get("truth_hash"):
        raise Task055AOrchestrationError("task055a_run_truth_hash_invalid")
    return manifest


def _publish_final(output_root: Path, spec_hash: str, result: Mapping[str, Any]) -> dict[str, Any]:
    semantic = dict(result)
    semantic["result_hash"] = canonical_hash(result)
    generation_id = f"task055a_result_{spec_hash[:24]}"
    target = output_root / "final" / "generations" / generation_id
    _publish_generation(target, "task055a_result.json", semantic)
    _atomic_json(output_root / "final" / "current.json", {"generation_id": generation_id, "spec_hash": spec_hash, "manifest": f"generations/{generation_id}/task055a_result.json"})
    return semantic | {"result_path": str(target / "task055a_result.json"), "orchestrator_resume_hit": False}


def _validate_final_result(result: Mapping[str, Any], output_root: Path) -> None:
    if result.get("status") not in {SUCCESS_STATUS, BLOCKED_STATUS}:
        raise Task055AOrchestrationError("task055a_final_status_invalid")
    expected = canonical_hash({key: value for key, value in result.items() if key not in {"result_hash", "result_path", "orchestrator_resume_hit"}})
    if result.get("result_hash") != expected:
        raise Task055AOrchestrationError("task055a_final_result_hash_invalid")
    if result.get("status") == SUCCESS_STATUS:
        if result.get("terminal_count") != 100 or result.get("immutable_resume_hit") is not True:
            raise Task055AOrchestrationError("task055a_final_success_evidence_invalid")
        for name, expected_hash in (
            ("primary", result.get("primary_truth_hash")),
            ("sibling", result.get("sibling_truth_hash")),
        ):
            pointer = _read_json(output_root / name / "current.json")
            manifest = _validate_run_generation(output_root / name / pointer["manifest"])
            if manifest.get("truth_hash") != expected_hash:
                raise Task055AOrchestrationError(f"task055a_final_{name}_truth_mismatch")
        if result.get("primary_truth_hash") != result.get("sibling_truth_hash"):
            raise Task055AOrchestrationError("task055a_final_ab_truth_mismatch")


def _load_existing_final(output_root: Path, spec_hash: str) -> dict[str, Any] | None:
    pointer = output_root / "final" / "current.json"
    if not pointer.exists():
        return None
    current = _read_json(pointer)
    if current.get("spec_hash") != spec_hash:
        return None
    manifest = output_root / "final" / current["manifest"]
    result = _read_json(manifest)
    return result | {"result_path": str(manifest)}


def _semantic_spec(config: Mapping[str, Any]) -> dict[str, Any]:
    physical_inventory = inspect_physical_states(config["physical_state_roots"])
    return {
        "config_schema": config["schema_version"],
        "observation_seal_sha256": sha256_file(config["observation_seal"]),
        "simulation_bundle_sha256": sha256_file(config["simulation_bundle"]),
        "physical_state_roots": {name: str(Path(config["physical_state_roots"][name]).resolve()) for name in PHYSICAL_STATE_NAMES},
        "physical_state_content_hash": canonical_hash(physical_inventory),
        "scenario_policy_hash": canonical_hash({name: PREREGISTERED_SCENARIOS[name].to_dict() for name in SCENARIO_NAMES}),
        "code_semantic_hash": _code_semantic_hash(),
    }


def _load_config(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, Mapping) else _read_json(Path(value))
    forbidden = sorted(FORBIDDEN_CONFIG_KEYS & set(payload))
    if forbidden:
        raise Task055AOrchestrationError(f"task055a_forbidden_config_keys:{forbidden}")
    required = {"schema_version", "observation_seal", "simulation_bundle", "output_root", "physical_state_roots"}
    missing = sorted(required - set(payload))
    extra = sorted(set(payload) - required)
    if missing or extra or payload.get("schema_version") != CONFIG_SCHEMA:
        raise Task055AOrchestrationError(f"task055a_config_contract_invalid:missing={missing}:extra={extra}")
    if not isinstance(payload["physical_state_roots"], Mapping):
        raise Task055AOrchestrationError("task055a_physical_state_roots_not_mapping")
    return payload


def _validate_exact20(values: Any) -> list[str]:
    result = [str(value) for value in (values or [])]
    if len(result) != 20 or len(set(result)) != 20 or result != sorted(result):
        raise Task055AOrchestrationError("task055a_exact20_identity_invalid")
    return result


def _validate_loaded_identity(bundle: Mapping[str, Any], exact_ids: Sequence[str], manifest: Mapping[str, Any]) -> None:
    if list(bundle.get("manifest", {}).get("exact20_ids") or []) != list(exact_ids):
        raise Task055AOrchestrationError("task055a_loaded_bundle_identity_mismatch")
    for name in ("factor_values", "factor_validity"):
        if sorted((bundle.get(name) or {}).keys()) != sorted(exact_ids):
            raise Task055AOrchestrationError(f"task055a_loaded_factor_set_mismatch:{name}")
    if bundle["manifest"].get("content_hash") != manifest.get("content_hash"):
        raise Task055AOrchestrationError("task055a_loaded_bundle_hash_mismatch")


def _execution_mask(masks: Mapping[str, Any], name: str, dates: int, assets: int) -> np.ndarray:
    array = np.asarray(masks[name], dtype=bool)
    if array.shape == (assets, dates):
        return array.T.copy()
    if array.shape == (dates, assets):
        return array.copy()
    raise Task055AOrchestrationError(f"task055a_execution_mask_shape_invalid:{name}:{array.shape}")


def _signal_mask(masks: Mapping[str, Any], name: str, dates: int, assets: int) -> np.ndarray:
    array = np.asarray(masks[name], dtype=bool)
    if array.shape == (assets, dates):
        return array.T.copy()
    if array.shape == (dates, assets):
        return array.copy()
    raise Task055AOrchestrationError(f"task055a_signal_mask_shape_invalid:{name}:{array.shape}")


def _lagged_adv_source(volume: np.ndarray, valid: np.ndarray, window: int = 20) -> np.ndarray:
    result = np.full(volume.shape, np.nan, dtype=float)
    for index in range(volume.shape[0]):
        start = max(0, index - window + 1)
        window_values = volume[start : index + 1]
        window_valid = valid[start : index + 1] & np.isfinite(window_values) & (window_values >= 0)
        count = window_valid.sum(axis=0)
        total = np.where(window_valid, window_values, 0.0).sum(axis=0)
        result[index] = np.divide(total, count, out=np.full(volume.shape[1], np.nan), where=count > 0)
    return result


def _benchmark_view(rows: Sequence[Mapping[str, Any]], dates: Sequence[str]) -> dict[str, Any]:
    by_date: dict[str, float] = {}
    for row in rows:
        date = str(row.get("trade_date", row.get("date", ""))).replace("-", "")
        value = row.get("open")
        if date and value is not None:
            by_date[date] = float(value)
    missing = [date for date in dates if date not in by_date or not np.isfinite(by_date[date])]
    if missing:
        raise Task055AOrchestrationError(f"task055a_benchmark_open_missing:{missing[:5]}")
    return {"dates": list(dates), "open": [by_date[date] for date in dates]}


def _physical_file_records(path: Path) -> int:
    if path.stat().st_size == 0:
        return 0
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as handle:
            return sum(bool(line.strip()) for line in handle)
    if path.suffix.lower() == ".json":
        value = _read_json(path)
        if value in ({}, [], None, ""):
            return 0
        if isinstance(value, list):
            return len(value)
        if isinstance(value, Mapping):
            for key in ("records", "items", "entries", "rows", "queue"):
                if isinstance(value.get(key), list):
                    return len(value[key])
        return 1
    return 1


def _code_semantic_hash() -> str:
    root = Path(__file__).resolve().parent
    return canonical_hash({name: sha256_file(root / name) for name in ("run.py", "policy.py", "simulator.py", "bundle.py")})


def _publish_generation(target: Path, filename: str, payload: Mapping[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, default=_json_default) + "\n"
    if target.exists():
        existing = target / filename
        if not existing.is_file() or existing.read_text(encoding="utf-8") != serialized:
            raise Task055AOrchestrationError(f"task055a_content_address_collision:{target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        (staging / filename).write_text(serialized, encoding="utf-8")
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Task055AOrchestrationError(f"task055a_json_object_required:{path}")
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the strict Task 055-A production orchestration")
    parser.add_argument("--config", required=True, help="Task 055-A orchestrator config JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_task055a(args.config)
    print(json.dumps({"status": result["status"], "result_path": result["result_path"]}, sort_keys=True))
    return 0 if result["status"] == SUCCESS_STATUS else 2


if __name__ == "__main__":
    raise SystemExit(main())
