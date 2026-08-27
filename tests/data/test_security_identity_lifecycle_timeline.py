import hashlib
import json
from pathlib import Path

import pytest

from auto_alpha.data.pit.engine import security_master
from auto_alpha.data.pit.engine import (
    derive_security_identity_lifecycle_timeline,
    publish_security_identity_lifecycle_intervals,
    validate_security_identity_lifecycle_intervals,
)
from auto_alpha.platform.artifacts.storage import canonical_hash


def _seed(
    security_id: str = "entity-a",
    *,
    security_code: str = "000022.SZ",
    security_name: str = "深赤湾A",
    lifecycle_state: str = "listed",
) -> dict[str, object]:
    return {
        "seed_version_id": f"{security_id}-seed-v1",
        "security_id": security_id,
        "as_of_date": "20171229",
        "security_code": security_code,
        "security_name": security_name,
        "lifecycle_state": lifecycle_state,
        "list_date": "19930505",
        "delist_date": None,
        "stable_identity_evidence_hash": "a" * 64,
        "source_evidence_hash": "b" * 64,
        "pit_evidence_eligible": True,
    }


def _event(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "code-change-1",
        "event_version_id": "code-change-1-v1",
        "version_number": 1,
        "supersedes_event_version_id": None,
        "security_id": "entity-a",
        "event_type": "security_code_change",
        "known_at": "20180102",
        "known_timing": "after_close",
        "effective_at": "20180102",
        "effective_timing": "after_close",
        "payload": {
            "old_security_code": "000022.SZ",
            "new_security_code": "001872.SZ",
        },
        "source_evidence_hash": "c" * 64,
        "pit_evidence_eligible": True,
    }
    row.update(overrides)
    return row


def _derive(
    *,
    security_ids: list[str] | None = None,
    seeds: list[dict[str, object]] | None = None,
    events: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return derive_security_identity_lifecycle_timeline(
        security_ids=security_ids or ["entity-a"],
        trade_dates=["20180102", "20180103", "20180104", "20180105"],
        pre_span_seeds=seeds if seeds is not None else [_seed()],
        event_versions=events if events is not None else [_event()],
    )


def _interval_only() -> dict[str, object]:
    timeline = _derive()
    timeline["rows"] = []
    timeline["daily_rows_materialized"] = False
    return timeline


def test_identity_code_change_is_not_backfilled_before_it_is_known() -> None:
    result = _derive()

    assert [row["security_code"] for row in result["rows"]] == [
        "000022.SZ",
        "001872.SZ",
        "001872.SZ",
        "001872.SZ",
    ]
    assert result["rows"][0]["applied_event_version_ids"] == []
    assert result["rows"][1]["applied_event_version_ids"] == [
        "code-change-1-v1"
    ]
    assert result["survivorship_backfill_used"] is False
    assert result["current_state_backfill_used"] is False
    assert result["derivation_complete"] is True
    assert result["data_admission_eligible"] is False
    assert result["independent_admission_verdict_required"] is True


def test_name_and_lifecycle_events_share_the_same_governed_timeline() -> None:
    name_change = _event(
        event_id="name-change-1",
        event_version_id="name-change-1-v1",
        event_type="security_name_change",
        known_at="20180103",
        known_timing="before_open",
        effective_at="20180103",
        effective_timing="before_open",
        payload={"old_security_name": "深赤湾A", "new_security_name": "招商港口"},
        source_evidence_hash="d" * 64,
    )
    delisting = _event(
        event_id="delisting-1",
        event_version_id="delisting-1-v1",
        event_type="delisting",
        known_at="20180103",
        known_timing="after_close",
        effective_at="20180104",
        effective_timing="before_open",
        payload={},
        source_evidence_hash="e" * 64,
    )

    result = _derive(events=[_event(), name_change, delisting])

    assert [row["security_name"] for row in result["rows"]] == [
        "深赤湾A",
        "招商港口",
        "招商港口",
        "招商港口",
    ]
    assert [row["lifecycle_state"] for row in result["rows"]] == [
        "listed",
        "listed",
        "delisted",
        "delisted",
    ]
    assert [row["active_on_trade_date"] for row in result["rows"]] == [
        True,
        True,
        False,
        False,
    ]
    assert result["intervals"] == [
        {
            "security_id": "entity-a",
            "trade_date_start": "20180102",
            "trade_date_end": "20180102",
            "security_code": "000022.SZ",
            "security_name": "深赤湾A",
            "lifecycle_state": "listed",
            "list_date": "19930505",
            "delist_date": None,
            "identity_resolved": True,
            "identity_unique": True,
            "active_on_trade_date": True,
        },
        {
            "security_id": "entity-a",
            "trade_date_start": "20180103",
            "trade_date_end": "20180103",
            "security_code": "001872.SZ",
            "security_name": "招商港口",
            "lifecycle_state": "listed",
            "list_date": "19930505",
            "delist_date": None,
            "identity_resolved": True,
            "identity_unique": True,
            "active_on_trade_date": True,
        },
        {
            "security_id": "entity-a",
            "trade_date_start": "20180104",
            "trade_date_end": "20180105",
            "security_code": "001872.SZ",
            "security_name": "招商港口",
            "lifecycle_state": "delisted",
            "list_date": "19930505",
            "delist_date": "20180104",
            "identity_resolved": True,
            "identity_unique": True,
            "active_on_trade_date": False,
        },
    ]


def test_missing_or_unproven_seed_never_uses_a_current_master_backfill() -> None:
    missing = _derive(seeds=[], events=[])
    unproven_seed = _seed()
    unproven_seed["pit_evidence_eligible"] = False
    unproven = _derive(seeds=[unproven_seed], events=[])

    for result, code in (
        (missing, "security_identity_pre_span_seed_missing"),
        (unproven, "security_identity_pre_span_seed_invalid"),
    ):
        assert all(row["identity_resolved"] is False for row in result["rows"])
        assert all(row["security_code"] is None for row in result["rows"])
        assert result["blockers"] == [{"code": code, "security_id": "entity-a"}]
        assert result["derivation_complete"] is False
        assert result["current_state_backfill_used"] is False


def test_late_revision_replaces_an_event_only_when_revision_is_observable() -> None:
    revision = _event(
        event_version_id="code-change-1-v2",
        version_number=2,
        supersedes_event_version_id="code-change-1-v1",
        known_at="20180104",
        known_timing="after_close",
        effective_at="20180102",
        effective_timing="after_close",
        payload={
            "old_security_code": "000022.SZ",
            "new_security_code": "001914.SZ",
        },
        source_evidence_hash="f" * 64,
    )

    result = _derive(events=[revision, _event()])

    assert [row["security_code"] for row in result["rows"]] == [
        "000022.SZ",
        "001872.SZ",
        "001872.SZ",
        "001914.SZ",
    ]
    assert result["rows"][2]["applied_event_version_ids"] == [
        "code-change-1-v1"
    ]
    assert result["rows"][3]["applied_event_version_ids"] == [
        "code-change-1-v2"
    ]


def test_invalid_revision_chain_fails_closed_for_the_whole_subject() -> None:
    revision = _event(
        event_version_id="code-change-1-v2",
        version_number=2,
        supersedes_event_version_id="does-not-exist",
        known_at="20180104",
        source_evidence_hash="f" * 64,
    )

    result = _derive(events=[_event(), revision])

    assert all(row["identity_resolved"] is False for row in result["rows"])
    assert result["blockers"] == [
        {
            "code": "security_identity_event_revision_chain_invalid",
            "event_id": "code-change-1",
            "security_id": "entity-a",
        }
    ]


@pytest.mark.parametrize(
    "changed_fields",
    (
        {
            "event_type": "security_name_change",
            "payload": {
                "old_security_name": "深赤湾A",
                "new_security_name": "招商港口",
            },
        },
        {"effective_at": "20180103"},
        {"effective_timing": "before_open"},
    ),
)
def test_revision_family_cannot_change_immutable_event_identity(
    changed_fields: dict[str, object],
) -> None:
    revision = _event(
        event_version_id="code-change-1-v2",
        version_number=2,
        supersedes_event_version_id="code-change-1-v1",
        known_at="20180103",
        **changed_fields,
    )

    result = _derive(events=[_event(), revision])

    assert result["derivation_complete"] is False
    assert all(row["identity_resolved"] is False for row in result["rows"])
    assert result["blockers"] == [
        {
            "code": "security_identity_event_revision_chain_invalid",
            "event_id": "code-change-1",
            "security_id": "entity-a",
        }
    ]


def test_same_day_conflicting_identity_events_fail_closed() -> None:
    competing = _event(
        event_id="code-change-2",
        event_version_id="code-change-2-v1",
        payload={
            "old_security_code": "000022.SZ",
            "new_security_code": "001914.SZ",
        },
        source_evidence_hash="d" * 64,
    )

    result = _derive(events=[_event(), competing])

    assert result["rows"][0]["identity_resolved"] is True
    assert all(row["identity_resolved"] is False for row in result["rows"][1:])
    assert result["blockers"] == [
        {
            "code": "security_identity_same_effective_time_conflict",
            "effective_at": "20180102",
            "event_version_ids": ["code-change-1-v1", "code-change-2-v1"],
            "field": "security_code",
            "security_id": "entity-a",
        }
    ]


def test_concurrent_code_collision_blocks_both_stable_identities() -> None:
    result = _derive(
        security_ids=["entity-a", "entity-b"],
        seeds=[
            _seed(),
            _seed(
                "entity-b",
                security_code="000022.SZ",
                security_name="另一实体",
            ),
        ],
        events=[],
    )

    assert all(row["identity_resolved"] is False for row in result["rows"])
    assert all(row["security_code"] is None for row in result["rows"])
    assert result["blockers"] == [
        {
            "code": "security_identity_concurrent_code_collision",
            "security_code": "000022.SZ",
            "security_ids": ["entity-a", "entity-b"],
            "trade_date_end": "20180105",
            "trade_date_start": "20180102",
        }
    ]


def test_identity_timeline_is_content_addressed_and_order_independent() -> None:
    name_change = _event(
        event_id="name-change-1",
        event_version_id="name-change-1-v1",
        event_type="security_name_change",
        known_at="20180103",
        known_timing="before_open",
        effective_at="20180103",
        effective_timing="before_open",
        payload={"old_security_name": "深赤湾A", "new_security_name": "招商港口"},
        source_evidence_hash="d" * 64,
    )
    forward = _derive(events=[_event(), name_change])
    reverse = _derive(events=[name_change, _event()])

    assert forward["rows"] == reverse["rows"]
    assert forward["intervals"] == reverse["intervals"]
    assert forward["content_hash"] == reverse["content_hash"]
    assert len(forward["content_hash"]) == 64
    assert len(forward["rows_root"]) == 64
    assert len(forward["intervals_root"]) == 64
    assert len(forward["derivation_implementation_root"]) == 64


def test_identity_timeline_can_emit_intervals_without_daily_row_materialization() -> None:
    materialized = _derive()
    interval_only = derive_security_identity_lifecycle_timeline(
        security_ids=["entity-a"],
        trade_dates=["20180102", "20180103", "20180104", "20180105"],
        pre_span_seeds=[_seed()],
        event_versions=[_event()],
        materialize_daily_rows=False,
    )

    assert interval_only["rows"] == []
    assert interval_only["daily_rows_materialized"] is False
    assert interval_only["daily_row_count"] == 4
    assert interval_only["intervals"] == materialized["intervals"]
    assert interval_only["rows_root"] == materialized["rows_root"]
    assert interval_only["content_hash"] == materialized["content_hash"]


def test_identity_intervals_publish_without_daily_rows(tmp_path) -> None:
    timeline = derive_security_identity_lifecycle_timeline(
        security_ids=["entity-a"],
        trade_dates=["20180102", "20180103", "20180104", "20180105"],
        pre_span_seeds=[_seed()],
        event_versions=[_event()],
        materialize_daily_rows=False,
    )

    published = publish_security_identity_lifecycle_intervals(
        timeline, tmp_path
    )
    validated = validate_security_identity_lifecycle_intervals(tmp_path)

    assert validated["generation_id"] == published["generation_id"]
    assert validated["intervals"] == timeline["intervals"]
    assert validated["derivation_content_hash"] == timeline["content_hash"]
    assert validated["data_admission_eligible"] is False
    assert validated["derivation_implementation_root"] == timeline[
        "derivation_implementation_root"
    ]
    assert set(validated["published_files"]) == {
        "security_identity_event_versions.jsonl",
        "security_identity_lifecycle_intervals.jsonl",
        "security_identity_pre_span_seeds.jsonl",
        "security_identity_security_ids.jsonl",
        "security_identity_trade_dates.jsonl",
    }


def test_identity_interval_publisher_replays_instead_of_trusting_self_signed_payload(
    tmp_path,
) -> None:
    timeline = _interval_only()
    timeline["intervals"][1]["security_code"] = "001914.SZ"
    timeline["intervals_root"] = canonical_hash(timeline["intervals"])
    semantic = {
        key: value
        for key, value in timeline.items()
        if key
        not in {
            "content_hash",
            "daily_rows_materialized",
            "event_versions_inputs",
            "intervals",
            "pre_span_seeds_inputs",
            "rows",
            "security_ids_axis",
            "trade_dates_axis",
        }
    }
    timeline["content_hash"] = canonical_hash(semantic)

    with pytest.raises(
        ValueError, match="security_identity_lifecycle_timeline_invalid"
    ):
        publish_security_identity_lifecycle_intervals(timeline, tmp_path)


@pytest.mark.parametrize(
    "mutation",
    ["gap", "overlap", "reverse", "payload", "noncanonical"],
)
def test_identity_interval_validator_independently_replays_exact_axis(
    tmp_path, mutation: str
) -> None:
    timeline = _interval_only()
    published = publish_security_identity_lifecycle_intervals(
        timeline, tmp_path / mutation
    )
    manifest_path = Path(published["manifest_path"])
    interval_path = manifest_path.parent / (
        "security_identity_lifecycle_intervals.jsonl"
    )
    rows = [
        json.loads(line)
        for line in interval_path.read_text(encoding="utf-8").splitlines()
    ]
    if mutation == "gap":
        rows.pop()
    elif mutation == "overlap":
        rows[1]["trade_date_start"] = "20180102"
    elif mutation == "reverse":
        rows.reverse()
    elif mutation == "payload":
        rows[1]["security_code"] = "001914.SZ"
    payload = b"".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=(mutation != "noncanonical"),
            separators=(",", ":") if mutation != "noncanonical" else None,
        ).encode()
        + b"\n"
        for row in rows
    )
    interval_path.write_bytes(payload)
    forged = _resign_identity_generation(
        manifest_path,
        changed_file=interval_path.name,
        record_count=len(rows),
        intervals=rows,
    )

    with pytest.raises(
        ValueError,
        match="security_identity_lifecycle_interval_evidence_invalid",
    ):
        validate_security_identity_lifecycle_intervals(forged)


def test_identity_interval_validator_rejects_axis_tampering_even_when_resigned(
    tmp_path,
) -> None:
    published = publish_security_identity_lifecycle_intervals(
        _interval_only(), tmp_path
    )
    manifest_path = Path(published["manifest_path"])
    axis_path = manifest_path.parent / "security_identity_trade_dates.jsonl"
    rows = [
        json.loads(line)
        for line in axis_path.read_text(encoding="utf-8").splitlines()
    ][:-1]
    axis_path.write_bytes(_canonical_jsonl(rows))
    forged = _resign_identity_generation(
        manifest_path,
        changed_file=axis_path.name,
        record_count=len(rows),
    )

    with pytest.raises(
        ValueError,
        match="security_identity_lifecycle_interval_evidence_invalid",
    ):
        validate_security_identity_lifecycle_intervals(forged)


def test_identity_interval_validator_rejects_implementation_drift(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = publish_security_identity_lifecycle_intervals(
        _interval_only(), tmp_path
    )
    monkeypatch.setattr(
        security_master,
        "_identity_derivation_implementation_root",
        lambda: "0" * 64,
    )

    with pytest.raises(
        ValueError,
        match="security_identity_lifecycle_interval_evidence_invalid",
    ):
        validate_security_identity_lifecycle_intervals(
            published["manifest_path"]
        )


def _canonical_jsonl(rows: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
        for row in rows
    )


def _resign_identity_generation(
    manifest_path: Path,
    *,
    changed_file: str,
    record_count: int,
    intervals: list[dict[str, object]] | None = None,
) -> Path:
    generation = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_path = generation / changed_file
    manifest["published_files"][changed_file] = {
        "record_count": record_count,
        "sha256": hashlib.sha256(changed_path.read_bytes()).hexdigest(),
        "size_bytes": changed_path.stat().st_size,
    }
    if intervals is not None:
        manifest["interval_count"] = len(intervals)
        manifest["intervals_root"] = canonical_hash(intervals)
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"security_identity_lifecycle_{content_hash[:24]}"
    manifest["content_hash"] = content_hash
    manifest["generation_id"] = generation_id
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    target = generation.parent / generation_id
    generation.rename(target)
    return target / manifest_path.name
