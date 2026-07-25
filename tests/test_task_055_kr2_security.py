from __future__ import annotations

import hashlib
import json
import os
import copy
from pathlib import Path

import pytest

from task_055_h.io import canonical_hash
from task_055_j.ledger import DurableHashJournal
from task_055_k.contracts import CANARY
from task_055_k.lease import ReplacementSafeLease, Task055KLeaseError
from task_055_k.stage_machine import ApplicationStageMachine, StageDefinition
from task_055_k.verifier import (
    Task055KVerifierError,
    verify_candidate_semantics,
    verify_scrubbed_evidence,
)
from dev_tools.task055kr_harness import _lightweight_stages, synthetic_accepted_response
from task_055_k.authority import normalize_ordered_keys
from task_055_h.io import read_json


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


def test_legacy_verifier_cannot_self_authorize_without_external_anchor(tmp_path: Path) -> None:
    with pytest.raises(Task055KVerifierError, match="external_release_anchor_required"):
        verify_scrubbed_evidence(
            tmp_path / "candidate.json",
            repository_root=tmp_path,
        )


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
