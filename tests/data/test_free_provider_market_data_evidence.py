from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path

import pytest

import auto_alpha.data.lake.store.free_provider_market_data_evidence as market_evidence
from auto_alpha.data.lake.store.admission import first_data_admission_profile
from auto_alpha.data.lake.store.free_provider_market_data_evidence import (
    _daily_bars_resume_implementation_root,
    _identity_interval_axis_valid,
    _load_identity_timeline_evidence,
    _market_projection_implementation_root,
    _replay_market_state_capture_to_directory,
    _stream_daily_bars_assessment,
    assess_daily_bars_replay,
    assess_index_daily_bars_replay,
    assess_trade_calendar_replay,
    build_daily_bars_evidence,
    main as market_data_evidence_main,
    publish_daily_bars_assessment,
    publish_index_daily_bars_assessment,
    publish_trade_calendar_assessment,
    validate_daily_bars_evidence,
    validate_index_daily_bars_evidence,
    validate_trade_calendar_evidence,
)
from auto_alpha.data.pit.engine.security_master import (
    derive_security_identity_lifecycle_timeline,
    publish_security_identity_lifecycle_intervals,
)
from auto_alpha.platform.artifacts.storage import canonical_hash, sha256_file


def _jsonl(rows: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows
    )


def test_index_daily_replay_closes_provider_neutral_values_validity_and_consumers() -> None:
    replayed = _jsonl(
        [
            {
                "amount": "2000.0000",
                "close": "12.0000",
                "date": "2012-01-04",
                "high": "13.0000",
                "historical_revision_proven": False,
                "isST": "0",
                "low": "9.0000",
                "open": "10.0000",
                "preclose": "9.5000",
                "source_payload_sha256": "a" * 64,
                "source_request_id": "baostock_index_daily_000300_SH",
                "tradestatus": "1",
                "ts_code": "000300.SH",
                "volume": "1000",
            },
            {
                "amount": "3000.0000",
                "close": "12.5000",
                "date": "2012-01-05",
                "high": "14.0000",
                "historical_revision_proven": False,
                "isST": "0",
                "low": "11.0000",
                "open": "12.0000",
                "preclose": "12.0000",
                "source_payload_sha256": "a" * 64,
                "source_request_id": "baostock_index_daily_000300_SH",
                "tradestatus": "1",
                "ts_code": "000300.SH",
                "volume": "1500",
            },
        ]
    )
    calendar = _jsonl(
        [
            {"trade_date": "20120104", "is_open": True},
            {"trade_date": "20120105", "is_open": True},
            {"trade_date": "20120106", "is_open": False},
        ]
    )

    result = assess_index_daily_bars_replay(
        replayed,
        calendar,
        profile=first_data_admission_profile(),
        date_start="20120104",
        date_end="20120105",
        source_binding={
            "capture_generation_id": "free_provider_backfill_fixture",
            "capture_content_hash": "b" * 64,
            "normalized_replay_root": "c" * 64,
            "calendar_source_sha256": "d" * 64,
            "operator_capture_contract_authorized": False,
            "provider_origin_attested": False,
            "capture_runtime_isolation_verified": False,
        },
    )

    assert result.semantic["technical_evidence_status"] == "blocked"
    assert result.semantic["formal_data_admission_ready"] is False
    assert result.semantic["coverage"] == {
        "expected_index_day_count": 2,
        "observed_index_day_count": 2,
        "missing_index_day_count": 0,
        "extra_index_day_count": 0,
        "duplicate_index_day_count": 0,
        "provisional_exact_cover": True,
    }
    assert result.semantic["validity"] == {
        "valid_row_count": 2,
        "invalid_row_count": 0,
        "required_field_count": 9,
        "all_required_values_valid": True,
    }
    assert result.semantic["consumer_closure"] == {
        "approved_fields": [
            "index_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "volume",
            "amount",
        ],
        "consumer_roles": ["benchmark_control"],
        "formula_input_authorized": False,
        "profile_contract_exact": True,
    }
    assert result.canonical_rows == (
        b'{"amount":"2000.0000","close":"12.0000","high":"13.0000",'
        b'"index_code":"000300.SH","low":"9.0000","open":"10.0000",'
        b'"pre_close":"9.5000","trade_date":"20120104","volume":"1000"}\n'
        b'{"amount":"3000.0000","close":"12.5000","high":"14.0000",'
        b'"index_code":"000300.SH","low":"11.0000","open":"12.0000",'
        b'"pre_close":"12.0000","trade_date":"20120105","volume":"1500"}\n'
    )
    assert result.coverage_gaps == b""
    assert set(result.semantic["blockers"]) == {
        "capture_runtime_isolation_not_attested",
        "data_admission_profile_human_approval_required",
        "operator_capture_contract_not_currently_authorized",
        "provider_acquisition_contract_not_activated",
        "provider_origin_not_attested",
        "index_daily_bars_independent_source_reference_resolution_pending",
        "source_freeze_consumer_binding_pending",
        "trade_calendar_data_admission_pending",
    }


def test_index_daily_replay_retains_coverage_and_value_failures_as_blocked_evidence() -> None:
    replayed = _jsonl(
        [
            {
                "amount": "-1",
                "close": "12",
                "date": "2012-01-04",
                "high": "10",
                "historical_revision_proven": False,
                "isST": "0",
                "low": "11",
                "open": "9",
                "preclose": "9.5",
                "source_payload_sha256": "a" * 64,
                "source_request_id": "baostock_index_daily_000300_SH",
                "tradestatus": "0",
                "ts_code": "000300.SH",
                "volume": "1000",
            }
        ]
    )
    calendar = _jsonl(
        [
            {"trade_date": "20120104", "is_open": True},
            {"trade_date": "20120105", "is_open": True},
        ]
    )

    result = assess_index_daily_bars_replay(
        replayed,
        calendar,
        profile=first_data_admission_profile(),
        date_start="20120104",
        date_end="20120105",
        source_binding={
            "capture_generation_id": "free_provider_backfill_fixture",
            "capture_content_hash": "b" * 64,
            "normalized_replay_root": "c" * 64,
            "calendar_source_sha256": "d" * 64,
            "operator_capture_contract_authorized": True,
            "provider_origin_attested": True,
            "capture_runtime_isolation_verified": True,
        },
    )

    assert result.semantic["technical_evidence_status"] == "blocked"
    assert result.semantic["coverage"]["missing_index_day_count"] == 1
    assert result.semantic["validity"]["invalid_row_count"] == 1
    assert set(result.semantic["blockers"]) >= {
        "index_daily_bars_index_day_exact_cover_failed",
        "index_daily_bars_required_value_validity_failed",
    }
    assert json.loads(result.coverage_gaps) == {
        "index_code": "000300.SH",
        "missing_trade_dates": ["20120105"],
        "extra_trade_dates": [],
        "duplicate_trade_dates": [],
    }
    validity = json.loads(result.validity_rows)
    assert validity == {
        "index_code": "000300.SH",
        "trade_date": "20120104",
        "valid": False,
        "reasons": [
            "amount_negative",
            "ohlc_order_invalid",
            "provider_trade_status_not_open",
        ],
    }


def test_index_daily_assessment_publishes_idempotent_tamper_evident_generation(
    tmp_path: Path,
) -> None:
    assessment = assess_index_daily_bars_replay(
        _jsonl(
            [
                {
                    "amount": "2000",
                    "close": "12",
                    "date": "2012-01-04",
                    "high": "13",
                    "low": "9",
                    "open": "10",
                    "preclose": "9.5",
                    "tradestatus": "1",
                    "ts_code": "000300.SH",
                    "volume": "1000",
                }
            ]
        ),
        _jsonl([{"trade_date": "20120104", "is_open": True}]),
        profile=first_data_admission_profile(),
        date_start="20120104",
        date_end="20120104",
        source_binding={
            "capture_generation_id": "free_provider_backfill_fixture",
            "capture_content_hash": "b" * 64,
            "normalized_replay_root": "c" * 64,
            "calendar_source_sha256": "d" * 64,
            "operator_capture_contract_authorized": False,
            "provider_origin_attested": False,
            "capture_runtime_isolation_verified": False,
        },
    )

    first = publish_index_daily_bars_assessment(assessment, tmp_path / "evidence")
    second = publish_index_daily_bars_assessment(assessment, tmp_path / "evidence")

    assert first["generation_id"] == second["generation_id"]
    assert validate_index_daily_bars_evidence(first["manifest_path"])[
        "technical_evidence_status"
    ] == "blocked"

    rows = Path(first["manifest_path"]).parent / "index_daily_bars.jsonl"
    rows.write_bytes(rows.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="index_daily_bars_evidence_invalid"):
        validate_index_daily_bars_evidence(first["manifest_path"])


def _untrusted_source_binding() -> dict[str, object]:
    return {
        "capture_source_profile_id": first_data_admission_profile()["profile_id"],
        "capture_scope": {
            "date_start": "20120101",
            "date_end": "20191231",
        },
        "capture_generation_id": "free_provider_backfill_fixture",
        "capture_content_hash": "b" * 64,
        "capture_contract_id": "c" * 64,
        "request_plan_hash": "d" * 64,
        "publication_signature_verified": True,
        "wire_replay_verified": True,
        "parser_replay_verified": True,
        "normalized_replay_root": "e" * 64,
        "normalized_replay_blockers": [],
        "published_normalized_identical": True,
        "operator_capture_contract_authorized": False,
        "provider_origin_attested": False,
        "capture_runtime_isolation_verified": False,
        "capture_adapter_implementation_root": "f" * 64,
        "current_capture_toolchain_implementation_root": "f" * 64,
        "capture_toolchain_implementation_match": True,
        "normalizer_conflicts_bound": True,
        "normalizer_conflict_count": 0,
        "normalizer_conflicts_root": (
            "e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855"
        ),
    }


def _write_identity_timeline(
    path: Path, intervals: list[dict[str, object]]
) -> tuple[dict[str, object], dict[str, object]]:
    trade_dates = sorted(
        {
            trade_date
            for interval in intervals
            for trade_date in market_evidence._date_span(
                str(interval["trade_date_start"]),
                str(interval["trade_date_end"]),
            )
        }
    )
    security_ids = sorted({str(row["security_id"]) for row in intervals})
    first_date = market_evidence.datetime.strptime(
        trade_dates[0], "%Y%m%d"
    ) - market_evidence.timedelta(days=1)
    seeds = []
    for security_id in security_ids:
        matches = [row for row in intervals if row["security_id"] == security_id]
        assert len(matches) == 1
        row = matches[0]
        seeds.append(
            {
                "seed_version_id": f"{security_id}-seed-v1",
                "security_id": security_id,
                "as_of_date": first_date.strftime("%Y%m%d"),
                "security_code": row["security_code"],
                "security_name": row["security_name"],
                "lifecycle_state": row["lifecycle_state"],
                "list_date": row["list_date"],
                "delist_date": row["delist_date"],
                "stable_identity_evidence_hash": "a" * 64,
                "source_evidence_hash": "b" * 64,
                "pit_evidence_eligible": True,
            }
        )
    timeline = derive_security_identity_lifecycle_timeline(
        security_ids=security_ids,
        trade_dates=trade_dates,
        pre_span_seeds=seeds,
        event_versions=[],
        materialize_daily_rows=False,
    )
    published = publish_security_identity_lifecycle_intervals(timeline, path)
    return _load_identity_timeline_evidence(published["manifest_path"])


def test_trade_calendar_replay_closes_exchange_day_axis_but_not_governance() -> None:
    result = assess_trade_calendar_replay(
        _jsonl(
            [
                {
                    "exchange": exchange,
                    "trade_date": trade_date,
                    "is_open": is_open,
                    "prev_trade_date": previous,
                }
                for trade_date, is_open, previous in (
                    ("20120101", False, "20111230"),
                    ("20120102", False, "20111230"),
                    ("20120103", True, "20111230"),
                )
                for exchange in ("SSE", "SZSE")
            ]
        ),
        profile=first_data_admission_profile(),
        date_start="20120101",
        date_end="20120103",
        source_binding=_untrusted_source_binding(),
    )

    assert result.semantic["dataset"] == "trade_calendar"
    assert result.semantic["technical_evidence_status"] == "blocked"
    assert result.semantic["formal_data_admission_ready"] is False
    assert result.semantic["coverage"] == {
        "expected_exchange_day_count": 6,
        "observed_exchange_day_count": 6,
        "missing_exchange_day_count": 0,
        "extra_exchange_day_count": 0,
        "duplicate_exchange_day_count": 0,
        "provisional_exact_cover": True,
    }
    assert result.semantic["pit_axis"]["open_trade_date_count"] == 1
    assert result.semantic["consumer_closure"] == {
        "approved_fields": [
            "exchange",
            "trade_date",
            "is_open",
            "prev_trade_date",
        ],
        "consumer_roles": ["date_axis", "scheduling_control"],
        "formula_input_authorized": False,
        "profile_contract_exact": True,
    }
    assert set(result.semantic["blockers"]) >= {
        "trade_calendar_session_authority_pending",
        "trade_calendar_independent_source_reference_resolution_pending",
        "source_freeze_consumer_binding_pending",
        "provider_acquisition_contract_not_activated",
        "data_admission_profile_human_approval_required",
    }


def test_trade_calendar_replay_blocks_missing_day_and_broken_previous_open() -> None:
    result = assess_trade_calendar_replay(
        _jsonl(
            [
                {
                    "exchange": "SSE",
                    "trade_date": "20120101",
                    "is_open": True,
                    "prev_trade_date": "20120102",
                },
                {
                    "exchange": "SZSE",
                    "trade_date": "20120101",
                    "is_open": True,
                    "prev_trade_date": "20111230",
                },
            ]
        ),
        profile=first_data_admission_profile(),
        date_start="20120101",
        date_end="20120102",
        source_binding=_untrusted_source_binding(),
    )

    assert result.semantic["technical_evidence_status"] == "blocked"
    assert result.semantic["coverage"]["missing_exchange_day_count"] == 2
    assert result.semantic["validity"]["invalid_row_count"] == 1
    assert set(result.semantic["blockers"]) >= {
        "trade_calendar_exchange_day_exact_cover_failed",
        "trade_calendar_required_value_validity_failed",
    }


def test_trade_calendar_requires_pre_span_previous_open_seed() -> None:
    result = assess_trade_calendar_replay(
        _jsonl(
            [
                {
                    "exchange": "SSE",
                    "trade_date": "20120103",
                    "is_open": True,
                    "prev_trade_date": None,
                }
            ]
        ),
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120103",
        source_binding=_untrusted_source_binding(),
        exchanges=("SSE",),
    )

    assert result.semantic["technical_evidence_status"] == "blocked"
    assert json.loads(result.validity_rows)["reasons"] == [
        "pre_span_previous_open_seed_missing"
    ]


def test_daily_bars_replay_closes_security_day_values_axis_and_consumers() -> None:
    calendar = _jsonl(
        [
            {
                "exchange": exchange,
                "trade_date": trade_date,
                "is_open": True,
                "prev_trade_date": previous,
            }
            for trade_date, previous in (
                ("20120103", "20111230"),
                ("20120104", "20120103"),
            )
            for exchange in ("SSE", "SZSE")
        ]
    )
    lifecycles = _jsonl(
        [
            {
                "ts_code": "600000.SH",
                "exchange": "SSE",
                "list_date": "19991110",
                "delist_date": None,
            },
            {
                "ts_code": "000001.SZ",
                "exchange": "SZSE",
                "list_date": "20120104",
                "delist_date": None,
            },
        ]
    )
    rows = _jsonl(
        [
            {
                "date": trade_date[:4] + "-" + trade_date[4:6] + "-" + trade_date[6:],
                "ts_code": code,
                "open": "10",
                "high": "12",
                "low": "9",
                "close": "11",
                "preclose": "10",
                "volume": "1000",
                "amount": "11000",
                "tradestatus": "1",
            }
            for code, trade_date in (
                ("600000.SH", "20120103"),
                ("600000.SH", "20120104"),
                ("000001.SZ", "20120104"),
            )
        ]
    )

    result = assess_daily_bars_replay(
        rows,
        calendar,
        lifecycles,
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120104",
        source_binding=_untrusted_source_binding(),
    )

    assert result.semantic["technical_evidence_status"] == "blocked"
    assert (
        "daily_bars_independent_source_reference_resolution_pending"
        in result.semantic["blockers"]
    )
    assert result.semantic["coverage"] == {
        "expected_security_day_count": 3,
        "observed_security_day_count": 3,
        "missing_security_day_count": 0,
        "extra_security_day_count": 0,
        "duplicate_security_day_count": 0,
        "provisional_exact_cover": True,
    }
    assert result.semantic["validity"]["invalid_row_count"] == 0
    assert result.semantic["pit_axis"]["security_count"] == 2
    assert result.semantic["consumer_closure"]["consumer_roles"] == [
        "formula_input",
        "target",
        "execution",
        "capacity",
    ]
    assert result.semantic["consumer_closure"]["formula_input_authorized"] is False
    assert set(result.semantic["blockers"]) >= {
        "securities_data_admission_pending",
        "trade_calendar_data_admission_pending",
        "source_freeze_consumer_binding_pending",
    }


def test_daily_bars_replay_blocks_ohlcv_and_security_day_gaps() -> None:
    result = assess_daily_bars_replay(
        _jsonl(
            [
                {
                    "date": "2012-01-03",
                    "ts_code": "600000.SH",
                    "open": "10",
                    "high": "8",
                    "low": "9",
                    "close": "11",
                    "preclose": "10",
                    "volume": "-1",
                    "amount": "100",
                    "tradestatus": "1",
                }
            ]
        ),
        _jsonl(
            [
                {
                    "exchange": "SSE",
                    "trade_date": date,
                    "is_open": True,
                    "prev_trade_date": previous,
                }
                for date, previous in (
                    ("20120103", "20111230"),
                    ("20120104", "20120103"),
                )
            ]
        ),
        _jsonl(
            [
                {
                    "ts_code": "600000.SH",
                    "exchange": "SSE",
                    "list_date": "19991110",
                    "delist_date": None,
                }
            ]
        ),
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120104",
        source_binding=_untrusted_source_binding(),
    )

    assert result.semantic["technical_evidence_status"] == "blocked"
    assert result.semantic["coverage"]["missing_security_day_count"] == 1
    assert result.semantic["validity"]["invalid_row_count"] == 1
    assert set(result.semantic["blockers"]) >= {
        "daily_bars_security_day_exact_cover_failed",
        "daily_bars_required_value_validity_failed",
    }


def test_daily_bars_delist_date_is_exclusive_and_conflicts_are_technical() -> None:
    source_binding = _untrusted_source_binding()
    source_binding["normalizer_conflict_count"] = 1
    source_binding["capture_toolchain_implementation_match"] = False
    result = assess_daily_bars_replay(
        _jsonl(
            [
                {
                    "date": "2012-01-03",
                    "ts_code": "600000.SH",
                    "open": "10",
                    "high": "12",
                    "low": "9",
                    "close": "11",
                    "preclose": "10",
                    "volume": "1000",
                    "amount": "11000",
                    "tradestatus": "1",
                }
            ]
        ),
        _jsonl(
            [
                {
                    "exchange": "SSE",
                    "trade_date": date,
                    "is_open": True,
                    "prev_trade_date": previous,
                }
                for date, previous in (
                    ("20120103", "20111230"),
                    ("20120104", "20120103"),
                )
            ]
        ),
        _jsonl(
            [
                {
                    "ts_code": "600000.SH",
                    "exchange": "SSE",
                    "list_date": "19991110",
                    "delist_date": "20120104",
                }
            ]
        ),
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120104",
        source_binding=source_binding,
    )

    assert result.semantic["coverage"]["expected_security_day_count"] == 1
    assert result.semantic["coverage"]["provisional_exact_cover"] is True
    assert "daily_bars_normalization_conflicts_present" in result.semantic[
        "blockers"
    ]
    assert "daily_bars_capture_toolchain_identity_mismatch" in result.semantic[
        "blockers"
    ]
    assert result.semantic["technical_evidence_status"] == "blocked"


def test_full_daily_bars_assessment_streams_to_sqlite_and_preserves_suspension_na(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bars = tmp_path / "provider_daily_bars.jsonl"
    bars.write_bytes(
        _jsonl(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20120103",
                    "open": "10",
                    "high": "12",
                    "low": "9",
                    "close": "11",
                    "pre_close": "10",
                    "volume": "1000",
                    "amount": "11000",
                    "provider_trade_status": 1,
                },
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20120104",
                    "open": "",
                    "high": "",
                    "low": "",
                    "close": "",
                    "pre_close": "11",
                    "volume": "0",
                    "amount": "0",
                    "provider_trade_status": 0,
                },
            ]
        )
    )
    calendar = tmp_path / "trade_calendar.jsonl"
    calendar.write_bytes(
        _jsonl(
            [
                {
                    "exchange": "SSE",
                    "trade_date": date,
                    "is_open": True,
                    "prev_trade_date": previous,
                }
                for date, previous in (
                    ("20120103", "20111230"),
                    ("20120104", "20120103"),
                )
            ]
        )
    )
    output = tmp_path / "prepared"
    output.mkdir()
    spill = tmp_path / "spill"
    spill.mkdir()
    identity_timeline, identity_binding = _write_identity_timeline(
        tmp_path / "identity_timeline.json",
        [
            {
                "security_id": "entity-600000",
                "trade_date_start": "20120103",
                "trade_date_end": "20120104",
                "security_code": "600000.SH",
                "security_name": "浦发银行",
                "lifecycle_state": "listed",
                "list_date": "19991110",
                "delist_date": None,
                "identity_resolved": True,
                "active_on_trade_date": True,
            }
        ],
    )
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == bars:
            raise AssertionError("full daily bars input must be streamed")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    semantic = _stream_daily_bars_assessment(
        bars,
        calendar,
        identity_timeline,
        output,
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120104",
        source_binding=_untrusted_source_binding(),
        identity_binding=identity_binding,
        spill_root=spill,
    )

    assert semantic["resource_execution"] == {
        "engine": "sqlite_disk_spill",
        "input_mode": "jsonl_stream",
        "output_mode": "jsonl_stream",
        "batch_row_limit": 10_000,
        "sqlite_cache_limit_mib": 64,
        "sqlite_spill_limit_bytes": 32 * 1024 * 1024 * 1024,
        "sqlite_mmap_bytes": 0,
        "work_identity": semantic["resource_execution"]["work_identity"],
        "resume_schema_version": "daily_bars_sqlite_resume_v3",
        "resume_implementation_root": semantic["resource_execution"][
            "resume_implementation_root"
        ],
        "checkpoint_granularity": (
            "committed_input_prefix_and_projected_state"
        ),
        "checkpoint_input_prefix_sha256": semantic["resource_execution"][
            "checkpoint_input_prefix_sha256"
        ],
        "checkpoint_projected_rows_sha256": semantic[
            "resource_execution"
        ]["checkpoint_projected_rows_sha256"],
        "checkpoint_static_axis_sha256": semantic["resource_execution"][
            "checkpoint_static_axis_sha256"
        ],
        "checkpoint_joined_state_sha256": semantic["resource_execution"][
            "checkpoint_joined_state_sha256"
        ],
        "source_binding_root": semantic["resource_execution"][
            "source_binding_root"
        ],
        "expected_axis_binding_root": semantic["resource_execution"][
            "expected_axis_binding_root"
        ],
        "resume_supported": True,
    }
    assert semantic["coverage"]["provisional_exact_cover"] is True
    assert semantic["validity"]["valid_row_count"] == 1
    assert semantic["validity"]["invalid_row_count"] == 1
    assert semantic["validity"]["not_applicable_candidate_count"] == 1
    assert semantic["pit_axis"]["identity_timeline_binding"] == identity_binding
    assert "daily_bars_streaming_resume_not_implemented" not in semantic[
        "blockers"
    ]
    validity = [
        json.loads(line)
        for line in (output / "daily_bars_validity.jsonl")
        .read_text()
        .splitlines()
    ]
    assert validity[1] == {
        "not_applicable_candidate": "proven_suspension",
        "reasons": [
            "provider_reported_suspension_requires_admitted_control"
        ],
        "trade_date": "20120104",
        "ts_code": "600000.SH",
        "valid": False,
    }


def _write_daily_bars_resume_fixture(
    root: Path,
) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    root.mkdir(parents=True, exist_ok=True)
    bars = root / "provider_daily_bars.jsonl"
    bars.write_bytes(
        _jsonl(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": trade_date,
                    "open": value,
                    "high": value,
                    "low": value,
                    "close": value,
                    "pre_close": value,
                    "volume": "1000",
                    "amount": "10000",
                    "provider_trade_status": 1,
                }
                for trade_date, value in (
                    ("20120103", "10"),
                    ("20120104", "11"),
                )
            ]
        )
    )
    calendar = root / "trade_calendar.jsonl"
    calendar.write_bytes(
        _jsonl(
            [
                {
                    "exchange": "SSE",
                    "trade_date": trade_date,
                    "is_open": True,
                    "prev_trade_date": previous,
                }
                for trade_date, previous in (
                    ("20120103", "20111230"),
                    ("20120104", "20120103"),
                )
            ]
        )
    )
    identity, binding = _write_identity_timeline(
        root / "identity_timeline.json",
        [
            {
                "security_id": "entity-600000",
                "trade_date_start": "20120103",
                "trade_date_end": "20120104",
                "security_code": "600000.SH",
                "security_name": "浦发银行",
                "lifecycle_state": "listed",
                "list_date": "19991110",
                "delist_date": None,
                "identity_resolved": True,
                "active_on_trade_date": True,
            }
        ],
    )
    return bars, calendar, identity, binding


def _run_daily_bars_resume_fixture(
    *,
    bars: Path,
    calendar: Path,
    identity: dict[str, object],
    binding: dict[str, object],
    output: Path,
    spill: Path,
    source_binding: dict[str, object] | None = None,
) -> dict[str, object]:
    return _stream_daily_bars_assessment(
        bars,
        calendar,
        identity,
        output,
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120104",
        source_binding=source_binding or _untrusted_source_binding(),
        identity_binding=binding,
        spill_root=spill,
    )


def test_daily_bars_sqlite_checkpoint_resumes_at_committed_byte_offset_and_matches_cold_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / "inputs"
    )
    original_project = market_evidence._project_daily_bar
    calls = 0
    interrupted = False

    def interrupt_once(row: dict[str, object]) -> object:
        nonlocal calls, interrupted
        calls += 1
        if calls == 2 and not interrupted:
            interrupted = True
            raise RuntimeError("controlled_stream_interruption")
        return original_project(row)

    monkeypatch.setattr(market_evidence, "DAILY_BARS_BATCH_ROW_LIMIT", 1)
    monkeypatch.setattr(market_evidence, "_project_daily_bar", interrupt_once)
    output = tmp_path / "resumed-output"
    spill = tmp_path / "resumed-spill"
    with pytest.raises(RuntimeError, match="controlled_stream_interruption"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )
    with sqlite3.connect(spill / "daily_bars_assessment.sqlite3") as connection:
        offset, ordinal, phase = connection.execute(
            "SELECT input_offset, next_ordinal, phase FROM resume_state"
        ).fetchone()
    assert 0 < offset < bars.stat().st_size
    assert (ordinal, phase) == (1, "ingesting")

    resumed = _run_daily_bars_resume_fixture(
        bars=bars,
        calendar=calendar,
        identity=identity,
        binding=binding,
        output=output,
        spill=spill,
    )
    # One source row is resumed; output and archived-source closure each replay two.
    assert calls == 7
    assert resumed["resource_execution"]["resume_supported"] is True
    assert _run_daily_bars_resume_fixture(
        bars=bars,
        calendar=calendar,
        identity=identity,
        binding=binding,
        output=output,
        spill=spill,
    ) == resumed
    # Cache reuse performs the two independent two-row closure replays only.
    assert calls == 11

    cold_output = tmp_path / "cold-output"
    cold = _run_daily_bars_resume_fixture(
        bars=bars,
        calendar=calendar,
        identity=identity,
        binding=binding,
        output=cold_output,
        spill=tmp_path / "cold-spill",
    )
    assert cold == resumed
    for name in (
        "daily_bars.jsonl",
        "daily_bars_validity.jsonl",
        "daily_bars_coverage_gaps.jsonl",
        "output_checkpoint.json",
    ):
        assert (cold_output / name).read_bytes() == (output / name).read_bytes()


def _interrupt_daily_bars_after_first_commit(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, dict[str, object], dict[str, object], Path, Path]:
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / "inputs"
    )
    original_project = market_evidence._project_daily_bar
    calls = 0

    def interrupt(row: dict[str, object]) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled_stream_interruption")
        return original_project(row)

    monkeypatch.setattr(market_evidence, "DAILY_BARS_BATCH_ROW_LIMIT", 1)
    monkeypatch.setattr(market_evidence, "_project_daily_bar", interrupt)
    output = tmp_path / "output"
    spill = tmp_path / "spill"
    with pytest.raises(RuntimeError, match="controlled_stream_interruption"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )
    monkeypatch.setattr(market_evidence, "_project_daily_bar", original_project)
    return bars, calendar, identity, binding, output, spill


def test_daily_bars_resume_rejects_consumed_prefix_value_drift_even_if_outer_hash_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bars, calendar, identity, binding, output, spill = (
        _interrupt_daily_bars_after_first_commit(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
    )
    trusted_sha256 = sha256_file(bars)
    original_sha256_file = market_evidence.sha256_file
    payload = bars.read_bytes()
    assert b'"open":"10"' in payload
    bars.write_bytes(payload.replace(b'"open":"10"', b'"open":"99"', 1))

    def stale_catalog_hash(path: object) -> str:
        if Path(path) == bars:
            return trusted_sha256
        return original_sha256_file(path)  # type: ignore[arg-type]

    monkeypatch.setattr(market_evidence, "sha256_file", stale_catalog_hash)
    with pytest.raises(ValueError, match="daily_bars_resume_state_drift"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )


def test_daily_bars_resume_rejects_projected_row_value_drift_with_same_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bars, calendar, identity, binding, output, spill = (
        _interrupt_daily_bars_after_first_commit(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
    )
    database = spill / "daily_bars_assessment.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE bars SET canonical_json=? WHERE ordinal=0",
            ('{"amount":"999"}',),
        )
        connection.commit()
    with pytest.raises(ValueError, match="daily_bars_resume_state_drift"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )


@pytest.mark.parametrize("axis", ("identity_intervals", "open_dates"))
def test_daily_bars_resume_rejects_same_count_static_axis_value_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, axis: str
) -> None:
    bars, calendar, identity, binding, output, spill = (
        _interrupt_daily_bars_after_first_commit(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
    )
    database = spill / "daily_bars_assessment.sqlite3"
    with sqlite3.connect(database) as connection:
        if axis == "identity_intervals":
            connection.execute(
                "UPDATE identity_intervals SET trade_date_end='20120105'"
            )
        else:
            connection.execute(
                """UPDATE open_dates SET trade_date='20120105'
                WHERE trade_date='20120104'"""
            )
        connection.commit()
    with pytest.raises(ValueError, match="daily_bars_resume_state_drift"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )


def test_daily_bars_resume_rejects_same_count_joined_expected_axis_drift(
    tmp_path: Path,
) -> None:
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / "inputs"
    )
    output = tmp_path / "output"
    spill = tmp_path / "spill"
    _run_daily_bars_resume_fixture(
        bars=bars,
        calendar=calendar,
        identity=identity,
        binding=binding,
        output=output,
        spill=spill,
    )
    database = spill / "daily_bars_assessment.sqlite3"
    with sqlite3.connect(database) as connection:
        binding_json, work_identity = connection.execute(
            "SELECT resume_binding_json, work_identity FROM resume_state"
        ).fetchone()
        connection.execute(
            """UPDATE expected SET trade_date='20120105'
            WHERE trade_date='20120104'"""
        )
        connection.commit()
        with pytest.raises(ValueError, match="daily_bars_resume_state_drift"):
            market_evidence._daily_bars_resume_state(
                connection,
                resume_binding=json.loads(binding_json),
                work_identity=work_identity,
                replayed_rows_path=bars,
            )


def test_daily_bars_output_checkpoint_rejects_fully_resigned_ohlc_drift(
    tmp_path: Path,
) -> None:
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / "inputs"
    )
    output = tmp_path / "output"
    spill = tmp_path / "spill"
    _run_daily_bars_resume_fixture(
        bars=bars,
        calendar=calendar,
        identity=identity,
        binding=binding,
        output=output,
        spill=spill,
    )
    rows_path = output / "daily_bars.jsonl"
    rows = [json.loads(line) for line in rows_path.read_bytes().splitlines()]
    rows[0].update(
        {
            "open": "99",
            "high": "99",
            "low": "99",
            "close": "99",
            "pre_close": "99",
        }
    )
    rows_path.write_bytes(_jsonl(rows))
    checkpoint_path = output / "output_checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    artifact = next(
        row for row in checkpoint["artifacts"] if row["name"] == "daily_bars.jsonl"
    )
    artifact["sha256"] = sha256_file(rows_path)
    artifact["size_bytes"] = rows_path.stat().st_size
    projection = checkpoint["semantic"]["provider_neutral_projection"]
    projection["canonical_rows_sha256"] = sha256_file(rows_path)
    projection["canonical_rows_root"] = sha256_file(rows_path)
    projection["canonical_rows_size_bytes"] = rows_path.stat().st_size
    checkpoint_semantic = {
        key: value for key, value in checkpoint.items() if key != "content_hash"
    }
    checkpoint["content_hash"] = canonical_hash(checkpoint_semantic)
    checkpoint_path.write_text(
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError, match="daily_bars_resume_output_semantic_invalid"
    ):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )


def test_daily_bars_work_identity_lock_is_nonblocking_between_workers(
    tmp_path: Path,
) -> None:
    work_identity = "a" * 64
    with market_evidence._daily_bars_work_lock(tmp_path, work_identity):
        with pytest.raises(
            ValueError, match="daily_bars_resume_work_identity_locked"
        ):
            with market_evidence._daily_bars_work_lock(
                tmp_path, work_identity
            ):
                raise AssertionError("second worker acquired the same identity")


@pytest.mark.parametrize("role", ("output", "spill", "work"))
def test_daily_bars_paths_reject_symlink_ancestors(
    tmp_path: Path, role: str
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    if role == "work":
        with pytest.raises(
            ValueError,
            match="daily_bars_resume_work_parent_symlink_forbidden",
        ):
            with market_evidence._daily_bars_work_lock(
                alias / "work", "a" * 64
            ):
                pass
        return
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / f"{role}-inputs"
    )
    output = alias / "output" if role == "output" else tmp_path / "output"
    spill = alias / "spill" if role == "spill" else tmp_path / "spill"
    with pytest.raises(ValueError, match=f"daily_bars_resume_{role}_root_symlink_forbidden"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )


@pytest.mark.parametrize("entry_kind", ("file", "symlink"))
def test_daily_bars_output_rejects_preexisting_entries_without_overwrite(
    tmp_path: Path, entry_kind: str
) -> None:
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / "inputs"
    )
    output = tmp_path / "output"
    output.mkdir()
    external = tmp_path / "external"
    external.write_bytes(b"do-not-touch")
    entry = output / "daily_bars.jsonl"
    if entry_kind == "file":
        entry.write_bytes(b"preexisting")
    else:
        entry.symlink_to(external)
    with pytest.raises(ValueError, match="daily_bars_resume_output_not_empty"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=tmp_path / "spill",
        )
    assert external.read_bytes() == b"do-not-touch"
    if entry_kind == "file":
        assert entry.read_bytes() == b"preexisting"
    else:
        assert entry.is_symlink()


@pytest.mark.parametrize("drift", ("raw", "identity", "toolchain"))
def test_daily_bars_resume_rejects_raw_identity_and_toolchain_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    bars, calendar, identity, binding = _write_daily_bars_resume_fixture(
        tmp_path / drift / "inputs"
    )
    original_project = market_evidence._project_daily_bar
    calls = 0

    def interrupt(row: dict[str, object]) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled_stream_interruption")
        return original_project(row)

    monkeypatch.setattr(market_evidence, "DAILY_BARS_BATCH_ROW_LIMIT", 1)
    monkeypatch.setattr(market_evidence, "_project_daily_bar", interrupt)
    output = tmp_path / drift / "output"
    spill = tmp_path / drift / "spill"
    with pytest.raises(RuntimeError, match="controlled_stream_interruption"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=binding,
            output=output,
            spill=spill,
        )
    monkeypatch.setattr(market_evidence, "_project_daily_bar", original_project)
    changed_binding = dict(binding)
    changed_source = _untrusted_source_binding()
    if drift == "raw":
        bars.write_bytes(bars.read_bytes() + b"\n")
    elif drift == "identity":
        changed_binding["identity_timeline_rows_root"] = "0" * 64
    else:
        changed_source["current_capture_toolchain_implementation_root"] = "0" * 64
    with pytest.raises(ValueError, match="daily_bars_resume_state_drift"):
        _run_daily_bars_resume_fixture(
            bars=bars,
            calendar=calendar,
            identity=identity,
            binding=changed_binding,
            output=output,
            spill=spill,
            source_binding=changed_source,
        )


def test_full_daily_builder_requires_governed_identity_timeline_before_capture(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError, match="daily_bars_identity_timeline_evidence_required"
    ):
        build_daily_bars_evidence(
            tmp_path / "missing-capture",
            tmp_path / "output",
        )

    with pytest.raises(
        SystemExit,
        match="--identity-timeline-evidence is required for daily_bars",
    ):
        market_data_evidence_main(
            [
                "--dataset",
                "daily_bars",
                "--capture",
                str(tmp_path / "missing-capture"),
                "--output-root",
                str(tmp_path / "output"),
            ]
        )


def test_market_evidence_command_reports_technical_output_without_admission(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        market_evidence,
        "build_trade_calendar_evidence",
        lambda *_args: {
            "technical_evidence_status": "verified",
            "formal_data_admission_ready": False,
        },
    )

    exit_code = market_data_evidence_main(
        [
            "--dataset",
            "trade_calendar",
            "--capture",
            "signed-capture",
            "--output-root",
            "evidence",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["technical_evidence_status"] == "verified"
    assert payload["formal_data_admission_ready"] is False


def test_market_only_disk_replay_emits_bars_calendar_and_conflicts_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture"
    capture.mkdir()
    manifest = capture / "free_provider_backfill_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    requests = [
        {
            "request_id": "calendar",
            "metadata": {"case": "trade_calendar"},
        },
        {
            "request_id": "history",
            "metadata": {"case": "history", "ts_code": "600000.SH"},
        },
    ]
    (capture / "request_plan.json").write_text(
        json.dumps({"requests": requests}), encoding="utf-8"
    )
    envelopes = capture / "raw_envelopes"
    envelopes.mkdir()
    for request_id in ("calendar", "history"):
        raw = request_id.encode()
        (envelopes / f"{request_id}.json").write_text(
            json.dumps(
                {
                    "raw_payload_base64": base64.b64encode(raw).decode(),
                    "raw_payload_sha256": request_id * 8,
                }
            ),
            encoding="utf-8",
        )
    (capture / "capture_journal.jsonl").write_bytes(
        _jsonl(
            [
                {
                    "event_type": "capture_attempt_terminal",
                    "request_id": request_id,
                    "raw_envelope_relative_path": (
                        f"raw_envelopes/{request_id}.json"
                    ),
                }
                for request_id in ("calendar", "history")
            ]
        )
    )

    def logical_rows(raw: bytes):
        if raw == b"calendar":
            return ["calendar_date", "is_trading_day"], [
                ["2012-01-03", "1"]
            ]
        return [
            "date",
            "code",
            "open",
            "high",
            "low",
            "close",
            "preclose",
            "volume",
            "amount",
            "tradestatus",
            "isST",
        ], [
            [
                "2012-01-03",
                "sh.600000",
                "10",
                "12",
                "9",
                "11",
                "10",
                "1000",
                "11000",
                "1",
                "0",
            ],
            [
                "2012-01-03",
                "sh.600000",
                "10",
                "12",
                "9",
                "11",
                "10",
                "1000",
                "11000",
                "1",
                "0",
            ],
        ]

    monkeypatch.setattr(
        "auto_alpha.data.lake.store.free_provider_market_data_evidence."
        "validate_free_provider_backfill",
        lambda _path: {"manifest_path": str(manifest)},
    )
    monkeypatch.setattr(
        "auto_alpha.data.lake.store.free_provider_market_data_evidence."
        "_baostock_logical_rows",
        logical_rows,
    )
    output = tmp_path / "replay"
    output.mkdir()
    paths, replay_root = _replay_market_state_capture_to_directory(
        manifest, output_directory=output
    )

    assert set(paths) == {
        "provider_daily_bars",
        "trade_calendar",
        "conflicts",
    }
    assert len(replay_root) == 64
    assert len(paths["provider_daily_bars"].read_text().splitlines()) == 1
    assert len(paths["trade_calendar"].read_text().splitlines()) == 2
    conflict = json.loads(paths["conflicts"].read_text())
    assert conflict["reason"] == "duplicate_trade_date"


def test_full_daily_builder_binds_interval_evidence_conflicts_and_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _timeline, identity_binding = _write_identity_timeline(
        tmp_path / "identity",
        [
            {
                "security_id": "entity-600000",
                "trade_date_start": "20120103",
                "trade_date_end": "20120103",
                "security_code": "600000.SH",
                "security_name": "浦发银行",
                "lifecycle_state": "listed",
                "list_date": "19991110",
                "delist_date": None,
                "identity_resolved": True,
                "active_on_trade_date": True,
            }
        ],
    )
    identity_manifest = next(
        (tmp_path / "identity/generations").glob(
            "*/security_identity_lifecycle_manifest.json"
        )
    )
    capture = tmp_path / "capture"
    capture.mkdir()
    capture_manifest = capture / "free_provider_backfill_manifest.json"
    capture_manifest.write_text("{}", encoding="utf-8")
    (capture / "activity_contract.json").write_text(
        json.dumps(
            {
                "provider": "baostock",
                "source_profile_id": first_data_admission_profile()[
                    "profile_id"
                ],
                "scope": {
                    "date_start": "20120103",
                    "date_end": "20120103",
                },
                "adapter_identity": {"implementation_root": "f" * 64},
            }
        ),
        encoding="utf-8",
    )

    def fake_replay(_capture: object, *, output_directory: object):
        root = Path(output_directory) / "normalized"
        root.mkdir()
        bars = root / "provider_daily_bars.jsonl"
        calendar = root / "trade_calendar.jsonl"
        conflicts = root / "conflicts.jsonl"
        bars.write_bytes(
            _jsonl(
                [
                    {
                        "ts_code": "600000.SH",
                        "trade_date": "20120103",
                        "open": "10",
                        "high": "12",
                        "low": "9",
                        "close": "11",
                        "pre_close": "10",
                        "volume": "1000",
                        "amount": "11000",
                        "provider_trade_status": 1,
                    }
                ]
            )
        )
        calendar.write_bytes(
            _jsonl(
                [
                    {
                        "exchange": "SSE",
                        "trade_date": "20120103",
                        "is_open": True,
                        "prev_trade_date": "20111230",
                    }
                ]
            )
        )
        conflicts.write_bytes(b"")
        paths = {
            "provider_daily_bars": bars,
            "trade_calendar": calendar,
            "conflicts": conflicts,
        }
        replay_root = canonical_hash(
            [
                {
                    "role": role,
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for role, path in sorted(paths.items())
            ]
        )
        return paths, replay_root

    monkeypatch.setattr(
        "auto_alpha.data.lake.store.free_provider_market_data_evidence."
        "validate_free_provider_backfill",
        lambda _path: {
            "manifest_path": str(capture_manifest),
            "generation_id": "capture-fixture",
            "content_hash": "1" * 64,
            "contract_id": "2" * 64,
            "request_plan_hash": "3" * 64,
            "publication_signature_verified": True,
        },
    )
    monkeypatch.setattr(
        "auto_alpha.data.lake.store.free_provider_market_data_evidence."
        "_replay_market_state_capture_to_directory",
        fake_replay,
    )
    monkeypatch.setattr(
        "auto_alpha.data.lake.store.free_provider_market_data_evidence."
        "_baostock_implementation_root",
        lambda: "f" * 64,
    )
    result = build_daily_bars_evidence(
        capture_manifest,
        tmp_path / "evidence",
        identity_timeline_evidence=identity_manifest,
        spill_root=tmp_path / "spill",
    )

    assert result["pit_axis"]["identity_timeline_binding"] == identity_binding
    assert result["source_binding"]["normalizer_conflict_count"] == 0
    assert result["source_binding"][
        "capture_toolchain_implementation_match"
    ] is True
    assert result["source_binding"]["market_projection_schema_version"] == (
        "market_state_projection_v2"
    )
    assert len(
        result["source_binding"]["market_projection_implementation_root"]
    ) == 64
    assert "daily_bars_streaming_resume_not_implemented" not in result[
        "blockers"
    ]


@pytest.mark.parametrize(
    ("assess", "publish", "validate", "rows_name"),
    (
        (
            "trade_calendar",
            publish_trade_calendar_assessment,
            validate_trade_calendar_evidence,
            "trade_calendar.jsonl",
        ),
        (
            "daily_bars",
            publish_daily_bars_assessment,
            validate_daily_bars_evidence,
            "daily_bars.jsonl",
        ),
    ),
)
def test_market_assessments_publish_idempotent_tamper_evident_generations(
    tmp_path: Path,
    assess: str,
    publish: object,
    validate: object,
    rows_name: str,
) -> None:
    calendar = _jsonl(
        [
            {
                "exchange": "SSE",
                "trade_date": "20120103",
                "is_open": True,
                "prev_trade_date": "20111230",
            }
        ]
    )
    if assess == "trade_calendar":
        assessment = assess_trade_calendar_replay(
            calendar,
            profile=first_data_admission_profile(),
            date_start="20120103",
            date_end="20120103",
            source_binding=_untrusted_source_binding(),
            exchanges=("SSE",),
        )
    else:
        assessment = assess_daily_bars_replay(
            _jsonl(
                [
                    {
                        "date": "2012-01-03",
                        "ts_code": "600000.SH",
                        "open": "10",
                        "high": "12",
                        "low": "9",
                        "close": "11",
                        "preclose": "10",
                        "volume": "1000",
                        "amount": "11000",
                        "tradestatus": "1",
                    }
                ]
            ),
            calendar,
            _jsonl(
                [
                    {
                        "ts_code": "600000.SH",
                        "exchange": "SSE",
                        "list_date": "19991110",
                        "delist_date": None,
                    }
                ]
            ),
            profile=first_data_admission_profile(),
            date_start="20120103",
            date_end="20120103",
            source_binding=_untrusted_source_binding(),
        )

    first = publish(assessment, tmp_path / assess)  # type: ignore[operator]
    second = publish(assessment, tmp_path / assess)  # type: ignore[operator]
    assert first["generation_id"] == second["generation_id"]
    assert validate(first["manifest_path"])["dataset"] == assess  # type: ignore[operator]

    rows = Path(first["manifest_path"]).parent / rows_name
    rows.write_bytes(rows.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match=f"{assess}_evidence_invalid"):
        validate(first["manifest_path"])  # type: ignore[operator]


def _valid_market_assessment(dataset: str) -> market_evidence.MarketDataAssessment:
    calendar = _jsonl(
        [
            {
                "exchange": "SSE",
                "trade_date": "20120103",
                "is_open": True,
                "prev_trade_date": "20111230",
            }
        ]
    )
    if dataset == "trade_calendar":
        return assess_trade_calendar_replay(
            calendar,
            profile=first_data_admission_profile(),
            date_start="20120103",
            date_end="20120103",
            source_binding=_untrusted_source_binding(),
            exchanges=("SSE",),
        )
    return assess_daily_bars_replay(
        _jsonl(
            [
                {
                    "date": "2012-01-03",
                    "ts_code": "600000.SH",
                    "open": "10",
                    "high": "12",
                    "low": "9",
                    "close": "11",
                    "preclose": "10",
                    "volume": "1000",
                    "amount": "11000",
                    "tradestatus": "1",
                }
            ]
        ),
        calendar,
        _jsonl(
            [
                {
                    "ts_code": "600000.SH",
                    "exchange": "SSE",
                    "list_date": "19991110",
                    "delist_date": None,
                }
            ]
        ),
        profile=first_data_admission_profile(),
        date_start="20120103",
        date_end="20120103",
        source_binding=_untrusted_source_binding(),
    )


def _valid_index_market_assessment() -> market_evidence.IndexDailyBarsAssessment:
    return assess_index_daily_bars_replay(
        _jsonl(
            [
                {
                    "amount": "2000",
                    "close": "12",
                    "date": "2012-01-04",
                    "high": "13",
                    "low": "9",
                    "open": "10",
                    "preclose": "9.5",
                    "tradestatus": "1",
                    "ts_code": "000300.SH",
                    "volume": "1000",
                }
            ]
        ),
        _jsonl([{"trade_date": "20120104", "is_open": True}]),
        profile=first_data_admission_profile(),
        date_start="20120104",
        date_end="20120104",
        source_binding={
            "capture_generation_id": "capture-fixture",
            "capture_content_hash": "b" * 64,
            "normalized_replay_root": "c" * 64,
            "calendar_source_sha256": "d" * 64,
            "operator_capture_contract_authorized": False,
            "provider_origin_attested": False,
            "capture_runtime_isolation_verified": False,
        },
    )


@pytest.mark.parametrize(
    "dataset", ("index_daily_bars", "trade_calendar", "daily_bars")
)
@pytest.mark.parametrize(
    "authority_field", ("data_admission_eligible", "alpha_search_authorized")
)
def test_market_validator_rejects_top_level_authority_field_injection(
    tmp_path: Path,
    dataset: str,
    authority_field: str,
) -> None:
    if dataset == "index_daily_bars":
        assessment = _valid_index_market_assessment()
        semantic = json.loads(json.dumps(assessment.semantic))
        semantic[authority_field] = True
        attacked = market_evidence.IndexDailyBarsAssessment(
            semantic=semantic,
            canonical_rows=assessment.canonical_rows,
            validity_rows=assessment.validity_rows,
            coverage_gaps=assessment.coverage_gaps,
            source_replay_rows=assessment.source_replay_rows,
            source_calendar_rows=assessment.source_calendar_rows,
        )
        published = publish_index_daily_bars_assessment(
            attacked, tmp_path / f"{dataset}-{authority_field}"
        )
        with pytest.raises(
            ValueError, match="index_daily_bars_evidence_invalid"
        ):
            validate_index_daily_bars_evidence(published["manifest_path"])
        return

    assessment = _valid_market_assessment(dataset)
    semantic = json.loads(json.dumps(assessment.semantic))
    semantic[authority_field] = True
    attacked = market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
    )
    publish = (
        publish_trade_calendar_assessment
        if dataset == "trade_calendar"
        else publish_daily_bars_assessment
    )
    validate = (
        validate_trade_calendar_evidence
        if dataset == "trade_calendar"
        else validate_daily_bars_evidence
    )
    published = publish(attacked, tmp_path / f"{dataset}-{authority_field}")
    with pytest.raises(ValueError, match=f"{dataset}_evidence_invalid"):
        validate(published["manifest_path"])


@pytest.mark.parametrize(
    ("dataset", "container"),
    (
        ("index_daily_bars", "scope"),
        ("index_daily_bars", "provider_neutral_projection"),
        ("index_daily_bars", "coverage"),
        ("index_daily_bars", "validity"),
        ("index_daily_bars", "consumer_closure"),
        ("index_daily_bars", "safety"),
        ("index_daily_bars", "source_binding"),
        ("trade_calendar", "pit_axis"),
        ("daily_bars", "pit_axis"),
        ("daily_bars", "resource_execution"),
    ),
)
def test_market_validator_rejects_unknown_nested_schema_field(
    tmp_path: Path,
    dataset: str,
    container: str,
) -> None:
    if dataset == "index_daily_bars":
        assessment = _valid_index_market_assessment()
        semantic = json.loads(json.dumps(assessment.semantic))
        semantic[container]["unexpected_schema_field"] = "forbidden"
        attacked = market_evidence.IndexDailyBarsAssessment(
            semantic=semantic,
            canonical_rows=assessment.canonical_rows,
            validity_rows=assessment.validity_rows,
            coverage_gaps=assessment.coverage_gaps,
            source_replay_rows=assessment.source_replay_rows,
            source_calendar_rows=assessment.source_calendar_rows,
        )
        published = publish_index_daily_bars_assessment(
            attacked, tmp_path / f"{dataset}-{container}"
        )
        with pytest.raises(
            ValueError, match="index_daily_bars_evidence_invalid"
        ):
            validate_index_daily_bars_evidence(published["manifest_path"])
        return

    assessment = _valid_market_assessment(dataset)
    semantic = json.loads(json.dumps(assessment.semantic))
    semantic[container]["unexpected_schema_field"] = "forbidden"
    attacked = market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
    )
    publish = (
        publish_trade_calendar_assessment
        if dataset == "trade_calendar"
        else publish_daily_bars_assessment
    )
    validate = (
        validate_trade_calendar_evidence
        if dataset == "trade_calendar"
        else validate_daily_bars_evidence
    )
    published = publish(attacked, tmp_path / f"{dataset}-{container}")
    with pytest.raises(ValueError, match=f"{dataset}_evidence_invalid"):
        validate(published["manifest_path"])


@pytest.mark.parametrize(
    "dataset", ("index_daily_bars", "trade_calendar", "daily_bars")
)
@pytest.mark.parametrize(
    "entry_kind", ("regular_file", "empty_directory", "fifo")
)
def test_market_validator_rejects_non_file_generation_entries(
    tmp_path: Path,
    dataset: str,
    entry_kind: str,
) -> None:
    if dataset == "index_daily_bars":
        published = publish_index_daily_bars_assessment(
            _valid_index_market_assessment(), tmp_path / dataset
        )
        validate = validate_index_daily_bars_evidence
        error = "index_daily_bars_evidence_invalid"
    else:
        assessment = _valid_market_assessment(dataset)
        publish = (
            publish_trade_calendar_assessment
            if dataset == "trade_calendar"
            else publish_daily_bars_assessment
        )
        validate = (
            validate_trade_calendar_evidence
            if dataset == "trade_calendar"
            else validate_daily_bars_evidence
        )
        published = publish(assessment, tmp_path / dataset)
        error = f"{dataset}_evidence_invalid"
    generation = Path(published["manifest_path"]).parent
    unexpected = generation / f"unexpected-{entry_kind}"
    if entry_kind == "regular_file":
        unexpected.write_bytes(b"unexpected")
    elif entry_kind == "empty_directory":
        unexpected.mkdir()
    else:
        os.mkfifo(unexpected)
    with pytest.raises(ValueError, match=error):
        validate(published["manifest_path"])


def _valid_disk_daily_bars_assessment(
    tmp_path: Path,
) -> market_evidence.MarketDataAssessment:
    bars, calendar, identity, identity_binding = (
        _write_daily_bars_resume_fixture(tmp_path / "inputs")
    )
    current_capture_root = market_evidence._baostock_implementation_root()
    normalized_replay_root = canonical_hash(
        sorted(
            [
                {
                    "role": "provider_daily_bars",
                    "sha256": sha256_file(bars),
                    "size_bytes": bars.stat().st_size,
                },
                {
                    "role": "trade_calendar",
                    "sha256": sha256_file(calendar),
                    "size_bytes": calendar.stat().st_size,
                },
                {
                    "role": "conflicts",
                    "sha256": market_evidence.EMPTY_SHA256,
                    "size_bytes": 0,
                },
            ],
            key=lambda row: row["role"],
        )
    )
    source_binding = _untrusted_source_binding() | {
        "capture_manifest_sha256": "1" * 64,
        "capture_adapter_implementation_root": current_capture_root,
        "current_capture_toolchain_implementation_root": current_capture_root,
        "capture_toolchain_implementation_match": True,
        "market_projection_implementation_root": (
            _market_projection_implementation_root()
        ),
        "market_projection_schema_version": "market_state_projection_v2",
        "parser_roles": [
            "provider_daily_bars",
            "trade_calendar",
            "conflicts",
        ],
        "normalized_replay_root": normalized_replay_root,
    }
    output = tmp_path / "output"
    semantic = _run_daily_bars_resume_fixture(
        bars=bars,
        calendar=calendar,
        identity=identity,
        binding=identity_binding,
        output=output,
        spill=tmp_path / "spill",
        source_binding=source_binding,
    )
    return market_evidence.MarketDataAssessment(
        semantic=dict(semantic),
        canonical_rows=(output / "daily_bars.jsonl").read_bytes(),
        validity_rows=(output / "daily_bars_validity.jsonl").read_bytes(),
        coverage_gaps=(
            output / "daily_bars_coverage_gaps.jsonl"
        ).read_bytes(),
        source_archive={
            name: (output / name).read_bytes()
            for name in (
                market_evidence.DAILY_BARS_SOURCE_ROWS_NAME,
                market_evidence.DAILY_BARS_SOURCE_CALENDAR_NAME,
                market_evidence.DAILY_BARS_SOURCE_CONFLICTS_NAME,
                market_evidence.DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
                market_evidence.DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
            )
        },
    )


@pytest.mark.parametrize(
    "drift", ("current_root", "expected_axis", "canonical_ohlc")
)
def test_disk_market_validator_rejects_self_consistently_resigned_source_or_axis_drift(
    tmp_path: Path, drift: str
) -> None:
    assessment = _valid_disk_daily_bars_assessment(tmp_path / "fixture")
    honest = publish_daily_bars_assessment(
        assessment, tmp_path / "honest"
    )
    validated = validate_daily_bars_evidence(honest["manifest_path"])
    assert validated["technical_evidence_status"] == "blocked"
    assert (
        "daily_bars_independent_source_reference_resolution_pending"
        in validated["blockers"]
    )
    semantic = json.loads(json.dumps(assessment.semantic))
    canonical_rows = assessment.canonical_rows
    if drift == "current_root":
        source = semantic["source_binding"]
        source["capture_adapter_implementation_root"] = "0" * 64
        source["current_capture_toolchain_implementation_root"] = "0" * 64
        source["capture_toolchain_implementation_match"] = True
        semantic["resource_execution"]["source_binding_root"] = canonical_hash(
            source
        )
    elif drift == "expected_axis":
        pit_axis = semantic["pit_axis"]
        source = semantic["source_binding"]
        coverage = semantic["coverage"]
        pit_axis["expected_security_day_root"] = "0" * 64
        expected_binding = {
            "date_start": semantic["scope"]["date_start"],
            "date_end": semantic["scope"]["date_end"],
            "expected_security_day_count": coverage[
                "expected_security_day_count"
            ],
            "expected_security_day_root": pit_axis[
                "expected_security_day_root"
            ],
            "exchange_open_dates_root": pit_axis[
                "exchange_open_dates_root"
            ],
            "trade_calendar_replay_sha256": source[
                "trade_calendar_replay_sha256"
            ],
            "identity_timeline_binding_root": source[
                "identity_timeline_binding_root"
            ],
        }
        binding_root = canonical_hash(expected_binding)
        pit_axis["expected_axis_binding_root"] = binding_root
        semantic["resource_execution"][
            "expected_axis_binding_root"
        ] = binding_root
    else:
        rows = [json.loads(line) for line in canonical_rows.splitlines()]
        rows[0].update(
            {
                "open": "99",
                "high": "99",
                "low": "99",
                "close": "99",
                "pre_close": "99",
            }
        )
        canonical_rows = _jsonl(rows)
        projection = semantic["provider_neutral_projection"]
        projection["canonical_rows_sha256"] = market_evidence.hashlib.sha256(
            canonical_rows
        ).hexdigest()
        projection["canonical_rows_size_bytes"] = len(canonical_rows)
        projection["canonical_rows_root"] = projection[
            "canonical_rows_sha256"
        ]
    resigned = market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
        source_archive=assessment.source_archive,
    )
    published = publish_daily_bars_assessment(
        resigned, tmp_path / f"drift-{drift}"
    )
    with pytest.raises(ValueError, match="daily_bars_evidence_invalid"):
        validate_daily_bars_evidence(published["manifest_path"])


def test_disk_market_assessment_cannot_publish_without_complete_source_archive(
    tmp_path: Path,
) -> None:
    assessment = _valid_disk_daily_bars_assessment(tmp_path / "fixture")
    incomplete = dict(assessment.source_archive or {})
    incomplete.pop(market_evidence.DAILY_BARS_SOURCE_ROWS_NAME)
    with pytest.raises(
        ValueError, match="daily_bars_assessment_source_archive_missing"
    ):
        publish_daily_bars_assessment(
            market_evidence.MarketDataAssessment(
                semantic=assessment.semantic,
                canonical_rows=assessment.canonical_rows,
                validity_rows=assessment.validity_rows,
                coverage_gaps=assessment.coverage_gaps,
                source_archive=incomplete,
            ),
            tmp_path / "missing-source",
        )


def _resign_market_assessment(
    assessment: market_evidence.MarketDataAssessment,
    *,
    rows: bytes | None = None,
    validity: bytes | None = None,
    gaps: bytes | None = None,
    semantic_updates: dict[str, object] | None = None,
) -> market_evidence.MarketDataAssessment:
    semantic = json.loads(json.dumps(assessment.semantic))
    canonical_rows = assessment.canonical_rows if rows is None else rows
    validity_rows = assessment.validity_rows if validity is None else validity
    coverage_gaps = assessment.coverage_gaps if gaps is None else gaps
    projection = semantic["provider_neutral_projection"]
    projection["canonical_rows_sha256"] = market_evidence.hashlib.sha256(
        canonical_rows
    ).hexdigest()
    projection["canonical_rows_size_bytes"] = len(canonical_rows)
    projection["canonical_rows_root"] = projection["canonical_rows_sha256"]
    semantic["validity_rows_sha256"] = market_evidence.hashlib.sha256(
        validity_rows
    ).hexdigest()
    semantic["validity_rows_size_bytes"] = len(validity_rows)
    semantic["validity_rows_root"] = semantic["validity_rows_sha256"]
    semantic["coverage_gaps_sha256"] = market_evidence.hashlib.sha256(
        coverage_gaps
    ).hexdigest()
    semantic["coverage_gaps_size_bytes"] = len(coverage_gaps)
    semantic["coverage_gaps_root"] = semantic["coverage_gaps_sha256"]
    semantic.update(semantic_updates or {})
    return market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=canonical_rows,
        validity_rows=validity_rows,
        coverage_gaps=coverage_gaps,
    )


@pytest.mark.parametrize("dataset", ("trade_calendar", "daily_bars"))
@pytest.mark.parametrize("artifact", ("rows", "validity", "gaps"))
def test_market_validator_rejects_deep_semantic_tamper_after_manifest_is_resigned(
    tmp_path: Path, dataset: str, artifact: str
) -> None:
    assessment = _valid_market_assessment(dataset)
    kwargs: dict[str, object] = {}
    semantic_updates: dict[str, object] = {}
    if artifact == "rows":
        row = json.loads(assessment.canonical_rows)
        if dataset == "trade_calendar":
            row["prev_trade_date"] = row["trade_date"]
        else:
            row["high"] = "8"
        kwargs["rows"] = _jsonl([row])
    elif artifact == "validity":
        row = json.loads(assessment.validity_rows)
        row["valid"] = False
        kwargs["validity"] = _jsonl([row])
        validity_summary = dict(assessment.semantic["validity"])
        validity_summary.update(
            {
                "valid_row_count": 0,
                "invalid_row_count": 1,
                "all_required_values_valid": False,
            }
        )
        semantic_updates["validity"] = validity_summary
    else:
        if dataset == "trade_calendar":
            kwargs["gaps"] = _jsonl(
                [
                    {
                        "missing_exchange_days": [
                            {"exchange": "SSE", "trade_date": "20120103"}
                        ],
                        "extra_exchange_days": [],
                        "duplicate_exchange_days": [],
                    }
                ]
            )
            coverage = {
                "expected_exchange_day_count": 1,
                "observed_exchange_day_count": 0,
                "missing_exchange_day_count": 1,
                "extra_exchange_day_count": 0,
                "duplicate_exchange_day_count": 0,
                "provisional_exact_cover": False,
            }
            blocker = "trade_calendar_exchange_day_exact_cover_failed"
        else:
            kwargs["gaps"] = _jsonl(
                [
                    {
                        "ts_code": "600000.SH",
                        "missing_trade_dates": ["20120103"],
                        "extra_trade_dates": [],
                        "duplicate_trade_dates": [],
                    }
                ]
            )
            coverage = {
                "expected_security_day_count": 1,
                "observed_security_day_count": 0,
                "missing_security_day_count": 1,
                "extra_security_day_count": 0,
                "duplicate_security_day_count": 0,
                "provisional_exact_cover": False,
            }
            blocker = "daily_bars_security_day_exact_cover_failed"
        blockers = sorted({*assessment.semantic["blockers"], blocker})
        semantic_updates.update(
            {
                "coverage": coverage,
                "blockers": blockers,
                "technical_evidence_status": "blocked",
            }
        )
    kwargs["semantic_updates"] = semantic_updates
    resigned = _resign_market_assessment(assessment, **kwargs)  # type: ignore[arg-type]
    publish = (
        publish_trade_calendar_assessment
        if dataset == "trade_calendar"
        else publish_daily_bars_assessment
    )
    validate = (
        validate_trade_calendar_evidence
        if dataset == "trade_calendar"
        else validate_daily_bars_evidence
    )
    published = publish(resigned, tmp_path / f"{dataset}-{artifact}")

    with pytest.raises(ValueError, match=f"{dataset}_evidence_invalid"):
        validate(published["manifest_path"])


@pytest.mark.parametrize("dataset", ("trade_calendar", "daily_bars"))
def test_market_validator_accepts_honest_blocked_missing_extra_duplicate_evidence(
    tmp_path: Path, dataset: str
) -> None:
    if dataset == "trade_calendar":
        assessment = assess_trade_calendar_replay(
            _jsonl(
                [
                    {
                        "exchange": "BSE",
                        "trade_date": "20120103",
                        "is_open": True,
                        "prev_trade_date": "20111230",
                    },
                    {
                        "exchange": "BSE",
                        "trade_date": "20120103",
                        "is_open": True,
                        "prev_trade_date": "20111230",
                    },
                ]
            ),
            profile=first_data_admission_profile(),
            date_start="20120103",
            date_end="20120103",
            source_binding=_untrusted_source_binding(),
            exchanges=("SSE",),
        )
        published = publish_trade_calendar_assessment(
            assessment, tmp_path / dataset
        )
        validated = validate_trade_calendar_evidence(published["manifest_path"])
    else:
        row = {
            "date": "2012-01-03",
            "ts_code": "000001.SZ",
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "preclose": "10",
            "volume": "1000",
            "amount": "11000",
            "tradestatus": "1",
        }
        assessment = assess_daily_bars_replay(
            _jsonl([row, row]),
            _jsonl(
                [
                    {
                        "exchange": "SSE",
                        "trade_date": "20120103",
                        "is_open": True,
                        "prev_trade_date": "20111230",
                    }
                ]
            ),
            _jsonl(
                [
                    {
                        "ts_code": "600000.SH",
                        "exchange": "SSE",
                        "list_date": "19991110",
                        "delist_date": None,
                    }
                ]
            ),
            profile=first_data_admission_profile(),
            date_start="20120103",
            date_end="20120103",
            source_binding=_untrusted_source_binding(),
        )
        published = publish_daily_bars_assessment(assessment, tmp_path / dataset)
        validated = validate_daily_bars_evidence(published["manifest_path"])
    assert validated["technical_evidence_status"] == "blocked"
    assert validated["coverage"]["provisional_exact_cover"] is False


@pytest.mark.parametrize("attack", ("false_without_blocker", "true_with_blocker"))
def test_market_validator_recomputes_wire_replay_boolean_and_blocker_bidirectionally(
    tmp_path: Path, attack: str
) -> None:
    assessment = _valid_market_assessment("daily_bars")
    semantic = json.loads(json.dumps(assessment.semantic))
    blockers = set(semantic["blockers"])
    if attack == "false_without_blocker":
        semantic["source_binding"]["wire_replay_verified"] = False
        blockers.discard("daily_bars_signed_wire_replay_unverified")
    else:
        semantic["source_binding"]["wire_replay_verified"] = True
        blockers.add("daily_bars_signed_wire_replay_unverified")
    semantic["blockers"] = sorted(blockers)
    semantic["technical_evidence_status"] = (
        "blocked"
        if blockers & market_evidence._technical_blockers("daily_bars")
        else "verified"
    )
    resigned = market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
    )
    published = publish_daily_bars_assessment(
        resigned, tmp_path / attack
    )
    with pytest.raises(ValueError, match="daily_bars_evidence_invalid"):
        validate_daily_bars_evidence(published["manifest_path"])


@pytest.mark.parametrize(
    "attack",
    (
        "canonical_bool_as_int",
        "canonical_string_as_int",
        "canonical_nullable_string_as_int",
        "count_int_as_bool",
        "semantic_bool_as_int",
    ),
)
def test_trade_calendar_validator_rejects_exact_type_attacks(
    tmp_path: Path, attack: str
) -> None:
    assessment = _valid_market_assessment("trade_calendar")
    rows = assessment.canonical_rows
    semantic_updates: dict[str, object] = {}
    if attack.startswith("canonical_"):
        row = json.loads(rows)
        if attack == "canonical_bool_as_int":
            row["is_open"] = 1
        elif attack == "canonical_string_as_int":
            row["exchange"] = 1
        else:
            row["prev_trade_date"] = 20111230
        rows = _jsonl([row])
    elif attack == "count_int_as_bool":
        coverage = dict(assessment.semantic["coverage"])
        coverage["expected_exchange_day_count"] = True
        semantic_updates["coverage"] = coverage
    else:
        coverage = dict(assessment.semantic["coverage"])
        coverage["provisional_exact_cover"] = 1
        semantic_updates["coverage"] = coverage
    resigned = _resign_market_assessment(
        assessment,
        rows=rows,
        semantic_updates=semantic_updates,
    )
    published = publish_trade_calendar_assessment(
        resigned, tmp_path / attack
    )
    with pytest.raises(ValueError, match="trade_calendar_evidence_invalid"):
        validate_trade_calendar_evidence(published["manifest_path"])


def test_index_validator_rejects_all_ohlc_changed_and_self_resigned(
    tmp_path: Path,
) -> None:
    assessment = assess_index_daily_bars_replay(
        _jsonl(
            [
                {
                    "amount": "2000",
                    "close": "12",
                    "date": "2012-01-04",
                    "high": "13",
                    "low": "9",
                    "open": "10",
                    "preclose": "9.5",
                    "tradestatus": "1",
                    "ts_code": "000300.SH",
                    "volume": "1000",
                }
            ]
        ),
        _jsonl([{"trade_date": "20120104", "is_open": True}]),
        profile=first_data_admission_profile(),
        date_start="20120104",
        date_end="20120104",
        source_binding={
            "capture_generation_id": "capture-fixture",
            "capture_content_hash": "b" * 64,
            "normalized_replay_root": "c" * 64,
            "calendar_source_sha256": "d" * 64,
            "operator_capture_contract_authorized": False,
            "provider_origin_attested": False,
            "capture_runtime_isolation_verified": False,
        },
    )
    falsely_verified_semantic = json.loads(json.dumps(assessment.semantic))
    falsely_verified_semantic["blockers"].remove(
        "index_daily_bars_independent_source_reference_resolution_pending"
    )
    falsely_verified_semantic["technical_evidence_status"] = "verified"
    falsely_verified = market_evidence.IndexDailyBarsAssessment(
        semantic=falsely_verified_semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
        source_replay_rows=assessment.source_replay_rows,
        source_calendar_rows=assessment.source_calendar_rows,
    )
    falsely_verified_generation = publish_index_daily_bars_assessment(
        falsely_verified, tmp_path / "index-falsely-verified"
    )
    with pytest.raises(ValueError, match="index_daily_bars_evidence_invalid"):
        validate_index_daily_bars_evidence(
            falsely_verified_generation["manifest_path"]
        )
    rows = [json.loads(line) for line in assessment.canonical_rows.splitlines()]
    rows[0].update(
        {
            "open": "99",
            "high": "99",
            "low": "99",
            "close": "99",
            "pre_close": "99",
        }
    )
    canonical_rows = _jsonl(rows)
    semantic = json.loads(json.dumps(assessment.semantic))
    projection = semantic["provider_neutral_projection"]
    projection["canonical_rows_sha256"] = market_evidence.hashlib.sha256(
        canonical_rows
    ).hexdigest()
    projection["canonical_rows_size_bytes"] = len(canonical_rows)
    projection["canonical_rows_root"] = canonical_hash(rows)
    resigned = market_evidence.IndexDailyBarsAssessment(
        semantic=semantic,
        canonical_rows=canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
        source_replay_rows=assessment.source_replay_rows,
        source_calendar_rows=assessment.source_calendar_rows,
    )
    published = publish_index_daily_bars_assessment(
        resigned, tmp_path / "index-resigned"
    )
    with pytest.raises(ValueError, match="index_daily_bars_evidence_invalid"):
        validate_index_daily_bars_evidence(published["manifest_path"])


def _resign_disk_conflicts(
    assessment: market_evidence.MarketDataAssessment,
    payload: bytes,
    *,
    declared_count: int,
) -> market_evidence.MarketDataAssessment:
    semantic = json.loads(json.dumps(assessment.semantic))
    archive = dict(assessment.source_archive or {})
    archive[market_evidence.DAILY_BARS_SOURCE_CONFLICTS_NAME] = payload
    source = semantic["source_binding"]
    source["normalizer_conflicts_root"] = market_evidence.hashlib.sha256(
        payload
    ).hexdigest()
    source["normalizer_conflicts_size_bytes"] = len(payload)
    source["normalizer_conflict_count"] = declared_count
    artifacts = [
        {
            "role": role,
            "sha256": market_evidence.hashlib.sha256(archive[name]).hexdigest(),
            "size_bytes": len(archive[name]),
        }
        for role, name in (
            ("provider_daily_bars", market_evidence.DAILY_BARS_SOURCE_ROWS_NAME),
            ("trade_calendar", market_evidence.DAILY_BARS_SOURCE_CALENDAR_NAME),
            ("conflicts", market_evidence.DAILY_BARS_SOURCE_CONFLICTS_NAME),
        )
    ]
    replay_root = canonical_hash(sorted(artifacts, key=lambda row: row["role"]))
    source["normalized_replay_root"] = replay_root
    source["archived_normalized_replay_root"] = replay_root
    blockers = set(semantic["blockers"])
    if declared_count:
        blockers.add("daily_bars_normalization_conflicts_present")
    else:
        blockers.discard("daily_bars_normalization_conflicts_present")
    semantic["blockers"] = sorted(blockers)
    semantic["resource_execution"]["source_binding_root"] = canonical_hash(source)
    return market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
        source_archive=archive,
    )


@pytest.mark.parametrize("attack", ("noncanonical", "count_mismatch"))
def test_disk_market_validator_replays_archived_conflicts_canonically_and_counts(
    tmp_path: Path, attack: str
) -> None:
    assessment = _valid_disk_daily_bars_assessment(tmp_path / "fixture")
    if attack == "noncanonical":
        resigned = _resign_disk_conflicts(
            assessment, b'{"reason": "conflict"}\n', declared_count=1
        )
    else:
        resigned = _resign_disk_conflicts(
            assessment, _jsonl([{"reason": "conflict"}]), declared_count=0
        )
    published = publish_daily_bars_assessment(
        resigned, tmp_path / attack
    )
    with pytest.raises(ValueError, match="daily_bars_evidence_invalid"):
        validate_daily_bars_evidence(published["manifest_path"])


@pytest.mark.parametrize("dataset", ("trade_calendar", "daily_bars"))
def test_market_validator_never_accepts_missing_source_closure_as_verified(
    tmp_path: Path, dataset: str
) -> None:
    assessment = _valid_market_assessment(dataset)
    semantic = json.loads(json.dumps(assessment.semantic))
    blocker = f"{dataset}_independent_source_reference_resolution_pending"
    semantic["blockers"].remove(blocker)
    semantic["technical_evidence_status"] = "verified"
    resigned = market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
    )
    publish = (
        publish_trade_calendar_assessment
        if dataset == "trade_calendar"
        else publish_daily_bars_assessment
    )
    validate = (
        validate_trade_calendar_evidence
        if dataset == "trade_calendar"
        else validate_daily_bars_evidence
    )
    published = publish(resigned, tmp_path / dataset)
    with pytest.raises(ValueError, match=f"{dataset}_evidence_invalid"):
        validate(published["manifest_path"])


@pytest.mark.parametrize(
    "attack",
    (
        "empty_blockers_but_false",
        "blocker_present_but_true",
        "wire_parser_disagree",
        "unbounded_blocker",
        "blockers_not_list",
    ),
)
def test_market_validator_binds_bounded_normalized_replay_state_bidirectionally(
    tmp_path: Path, attack: str
) -> None:
    assessment = _valid_market_assessment("daily_bars")
    semantic = json.loads(json.dumps(assessment.semantic))
    source = semantic["source_binding"]
    blockers = set(semantic["blockers"])
    if attack == "empty_blockers_but_false":
        source["normalized_replay_blockers"] = []
        source["wire_replay_verified"] = False
        source["parser_replay_verified"] = False
        blockers.update(
            {
                "daily_bars_signed_wire_replay_unverified",
                "daily_bars_parser_replay_unverified",
            }
        )
    elif attack == "blocker_present_but_true":
        source["normalized_replay_blockers"] = ["current_parser_replay_failed"]
        source["wire_replay_verified"] = True
        source["parser_replay_verified"] = True
        blockers.discard("daily_bars_signed_wire_replay_unverified")
        blockers.discard("daily_bars_parser_replay_unverified")
    elif attack == "wire_parser_disagree":
        source["normalized_replay_blockers"] = []
        source["wire_replay_verified"] = True
        source["parser_replay_verified"] = False
        blockers.discard("daily_bars_signed_wire_replay_unverified")
        blockers.add("daily_bars_parser_replay_unverified")
    elif attack == "unbounded_blocker":
        source["normalized_replay_blockers"] = ["arbitrary_exception_text"]
        source["wire_replay_verified"] = False
        source["parser_replay_verified"] = False
        blockers.update(
            {
                "daily_bars_signed_wire_replay_unverified",
                "daily_bars_parser_replay_unverified",
            }
        )
    else:
        source["normalized_replay_blockers"] = "current_parser_replay_failed"
        source["wire_replay_verified"] = False
        source["parser_replay_verified"] = False
        blockers.update(
            {
                "daily_bars_signed_wire_replay_unverified",
                "daily_bars_parser_replay_unverified",
            }
        )
    semantic["blockers"] = sorted(blockers)
    resigned = market_evidence.MarketDataAssessment(
        semantic=semantic,
        canonical_rows=assessment.canonical_rows,
        validity_rows=assessment.validity_rows,
        coverage_gaps=assessment.coverage_gaps,
    )
    published = publish_daily_bars_assessment(
        resigned, tmp_path / attack
    )
    with pytest.raises(ValueError, match="daily_bars_evidence_invalid"):
        validate_daily_bars_evidence(published["manifest_path"])


def test_identity_interval_axis_rejects_gaps_and_overlaps() -> None:
    base = {
        "security_id": "entity-600000",
        "security_code": "600000.SH",
        "identity_resolved": True,
        "identity_unique": True,
        "active_on_trade_date": True,
    }
    open_dates = {"SSE": {"20120103", "20120104", "20120105"}}
    for ranges in (
        (("20120103", "20120103"), ("20120105", "20120105")),
        (("20120103", "20120104"), ("20120104", "20120105")),
    ):
        intervals = {
            "entity-600000": [
                dict(base, trade_date_start=start, trade_date_end=end)
                for start, end in ranges
            ]
        }
        assert _identity_interval_axis_valid(
            intervals,
            open_dates=open_dates,
            expected_daily_row_count=3,
        ) is False


def test_market_work_roots_bind_the_complete_module_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection_root = _market_projection_implementation_root()
    resume_root = _daily_bars_resume_implementation_root()
    original = market_evidence.sha256_file

    def drift_module(path: object) -> str:
        if Path(path).resolve() == Path(market_evidence.__file__).resolve():
            return "0" * 64
        return original(path)  # type: ignore[arg-type]

    monkeypatch.setattr(market_evidence, "sha256_file", drift_module)
    assert _market_projection_implementation_root() != projection_root
    assert _daily_bars_resume_implementation_root() != resume_root
