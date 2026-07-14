from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from data_lake.task052_freeze import create_task052_governed_freeze, validate_task052_governed_freeze
from matrix_store.strict_engineering import StrictEngineeringPITMatrixBuilder, StrictEngineeringPITMatrixConfig
from universe.task052 import Task052HistoricalUniverseProofBuilder


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path):
    dates = ["20240102", "20240103", "20240104", "20240105", "20240108"]
    codes = [f"S{index:03d}" for index in range(300)]
    source = tmp_path / "source"
    calendar = source / "trade_calendar.jsonl"
    members = source / "index_members.jsonl"
    _write_jsonl(calendar, ({"trade_date": date, "is_open": True} for date in dates))
    snapshot_rows = []
    for snapshot_date in (dates[0], dates[-1]):
        snapshot_rows.extend(
            {
                "index_code": "000300.SH",
                "trade_date": snapshot_date,
                "ts_code": code,
                "weight": 100.0 / 300.0,
            }
            for code in codes
        )
    _write_jsonl(members, snapshot_rows)
    lineage = source / "lineage.json"
    lineage.write_text(
        json.dumps(
            {
                "index_members_sha256": _sha256(members),
                "trade_calendar_sha256": _sha256(calendar),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    universe = Task052HistoricalUniverseProofBuilder().build(
        members, calendar, lineage, tmp_path / "universe"
    )

    bars = source / "daily_bars.jsonl"
    adjustments = source / "adjustment_factors.jsonl"
    limits = source / "daily_limits.jsonl"
    securities = source / "securities.jsonl"
    suspensions = source / "suspensions.jsonl"
    suspension_coverage = source / "suspension_coverage.jsonl"
    stock_st = source / "stock_st.jsonl"
    stock_st_coverage = source / "stock_st_coverage.jsonl"
    bar_rows = []
    adjustment_rows = []
    limit_rows = []
    for stock_number, code in enumerate(codes):
        for date_number, date in enumerate(dates):
            raw_open = 10.0 + stock_number / 1000.0 + date_number
            bar_rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "open": raw_open,
                    "high": raw_open + 1.0,
                    "low": raw_open - 1.0,
                    "close": raw_open + 0.5,
                    "pre_close": raw_open - 0.5,
                    "vol": 1000.0 + date_number,
                    "amount": 10000.0 + date_number,
                }
            )
            adjustment_rows.append(
                {"ts_code": code, "trade_date": date, "adj_factor": 1.0 + date_number * 0.01}
            )
            limit_rows.append(
                {
                    "ts_code": code,
                    "trade_date": date,
                    "up_limit": raw_open + 2.0,
                    "down_limit": raw_open - 2.0,
                }
            )
    _write_jsonl(bars, bar_rows)
    _write_jsonl(adjustments, adjustment_rows)
    _write_jsonl(limits, limit_rows)
    _write_jsonl(
        securities,
        ({"ts_code": code, "list_date": "20200101", "delist_date": None} for code in codes),
    )
    _write_jsonl(
        suspensions,
        [
            {
                "ts_code": "S000",
                "trade_date": dates[2],
                "suspend_type": "S",
                "suspend_timing": None,
            }
        ],
    )
    coverage_rows = [
        {"ts_code": code, "start_date": dates[0], "end_date": dates[-1], "validated": True}
        for code in codes
    ]
    _write_jsonl(suspension_coverage, coverage_rows)
    _write_jsonl(stock_st, [])
    _write_jsonl(stock_st_coverage, coverage_rows)
    artifacts = {
        "daily_bars": bars,
        "adjustment_factors": adjustments,
        "daily_limits": limits,
        "securities": securities,
        "suspensions": suspensions,
        "suspension_coverage_ledger": suspension_coverage,
        "stock_st": stock_st,
        "stock_st_coverage_ledger": stock_st_coverage,
    }
    freeze = create_task052_governed_freeze(
        artifacts, tmp_path / "freeze", source_lineage_manifest_path=lineage
    )
    return dates, codes, universe, freeze, artifacts, lineage


def test_terminal_snapshot_is_retained_but_not_effective(tmp_path):
    dates, _, universe, _, _, _ = _fixture(tmp_path)
    proof = json.loads(Path(universe.proof_manifest_path).read_text(encoding="utf-8"))
    assert proof["snapshot_effective_dates"] == {dates[0]: dates[1]}
    assert proof["snapshots_not_effective_within_axis"] == [dates[-1]]
    snapshot_rows = [
        json.loads(line)
        for line in (Path(universe.generation_dir) / "accepted_index_snapshots.jsonl").read_text().splitlines()
    ]
    terminal = [row for row in snapshot_rows if row["snapshot_date"] == dates[-1]]
    assert len(terminal) == 300
    assert {row["effective_status"] for row in terminal} == {"not_effective_within_axis"}
    assert {row["effective_trade_date"] for row in terminal} == {None}


def test_conservative_event_day_masks_target_and_preserves_signal_separation(tmp_path):
    dates, codes, universe, freeze, _, _ = _fixture(tmp_path)
    builder = StrictEngineeringPITMatrixBuilder(
        StrictEngineeringPITMatrixConfig(min_cross_section_breadth=30, research_observable_cutoff=dates[-1])
    )
    result = builder.build(
        governed_freeze_dir=freeze.generation_dir,
        historical_universe_dir=universe.generation_dir,
        output_root=tmp_path / "matrix",
    )
    root = Path(result.generation_dir)
    stock = codes.index("S000")
    event_date = dates.index("20240104")
    event_present = np.load(root / "suspension_event_present.npy", allow_pickle=False)
    timing_known = np.load(root / "suspension_timing_known.npy", allow_pickle=False)
    conservative = np.load(root / "conservative_open_excluded.npy", allow_pickle=False)
    execution = np.load(root / "open_execution_value.npy", allow_pickle=False)
    signal = np.load(root / "signal_eligible_at_close.npy", allow_pickle=False)
    target_available = np.load(root / "target_available.npy", allow_pickle=False)
    volume = np.load(root / "volume.npy", allow_pickle=False)
    volume_validity = np.load(root / "volume_validity.npy", allow_pickle=False)

    assert event_present[stock, event_date]
    assert not timing_known[stock, event_date]
    assert conservative[stock, event_date]
    assert not execution[stock, event_date]
    assert signal[stock, event_date - 1]
    assert not target_available[stock, event_date - 1]
    assert not target_available[stock, event_date - 2]
    assert volume_validity[stock, event_date]
    assert volume[stock, event_date] == 1002.0

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    readiness = json.loads(Path(result.readiness_path).read_text(encoding="utf-8"))
    assert manifest["suspension_policy"]["name"] == "conservative_event_day_open_exclusion_v1"
    assert manifest["source_timing_semantics_certified"] is False
    assert manifest["intraday_simulation_supported"] is False
    assert manifest["universe_mode"] == "daily_lagged_historical_constituents"
    assert readiness["strict_matrix_built"] is True
    assert readiness["strict_matrix_replay_safe"] is True
    assert readiness["certification_ready"] is False


def test_adjusted_target_and_content_hash_are_deterministic(tmp_path):
    dates, codes, universe, freeze, artifacts, lineage = _fixture(tmp_path)
    builder = StrictEngineeringPITMatrixBuilder(
        StrictEngineeringPITMatrixConfig(min_cross_section_breadth=30, research_observable_cutoff=dates[-1])
    )
    first = builder.build(
        governed_freeze_dir=freeze.generation_dir,
        historical_universe_dir=universe.generation_dir,
        output_root=tmp_path / "matrix_a",
    )
    second_freeze = create_task052_governed_freeze(
        artifacts, tmp_path / "freeze_b", source_lineage_manifest_path=lineage
    )
    second = builder.build(
        governed_freeze_dir=second_freeze.generation_dir,
        historical_universe_dir=universe.generation_dir,
        output_root=tmp_path / "matrix_b",
    )
    first_root = Path(first.generation_dir)
    second_root = Path(second.generation_dir)
    stock = codes.index("S001")
    target = np.load(first_root / "target_open_t1_t2.npy", allow_pickle=False)
    target_available = np.load(first_root / "target_available.npy", allow_pickle=False)
    adjusted = np.load(first_root / "adjusted_open.npy", allow_pickle=False)
    expected = adjusted[stock, 3] / adjusted[stock, 2] - 1.0
    assert target_available[stock, 1]
    assert target[stock, 1] == np.float32(expected)
    assert first.content_hash == second.content_hash
    first_manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))
    second_manifest = json.loads(Path(second.manifest_path).read_text(encoding="utf-8"))
    assert first_manifest["partition_sha256"] == second_manifest["partition_sha256"]
    assert validate_task052_governed_freeze(freeze.generation_dir)["valid"] is True


def test_unexplained_gap_is_never_used_by_signal_or_target(tmp_path):
    dates, codes, universe, _, artifacts, lineage = _fixture(tmp_path)
    bars = artifacts["daily_bars"]
    rows = [json.loads(line) for line in bars.read_text(encoding="utf-8").splitlines()]
    rows = [row for row in rows if not (row["ts_code"] == "S001" and row["trade_date"] == dates[2])]
    _write_jsonl(bars, rows)
    freeze = create_task052_governed_freeze(
        artifacts, tmp_path / "freeze_gap", source_lineage_manifest_path=lineage
    )
    result = StrictEngineeringPITMatrixBuilder(
        StrictEngineeringPITMatrixConfig(min_cross_section_breadth=30)
    ).build(
        governed_freeze_dir=freeze.generation_dir,
        historical_universe_dir=universe.generation_dir,
        output_root=tmp_path / "matrix_gap",
    )
    root = Path(result.generation_dir)
    stock = codes.index("S001")
    gap_date = dates.index(dates[2])
    gap = np.load(root / "unexplained_data_gap.npy", allow_pickle=False)
    signal = np.load(root / "signal_eligible_at_close.npy", allow_pickle=False)
    target = np.load(root / "target_available.npy", allow_pickle=False)
    readiness = json.loads(Path(result.readiness_path).read_text(encoding="utf-8"))
    assert gap[stock, gap_date]
    assert not signal[stock, gap_date]
    assert not target[stock, gap_date - 1]
    assert not target[stock, gap_date - 2]
    assert any(item.startswith("localized_unexplained_data_gap:") for item in readiness["quality_warnings"])
    assert readiness["strict_matrix_replay_safe"] is True
