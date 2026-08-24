from auto_alpha.data.pit.engine import derive_control_state_timeline


def _seed(state: str = "clear") -> dict[str, object]:
    return {
        "security_id": "000001.SZ",
        "state": state,
        "as_of_date": "20240101",
        "source_evidence_hash": "a" * 64,
        "pit_evidence_eligible": True,
    }


def _event(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "st-1",
        "event_version_id": "st-1-v1",
        "security_id": "000001.SZ",
        "state": "restricted",
        "known_at": "20240103",
        "known_timing": "before_open",
        "effective_at": "20240104",
        "effective_timing": "before_open",
        "source_evidence_hash": "b" * 64,
        "pit_evidence_eligible": True,
    }
    row.update(overrides)
    return row


def test_control_state_does_not_apply_an_event_before_both_times() -> None:
    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103", "20240104", "20240105"],
        pre_span_seeds=[_seed()],
        event_versions=[_event()],
    )

    assert [row["state"] for row in result["rows"]] == [
        "clear",
        "clear",
        "restricted",
        "restricted",
    ]
    assert result["blockers"] == []
    assert result["derivation_complete"] is True
    assert result["data_admission_eligible"] is False
    assert result["independent_admission_verdict_required"] is True


def test_control_state_without_pre_span_seed_is_unknown_and_blocked() -> None:
    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103"],
        pre_span_seeds=[],
        event_versions=[],
    )

    assert [row["state"] for row in result["rows"]] == ["unknown", "unknown"]
    assert all(row["usable"] is False for row in result["rows"])
    assert result["blockers"] == [
        {
            "code": "control_state_pre_span_seed_missing",
            "security_id": "000001.SZ",
        }
    ]
    assert result["derivation_complete"] is False


def test_control_state_uses_conservative_intraday_transitions() -> None:
    restriction = _event(
        known_at="20240103",
        known_timing="intraday",
        effective_at="20240103",
        effective_timing="intraday",
    )
    clearance = _event(
        event_id="st-2",
        event_version_id="st-2-v1",
        state="clear",
        known_at="20240104",
        known_timing="intraday",
        effective_at="20240104",
        effective_timing="intraday",
        source_evidence_hash="c" * 64,
    )

    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103", "20240104", "20240105"],
        pre_span_seeds=[_seed()],
        event_versions=[restriction, clearance],
    )

    assert [row["state"] for row in result["rows"]] == [
        "clear",
        "restricted",
        "restricted",
        "clear",
    ]


def test_control_state_unknown_timing_fails_closed_from_possible_transition() -> None:
    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103", "20240104"],
        pre_span_seeds=[_seed()],
        event_versions=[_event(effective_at="20240103", effective_timing="unknown")],
    )

    assert [row["state"] for row in result["rows"]] == [
        "clear",
        "unknown",
        "unknown",
    ]
    assert result["blockers"] == [
        {
            "code": "control_event_timing_unknown",
            "event_version_id": "st-1-v1",
            "security_id": "000001.SZ",
        }
    ]
    assert result["derivation_complete"] is False


def test_control_state_conflicting_same_time_versions_fail_closed() -> None:
    restricted = _event()
    clear = _event(
        event_id="st-2",
        event_version_id="st-2-v1",
        state="clear",
        source_evidence_hash="c" * 64,
    )

    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240103", "20240104", "20240105"],
        pre_span_seeds=[_seed()],
        event_versions=[clear, restricted],
    )

    assert [row["state"] for row in result["rows"]] == [
        "clear",
        "conflict",
        "conflict",
    ]
    assert result["blockers"] == [
        {
            "code": "control_event_state_conflict",
            "event_version_ids": ["st-1-v1", "st-2-v1"],
            "security_id": "000001.SZ",
            "trade_date": "20240104",
        }
    ]
    assert result["derivation_complete"] is False


def test_late_known_event_never_backfills_its_effective_history() -> None:
    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103", "20240104", "20240105"],
        pre_span_seeds=[_seed()],
        event_versions=[
            _event(
                effective_at="20240102",
                known_at="20240104",
            )
        ],
    )

    assert [row["state"] for row in result["rows"]] == [
        "clear",
        "clear",
        "restricted",
        "restricted",
    ]
    assert result["rows"][1]["event_version_ids"] == []
    assert result["rows"][2]["event_version_ids"] == ["st-1-v1"]


def test_current_or_unproven_seed_cannot_initialize_historical_state() -> None:
    seed = _seed()
    seed["as_of_date"] = "20240105"

    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103"],
        pre_span_seeds=[seed],
        event_versions=[],
    )

    assert [row["state"] for row in result["rows"]] == ["unknown", "unknown"]
    assert result["blockers"] == [
        {
            "code": "control_state_pre_span_seed_invalid",
            "security_id": "000001.SZ",
        }
    ]
    assert result["derivation_complete"] is False


def test_unproven_event_version_makes_the_subject_history_unknown() -> None:
    result = derive_control_state_timeline(
        security_ids=["000001.SZ"],
        trade_dates=["20240102", "20240103"],
        pre_span_seeds=[_seed()],
        event_versions=[_event(pit_evidence_eligible=False)],
    )

    assert [row["state"] for row in result["rows"]] == ["unknown", "unknown"]
    assert result["blockers"] == [
        {
            "code": "control_event_version_invalid",
            "event_version_id": "st-1-v1",
            "security_id": "000001.SZ",
        }
    ]
    assert result["derivation_complete"] is False


def test_control_state_identity_is_deterministic_across_input_order() -> None:
    events = [
        _event(),
        _event(
            event_id="st-2",
            event_version_id="st-2-v1",
            state="clear",
            known_at="20240105",
            effective_at="20240105",
            source_evidence_hash="c" * 64,
        ),
    ]
    kwargs = {
        "security_ids": ["000001.SZ"],
        "trade_dates": ["20240103", "20240104", "20240105"],
        "pre_span_seeds": [_seed()],
    }

    forward = derive_control_state_timeline(**kwargs, event_versions=events)
    reverse = derive_control_state_timeline(
        **kwargs, event_versions=list(reversed(events))
    )

    assert forward["content_hash"] == reverse["content_hash"]
    assert len(forward["content_hash"]) == 64
    assert len(forward["rows_root"]) == 64
    assert forward["rows"] == reverse["rows"]
