from __future__ import annotations

import json
from pathlib import Path

import pytest

import task_055_g.run as task055g_run


def _patch_final_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task055g_run,
        "validate_access_plan",
        lambda path: {"manifest_path": str(path), "content_hash": "access"},
    )
    monkeypatch.setattr(
        task055g_run,
        "validate_access_ledger",
        lambda path, plan: {
            "manifest_path": str(path),
            "content_hash": "ledger",
            "prospective_holdout_accessed": False,
        },
    )
    monkeypatch.setattr(
        task055g_run,
        "validate_truth_v2",
        lambda path: {"manifest_path": str(path), "content_hash": "truth", "status": "published"},
    )
    monkeypatch.setattr(
        task055g_run,
        "validate_fee_schedule_v2",
        lambda path: {"manifest_path": str(path), "content_hash": "fee", "status": "passed"},
    )
    monkeypatch.setattr(
        task055g_run,
        "independent_verify_fee_schedule",
        lambda schedule: {"content_hash": "fee-independent", "status": "passed"},
    )
    monkeypatch.setattr(
        task055g_run,
        "verify_authoritative_operational_seal",
        lambda governed_root, path: {
            "manifest_path": str(path),
            "content_hash": "operational",
            "status": "passed",
            "state_counts": {
                "certification_queue": 0,
                "certified_pool": 0,
                "portfolio_campaign": 0,
                "production_candidate": 0,
                "optimizer_activation": 0,
                "paper_registry": 0,
                "live_registry": 0,
            },
        },
    )
    monkeypatch.setattr(
        task055g_run,
        "validate_fee_aware_causal_frontier",
        lambda path: {
            "manifest_path": str(path),
            "content_hash": "causal",
            "status": "published",
            "round_one_frontier_count": 2,
            "missing_key_root": "frontier",
        },
    )
    monkeypatch.setattr(
        task055g_run,
        "validate_semantic_verification",
        lambda path: {
            "manifest_path": str(path),
            "content_hash": "semantic",
            "status": "passed",
            "prospective_holdout_accessed": False,
        },
    )
    monkeypatch.setattr(
        task055g_run,
        "verify_state_read_only",
        lambda state_root: {
            "content_hash": "network",
            "status": "verified",
            "physical_attempt_count": 0,
        },
    )


def _artifact_paths(task_root: Path) -> dict[str, str]:
    paths = {}
    for key in task055g_run._FINAL_ARTIFACT_KEYS:
        if key == "network_state_root":
            path = task_root / "network_state"
            path.mkdir(parents=True)
        else:
            path = task_root / "artifacts" / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        paths[key] = str(path.relative_to(task_root))
    return paths


def _report_semantic(task_root: Path, *, waiting: bool) -> dict:
    artifacts = _artifact_paths(task_root)
    readiness = {
        "fee_schedule_ready": waiting,
        "operational_seal_ready": True,
        "fee_aware_frontier_ready": waiting,
        "ready_for_exact_daily_canary": waiting,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    return {
        "schema_version": task055g_run.FINAL_REPORT_SCHEMA,
        "status": (
            task055g_run.FINAL_WAITING_STATUS
            if waiting
            else task055g_run.FINAL_BLOCKED_STATUS
        ),
        "stage": (
            "fee_aware_round_one_exact_daily_frontier_sealed"
            if waiting
            else "offline_engineering_baseline_blocked"
        ),
        "network_accessed": False,
        "network_request_count": 0,
        "prospective_holdout_accessed": False,
        "max_read_date": "20260630",
        "access_plan": {"content_hash": "access"},
        "read_ledger": {"content_hash": "ledger"},
        "truth_v2": {"content_hash": "truth"},
        "fee_schedule_v2": {
            "content_hash": "fee",
            "independent_verification_content_hash": "fee-independent",
        },
        "operational_state": {"content_hash": "operational"},
        "causal_frontier": {
            "content_hash": "causal",
            "round_one_frontier_count": 2,
            "frontier_root": "frontier",
        },
        "network_plan": {"network_executed": False, "token_read": False},
        "network_state": {
            "content_hash": "network",
            "ledger": {"physical_attempt_count": 0},
        },
        "semantic_verification": {"content_hash": "semantic"},
        "readiness": readiness,
        "queues": {
            "certification": 0,
            "certified_pool": 0,
            "portfolio": 0,
            "production_candidate": 0,
            "optimizer": 0,
            "paper": 0,
            "live": 0,
        },
        "engineering_blockers": (
            [] if waiting else [{"stage": "fee_schedule_v2", "code": "fee_missing"}]
        ),
        "certification_blockers": [
            {"code": code} for code in task055g_run.CERTIFICATION_BLOCKERS
        ],
        "artifacts": artifacts,
    }


def test_final_verification_is_content_addressed_and_immutable(tmp_path: Path) -> None:
    verification = {
        "schema_version": task055g_run.FINAL_VERIFICATION_SCHEMA,
        "status": task055g_run.FINAL_VERIFICATION_WAITING_STATUS,
        "top_status": task055g_run.FINAL_WAITING_STATUS,
    }
    verification["content_hash"] = task055g_run.canonical_hash(verification)
    published = task055g_run._publish_final_verification(
        tmp_path / "final_verification",
        verification,
    )
    manifest = Path(published["manifest_path"])
    pointer = json.loads(
        (tmp_path / "final_verification" / "current.json").read_text(encoding="utf-8")
    )
    assert manifest.parent.name == published["generation_id"]
    assert pointer["manifest"] == str(
        manifest.relative_to(tmp_path / "final_verification")
    )
    assert not (tmp_path / "final_verification.json").exists()

    manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(task055g_run.Task055GError, match="immutable_generation_content_mismatch"):
        task055g_run._publish_final_verification(
            tmp_path / "final_verification",
            verification,
        )


def test_final_report_verifier_accepts_native_waiting_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_final_validators(monkeypatch)
    task_root = tmp_path / "task"
    semantic = _report_semantic(task_root, waiting=True)
    published = task055g_run._publish_final_report(task_root / "final", semantic)
    result = task055g_run.verify_task055g_final_report(
        published["manifest_path"],
        governed_root=tmp_path / "governed",
        task_root=task_root,
    )
    assert result["status"] == task055g_run.FINAL_VERIFICATION_WAITING_STATUS
    assert result["missing_artifacts"] == []
    assert result["network_physical_attempt_count"] == 0


def test_final_report_verifier_rejects_waiting_with_missing_or_absolute_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_final_validators(monkeypatch)
    task_root = tmp_path / "task"
    semantic = _report_semantic(task_root, waiting=True)
    semantic["artifacts"].pop("semantic_verification")
    published = task055g_run._publish_final_report(task_root / "final", semantic)
    with pytest.raises(task055g_run.Task055GError, match="waiting_artifacts_missing"):
        task055g_run.verify_task055g_final_report(
            published["manifest_path"],
            governed_root=tmp_path / "governed",
            task_root=task_root,
        )

    other_root = tmp_path / "task_absolute"
    semantic = _report_semantic(other_root, waiting=True)
    semantic["artifacts"]["access_plan"] = str(
        other_root / "artifacts" / "access_plan.json"
    )
    published = task055g_run._publish_final_report(other_root / "final", semantic)
    with pytest.raises(task055g_run.Task055GError, match="path_not_relative"):
        task055g_run.verify_task055g_final_report(
            published["manifest_path"],
            governed_root=tmp_path / "governed",
            task_root=other_root,
        )


def test_blocked_report_requires_justified_missing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_final_validators(monkeypatch)
    task_root = tmp_path / "task"
    semantic = _report_semantic(task_root, waiting=False)
    for key in (
        "fee_schedule_v2",
        "causal_frontier",
        "semantic_verification",
        "network_state_root",
    ):
        semantic["artifacts"].pop(key)
    published = task055g_run._publish_final_report(task_root / "final", semantic)
    result = task055g_run.verify_task055g_final_report(
        published["manifest_path"],
        governed_root=tmp_path / "governed",
        task_root=task_root,
    )
    assert result["status"] == task055g_run.FINAL_VERIFICATION_BLOCKED_STATUS
    assert set(result["missing_artifacts"]) == {
        "fee_schedule_v2",
        "causal_frontier",
        "semantic_verification",
        "network_state_root",
    }

    semantic = _report_semantic(tmp_path / "unjustified", waiting=False)
    semantic["artifacts"].pop("operational_seal")
    published = task055g_run._publish_final_report(
        tmp_path / "unjustified" / "final",
        semantic,
    )
    with pytest.raises(task055g_run.Task055GError, match="missing_artifact_unjustified"):
        task055g_run.verify_task055g_final_report(
            published["manifest_path"],
            governed_root=tmp_path / "governed",
            task_root=tmp_path / "unjustified",
        )


def test_run_module_does_not_load_tushare_credentials() -> None:
    source = Path(task055g_run.__file__).read_text(encoding="utf-8")
    assert "TUSHARE_TOKEN" not in source
    assert "load_credential_once" not in source
