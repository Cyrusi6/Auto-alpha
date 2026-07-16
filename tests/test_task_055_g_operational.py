from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_055_g.operational import (
    OPERATIONAL_STATES,
    OperationalSealError,
    build_authoritative_writer_registry,
    publish_authoritative_operational_seal,
    scan_authoritative_operational_state,
    verify_authoritative_operational_seal,
)


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_genesis_publishes_authoritative_empty_seal_and_verifier_rescans(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    output = tmp_path / "seal"

    result = publish_authoritative_operational_seal(authority, output, initialize_genesis=True)

    assert result["status"] == "passed"
    assert result["state_counts"] == {name: 0 for name in OPERATIONAL_STATES}
    verified = verify_authoritative_operational_seal(authority, output)
    assert verified["content_hash"] == result["content_hash"]


def test_shadow_operational_root_cannot_hide_authoritative_pending_queue(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    (authority / "operational_state" / "certification_queue").mkdir(parents=True)
    publish_authoritative_operational_seal(authority, tmp_path / "initial", initialize_genesis=True)
    _jsonl(
        authority / "artifacts" / "validation_campaign_store" / "factor_certification_queue.jsonl",
        [{"queue_id": "q1", "validation_candidate_id": "vc1", "factor_id": "f1", "priority": 1, "status": "pending"}],
    )

    scan = scan_authoritative_operational_state(authority)

    assert scan["status"] == "blocked"
    assert scan["state_counts"]["certification_queue"] == 1


def test_physical_pending_record_wins_over_self_reported_zero(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    publish_authoritative_operational_seal(authority, tmp_path / "initial", initialize_genesis=True)
    portfolio = authority / "artifacts" / "portfolio_campaign"
    (portfolio / "portfolio_certification_campaign_registry.json").write_text(
        json.dumps(
            {
                "status": "registered",
                "campaign_count": 0,
                "item_count": 0,
                "production_candidate_bundle_count": 0,
                "optimizer_policy_activation_queue_count": 0,
                "record_count": 0,
            }
        ),
        encoding="utf-8",
    )
    _jsonl(
        portfolio / "optimizer_policy_activation_queue.jsonl",
        [{"activation_queue_id": "a1", "factor_id": "f1", "status": "pending"}],
    )

    scan = scan_authoritative_operational_state(authority)

    assert scan["state_counts"]["optimizer_activation"] == 1
    assert scan["total_operational_record_count"] == 1


def test_unknown_nonempty_format_fails_closed(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    publish_authoritative_operational_seal(authority, tmp_path / "initial", initialize_genesis=True)
    unknown = authority / "artifacts" / "model_registry" / "registry.sqlite"
    unknown.write_bytes(b"not-an-approved-operational-format")

    with pytest.raises(OperationalSealError, match="unknown_operational_format_or_schema"):
        scan_authoritative_operational_state(authority)


def test_nested_symlink_fails_closed(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    publish_authoritative_operational_seal(authority, tmp_path / "initial", initialize_genesis=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    nested = authority / "artifacts" / "account" / "nested"
    nested.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OperationalSealError, match="nested_operational_symlink"):
        scan_authoritative_operational_state(authority)


def test_independent_verifier_detects_post_seal_drift(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    output = tmp_path / "seal"
    publish_authoritative_operational_seal(authority, output, initialize_genesis=True)
    _jsonl(
        authority / "artifacts" / "factor_certification_campaign" / "certified_factor_pool.jsonl",
        [{"certified_factor_pool_id": "p1", "factor_id": "f1", "certification_status": "passed"}],
    )

    with pytest.raises(OperationalSealError, match="physical_state_drift"):
        verify_authoritative_operational_seal(authority, output)


def test_registry_has_unique_authoritative_roots_and_source_hashes(tmp_path: Path) -> None:
    registry = build_authoritative_writer_registry(tmp_path / "authority")

    roots = [writer["canonical_root"] for writer in registry["writers"]]
    assert len(roots) == len(set(roots))
    assert all(writer["source_proofs"] for writer in registry["writers"])
    assert all(len(proof["sha256"]) == 64 for writer in registry["writers"] for proof in writer["source_proofs"])
