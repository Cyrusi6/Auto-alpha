from __future__ import annotations

import hashlib
import json
import os
import copy
from pathlib import Path

import pytest

from auto_alpha.platform.network_authority._internal.authorization.io import canonical_hash
from auto_alpha.platform.network_authority._internal.runtime.ledger import DurableHashJournal
from auto_alpha.platform.network_authority.contracts import CANARY
from auto_alpha.platform.network_authority.lease import ReplacementSafeLease, Task055KLeaseError
from auto_alpha.platform.network_authority.stage_machine import ApplicationStageMachine, StageDefinition
from auto_alpha.platform.network_authority.verifier import (
    Task055KVerifierError,
    _verify_artifact_closure,
    verify_candidate_semantics,
    verify_scrubbed_evidence,
)
from auto_alpha.platform.network_authority.release import _load_rehearsal_release_catalog
from auto_alpha.platform.network_authority.run import _publish_content_addressed_evidence
from dev_tools.network_authority_harness import _lightweight_stages, synthetic_accepted_response
from auto_alpha.platform.network_authority.authority import normalize_ordered_keys
from auto_alpha.platform.network_authority._internal.authorization.io import read_json


def _ordered_keys() -> list[dict]:
    return normalize_ordered_keys(
        read_json("evidence/task_055_j/task055j_scrubbed_evidence.json")[
            "ordered_exact_daily_keys"
        ]
    )


def _accepted(tmp_path: Path):
    return synthetic_accepted_response(
        authority_root=tmp_path / "authority",
        ordered_keys=_ordered_keys(),
        implementation_commit="c" * 40,
        source_root="d" * 64,
        items=[],
    )[0]


def test_atomic_same_appearance_replacement_loses_active_lease(tmp_path: Path) -> None:
    parent = tmp_path / "authority"
    parent.mkdir()
    lease = ReplacementSafeLease.acquire(
        parent=parent,
        lock_name="single.lock",
        scope="scope",
        root_binding="a" * 64,
        attempt="attempt",
    )
    replacement = parent / "replacement"
    replacement.write_bytes(lease.expected_bytes)
    replacement.chmod(0o600)
    os.setxattr(replacement, "user.task055kr2_lease_instance", lease.instance_secret)
    os.replace(replacement, parent / "single.lock")
    with pytest.raises(Task055KLeaseError, match="lease_lost"):
        lease.checkpoint("after_atomic_replace")
    lease.abandon()


def test_same_inode_truncate_rewrite_loses_lease(tmp_path: Path) -> None:
    parent = tmp_path / "authority"
    parent.mkdir()
    lease = ReplacementSafeLease.acquire(
        parent=parent,
        lock_name="single.lock",
        scope="scope",
        root_binding="a" * 64,
        attempt="attempt",
    )
    (parent / "single.lock").write_text("forged", encoding="utf-8")
    with pytest.raises(Task055KLeaseError, match="held_content"):
        lease.checkpoint("after_same_inode_rewrite")
    lease.abandon()


def test_hardlink_and_parent_replacement_are_detected(tmp_path: Path) -> None:
    parent = tmp_path / "authority"
    parent.mkdir()
    lease = ReplacementSafeLease.acquire(
        parent=parent,
        lock_name="single.lock",
        scope="scope",
        root_binding="a" * 64,
        attempt="attempt",
    )
    os.link(parent / "single.lock", parent / "hardlink")
    with pytest.raises(Task055KLeaseError, match="link_count"):
        lease.checkpoint("after_hardlink")
    lease.abandon()

    other = tmp_path / "parent-replace"
    other.mkdir()
    second = ReplacementSafeLease.acquire(
        parent=other,
        lock_name="single.lock",
        scope="scope",
        root_binding="b" * 64,
        attempt="attempt",
    )
    moved = tmp_path / "parent-old"
    other.rename(moved)
    other.mkdir()
    with pytest.raises(Task055KLeaseError, match="canonical_parent_replaced"):
        second.checkpoint("after_parent_replace")
    second.abandon()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_unsafe_preexisting_lease_path_fails_closed(tmp_path: Path, kind: str) -> None:
    parent = tmp_path / kind
    parent.mkdir()
    lock = parent / "single.lock"
    if kind == "symlink":
        target = parent / "target"
        target.write_text("", encoding="utf-8")
        lock.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(lock)
    else:
        lock.mkdir()
    with pytest.raises(Task055KLeaseError):
        ReplacementSafeLease.acquire(
            parent=parent,
            lock_name="single.lock",
            scope="scope",
            root_binding="a" * 64,
            attempt="attempt",
            allow_legacy_empty_bootstrap=True,
        )


def test_fence_advances_and_released_owner_cannot_resurrect(tmp_path: Path) -> None:
    parent = tmp_path / "authority"
    parent.mkdir()
    first = ReplacementSafeLease.acquire(
        parent=parent,
        lock_name="single.lock",
        scope="scope",
        root_binding="a" * 64,
        attempt="attempt",
    )
    first_fence = first.binding()["fence"]
    first.release()
    second = ReplacementSafeLease.acquire(
        parent=parent,
        lock_name="single.lock",
        scope="scope",
        root_binding="a" * 64,
        attempt="attempt",
    )
    assert second.binding()["fence"] == first_fence + 1
    with pytest.raises(Task055KLeaseError, match="lease_closed"):
        first.checkpoint("stale_owner_resurrection")
    second.release()


@pytest.mark.parametrize("target_ordinal", range(1, 13))
def test_each_stage_rejects_lock_loss_before_commit(
    tmp_path: Path, target_ordinal: int
) -> None:
    accepted = _accepted(tmp_path)
    stages = list(_lightweight_stages())
    original = stages[target_ordinal - 1]

    def replace_during_component(runtime):
        result = original.executor(runtime)
        lock = runtime.application_root / "application.lock"
        lock.unlink()
        lock.write_text("replacement", encoding="utf-8")
        return result

    stages[target_ordinal - 1] = StageDefinition(
        name=original.name,
        executor=replace_during_component,
        validator=original.validator,
        validator_fqn=original.validator_fqn,
    )
    root = tmp_path / "application"
    machine = ApplicationStageMachine(
        application_root=root,
        application_spec_hash=canonical_hash(["kr2", target_ordinal]),
        evidence_scope="synthetic_rehearsal_only",
        accepted=accepted,
        context={
            "context_root": canonical_hash("context"),
            "runtime_semantic_source_hash": canonical_hash("source"),
        },
        stages=stages,
    )
    with pytest.raises(Exception, match="lease_lost"):
        machine.run()
    commits = [
        row
        for row in DurableHashJournal(
            root / "stage_journal", name="task055kr_application"
        ).rows()
        if row.get("event") == "stage_committed"
    ]
    assert len(commits) == target_ordinal - 1
    assert not (root / "current.json").exists()


def _candidate_and_anchor() -> tuple[dict, dict]:
    keys = []
    for ordinal in range(1, 18):
        if ordinal == 1:
            keys.append({"ordinal": 1, **dict(CANARY)})
        else:
            keys.append(
                {
                    "ordinal": ordinal,
                    "api_name": "daily",
                    "ts_code": f"{ordinal:06d}.SZ",
                    "trade_date": "20160104",
                    "fields": list(CANARY["fields"]),
                    "request_fingerprint": hashlib.sha256(f"r{ordinal}".encode()).hexdigest(),
                    "transport_identity": hashlib.sha256(f"t{ordinal}".encode()).hexdigest(),
                    "evidence_use_identity": hashlib.sha256(f"e{ordinal}".encode()).hexdigest(),
                }
            )
    catalog = [
        {
            "role": "candidate_authority",
            "relative_path": "governance/authority.json",
            "sha256": "1" * 64,
            "content_hash": "2" * 64,
        }
    ]
    expected = {
        "status": "task055kr2_candidate_ready_for_independent_audit_no_network_executed",
        "implementation_commit": "3" * 40,
        "baseline_commit": "df24308eadab07128b9efead884355247e58a382",
        "ordered_exact_daily_keys": keys,
        "ordered_key_root": _hash(keys),
        "canary": dict(CANARY),
        "budgets": {"limits": {"physical_attempts": 160}},
        "root_bindings": {"task_root": "validation_runs/task"},
        "source_entries": [
            {
                "path": "src/auto_alpha/platform/network_authority/verifier.py",
                "git_blob_id": "1" * 40,
                "git_index_mode": "100644",
                "sha256": "3" * 64,
                "size_bytes": 1,
            }
        ],
        "source_root": "4" * 64,
        "artifact_catalog": catalog,
        "artifact_catalog_root": _hash(catalog),
        "lineage": {"candidate_authority": "2" * 64},
        "cross_lineage": {"checkpoint": {"candidate_authority": "2" * 64}},
        "application_stage_order": [f"stage-{index}" for index in range(12)],
        "application_role_roots": {"stage-0": "4" * 64},
        "synthetic_receipt_attestations": {"positive": {}, "empty": {}},
        "broker_contract_hash": "5" * 64,
        "network_authorized": False,
        "executable": False,
        "authorization_eligible": False,
        "operational_state_unproven": True,
        "contains_credentials": False,
        "contains_market_values": False,
        "contains_absolute_paths": False,
        "prospective_holdout_accessed": False,
        "production_execution_ancestor": False,
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    candidate = copy.deepcopy(expected)
    candidate["content_hash"] = _hash(candidate)
    anchor_semantic = {
        "schema_version": "task055kr2_external_release_candidate_anchor_v1",
        "status": expected["status"],
        "semantic_expectations": expected,
        "candidate_self_check_is_independent_review": False,
        "network_authorized": False,
        "executable": False,
        "authorization_eligible": False,
    }
    anchor = anchor_semantic | {"content_hash": _hash(anchor_semantic)}
    return candidate, anchor


def test_fixed_anchor_semantic_positive_and_fully_rehashed_mutation() -> None:
    candidate, anchor = _candidate_and_anchor()
    assert verify_candidate_semantics(anchor=anchor, candidate=candidate)["status"] == "passed"
    candidate["implementation_commit"] = "6" * 40
    candidate["lineage"]["candidate_authority"] = "7" * 64
    candidate["artifact_catalog"][0]["content_hash"] = "7" * 64
    candidate["artifact_catalog_root"] = _hash(candidate["artifact_catalog"])
    candidate["content_hash"] = _hash(
        {key: value for key, value in candidate.items() if key != "content_hash"}
    )
    with pytest.raises(Task055KVerifierError, match="semantic_mismatch:implementation_commit"):
        verify_candidate_semantics(anchor=anchor, candidate=candidate)


def test_fixed_anchor_rejects_rehashed_candidate_source_root() -> None:
    candidate, anchor = _candidate_and_anchor()
    candidate["source_root"] = "6" * 64
    candidate["content_hash"] = _hash(
        {key: value for key, value in candidate.items() if key != "content_hash"}
    )
    with pytest.raises(Task055KVerifierError, match="semantic_mismatch:source_root"):
        verify_candidate_semantics(anchor=anchor, candidate=candidate)


def test_legacy_verifier_cannot_self_authorize_without_external_anchor(tmp_path: Path) -> None:
    with pytest.raises(Task055KVerifierError, match="external_release_anchor_required"):
        verify_scrubbed_evidence(
            tmp_path / "candidate.json",
            repository_root=tmp_path,
        )


def test_nested_rehearsal_catalog_resolves_from_rehearsal_generation_root(
    tmp_path: Path,
) -> None:
    governed = tmp_path / "governed"
    rehearsal_root = governed / "validation_runs/task/native_rehearsal"
    artifact = rehearsal_root / "artifacts/child.json"
    artifact.parent.mkdir(parents=True)
    child_semantic = {"schema_version": "fixture_v1", "status": "passed"}
    child = child_semantic | {"content_hash": _hash(child_semantic)}
    artifact.write_text(json.dumps(child), encoding="utf-8")
    rehearsal_path = (
        rehearsal_root
        / "report/generations/task055kr_native_rehearsal_fixture/rehearsal_manifest.json"
    )
    rehearsal_path.parent.mkdir(parents=True)
    rehearsal_path.write_text(
        json.dumps(
            {
                "artifact_catalog": [
                    {
                        "role": "child",
                        "relative_path": "artifacts/child.json",
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        "content_hash": child["content_hash"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    relative = rehearsal_path.relative_to(governed).as_posix()
    rehearsal, catalog, resolved_root = _load_rehearsal_release_catalog(
        governed=governed,
        top_catalog=[{"role": "native_rehearsal", "relative_path": relative}],
    )
    assert rehearsal["artifact_catalog"] == catalog
    assert resolved_root == rehearsal_root


def test_content_addressed_evidence_preserves_legacy_flat_file(tmp_path: Path) -> None:
    root = tmp_path / "scrubbed_evidence"
    root.mkdir()
    legacy = root / "task055kr2_candidate_evidence.json"
    legacy.write_text('{"historical":true}\n', encoding="utf-8")
    first = {"status": "first", "content_hash": "a" * 64}
    second = {"status": "second", "content_hash": "b" * 64}

    first_path = _publish_content_addressed_evidence(
        root=root,
        evidence_name=legacy.name,
        payload=first,
    )
    repeated_path = _publish_content_addressed_evidence(
        root=root,
        evidence_name=legacy.name,
        payload=first,
    )
    second_path = _publish_content_addressed_evidence(
        root=root,
        evidence_name=legacy.name,
        payload=second,
    )

    assert first_path == repeated_path
    assert first_path != second_path
    assert legacy.read_text(encoding="utf-8") == '{"historical":true}\n'
    assert json.loads(first_path.read_text(encoding="utf-8")) == first
    assert json.loads(second_path.read_text(encoding="utf-8")) == second


def test_verifier_resolves_nested_rehearsal_catalog_from_generation_root(
    tmp_path: Path,
) -> None:
    governed = tmp_path / "governed"
    rehearsal_root = governed / "validation_runs/task/native_rehearsal"
    stage_order = [f"stage-{ordinal}" for ordinal in range(1, 13)]
    application_roots = {}
    application_stages = {}
    nested_catalog = []
    for branch in ("positive", "empty"):
        for replica in ("primary", "sibling"):
            role = f"{branch}_{replica}_application"
            stages = [
                {"ordinal": ordinal, "stage": stage}
                for ordinal, stage in enumerate(stage_order, start=1)
            ]
            semantic = {"schema_version": "fixture_v1", "stages": stages}
            payload = semantic | {"content_hash": _hash(semantic)}
            path = rehearsal_root / f"applications/{role}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            nested_catalog.append(
                {
                    "role": role,
                    "relative_path": path.relative_to(rehearsal_root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "content_hash": payload["content_hash"],
                }
            )
            application_roots[role] = payload["content_hash"]
            application_stages[role] = stages
    nested_catalog.sort(key=lambda row: row["role"])
    rehearsal_semantic = {
        "schema_version": "fixture_v1",
        "artifact_catalog": nested_catalog,
    }
    rehearsal = rehearsal_semantic | {"content_hash": _hash(rehearsal_semantic)}
    rehearsal_path = (
        rehearsal_root
        / "report/generations/task055kr_native_rehearsal_fixture/rehearsal_manifest.json"
    )
    rehearsal_path.parent.mkdir(parents=True)
    rehearsal_path.write_text(json.dumps(rehearsal), encoding="utf-8")
    top_catalog = [
        {
            "role": "native_rehearsal",
            "relative_path": rehearsal_path.relative_to(governed).as_posix(),
            "sha256": hashlib.sha256(rehearsal_path.read_bytes()).hexdigest(),
            "content_hash": rehearsal["content_hash"],
        }
    ]
    anchor = {
        "top_level_artifact_catalog": top_catalog,
        "top_level_artifact_role_count": 1,
        "rehearsal_artifact_catalog": nested_catalog,
        "rehearsal_artifact_role_count": 4,
        "application_roots": application_roots,
        "application_stage_roots": application_stages,
        "semantic_expectations": {"application_stage_order": stage_order},
    }

    assert _verify_artifact_closure(anchor=anchor, governed=governed) == rehearsal_root


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
