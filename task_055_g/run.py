"""Task 055-G production DAG: offline truth, official Fee, and sealed frontier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from .access import (
    AccessBroker,
    build_production_access_plan,
    canonical_hash,
    publish_bootstrap_access_plan,
    validate_access_ledger,
    validate_access_plan,
)
from .causal import (
    build_fee_aware_causal_frontier,
    validate_fee_aware_causal_frontier,
)
from .contracts import (
    EXPECTED_BASELINE,
    FINAL_BLOCKED_STATUS,
    FINAL_REPORT_SCHEMA,
    FINAL_WAITING_STATUS,
    MAX_DATE,
    SIMULATION_END,
    SIMULATION_START,
)
from .fees import (
    independent_verify_fee_schedule,
    official_fee_workflow_spec,
    run_fee_dag,
    validate_fee_schedule_v2,
)
from .lineage import resolve_and_validate_parent_lineage
from .network_state import (
    consolidate,
    final_verify as publish_network_state_verification,
    ledger_summary,
    run_until_blocked,
    verify_state_read_only,
)
from .operational import (
    publish_authoritative_operational_seal,
    verify_authoritative_operational_seal,
)
from .truth import build_truth_v2, validate_truth_v2
from .verifier import validate_semantic_verification, verify_task055g_semantics


class Task055GError(RuntimeError):
    pass


CERTIFICATION_BLOCKERS = (
    "historical_selection_contamination",
    "selection_data_reused",
    "execution_modeled",
    "suspension_timing_semantics_uncertified",
    "constituent_publication_timing_unknown",
    "vendor_historical_revision_risk",
    "prospective_holdout_not_arrived",
    "uncalibrated_broker_commission_slippage_impact",
)

FINAL_VERIFICATION_SCHEMA = "task055g_independent_final_verification_v1"
FINAL_VERIFICATION_WAITING_STATUS = "verified_waiting_for_network_authorization"
FINAL_VERIFICATION_BLOCKED_STATUS = "verified_blocked"

_FINAL_ARTIFACT_KEYS = frozenset(
    {
        "access_plan",
        "producer_read_ledger",
        "truth_v2",
        "fee_schedule_v2",
        "operational_seal",
        "causal_frontier",
        "semantic_verification",
        "network_state_root",
    }
)

_MISSING_ARTIFACT_BLOCKER_STAGES = {
    "access_plan": frozenset({"access_and_parent_lineage"}),
    "producer_read_ledger": frozenset({"access_and_parent_lineage"}),
    "truth_v2": frozenset({"access_and_parent_lineage", "truth_v2"}),
    "fee_schedule_v2": frozenset({"access_and_parent_lineage", "fee_schedule_v2"}),
    "operational_seal": frozenset({"operational_state"}),
    "causal_frontier": frozenset(
        {
            "access_and_parent_lineage",
            "truth_v2",
            "fee_schedule_v2",
            "causal_frontier_or_network_state",
        }
    ),
    "network_state_root": frozenset(
        {
            "access_and_parent_lineage",
            "truth_v2",
            "fee_schedule_v2",
            "causal_frontier_or_network_state",
        }
    ),
    "semantic_verification": frozenset(
        {
            "access_and_parent_lineage",
            "truth_v2",
            "fee_schedule_v2",
            "causal_frontier_or_network_state",
            "independent_semantic_verification",
        }
    ),
}


def run_task055g(
    *,
    repository_root: str | Path,
    governed_root: str | Path,
    output_root: str | Path,
    allow_official_fee_network: bool,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline = _verify_baseline(repository)
    code_hashes = semantic_source_hashes(repository)
    builder_code_hash = canonical_hash(code_hashes)

    stages: dict[str, Any] = {}
    engineering_blockers: list[dict[str, Any]] = []
    broker: AccessBroker | None = None
    parents: dict[str, Any] | None = None
    producer_ledger: dict[str, Any] | None = None
    try:
        bootstrap = publish_bootstrap_access_plan(output / "access" / "bootstrap")
        production_plan = build_production_access_plan(
            governed_root=governed,
            bootstrap_plan=bootstrap["manifest_path"],
            output_root=output / "access" / "production",
        )
        stages["access_plan"] = production_plan
        broker = AccessBroker(governed, production_plan["manifest_path"])
        parents = resolve_and_validate_parent_lineage(
            governed_root=governed,
            access_plan=production_plan["manifest_path"],
            broker=broker,
        )
        stages["parent_lineage"] = parents
    except Exception as exc:
        engineering_blockers.append({"stage": "access_and_parent_lineage", "code": str(exc)})

    truth = None
    if broker is not None and parents is not None:
        try:
            truth = build_truth_v2(
                governed_root=governed,
                parents=parents,
                output_root=output / "truth_v2",
                broker=broker,
                builder_code_hash=builder_code_hash,
            )
            truth = validate_truth_v2(truth["manifest_path"])
            stages["truth_v2"] = truth
        except Exception as exc:
            engineering_blockers.append({"stage": "truth_v2", "code": str(exc)})

    fee_dag = None
    if broker is not None and parents is not None:
        try:
            if not allow_official_fee_network:
                raise Task055GError("official_fee_network_authorization_required")
            policy_copy = _publish_policy_input_copy(
                governed=governed,
                relative_path=str(parents["policy_seal"]),
                output=output / "input_closure" / "task055a_policy_seal.json",
                broker=broker,
            )
            spec = official_fee_workflow_spec()
            fee_dag = run_fee_dag(
                output_root=output / "fee_workflow",
                policy_seal=policy_copy,
                simulation_start=SIMULATION_START,
                simulation_end=SIMULATION_END,
                documents=spec["documents"],
                extractors=spec["extractors"],
                allow_network=True,
            )
            validate_fee_schedule_v2(fee_dag["schedule"]["manifest_path"])
            stages["fee_workflow"] = fee_dag
        except Exception as exc:
            engineering_blockers.append({"stage": "fee_schedule_v2", "code": str(exc)})

    operational = None
    try:
        operational = publish_authoritative_operational_seal(
            governed,
            output / "operational_state",
            initialize_genesis=True,
        )
        verified_operational = verify_authoritative_operational_seal(
            governed,
            operational["manifest_path"],
        )
        operational = operational | verified_operational
        stages["operational_state"] = operational
    except Exception as exc:
        engineering_blockers.append({"stage": "operational_state", "code": str(exc)})

    causal = None
    network = None
    if broker is not None and parents is not None and truth is not None and fee_dag is not None:
        try:
            causal = build_fee_aware_causal_frontier(
                truth_manifest=truth["manifest_path"],
                matrix_root=governed / str(parents["matrix_root"]),
                simulation_bundle_manifest=str(parents["simulation_bundle"]),
                fee_schedule_manifest=fee_dag["schedule"]["manifest_path"],
                output_root=output / "causal_frontier",
                broker=broker,
                parent_lineage_content_hash=parents["content_hash"],
                builder_code_hash=builder_code_hash,
            )
            causal = validate_fee_aware_causal_frontier(causal["manifest_path"])
            stages["causal_frontier"] = causal
            consolidation = consolidate(
                state_root=output / "network_state",
                plan_manifest=causal["network_plan"],
            )
            blocked = run_until_blocked(state_root=output / "network_state")
            state_verification = publish_network_state_verification(
                state_root=output / "network_state"
            )
            network = {
                "consolidation": consolidation,
                "run_until_blocked": blocked,
                "verification": state_verification,
                "ledger": ledger_summary(output / "network_state"),
            }
            stages["network_state"] = network
        except Exception as exc:
            engineering_blockers.append({"stage": "causal_frontier_or_network_state", "code": str(exc)})

    if broker is not None:
        try:
            producer_ledger = broker.publish_ledger(
                output / "access" / "producer_read_ledger"
            )
            validate_access_ledger(
                producer_ledger["manifest_path"],
                plan=stages["access_plan"]["manifest_path"],
            )
            stages["producer_read_ledger"] = producer_ledger
        except Exception as exc:
            engineering_blockers.append(
                {"stage": "access_and_parent_lineage", "code": str(exc)}
            )

    semantic = None
    if (
        parents is not None
        and truth is not None
        and causal is not None
        and fee_dag is not None
        and stages.get("access_plan") is not None
    ):
        try:
            semantic = verify_task055g_semantics(
                governed_root=governed,
                access_plan=stages["access_plan"]["manifest_path"],
                producer_truth_manifest=truth["manifest_path"],
                causal_manifest=causal["manifest_path"],
                fee_schedule_manifest=fee_dag["schedule"]["manifest_path"],
                output_root=output / "semantic_verification",
            )
            semantic = validate_semantic_verification(semantic["manifest_path"])
            stages["semantic_verification"] = semantic
        except Exception as exc:
            engineering_blockers.append({"stage": "independent_semantic_verification", "code": str(exc)})

    if operational is not None:
        try:
            verify_authoritative_operational_seal(governed, operational["manifest_path"])
        except Exception as exc:
            engineering_blockers.append({"stage": "operational_state_post_scan", "code": str(exc)})

    report = _build_report(
        baseline=baseline,
        code_hashes=code_hashes,
        builder_code_hash=builder_code_hash,
        stages=stages,
        engineering_blockers=engineering_blockers,
    )
    report = _relative_artifacts(report, output)
    published = _publish_final_report(output / "final", report)
    final_verification = verify_task055g_final_report(
        published["manifest_path"],
        governed_root=governed,
        task_root=output,
    )
    published_verification = _publish_final_verification(
        output / "final_verification",
        final_verification,
    )
    _atomic_json(
        output / "current.json",
        {
            "generation_id": published["generation_id"],
            "content_hash": published["content_hash"],
            "manifest": str(Path(published["manifest_path"]).relative_to(output)),
            "final_verification_generation_id": published_verification["generation_id"],
            "final_verification_content_hash": published_verification["content_hash"],
            "final_verification_manifest": str(
                Path(published_verification["manifest_path"]).relative_to(output)
            ),
        },
    )
    return published | {"final_verification": published_verification}


def verify_task055g_final_report(
    report_path: str | Path,
    *,
    governed_root: str | Path,
    task_root: str | Path,
) -> dict[str, Any]:
    root = Path(task_root).resolve()
    path = Path(report_path).resolve()
    if root != path and root not in path.parents:
        raise Task055GError("final_report_outside_task_root")
    report = json.loads(path.read_text(encoding="utf-8"))
    semantic = {
        key: value
        for key, value in report.items()
        if key not in {"content_hash", "generation_id"}
    }
    if report.get("schema_version") != FINAL_REPORT_SCHEMA or canonical_hash(semantic) != report.get("content_hash"):
        raise Task055GError("final_report_content_hash_invalid")
    expected_generation_id = f"task055g_report_{report['content_hash'][:24]}"
    expected_relative_path = (
        Path("final")
        / "generations"
        / expected_generation_id
        / "task055g_report.json"
    )
    if report.get("generation_id") != expected_generation_id:
        raise Task055GError("final_report_generation_id_invalid")
    if path != (root / expected_relative_path).resolve():
        raise Task055GError("final_report_generation_path_invalid")

    status = report.get("status")
    if status not in {FINAL_WAITING_STATUS, FINAL_BLOCKED_STATUS}:
        raise Task055GError("final_report_status_invalid")
    _verify_report_boundary_claims(report, waiting=status == FINAL_WAITING_STATUS)

    artifacts = dict(report.get("artifacts") or {})
    unknown_artifacts = sorted(set(artifacts) - _FINAL_ARTIFACT_KEYS)
    if unknown_artifacts:
        raise Task055GError(f"final_report_unknown_artifacts:{','.join(unknown_artifacts)}")
    resolved_artifacts = {
        key: _resolve_task_artifact(
            root,
            key,
            value,
            directory=key == "network_state_root",
        )
        for key, value in artifacts.items()
    }
    if len(set(resolved_artifacts.values())) != len(resolved_artifacts):
        raise Task055GError("final_report_duplicate_artifact_path")
    validated = _validate_final_artifacts(
        resolved_artifacts,
        governed_root=governed_root,
    )
    _verify_report_artifact_summaries(report, validated)
    if (
        (validated.get("producer_read_ledger") or {}).get(
            "prospective_holdout_accessed"
        )
        is True
        or (validated.get("semantic_verification") or {}).get(
            "prospective_holdout_accessed"
        )
        is True
    ):
        raise Task055GError("final_report_future_access_detected")
    if int(
        (validated.get("network_state_root") or {}).get("physical_attempt_count")
        or 0
    ) != 0:
        raise Task055GError("final_report_tushare_request_detected")

    missing_artifacts = sorted(_FINAL_ARTIFACT_KEYS - set(resolved_artifacts))
    engineering_blockers = list(report.get("engineering_blockers") or ())
    blocker_stages = {
        str(row.get("stage"))
        for row in engineering_blockers
        if isinstance(row, Mapping) and row.get("stage")
    }
    if status == FINAL_WAITING_STATUS:
        if missing_artifacts:
            raise Task055GError(
                f"final_report_waiting_artifacts_missing:{','.join(missing_artifacts)}"
            )
        if engineering_blockers:
            raise Task055GError("final_report_waiting_has_engineering_blockers")
        _verify_waiting_state(report, validated)
        verification_status = FINAL_VERIFICATION_WAITING_STATUS
    else:
        if not engineering_blockers:
            raise Task055GError("final_report_blocked_without_engineering_blocker")
        if report.get("stage") != "offline_engineering_baseline_blocked":
            raise Task055GError("final_report_blocked_stage_invalid")
        unjustified = [
            key
            for key in missing_artifacts
            if not (blocker_stages & _MISSING_ARTIFACT_BLOCKER_STAGES[key])
        ]
        if unjustified:
            raise Task055GError(
                f"final_report_blocked_missing_artifact_unjustified:{','.join(unjustified)}"
            )
        if not missing_artifacts and _waiting_conditions_hold(report, validated):
            raise Task055GError("final_report_blocked_without_observed_failure")
        verification_status = FINAL_VERIFICATION_BLOCKED_STATUS

    access_ledger = validated.get("producer_read_ledger") or {}
    network = validated.get("network_state_root") or {}
    causal = validated.get("causal_frontier") or {}
    operational = validated.get("operational_seal") or {}
    attestation = {
        "schema_version": FINAL_VERIFICATION_SCHEMA,
        "status": verification_status,
        "top_status": status,
        "report_content_hash": report["content_hash"],
        "validated_artifacts": {
            key: value.get("content_hash")
            for key, value in sorted(validated.items())
        },
        "missing_artifacts": missing_artifacts,
        "engineering_blocker_stages": sorted(blocker_stages),
        "access_plan_content_hash": (validated.get("access_plan") or {}).get("content_hash"),
        "access_ledger_content_hash": access_ledger.get("content_hash"),
        "truth_content_hash": (validated.get("truth_v2") or {}).get("content_hash"),
        "fee_content_hash": (validated.get("fee_schedule_v2") or {}).get("content_hash"),
        "fee_independent_verification_content_hash": (
            validated.get("fee_independent_verification") or {}
        ).get("content_hash"),
        "operational_content_hash": operational.get("content_hash"),
        "causal_content_hash": causal.get("content_hash"),
        "semantic_verification_content_hash": (
            validated.get("semantic_verification") or {}
        ).get("content_hash"),
        "network_state_verification_content_hash": network.get("content_hash"),
        "frontier_count": causal.get("round_one_frontier_count"),
        "frontier_root": causal.get("missing_key_root"),
        "network_physical_attempt_count": network.get("physical_attempt_count"),
        "prospective_holdout_accessed": access_ledger.get(
            "prospective_holdout_accessed"
        ),
        "operational_queues_verified_empty": (
            bool(operational)
            and all(int(value) == 0 for value in operational.get("state_counts", {}).values())
        ),
    }
    return attestation | {"content_hash": canonical_hash(attestation)}


def _validate_final_artifacts(
    artifacts: Mapping[str, Path],
    *,
    governed_root: str | Path,
) -> dict[str, dict[str, Any]]:
    validated: dict[str, dict[str, Any]] = {}
    if "access_plan" in artifacts:
        validated["access_plan"] = validate_access_plan(artifacts["access_plan"])
    if "producer_read_ledger" in artifacts:
        if "access_plan" not in validated:
            raise Task055GError("final_report_access_ledger_without_access_plan")
        validated["producer_read_ledger"] = validate_access_ledger(
            artifacts["producer_read_ledger"],
            plan=validated["access_plan"]["manifest_path"],
        )
    if "truth_v2" in artifacts:
        validated["truth_v2"] = validate_truth_v2(artifacts["truth_v2"])
    if "fee_schedule_v2" in artifacts:
        fee = validate_fee_schedule_v2(artifacts["fee_schedule_v2"])
        validated["fee_schedule_v2"] = fee
        validated["fee_independent_verification"] = independent_verify_fee_schedule(
            schedule=artifacts["fee_schedule_v2"]
        )
    if "operational_seal" in artifacts:
        validated["operational_seal"] = verify_authoritative_operational_seal(
            governed_root,
            artifacts["operational_seal"],
        )
    if "causal_frontier" in artifacts:
        validated["causal_frontier"] = validate_fee_aware_causal_frontier(
            artifacts["causal_frontier"]
        )
    if "semantic_verification" in artifacts:
        validated["semantic_verification"] = validate_semantic_verification(
            artifacts["semantic_verification"]
        )
    if "network_state_root" in artifacts:
        validated["network_state_root"] = verify_state_read_only(
            state_root=artifacts["network_state_root"]
        )
    return validated


def _verify_report_boundary_claims(
    report: Mapping[str, Any],
    *,
    waiting: bool,
) -> None:
    if report.get("network_accessed") is not False or int(
        report.get("network_request_count") or 0
    ) != 0:
        raise Task055GError("final_report_tushare_request_claim_invalid")
    if report.get("prospective_holdout_accessed") is not False:
        raise Task055GError("final_report_future_access_claim_invalid")
    max_read_date = report.get("max_read_date")
    if max_read_date and str(max_read_date) > MAX_DATE:
        raise Task055GError("final_report_max_read_date_invalid")
    readiness = dict(report.get("readiness") or {})
    for key in (
        "certification_ready",
        "portfolio_ready",
        "paper_ready",
        "live_ready",
    ):
        if readiness.get(key) is not False:
            raise Task055GError(f"final_report_readiness_boundary_invalid:{key}")
    if waiting and readiness.get("ready_for_exact_daily_canary") is not True:
        raise Task055GError("final_report_waiting_readiness_invalid")
    if not waiting and readiness.get("ready_for_exact_daily_canary") is not False:
        raise Task055GError("final_report_blocked_readiness_invalid")
    plan = dict(report.get("network_plan") or {})
    if waiting and (
        plan.get("network_executed") is not False or plan.get("token_read") is not False
    ):
        raise Task055GError("final_report_waiting_network_plan_boundary_invalid")
    if plan.get("network_executed") is True or plan.get("token_read") is True:
        raise Task055GError("final_report_network_plan_execution_detected")
    blocker_codes = {
        str(row.get("code"))
        for row in report.get("certification_blockers") or ()
        if isinstance(row, Mapping)
    }
    missing_certification = sorted(set(CERTIFICATION_BLOCKERS) - blocker_codes)
    if missing_certification:
        raise Task055GError(
            f"final_report_certification_blockers_missing:{','.join(missing_certification)}"
        )


def _verify_report_artifact_summaries(
    report: Mapping[str, Any],
    validated: Mapping[str, Mapping[str, Any]],
) -> None:
    section_map = {
        "access_plan": "access_plan",
        "producer_read_ledger": "read_ledger",
        "truth_v2": "truth_v2",
        "fee_schedule_v2": "fee_schedule_v2",
        "operational_seal": "operational_state",
        "causal_frontier": "causal_frontier",
        "semantic_verification": "semantic_verification",
    }
    for artifact_key, section_key in section_map.items():
        if artifact_key not in validated:
            continue
        expected = validated[artifact_key].get("content_hash")
        actual = (report.get(section_key) or {}).get("content_hash")
        if not expected or actual != expected:
            raise Task055GError(
                f"final_report_artifact_summary_hash_mismatch:{artifact_key}"
            )
    network = validated.get("network_state_root")
    if network is not None:
        summary = report.get("network_state") or {}
        if summary.get("content_hash") != network.get("content_hash"):
            raise Task055GError("final_report_network_summary_hash_mismatch")
        if int((summary.get("ledger") or {}).get("physical_attempt_count") or 0) != int(
            network.get("physical_attempt_count") or 0
        ):
            raise Task055GError("final_report_network_attempt_summary_mismatch")
    operational = validated.get("operational_seal")
    if operational is not None and (report.get("queues") or {}) != _queue_counts(operational):
        raise Task055GError("final_report_operational_queue_summary_mismatch")
    fee_independent = validated.get("fee_independent_verification")
    if fee_independent is not None:
        reported_hash = (report.get("fee_schedule_v2") or {}).get(
            "independent_verification_content_hash"
        )
        if reported_hash != fee_independent.get("content_hash"):
            raise Task055GError("final_report_fee_independent_summary_hash_mismatch")


def _verify_waiting_state(
    report: Mapping[str, Any],
    validated: Mapping[str, Mapping[str, Any]],
) -> None:
    if report.get("stage") != "fee_aware_round_one_exact_daily_frontier_sealed":
        raise Task055GError("final_report_waiting_stage_invalid")
    if not _waiting_conditions_hold(report, validated):
        raise Task055GError("final_report_waiting_state_invalid")


def _waiting_conditions_hold(
    report: Mapping[str, Any],
    validated: Mapping[str, Mapping[str, Any]],
) -> bool:
    required = _FINAL_ARTIFACT_KEYS | {"fee_independent_verification"}
    if not required <= set(validated):
        return False
    access_ledger = validated["producer_read_ledger"]
    fee = validated["fee_schedule_v2"]
    operational = validated["operational_seal"]
    causal = validated["causal_frontier"]
    semantic = validated["semantic_verification"]
    network = validated["network_state_root"]
    readiness = report.get("readiness") or {}
    if (
        access_ledger.get("prospective_holdout_accessed") is not False
        or semantic.get("prospective_holdout_accessed") is not False
        or fee.get("status") != "passed"
        or operational.get("status") != "passed"
        or causal.get("status") != "published"
        or int(causal.get("round_one_frontier_count") or 0) <= 0
        or semantic.get("status") != "passed"
        or network.get("status") != "verified"
        or int(network.get("physical_attempt_count") or 0) != 0
    ):
        return False
    if any(int(value) != 0 for value in operational.get("state_counts", {}).values()):
        return False
    if any(
        readiness.get(key) is not True
        for key in (
            "fee_schedule_ready",
            "operational_seal_ready",
            "fee_aware_frontier_ready",
            "ready_for_exact_daily_canary",
        )
    ):
        return False
    causal_summary = report.get("causal_frontier") or {}
    if (
        causal_summary.get("frontier_root") != causal.get("missing_key_root")
        or int(causal_summary.get("round_one_frontier_count") or 0)
        != int(causal.get("round_one_frontier_count") or 0)
    ):
        return False
    queue_counts = report.get("queues") or {}
    expected_queues = _queue_counts(operational)
    return queue_counts == expected_queues


def _resolve_task_artifact(
    task_root: Path,
    key: str,
    value: Any,
    *,
    directory: bool,
) -> Path:
    if not isinstance(value, str) or not value:
        raise Task055GError(f"final_artifact_path_invalid:{key}")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055GError(f"final_artifact_path_not_relative:{key}")
    resolved = (task_root / relative).resolve()
    if task_root != resolved and task_root not in resolved.parents:
        raise Task055GError(f"final_artifact_outside_task_root:{key}")
    if directory:
        if not resolved.is_dir():
            raise Task055GError(f"final_artifact_directory_missing:{key}")
    elif not resolved.is_file():
        raise Task055GError(f"final_artifact_file_missing:{key}")
    return resolved


def semantic_source_hashes(repository: Path) -> dict[str, str]:
    relatives = [
        "task_055_g/access.py",
        "task_055_g/bundle.py",
        "task_055_g/causal.py",
        "task_055_g/contracts.py",
        "task_055_g/fees.py",
        "task_055_g/lineage.py",
        "task_055_g/network_state.py",
        "task_055_g/operational.py",
        "task_055_g/truth.py",
        "task_055_g/verifier.py",
        "task_055_g/run.py",
        "task_055_f/truth_v2.py",
        "task_055_a/policy.py",
        "task_055_a/simulator.py",
    ]
    result = {}
    for relative in relatives:
        path = repository / relative
        if not path.is_file():
            raise Task055GError(f"semantic_source_missing:{relative}")
        result[relative] = _sha256(path)
    return result


def _build_report(
    *,
    baseline: Mapping[str, Any],
    code_hashes: Mapping[str, str],
    builder_code_hash: str,
    stages: Mapping[str, Any],
    engineering_blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    causal = stages.get("causal_frontier") or {}
    fee_dag = stages.get("fee_workflow") or {}
    fee = fee_dag.get("schedule") or {}
    operational = stages.get("operational_state") or {}
    semantic = stages.get("semantic_verification") or {}
    network = stages.get("network_state") or {}
    network_ledger = network.get("ledger") or {}
    producer_ledger = stages.get("producer_read_ledger") or {}
    access_ok = bool(stages.get("access_plan") and stages.get("parent_lineage"))
    queue_counts = _queue_counts(operational)
    effective_blockers = list(engineering_blockers)
    ready = (
        not effective_blockers
        and access_ok
        and (stages.get("truth_v2") or {}).get("status") == "published"
        and fee.get("status") == "passed"
        and operational.get("status") == "passed"
        and causal.get("status") == "published"
        and int(causal.get("round_one_frontier_count") or 0) > 0
        and semantic.get("status") == "passed"
        and (network.get("verification") or {}).get("status") == "verified"
        and int(network_ledger.get("physical_attempt_count") or 0) == 0
        and producer_ledger.get("prospective_holdout_accessed") is False
        and semantic.get("prospective_holdout_accessed") is False
        and all(value == 0 for value in queue_counts.values())
    )
    status = FINAL_WAITING_STATUS if ready else FINAL_BLOCKED_STATUS
    if not ready:
        effective_blockers.extend(
            _derived_final_gate_blockers(
                access_ok=access_ok,
                stages=stages,
                fee=fee,
                operational=operational,
                causal=causal,
                semantic=semantic,
                network=network,
                network_ledger=network_ledger,
                producer_ledger=producer_ledger,
                queue_counts=queue_counts,
                existing=effective_blockers,
            )
        )
    artifacts = _artifact_paths(stages)
    return {
        "schema_version": FINAL_REPORT_SCHEMA,
        "status": status,
        "stage": (
            "fee_aware_round_one_exact_daily_frontier_sealed"
            if ready
            else "offline_engineering_baseline_blocked"
        ),
        "baseline": dict(baseline),
        "network_accessed": bool(int(network_ledger.get("physical_attempt_count") or 0)),
        "network_request_count": int(network_ledger.get("physical_attempt_count") or 0),
        "network_logical_request_count": int(network_ledger.get("logical_request_count") or 0),
        "max_request_date": network_ledger.get("max_request_date"),
        "official_fee_https_request_count": len((fee_dag.get("acquisition") or {}).get("documents") or ()),
        "prospective_holdout_accessed": bool(
            producer_ledger.get("prospective_holdout_accessed")
            or semantic.get("prospective_holdout_accessed")
        ),
        "max_read_date": max(
            [
                value
                for value in (
                    producer_ledger.get("max_read_date"),
                    semantic.get("max_read_date"),
                )
                if value
            ],
            default=None,
        ),
        "parent_lineage": _parent_summary(stages.get("parent_lineage")),
        "access_plan": _manifest_summary(stages.get("access_plan")),
        "read_ledger": _manifest_summary(producer_ledger),
        "truth_v2": _truth_summary(stages.get("truth_v2")),
        "fee_schedule_v2": _fee_summary(fee_dag),
        "operational_state": _operational_summary(operational),
        "causal_frontier": _causal_summary(causal),
        "network_plan": _network_plan_summary(causal.get("network_plan") or {}),
        "network_state": {
            "status": (network.get("verification") or {}).get("status"),
            "content_hash": (network.get("verification") or {}).get("content_hash"),
            "blocking_gate": (network.get("run_until_blocked") or {}).get("blocking_gate"),
            "ledger": dict(network_ledger),
        },
        "semantic_verification": _manifest_summary(semantic),
        "readiness": {
            "fee_schedule_ready": fee.get("status") == "passed",
            "operational_seal_ready": operational.get("status") == "passed",
            "fee_aware_frontier_ready": causal.get("status") == "published",
            "ready_for_exact_daily_canary": ready,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "queues": queue_counts,
        "engineering_blockers": effective_blockers,
        "certification_blockers": [{"code": value} for value in CERTIFICATION_BLOCKERS],
        "blockers": effective_blockers + [{"code": value} for value in CERTIFICATION_BLOCKERS],
        "code_semantic_hash": builder_code_hash,
        "code_source_hashes": dict(code_hashes),
        "artifacts": artifacts,
    }


def _queue_counts(operational: Mapping[str, Any]) -> dict[str, int]:
    state_counts = operational.get("state_counts") or {}
    return {
        "certification": int(state_counts.get("certification_queue", 0)),
        "certified_pool": int(state_counts.get("certified_pool", 0)),
        "portfolio": int(state_counts.get("portfolio_campaign", 0)),
        "production_candidate": int(state_counts.get("production_candidate", 0)),
        "optimizer": int(state_counts.get("optimizer_activation", 0)),
        "paper": int(state_counts.get("paper_registry", 0)),
        "live": int(state_counts.get("live_registry", 0)),
    }


def _derived_final_gate_blockers(
    *,
    access_ok: bool,
    stages: Mapping[str, Any],
    fee: Mapping[str, Any],
    operational: Mapping[str, Any],
    causal: Mapping[str, Any],
    semantic: Mapping[str, Any],
    network: Mapping[str, Any],
    network_ledger: Mapping[str, Any],
    producer_ledger: Mapping[str, Any],
    queue_counts: Mapping[str, int],
    existing: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing_pairs = {
        (str(row.get("stage")), str(row.get("code")))
        for row in existing
        if isinstance(row, Mapping)
    }
    checks = (
        (access_ok, "access_and_parent_lineage", "access_or_parent_lineage_not_verified"),
        (
            (stages.get("truth_v2") or {}).get("status") == "published",
            "truth_v2",
            "truth_v2_not_published",
        ),
        (fee.get("status") == "passed", "fee_schedule_v2", "fee_schedule_v2_not_passed"),
        (
            operational.get("status") == "passed",
            "operational_state",
            "operational_state_not_passed",
        ),
        (
            causal.get("status") == "published",
            "causal_frontier_or_network_state",
            "causal_frontier_not_published",
        ),
        (
            int(causal.get("round_one_frontier_count") or 0) > 0,
            "causal_frontier_or_network_state",
            "round_one_frontier_empty",
        ),
        (
            semantic.get("status") == "passed",
            "independent_semantic_verification",
            "semantic_verification_not_passed",
        ),
        (
            (network.get("verification") or {}).get("status") == "verified",
            "causal_frontier_or_network_state",
            "network_state_not_verified",
        ),
        (
            int(network_ledger.get("physical_attempt_count") or 0) == 0,
            "causal_frontier_or_network_state",
            "tushare_physical_attempt_detected",
        ),
        (
            producer_ledger.get("prospective_holdout_accessed") is False,
            "access_and_parent_lineage",
            "producer_read_boundary_not_proven",
        ),
        (
            semantic.get("prospective_holdout_accessed") is False,
            "independent_semantic_verification",
            "semantic_read_boundary_not_proven",
        ),
        (
            all(value == 0 for value in queue_counts.values()),
            "operational_state",
            "operational_queue_nonempty",
        ),
    )
    result = []
    for passed, stage, code in checks:
        if not passed and (stage, code) not in existing_pairs:
            result.append({"stage": stage, "code": code})
    return result


def _publish_final_report(root: Path, semantic: Mapping[str, Any]) -> dict[str, Any]:
    return _publish_content_addressed_manifest(
        root=root,
        semantic=semantic,
        generation_prefix="task055g_report",
        manifest_name="task055g_report.json",
        staging_prefix=".task055g.final.",
    )


def _publish_final_verification(
    root: Path,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    semantic = {
        key: value
        for key, value in verification.items()
        if key not in {"content_hash", "generation_id", "manifest_path"}
    }
    expected_hash = canonical_hash(semantic)
    if verification.get("content_hash") not in {None, expected_hash}:
        raise Task055GError("final_verification_content_hash_invalid")
    return _publish_content_addressed_manifest(
        root=root,
        semantic=semantic,
        generation_prefix="task055g_final_verification",
        manifest_name="task055g_final_verification.json",
        staging_prefix=".task055g.final-verification.",
    )


def _publish_content_addressed_manifest(
    *,
    root: Path,
    semantic: Mapping[str, Any],
    generation_prefix: str,
    manifest_name: str,
    staging_prefix: str,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(semantic)
    generation_id = f"{generation_prefix}_{content_hash[:24]}"
    manifest = dict(semantic) | {
        "content_hash": content_hash,
        "generation_id": generation_id,
    }
    target = root / "generations" / generation_id
    target_manifest = target / manifest_name
    if target.exists():
        if not target_manifest.is_file():
            raise Task055GError(f"immutable_generation_manifest_missing:{generation_id}")
        existing = json.loads(target_manifest.read_text(encoding="utf-8"))
        if existing != manifest:
            raise Task055GError(f"immutable_generation_content_mismatch:{generation_id}")
    else:
        staging = Path(tempfile.mkdtemp(prefix=staging_prefix, dir=root))
        try:
            path = staging / manifest_name
            path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    pointer = {
        "generation_id": generation_id,
        "content_hash": content_hash,
        "manifest": f"generations/{generation_id}/{manifest_name}",
    }
    _atomic_json(root / "current.json", pointer)
    return manifest | {"manifest_path": str(target_manifest)}


def _artifact_paths(stages: Mapping[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, value in (
        ("access_plan", stages.get("access_plan")),
        ("producer_read_ledger", stages.get("producer_read_ledger")),
        ("truth_v2", stages.get("truth_v2")),
        ("fee_schedule_v2", (stages.get("fee_workflow") or {}).get("schedule")),
        ("operational_seal", stages.get("operational_state")),
        ("causal_frontier", stages.get("causal_frontier")),
        ("semantic_verification", stages.get("semantic_verification")),
    ):
        path = (value or {}).get("manifest_path")
        if path:
            paths[name] = path
    network = stages.get("network_state") or {}
    if network:
        paths["network_state_root"] = str(Path((network.get("consolidation") or {})["manifest_path"]).parents[4])
    return paths


def _parent_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    return {
        "status": "passed" if payload else "blocked",
        "content_hash": payload.get("content_hash"),
        "semantic": payload.get("semantic"),
    }


def _manifest_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    return {
        "status": payload.get("status"),
        "content_hash": payload.get("content_hash"),
        "generation_id": payload.get("generation_id"),
        "max_read_date": payload.get("max_read_date"),
        "prospective_holdout_accessed": payload.get("prospective_holdout_accessed"),
    }


def _truth_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    return {
        "status": payload.get("status"),
        "content_hash": payload.get("content_hash"),
        "record_count": payload.get("record_count"),
        "state_counts": payload.get("state_counts"),
        "suspend_type_counts": payload.get("suspend_type_counts"),
        "key_root": payload.get("key_root"),
    }


def _fee_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    schedule = payload.get("schedule") or {}
    independent = payload.get("independent_verification") or {}
    return {
        "status": schedule.get("status"),
        "content_hash": schedule.get("content_hash"),
        "rules_root": schedule.get("rules_root"),
        "rule_count": len(schedule.get("rules") or ()),
        "document_acquisition_content_hash": schedule.get("document_acquisition_content_hash"),
        "document_merkle_root": schedule.get("document_merkle_root"),
        "transport_ledger_root": schedule.get("transport_ledger_root"),
        "policy_seal_hash": schedule.get("policy_seal_hash"),
        "independent_verification_content_hash": independent.get("content_hash"),
        "certification_ready": False,
    }


def _operational_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    return {
        "status": payload.get("status"),
        "content_hash": payload.get("content_hash"),
        "writer_registry_content_hash": payload.get("writer_registry_content_hash"),
        "physical_scan_content_hash": payload.get("physical_scan_content_hash"),
        "state_counts": payload.get("state_counts") or {},
        "total_operational_record_count": payload.get("total_operational_record_count", -1),
    }


def _causal_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    return {
        "status": payload.get("status"),
        "content_hash": payload.get("content_hash"),
        "run_count": payload.get("run_count"),
        "terminal_counts": payload.get("terminal_counts"),
        "round_one_frontier_count": payload.get("round_one_frontier_count"),
        "frontier_root": payload.get("missing_key_root"),
        "held_mark_count": payload.get("held_mark_count"),
        "authorized_modeled_held_mark_count": payload.get("authorized_modeled_held_mark_count"),
    }


def _network_plan_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": value.get("status"),
        "schema_version": value.get("schema_version"),
        "plan_hash": value.get("plan_hash"),
        "frontier_root": value.get("frontier_root"),
        "request_count": len(value.get("requests") or ()),
        "network_executed": value.get("network_executed"),
        "token_read": value.get("token_read"),
    }


def _publish_policy_input_copy(
    *, governed: Path, relative_path: str, output: Path, broker: AccessBroker
) -> Path:
    raw = broker.read_bytes(relative_path, principal="task055g_fee_plan_input_publisher")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, output)
    return output


def _verify_baseline(repository: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repository, text=True).strip()

    head = git("rev-parse", "HEAD")
    remote = git("rev-parse", "origin/main")
    if head != EXPECTED_BASELINE or remote != EXPECTED_BASELINE:
        raise Task055GError(f"baseline_mismatch:head={head}:origin={remote}")
    return {"expected": EXPECTED_BASELINE, "head": head, "origin_main": remote}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_artifacts(report: Mapping[str, Any], task_root: Path) -> dict[str, Any]:
    task_root = task_root.resolve()
    result = dict(report)
    artifacts = {}
    for key, value in dict(report.get("artifacts") or {}).items():
        raw_path = Path(value)
        path = (raw_path if raw_path.is_absolute() else task_root / raw_path).resolve()
        if task_root not in path.parents and path != task_root:
            raise Task055GError(f"final_artifact_outside_task_root:{key}")
        artifacts[key] = str(path.relative_to(task_root))
    result["artifacts"] = artifacts
    return result


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Task 055-G production offline/official-fee DAG")
    parser.add_argument("--repository-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--governed-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--allow-official-fee-network", action="store_true")
    parser.add_argument("--final-verify", action="store_true")
    parser.add_argument("--report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.final_verify:
            if not args.report:
                raise Task055GError("final_verify_report_required")
            result = verify_task055g_final_report(
                args.report,
                governed_root=args.governed_root,
                task_root=args.output_root,
            )
        else:
            result = run_task055g(
                repository_root=args.repository_root,
                governed_root=args.governed_root,
                output_root=args.output_root,
                allow_official_fee_network=bool(args.allow_official_fee_network),
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
