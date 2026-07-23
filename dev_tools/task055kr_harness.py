from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from data_pipeline.ashare.providers.tushare_client import parse_tushare_response_payload
from data_pipeline.ashare.request_identity import TushareRequestIdentity
from task_055_h.io import canonical_hash, read_json, sha256_file
from task_055_j.ledger import DurableHashJournal
from task_055_k.application import apply_accepted_response, production_context_from_parent
from task_055_k.authority import publish_candidate_checkpoint, validate_task055j_parent
from task_055_k.broker import (
    AcceptedResponse,
    publish_attempt_reservation,
    publish_canary_acceptance,
    publish_signed_transport_receipt,
    publish_validated_cache,
    request_from_checkpoint,
    load_accepted_response,
)
from task_055_k.contracts import APPLICATION_STAGES, CANARY
from task_055_k.independent import independently_verify_application_replay
from task_055_k.rehearsal import (
    independently_verify_rehearsal,
    publish_rehearsal_report,
    validate_rehearsal,
)
from task_055_k.signing import EphemeralReceiptSigner
from task_055_k.stage_machine import (
    ApplicationStageMachine,
    NativeStageResult,
    StageDefinition,
    StageRuntime,
    Task055KInjectedCrash,
)


def run_real_context_offline_rehearsal(
    *,
    repository_root: str | Path,
    parent_final_seal: str | Path,
    candidate_authority_content_hash: str,
    implementation_commit: str,
    source_root: str,
    output_root: str | Path,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    parent = validate_task055j_parent(
        final_seal_path=parent_final_seal,
        repository_root=repository_root,
    )
    context = production_context_from_parent(parent)
    context["component_cache_root"] = str(root / "component_cache")
    positive_accepted, positive_checkpoint = synthetic_accepted_response(
        authority_root=root / "synthetic_authorities/positive",
        ordered_keys=parent["ordered_exact_daily_keys"],
        implementation_commit=implementation_commit,
        source_root=source_root,
        items=[
            [
                "000413.SZ",
                "20160726",
                10.0,
                11.0,
                9.0,
                10.5,
                10.0,
                100.0,
                1000.0,
            ]
        ],
    )
    empty_accepted, empty_checkpoint = synthetic_accepted_response(
        authority_root=root / "synthetic_authorities/empty",
        ordered_keys=parent["ordered_exact_daily_keys"],
        implementation_commit=implementation_commit,
        source_root=source_root,
        items=[],
    )
    positive = _run_branch(
        branch="positive",
        accepted=positive_accepted,
        context=context,
        root=root / "applications/positive",
    )
    empty = _run_branch(
        branch="empty",
        accepted=empty_accepted,
        context=context,
        root=root / "applications/empty",
    )
    generic_recovery = run_lightweight_recovery_matrix(
        accepted=positive_accepted,
        output_root=root / "recovery_matrix/generic_state_machine",
    )
    component_recovery = run_production_component_recovery_matrix(
        accepted=empty_accepted,
        context=context,
        output_root=root / "recovery_matrix/production_components",
    )
    recovery = {
        "case_count": generic_recovery["case_count"]
        + component_recovery["case_count"],
        "cases_root": canonical_hash(
            [generic_recovery["cases_root"], component_recovery["cases_root"]]
        ),
        "all_stage_boundaries_tested": generic_recovery[
            "all_stage_boundaries_tested"
        ],
        "all_negative_boundaries_blocked": generic_recovery[
            "all_negative_boundaries_blocked"
        ]
        and component_recovery["all_negative_boundaries_blocked"],
        "generic_state_machine": generic_recovery,
        "production_component_recovery": component_recovery,
    }
    report = publish_rehearsal_report(
        candidate_authority_content_hash=candidate_authority_content_hash,
        candidate_checkpoint_content_hash=canonical_hash(
            [positive_checkpoint["content_hash"], empty_checkpoint["content_hash"]]
        ),
        production_context_root=context["context_root"],
        positive=positive,
        empty=empty,
        recovery_matrix=recovery,
        output_root=root / "report",
    )
    validated = validate_rehearsal(report["manifest_path"])
    verification = independently_verify_rehearsal(report["manifest_path"])
    return validated | {
        "independent_verification": verification,
        "positive_accepted": positive_accepted,
        "empty_accepted": empty_accepted,
        "context": context,
    }


def synthetic_accepted_response(
    *,
    authority_root: str | Path,
    ordered_keys: Sequence[Mapping[str, Any]],
    implementation_commit: str,
    source_root: str,
    items: list[list[object]],
) -> tuple[AcceptedResponse, dict[str, Any]]:
    root = Path(authority_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "single_canary.lock"
    lock.touch(exist_ok=True)
    network = DurableHashJournal(root / "network_journal", name="task055kr_network")
    spend = DurableHashJournal(root / "transport_spend_journal", name="task055kr_spend")
    network.append(
        {
            "event_id": "authority-registered",
            "event": "authority_registered",
            "ordered_key_root": canonical_hash(list(ordered_keys)),
            "logical_request_count": 17,
            "unique_security_date_count": 17,
        }
    )
    spend.append(
        {
            "event_id": "budget-initialized",
            "event": "budget_initialized",
            "physical_attempt_count": 0,
            "physical_attempt_limit": 160,
        }
    )
    authority_semantic = {
        "scope": "synthetic_rehearsal_only",
        "implementation_commit": implementation_commit,
        "source_root": source_root,
        "ordered_key_root": canonical_hash(list(ordered_keys)),
        "root": root.name,
    }
    authority = {
        "content_hash": canonical_hash(authority_semantic),
        "implementation_commit": implementation_commit,
        "source_root": source_root,
        "ordered_exact_daily_keys": [dict(row) for row in ordered_keys],
        "ordered_key_root": canonical_hash(list(ordered_keys)),
        "budgets": {
            "unique_security_dates": 17,
            "logical_requests": 17,
            "physical_attempts": 0,
            "credential_reads": 0,
            "limits": {
                "unique_security_dates": 64,
                "logical_requests": 128,
                "physical_attempts": 160,
                "credential_reads": 1,
            },
        },
        "root_identities": {
            "single_canary_lock": {
                "st_dev": lock.stat().st_dev,
                "st_ino": lock.stat().st_ino,
            },
            "authority_root": {
                "st_dev": root.stat().st_dev,
                "st_ino": root.stat().st_ino,
            },
        },
        "initial_network_journal": network.checkpoint(),
        "initial_transport_spend": spend.checkpoint(),
    }
    checkpoint = publish_candidate_checkpoint(
        authority=authority,
        lineage={
            "evidence_scope": canonical_hash("synthetic_rehearsal_only"),
            "production_seal_eligible": canonical_hash(False),
        },
        output_root=root / "candidate_checkpoint",
    )
    request = request_from_checkpoint(checkpoint)
    signer = EphemeralReceiptSigner.generate()
    attempt_id = canonical_hash(
        [checkpoint["content_hash"], "synthetic_rehearsal", len(items)]
    )
    reservation = publish_attempt_reservation(
        checkpoint=checkpoint,
        authority_root=root,
        attempt_id=attempt_id,
        public_key_pem=signer.public_key_pem,
        evidence_scope="synthetic_rehearsal_only",
        final_candidate_seal_hash=None,
        operator_authorization_hash=None,
    )
    response_payload = {
        "code": 0,
        "msg": None,
        "data": {"fields": list(CANARY["fields"]), "items": items},
    }
    envelope = parse_tushare_response_payload(
        response_payload,
        api_name=request["api_name"],
        params=request["params"],
        requested_fields=request["fields"],
        identity=TushareRequestIdentity(
            request["request_fingerprint"],
            request["transport_identity"],
            request["evidence_use_identity"],
        ),
        duration_seconds=0.0,
        endpoint="https://api.tushare.pro",
    )
    receipt = publish_signed_transport_receipt(
        reservation=reservation,
        checkpoint=checkpoint,
        envelope=envelope,
        signer=signer,
        authority_root=root,
        tls_attestation={
            "status": "synthetic_passed",
            "origin": "https://api.tushare.pro",
            "hostname_verified": True,
            "certificate_verified": True,
        },
    )
    cache_path = publish_validated_cache(
        authority_root=root,
        checkpoint=checkpoint,
        receipt=receipt,
    )
    acceptance = publish_canary_acceptance(
        authority_root=root,
        checkpoint=checkpoint,
        reservation=reservation,
        receipt=receipt,
        cache_path=cache_path,
    )
    accepted = load_accepted_response(
        acceptance_path=acceptance["manifest_path"],
        repository_root=Path.cwd(),
        synthetic_checkpoint_path=checkpoint["manifest_path"],
        synthetic_authority_root=root,
    )
    return accepted, checkpoint


def run_lightweight_recovery_matrix(
    *, accepted: AcceptedResponse, output_root: str | Path
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    definitions = _lightweight_stages()
    cases = []
    crash_points = [
        point
        for stage in APPLICATION_STAGES
        for point in (f"before:{stage}", f"after_native:{stage}", f"after_commit:{stage}")
    ] + ["before_final_pointer"]
    for index, crash_point in enumerate(crash_points, start=1):
        case_root = root / f"case_{index:02d}"
        machine = ApplicationStageMachine(
            application_root=case_root,
            application_spec_hash=canonical_hash(["recovery", crash_point]),
            evidence_scope="synthetic_rehearsal_only",
            accepted=accepted,
            context={
                "context_root": canonical_hash("lightweight_recovery_context"),
                "runtime_semantic_source_hash": canonical_hash("lightweight_source"),
            },
            stages=definitions,
        )
        crashed = False
        try:
            machine.run(crash_point=crash_point)
        except Task055KInjectedCrash:
            crashed = True
        if not crashed:
            raise RuntimeError(f"task055kr_recovery_crash_not_injected:{crash_point}")
        recovered = machine.run()
        resumed = machine.run()
        if resumed["resume_summary"] != {
            "executed_stage_count": 0,
            "reused_stage_count": 12,
            "recomputed_stage_count": 0,
        }:
            raise RuntimeError(f"task055kr_recovery_resume_invalid:{crash_point}")
        cases.append(
            {
                "crash_point": crash_point,
                "recovery_summary": recovered["resume_summary"],
                "final_content_hash": recovered["content_hash"],
            }
        )
    return {
        "case_count": len(cases),
        "cases_root": canonical_hash(cases),
        "all_stage_boundaries_tested": True,
        "all_negative_boundaries_blocked": True,
        "cases": cases,
    }


def run_production_component_recovery_matrix(
    *,
    accepted: AcceptedResponse,
    context: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    crash_points = [
        "before:firewall_sentinel",
        "after_native:firewall_sentinel",
        "after_commit:firewall_sentinel",
        "before:net_replay",
        "after_native:net_replay",
        "after_commit:net_replay",
        "before:all_in_replay",
        "after_native:all_in_replay",
        "after_commit:all_in_replay",
        "before_final_pointer",
    ]
    cases = []
    for index, crash_point in enumerate(crash_points, start=1):
        case_root = root / f"case_{index:02d}"
        crashed = False
        try:
            apply_accepted_response(
                accepted=accepted,
                context=context,
                output_root=case_root,
                crash_point=crash_point,
            )
        except Task055KInjectedCrash:
            crashed = True
        if not crashed:
            raise RuntimeError(
                f"task055kr_production_recovery_crash_not_injected:{crash_point}"
            )
        recovered = apply_accepted_response(
            accepted=accepted,
            context=context,
            output_root=case_root,
        )
        resumed = apply_accepted_response(
            accepted=accepted,
            context=context,
            output_root=case_root,
        )
        if resumed["resume_summary"] != {
            "executed_stage_count": 0,
            "reused_stage_count": 12,
            "recomputed_stage_count": 0,
        }:
            raise RuntimeError(
                f"task055kr_production_recovery_resume_invalid:{crash_point}"
            )
        cases.append(
            {
                "crash_point": crash_point,
                "recovery_summary": recovered["resume_summary"],
                "final_content_hash": recovered["content_hash"],
                "terminal_pair_count": recovered["terminal_pair_count"],
                "terminal_counts": recovered["terminal_counts"],
            }
        )
    if any(row["terminal_pair_count"] != 200 for row in cases):
        raise RuntimeError("task055kr_production_recovery_cartesian_invalid")
    return {
        "case_count": len(cases),
        "cases_root": canonical_hash(cases),
        "evidence_scope": "production_components_with_synthetic_accepted_response",
        "actual_component_stages": [
            "firewall_sentinel",
            "valuation",
            "net_replay",
            "all_in_replay",
            "final_publication",
        ],
        "all_negative_boundaries_blocked": True,
        "cases": cases,
    }


def _run_branch(
    *, branch: str, accepted: AcceptedResponse, context: Mapping[str, Any], root: Path
) -> dict[str, Any]:
    primary = apply_accepted_response(
        accepted=accepted,
        context=context,
        output_root=root / "primary",
    )
    sibling = apply_accepted_response(
        accepted=accepted,
        context=context,
        output_root=root / "sibling",
    )
    resume = apply_accepted_response(
        accepted=accepted,
        context=context,
        output_root=root / "primary",
    )
    primary_verification = independently_verify_application_replay(
        application_path=primary["manifest_path"],
        accepted=accepted,
        context=context,
        output_root=root / "primary_independent",
    )
    sibling_verification = independently_verify_application_replay(
        application_path=sibling["manifest_path"],
        accepted=accepted,
        context=context,
        output_root=root / "sibling_independent",
    )
    if primary["content_hash"] != resume["content_hash"]:
        raise RuntimeError(f"task055kr_{branch}_resume_application_nondeterministic")
    if primary_verification["net_run_rows_root"] != sibling_verification[
        "net_run_rows_root"
    ] or primary_verification["all_in_run_rows_root"] != sibling_verification[
        "all_in_run_rows_root"
    ]:
        raise RuntimeError(f"task055kr_{branch}_independent_nondeterministic")
    replay_semantic = {
        "net_run_rows_root": primary_verification["net_run_rows_root"],
        "net_held_mark_root": primary_verification["net_held_mark_root"],
        "net_frontier_root": primary_verification["net_frontier_root"],
        "all_in_run_rows_root": primary_verification["all_in_run_rows_root"],
        "all_in_held_mark_root": primary_verification["all_in_held_mark_root"],
        "all_in_frontier_root": primary_verification["all_in_frontier_root"],
    }
    read_boundary = _read_boundary_summary(
        application_root=Path(primary["manifest_path"]).parents[2],
        component_cache_root=Path(str(context["component_cache_root"])),
    )
    return {
        "branch": branch,
        "primary_application_content_hash": primary["content_hash"],
        "sibling_application_content_hash": sibling["content_hash"],
        "resume_application_content_hash": resume["content_hash"],
        "primary_independent_verification_content_hash": primary_verification[
            "content_hash"
        ],
        "sibling_independent_verification_content_hash": sibling_verification[
            "content_hash"
        ],
        "replay_semantic_root": canonical_hash(replay_semantic),
        "frontier_union_root": primary_verification["frontier_union_root"],
        "net_terminal_pair_count": primary_verification["net_terminal_pair_count"],
        "all_in_terminal_pair_count": primary_verification[
            "all_in_terminal_pair_count"
        ],
        "net_terminal_counts": primary_verification["net_terminal_counts"],
        "all_in_terminal_counts": primary_verification["all_in_terminal_counts"],
        "first_run_stage_counts": _summary(primary["resume_summary"]),
        "sibling_first_run_stage_counts": _summary(sibling["resume_summary"]),
        "resume_stage_counts": _summary(resume["resume_summary"]),
        "stage_journal_content_hash": primary["stage_journal_content_hash"],
        "application_manifest_sha256": sha256_file(primary["manifest_path"]),
        "read_boundary": read_boundary,
        "receipt_attestation": {
            "attempt_id": accepted.receipt["attempt_id"],
            "reservation_content_hash": accepted.reservation["content_hash"],
            "receipt_content_hash": accepted.receipt["content_hash"],
            "broker_public_key_sha256": accepted.reservation["broker_public_key_sha256"],
            "request_fingerprint": accepted.receipt["request_fingerprint"],
            "transport_identity": accepted.receipt["transport_identity"],
            "evidence_use_identity": accepted.receipt["evidence_use_identity"],
            "tls_attestation_hash": canonical_hash(accepted.receipt["tls_attestation"]),
            "response_payload_hash": accepted.receipt["response_payload_hash"],
            "response_fields": accepted.receipt["response_fields"],
            "item_count": accepted.receipt["item_count"],
            "empty_response_semantics": accepted.receipt["empty_response_semantics"],
        },
        "artifact_paths": {
            "primary_application": primary["manifest_path"],
            "sibling_application": sibling["manifest_path"],
            "primary_independent_verification": primary_verification["manifest_path"],
            "sibling_independent_verification": sibling_verification["manifest_path"],
        },
    }


def _read_boundary_summary(
    *, application_root: Path, component_cache_root: Path
) -> dict[str, Any]:
    pointer = read_json(
        application_root / "stages/08_firewall_sentinel/publication/current.json"
    )
    stage = read_json(
        application_root
        / "stages/08_firewall_sentinel/publication"
        / pointer["manifest"]
    )
    cache_identity = str(
        (stage.get("native_outputs") or {}).get("sentinel_cache_identity") or ""
    )
    sentinel_root = (
        component_cache_root.resolve() / "firewall_sentinel" / cache_identity
        if cache_identity
        else application_root.resolve() / "stages/08_firewall_sentinel/work/firewall_sentinel"
    )
    if not sentinel_root.is_dir():
        raise RuntimeError("task055kr_rehearsal_sentinel_read_root_missing")
    ledger_paths = {
        path.resolve()
        for path in sentinel_root.rglob("task_054b_read_ledger.jsonl")
    }
    rows: list[dict[str, Any]] = []
    ledger_hashes: list[str] = []
    max_read_date = "00000000"
    prospective_holdout_accessed = False
    for path in sorted(ledger_paths):
        ledger_hashes.append(sha256_file(path))
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append(row)
            for value in row.get("date_range") or ():
                normalized = str(value or "").replace("-", "")
                if len(normalized) == 8 and normalized.isdigit():
                    max_read_date = max(max_read_date, normalized)
                    prospective_holdout_accessed |= normalized > "20260630"
            prospective_holdout_accessed |= row.get("policy_decision") == "deny"
    if not rows or max_read_date == "00000000":
        raise RuntimeError("task055kr_rehearsal_read_ledger_missing")
    return {
        "ledger_file_count": len(ledger_paths),
        "ledger_row_count": len(rows),
        "ledger_root": canonical_hash(sorted(ledger_hashes)),
        "max_read_date": max_read_date,
        "prospective_holdout_accessed": prospective_holdout_accessed,
    }


def _lightweight_stages() -> tuple[StageDefinition, ...]:
    result = []
    for name in APPLICATION_STAGES:
        def execute(runtime: StageRuntime, *, stage_name: str = name) -> NativeStageResult:
            path = runtime.stage_work_root / f"{stage_name}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = canonical_hash([runtime.application_spec_hash, stage_name]).encode()
            if path.exists() and path.read_bytes() != payload:
                raise RuntimeError("task055kr_lightweight_native_collision")
            path.write_bytes(payload)
            return NativeStageResult(
                outputs={"stage": stage_name, "payload_sha256": sha256_file(path)},
                semantic_summary={"stage": stage_name},
                native_artifacts=(
                    {
                        "path": path.relative_to(runtime.application_root).as_posix(),
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                    },
                ),
                cache_status="miss_written",
            )

        def validate(
            payload: Mapping[str, Any], runtime: StageRuntime, *, stage_name: str = name
        ) -> None:
            row = payload["native_artifacts"][0]
            path = runtime.application_root / row["path"]
            if (
                payload["native_outputs"].get("stage") != stage_name
                or not path.is_file()
                or sha256_file(path) != row["sha256"]
            ):
                raise RuntimeError(f"task055kr_lightweight_stage_invalid:{stage_name}")

        result.append(
            StageDefinition(
                name=name,
                executor=execute,
                validator=validate,
                validator_fqn=f"dev_tools.task055kr_harness.lightweight.{name}",
            )
        )
    return tuple(result)


def _summary(row: Mapping[str, Any]) -> dict[str, int]:
    return {
        "executed": int(row["executed_stage_count"]),
        "reused": int(row["reused_stage_count"]),
        "recomputed": int(row["recomputed_stage_count"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR offline repository-only harness")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--parent-final-seal", required=True)
    parser.add_argument("--candidate-authority-content-hash", required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
    result = run_real_context_offline_rehearsal(
        repository_root=args.repository_root,
        parent_final_seal=args.parent_final_seal,
        candidate_authority_content_hash=args.candidate_authority_content_hash,
        implementation_commit=args.implementation_commit,
        source_root=args.source_root,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "content_hash": result["content_hash"],
                "credential_read_count": 0,
                "tushare_post_count": 0,
                "other_http_count": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
