from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_alpha.data.lake.store.admission import first_data_admission_profile
from auto_alpha.data.lake.store.free_provider_market_data_evidence import (
    assess_index_daily_bars_replay,
    publish_index_daily_bars_assessment,
    validate_index_daily_bars_evidence,
)


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

    assert result.semantic["technical_evidence_status"] == "verified"
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
    ] == "verified"

    rows = Path(first["manifest_path"]).parent / "index_daily_bars.jsonl"
    rows.write_bytes(rows.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="index_daily_bars_evidence_invalid"):
        validate_index_daily_bars_evidence(first["manifest_path"])
