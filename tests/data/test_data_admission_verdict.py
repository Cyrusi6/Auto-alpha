from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from auto_alpha.data.lake.store.admission import (
    AdmissionVerificationError,
    CoveragePopulation,
    DataAdmissionScope,
    SecurityLifecycle,
    compile_coverage_plan,
    first_data_admission_profile,
    validate_data_admission_verdict,
    verify_data_admission,
)
from auto_alpha.platform.artifacts.storage import canonical_hash
from auto_alpha.platform.artifacts.storage import sha256_file
from auto_alpha.platform.artifacts.schema.validator import validate_artifact
from auto_alpha.data.lake.store.run_source_freeze import main as freeze_cli_main
from tests.data.admission_evidence import (
    build_attempt_pair,
    controlled_acquisition_contract,
    write_coverage_evidence,
)


def test_producer_self_authorization_cannot_issue_an_admitted_verdict(
    tmp_path: Path,
) -> None:
    profile = first_data_admission_profile()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile, sort_keys=True), encoding="utf-8")

    source_semantic = {
        "schema_version": "canonical_ashare_research_freeze_v1",
        "source_catalog_hash": "a" * 64,
        "alpha_search_authorized": True,
        "strict_derived_bundle": {
            "frozen_artifacts": [
                {"role": "target_availability", "sha256": "b" * 64}
            ]
        },
    }
    source_hash = canonical_hash(source_semantic)
    source = source_semantic | {
        "content_hash": source_hash,
        "generation_id": f"source_freeze_{source_hash[:24]}",
    }
    source_path = tmp_path / "source" / "source_freeze_manifest.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(json.dumps(source, sort_keys=True), encoding="utf-8")

    first = verify_data_admission(
        profile_path,
        source_path,
        DataAdmissionScope(
            access_view="research",
            date_start="20190102",
            date_end="20190102",
            as_of_market_date="20190102",
        ),
        tmp_path / "verdicts",
    )
    second = verify_data_admission(
        profile_path,
        source_path,
        first.scope,
        tmp_path / "verdicts",
    )

    assert first.outcome == "blocked"
    assert first.admitted is False
    assert first.verdict_id == second.verdict_id
    assert first.content_hash == second.content_hash
    assert {row.code for row in first.blockers} >= {
        "coverage_population_evidence_missing",
        "coverage_receipts_missing",
        "target_values_missing",
        "deterministic_freeze_replay_evidence_missing",
    }
    manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))
    assert manifest["outcome"] == "blocked"
    assert manifest["source_generation_id"] == source["generation_id"]
    assert manifest["producer_claims_ignored"] == ["alpha_search_authorized"]
    assert validate_artifact(first.manifest_path, strict=True).valid is True

    with pytest.raises(AdmissionVerificationError, match="data_admission_verdict_blocked"):
        validate_data_admission_verdict(first.manifest_path, require_admitted=True)
    assert freeze_cli_main(
        [
            "verify-admission",
            "--profile-manifest",
            str(profile_path),
            "--source-generation-manifest",
            str(source_path),
            "--access-view",
            "research",
            "--date-start",
            "20190102",
            "--date-end",
            "20190102",
            "--as-of-market-date",
            "20190102",
            "--verdict-root",
            str(tmp_path / "cli_verdicts"),
        ]
    ) == 2


def test_verdict_content_tampering_is_rejected(tmp_path: Path) -> None:
    profile = first_data_admission_profile()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile, sort_keys=True), encoding="utf-8")
    source_hash = canonical_hash({"schema_version": "ashare_source_freeze_generation_v1"})
    source = {
        "schema_version": "ashare_source_freeze_generation_v1",
        "content_hash": source_hash,
        "generation_id": f"ashare_source_freeze_{source_hash[:24]}",
    }
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source, sort_keys=True), encoding="utf-8")
    verdict = verify_data_admission(
        profile_path,
        source_path,
        DataAdmissionScope("research", "20190102", "20190102", "20190102"),
        tmp_path / "verdicts",
    )
    manifest_path = Path(verdict.manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outcome"] = "admitted"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    with pytest.raises(AdmissionVerificationError, match="data_admission_verdict_invalid"):
        validate_data_admission_verdict(manifest_path)


def test_controlled_evidence_stops_at_unresolved_source_and_bundle_contracts(
    tmp_path: Path,
) -> None:
    profile = _active_first_profile()
    profile_path = _write_json(tmp_path / "profile.json", profile)
    scope = DataAdmissionScope("research", "20190102", "20190102", "20190102")
    population = CoveragePopulation(
        securities=(SecurityLifecycle("000001.SZ", "20100101"),),
        trading_dates=("20190102",),
        exchanges=("SSE", "SZSE"),
        index_codes=("000300.SH",),
    )
    source_root = tmp_path / "source"
    population_semantic = {
        "schema_version": "data_coverage_population_v1",
        **population.to_dict(),
    }
    population_payload = population_semantic | {
        "content_hash": canonical_hash(population_semantic)
    }
    population_path = _write_json(source_root / "population.json", population_payload)
    plan = compile_coverage_plan(profile, scope, population)
    coverage_root = _write_complete_coverage(source_root / "coverage", plan)
    active_datasets = {row.dataset for row in plan.dataset_contracts}

    source_artifact_root = canonical_hash({"controlled_source": "v1"})
    data_scope_path = _write_data_scope_evidence(
        source_root,
        profile_id=str(profile["profile_id"]),
        active_datasets=active_datasets,
        scope=scope,
        coverage_root=coverage_root,
        source_artifact_root=source_artifact_root,
    )
    admission_evidence = {
        "lifecycle_population_relative_path": "population.json",
        "lifecycle_population_sha256": sha256_file(population_path),
        "coverage_evidence_relative_path": "coverage",
        "coverage_evidence_manifest_sha256": sha256_file(
            source_root / "coverage" / "coverage_evidence_manifest.json"
        ),
        "data_scope_evidence_relative_path": "data_scope_evidence.json",
        "data_scope_evidence_sha256": sha256_file(data_scope_path),
    }
    preliminary_source_path = _write_source_generation(
        source_root / "source_without_replay.json",
        source_artifact_root=source_artifact_root,
        admission_evidence=admission_evidence,
        active_datasets=active_datasets,
    )
    preliminary = verify_data_admission(
        profile_path,
        preliminary_source_path,
        scope,
        tmp_path / "preliminary_verdicts",
    )
    assert {row.code for row in preliminary.blockers} == {
        "canonical_bundle_contract_unresolved",
        "data_admission_profile_human_approval_required",
        "deterministic_freeze_replay_evidence_missing",
        "source_generation_structural_validation_failed",
    }

    changed_source_root = tmp_path / "source_with_inactive_change"
    shutil.copytree(source_root, changed_source_root)
    changed_global_root = canonical_hash({"controlled_source": "inactive-change"})
    changed_scope_path = _write_data_scope_evidence(
        changed_source_root,
        profile_id=str(profile["profile_id"]),
        active_datasets=active_datasets,
        scope=scope,
        coverage_root=coverage_root,
        source_artifact_root=changed_global_root,
    )
    changed_evidence = {
        **admission_evidence,
        "data_scope_evidence_sha256": sha256_file(changed_scope_path),
    }
    changed_source_path = _write_source_generation(
        changed_source_root / "source_inactive_change.json",
        source_artifact_root=changed_global_root,
        admission_evidence=changed_evidence,
        active_datasets=active_datasets,
    )
    changed = verify_data_admission(
        profile_path,
        changed_source_path,
        scope,
        tmp_path / "inactive_change_verdicts",
    )
    assert changed.data_scope_root == preliminary.data_scope_root
    assert preliminary.data_scope_root is not None

    replay_semantic = {
        "schema_version": "deterministic_freeze_replay_evidence_v1",
        "source_artifact_root": source_artifact_root,
        "profile_id": profile["profile_id"],
        "scope": scope.to_dict(),
        "rebuilds": [
            {
                "worker_count": workers,
                "output_identity": canonical_hash({"output": ordinal}),
                "data_scope_root": preliminary.data_scope_root,
                "artifact_byte_root": canonical_hash({"artifacts": "identical"}),
            }
            for ordinal, workers in enumerate((1, 4), start=1)
        ],
        "verifier_execution_identity": canonical_hash({"replay_verifier": "v1"}),
    }
    replay_path = _write_json(
        source_root / "replay.json",
        replay_semantic | {"content_hash": canonical_hash(replay_semantic)},
    )
    final_evidence = admission_evidence | {
        "deterministic_replay_relative_path": "replay.json",
        "deterministic_replay_evidence_sha256": sha256_file(replay_path),
    }
    source_path = _write_source_generation(
        source_root / "source.json",
        source_artifact_root=source_artifact_root,
        admission_evidence=final_evidence,
        active_datasets=active_datasets,
    )

    verdict = verify_data_admission(
        profile_path,
        source_path,
        scope,
        tmp_path / "verdicts",
    )

    assert verdict.outcome == "blocked"
    assert {row.code for row in verdict.blockers} == {
        "canonical_bundle_contract_unresolved",
        "data_admission_profile_human_approval_required",
        "source_generation_structural_validation_failed",
    }
    assert verdict.coverage_root == coverage_root
    assert verdict.data_scope_root == preliminary.data_scope_root
    with pytest.raises(AdmissionVerificationError, match="data_admission_verdict_blocked"):
        validate_data_admission_verdict(
            verdict.manifest_path,
            expected_source_generation_id=verdict.source_generation_id,
            expected_scope=scope,
            require_admitted=True,
        )


def _active_first_profile() -> dict[str, object]:
    profile = dict(first_data_admission_profile())
    profile.pop("profile_id")
    profile.pop("content_hash")
    profile["activation_status"] = "active"
    for row in profile["datasets"]:
        if row["role"] != "inactive":
            row["acquisition_contracts"] = [
                controlled_acquisition_contract(str(row["dataset"]))
            ]
    content_hash = canonical_hash(profile)
    return profile | {
        "profile_id": f"dap_{content_hash[:24]}",
        "content_hash": content_hash,
    }


def _write_complete_coverage(root: Path, plan) -> str:
    contracts = {row.dataset: row for row in plan.dataset_contracts}
    events: list[dict[str, object]] = []
    previous_hash = ""
    for attempt_ordinal, obligation in enumerate(plan.obligations, start=1):
        empty_allowed = (
            contracts[obligation.dataset].empty_policy == "observed_empty_allowed"
            and obligation.dataset != "securities"
        )
        start, receipt = build_attempt_pair(
            root,
            obligation,
            contract=contracts[obligation.dataset],
            population=plan.population,
            attempt_ordinal=attempt_ordinal,
            sequence_start=2 * attempt_ordinal - 1,
            previous_event_hash=previous_hash,
            empty=empty_allowed,
        )
        events.extend((start, receipt))
        previous_hash = receipt["event_hash"]
    write_coverage_evidence(root, plan=plan, events=events)
    from auto_alpha.data.lake.store.admission import verify_coverage

    verification = verify_coverage(plan, root)
    assert verification.outcome == "admitted"
    return verification.coverage_root


def _write_data_scope_evidence(
    root: Path,
    *,
    profile_id: str,
    active_datasets: set[str],
    scope: DataAdmissionScope,
    coverage_root: str,
    source_artifact_root: str,
) -> Path:
    active_sources = [
        {"dataset": dataset, "content_root": content_root}
        for dataset, content_root in _controlled_source_dataset_roots(
            active_datasets
        ).items()
    ]
    active_source_root = canonical_hash(active_sources)
    active_parent_roots = [row["content_root"] for row in active_sources]
    roles = (
        "stock_axis",
        "date_axis",
        "feature_axis",
        "feature_values",
        "feature_validity",
        "target_values",
        "target_availability",
        "target_contract",
        "pit_universe_membership",
        "source_to_derived_lineage",
        "pit_audit",
        "quality_report",
        "reconciliation_report",
    )
    artifacts = []
    for role in roles:
        artifact_path = _write_json(root / "artifacts" / f"{role}.json", {"role": role})
        artifacts.append(
            {
                "role": role,
                "relative_path": artifact_path.relative_to(root).as_posix(),
                "sha256": sha256_file(artifact_path),
                "size_bytes": artifact_path.stat().st_size,
                "parent_roots": active_parent_roots,
            }
        )
    semantic = {
        "schema_version": "data_scope_evidence_v1",
        "profile_id": profile_id,
        "source_artifact_root": source_artifact_root,
        "active_source_artifacts": active_sources,
        "active_source_root": active_source_root,
        "scope": scope.to_dict(),
        "coverage_root": coverage_root,
        "artifacts": artifacts,
        "transform_identities": {
            key: canonical_hash({"transform": key})
            for key in (
                "provider_adapter",
                "normalization",
                "pit_transform",
                "target_formula",
                "producer_code",
                "toolchain",
            )
        },
        "metrics": {
            key: 0
            for key in (
                "unexplained_unknown",
                "conflicting_primary_key",
                "unexplained_duplicate",
                "parse_error",
                "pit_availability_gap",
                "coverage_gap",
                "lineage_gap",
                "unexplained_target_unknown",
            )
        },
        "validity_breadth": {
            "human_approved": True,
            "minimum_valid_security_count": 1,
            "minimum_pit_universe_fraction": 1.0,
            "approval_identity": canonical_hash({"approval": "controlled"}),
        },
    }
    return _write_json(
        root / "data_scope_evidence.json",
        semantic | {"content_hash": canonical_hash(semantic)},
    )


def _write_source_generation(
    path: Path,
    *,
    source_artifact_root: str,
    admission_evidence: dict[str, object],
    active_datasets: set[str],
) -> Path:
    partitions = _controlled_partition_rows(active_datasets)
    semantic = {
        "schema_version": "ashare_source_freeze_generation_v1",
        "source_artifact_root": source_artifact_root,
        "admission_evidence": admission_evidence,
        "admission_evidence_root": canonical_hash(admission_evidence),
        "partitions": partitions,
    }
    content_hash = canonical_hash(semantic)
    return _write_json(
        path,
        semantic
        | {
            "content_hash": content_hash,
            "generation_id": f"ashare_source_freeze_{content_hash[:24]}",
        },
    )


def _controlled_partition_rows(active_datasets: set[str]) -> list[dict[str, object]]:
    return [
        {
            "dataset": dataset,
            "relative_path": f"partitions/{dataset}.parquet",
            "sha256": canonical_hash({"partition": dataset}),
            "record_count": 1,
        }
        for dataset in sorted(active_datasets)
    ]


def _controlled_source_dataset_roots(active_datasets: set[str]) -> dict[str, str]:
    return {
        str(row["dataset"]): canonical_hash(
            [
                {
                    "path": row["relative_path"],
                    "sha256": row["sha256"],
                    "records": row["record_count"],
                }
            ]
        )
        for row in _controlled_partition_rows(active_datasets)
    }


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path
