"""Provider-neutral, non-revising PIT control-state timelines."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import re
from typing import Any

from auto_alpha.platform.artifacts.storage import canonical_hash


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_STATES = frozenset({"clear", "restricted"})
_TIMINGS = frozenset({"before_open", "intraday", "after_close"})


def derive_control_state_timeline(
    *,
    security_ids: Sequence[str],
    trade_dates: Sequence[str],
    pre_span_seeds: Sequence[Mapping[str, Any]],
    event_versions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve normalized restriction events without granting data admission."""

    dates = sorted(set(str(value) for value in trade_dates))
    if not dates or any(_exact_date(value) is None for value in dates):
        raise ValueError("control_state_trade_dates_invalid")
    subjects = sorted(set(str(value) for value in security_ids))
    if not subjects or any(not value for value in subjects):
        raise ValueError("control_state_security_ids_invalid")

    blockers: list[dict[str, Any]] = []
    seed_rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in pre_span_seeds:
        seed_rows.setdefault(str(row.get("security_id") or ""), []).append(row)
    seeds: dict[str, str] = {}
    invalid_seeds: set[str] = set()
    for security_id in subjects:
        candidates = seed_rows.get(security_id, [])
        valid = len(candidates) == 1 and _seed_valid(candidates[0], dates[0])
        if candidates and not valid:
            invalid_seeds.add(security_id)
            blockers.append(
                {
                    "code": "control_state_pre_span_seed_invalid",
                    "security_id": security_id,
                }
            )
        elif valid:
            seeds[security_id] = str(candidates[0].get("state"))
    events: dict[str, list[Mapping[str, Any]]] = {}
    for row in event_versions:
        events.setdefault(str(row.get("security_id") or ""), []).append(row)

    rows: list[dict[str, Any]] = []
    for security_id in subjects:
        state = seeds.get(security_id, "unknown")
        if security_id not in seeds and security_id not in invalid_seeds:
            blockers.append(
                {
                    "code": "control_state_pre_span_seed_missing",
                    "security_id": security_id,
                }
            )
        scheduled: list[tuple[str, str, Mapping[str, Any]]] = []
        subject_events = events.get(security_id, ())
        for event in subject_events:
            version_id = str(event.get("event_version_id") or "")
            if not _event_valid(event, security_id):
                state = "unknown"
                blockers.append(
                    {
                        "code": "control_event_version_invalid",
                        "event_version_id": version_id,
                        "security_id": security_id,
                    }
                )
                continue
            state_value = str(event.get("state") or "")
            known_at = str(event.get("known_at") or "")
            effective_at = str(event.get("effective_at") or "")
            timings = {
                str(event.get("known_timing") or ""),
                str(event.get("effective_timing") or ""),
            }
            if not timings <= _TIMINGS:
                blockers.append(
                    {
                        "code": "control_event_timing_unknown",
                        "event_version_id": version_id,
                        "security_id": security_id,
                    }
                )
                unknown_event = dict(event) | {"state": "unknown"}
                application_date = _first_trade_on_or_after(
                    max(known_at, effective_at), dates
                )
                scheduled.append((application_date, version_id, unknown_event))
                continue
            application_date = max(
                _transition_date(
                    known_at,
                    str(event.get("known_timing")),
                    state_value,
                    dates,
                ),
                _transition_date(
                    effective_at,
                    str(event.get("effective_timing")),
                    state_value,
                    dates,
                ),
            )
            scheduled.append((application_date, version_id, event))
        scheduled.sort(key=lambda item: (item[0], item[1]))
        by_application_date: dict[
            str, list[tuple[str, Mapping[str, Any]]]
        ] = {}
        for application_date, version_id, event in scheduled:
            by_application_date.setdefault(application_date, []).append(
                (version_id, event)
            )
        for trade_date in dates:
            transitions = by_application_date.get(trade_date, [])
            applied = [version_id for version_id, _event in transitions]
            transition_states = {
                str(event.get("state") or "unknown")
                for _version_id, event in transitions
            }
            if len(transition_states) > 1:
                state = "conflict"
                blockers.append(
                    {
                        "code": "control_event_state_conflict",
                        "event_version_ids": sorted(applied),
                        "security_id": security_id,
                        "trade_date": trade_date,
                    }
                )
            elif transition_states:
                state = next(iter(transition_states))
            rows.append(
                {
                    "security_id": security_id,
                    "trade_date": trade_date,
                    "state": state,
                    "usable": state in {"clear", "restricted"},
                    "event_version_ids": applied,
                }
            )

    blockers.sort(key=canonical_hash)
    semantic = {
        "schema_version": "pit_control_state_timeline_v1",
        "security_ids_root": canonical_hash(subjects),
        "trade_dates_root": canonical_hash(dates),
        "pre_span_seeds_input_root": canonical_hash(
            _canonical_input_rows(pre_span_seeds)
        ),
        "event_versions_input_root": canonical_hash(
            _canonical_input_rows(event_versions)
        ),
        "rows_root": canonical_hash(rows),
        "blockers": blockers,
        "derivation_complete": not blockers,
        "data_admission_eligible": False,
        "independent_admission_verdict_required": True,
    }
    return semantic | {"rows": rows, "content_hash": canonical_hash(semantic)}


def _transition_date(
    event_date: str,
    timing: str,
    state: str,
    trade_dates: Sequence[str],
) -> str:
    delayed = timing == "after_close" or (
        timing == "intraday" and state == "clear"
    )
    candidates = [
        trade_date
        for trade_date in trade_dates
        if (trade_date > event_date if delayed else trade_date >= event_date)
    ]
    return candidates[0] if candidates else "99999999"


def _first_trade_on_or_after(event_date: str, trade_dates: Sequence[str]) -> str:
    candidates = [date for date in trade_dates if date >= event_date]
    return candidates[0] if candidates else "99999999"


def _seed_valid(row: Mapping[str, Any], first_trade_date: str) -> bool:
    as_of_date = _exact_date(row.get("as_of_date"))
    return bool(
        str(row.get("state") or "") in _STATES
        and as_of_date is not None
        and as_of_date < first_trade_date
        and _HEX_64.fullmatch(str(row.get("source_evidence_hash") or ""))
        and row.get("pit_evidence_eligible") is True
    )


def _event_valid(row: Mapping[str, Any], security_id: str) -> bool:
    return bool(
        str(row.get("security_id") or "") == security_id
        and str(row.get("event_id") or "")
        and str(row.get("event_version_id") or "")
        and str(row.get("state") or "") in _STATES
        and _exact_date(row.get("known_at")) is not None
        and _exact_date(row.get("effective_at")) is not None
        and _HEX_64.fullmatch(str(row.get("source_evidence_hash") or ""))
        and row.get("pit_evidence_eligible") is True
    )


def _exact_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _canonical_input_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values = [dict(row) for row in rows]
    return sorted(values, key=canonical_hash)
