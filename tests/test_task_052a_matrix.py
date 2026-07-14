from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from artifact_schema.validator import validate_artifact
from data_lake.task052_freeze import (
    GovernedFreezeError,
    create_task052_governed_freeze,
    validate_task052_governed_freeze,
)
from matrix_store.strict_engineering import (
    StrictEngineeringMatrixError,
    StrictEngineeringPITMatrixBuilder,
    StrictEngineeringPITMatrixConfig,
)
from universe.task052 import (
    Task052HistoricalUniverseProofBuilder,
    Task052UniversePolicy,
    Task052UniverseProofError,
)


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_universe_fixture(tmp_path: Path):
    calendar_path = tmp_path / "source" / "trade_calendar.jsonl"
    trade_dates = ["20240102", "20240103", "20240104", "20240201", "20240202", "20240205", "20240206"]
    _write_jsonl(calendar_path, ({"trade_date": date, "is_open": True} for date in trade_dates))

    first_members = [f"S{index:03d}" for index in range(300)]
    second_members = first_members[1:] + ["S300"]
    members_path = tmp_path / "source" / "index_members.jsonl"
    rows = []
    for snapshot_date, members in (("20240102", first_members), ("20240201", second_members)):
        rows.extend(
            {
                "index_code": "000300.SH",
                "trade_date": snapshot_date,
                "ts_code": code,
                "weight": 100.0 / 300.0,
            }
            for code in members
        )
    _write_jsonl(members_path, rows)
    lineage_path = tmp_path / "source" / "source_lineage.json"
    lineage_path.write_text(
        json.dumps(
            {
                "sources": [
                    {"dataset": "index_members", "sha256": _sha256(members_path)},
                    {"dataset": "trade_calendar", "sha256": _sha256(calendar_path)},
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    result = Task052HistoricalUniverseProofBuilder().build(
        members_path,
        calendar_path,
        lineage_path,
        tmp_path / "universe_generations",
    )
    return result, members_path, calendar_path, lineage_path, trade_dates


def test_historical_universe_proof_is_hardened_and_lagged(tmp_path):
    result, _, _, _, trade_dates = _build_universe_fixture(tmp_path)
    root = Path(result.generation_dir)
    proof = json.loads(Path(result.proof_manifest_path).read_text(encoding="utf-8"))
    codes = json.loads((root / "ts_codes.json").read_text(encoding="utf-8"))
    membership = np.load(root / "index_membership.npy", allow_pickle=False)

    assert proof["rejected_snapshot_count"] == 0
    assert proof["member_count"] == 300
    assert proof["all_snapshot_member_counts_exact"] is True
    assert proof["weight_policy_passed"] is True
    assert proof["natural_month_coverage"]["complete"] is True
    assert proof["source_lineage"]["source_lineage_evidence"]["index_members_hash_pinned"] is True
    assert proof["removed_member_leakage"] == {
        "count": 0,
        "examples": [],
        "method": "daily_expected_membership_recomputation",
    }
    assert proof["snapshot_effective_dates"] == {"20240102": "20240103", "20240201": "20240202"}
    assert not membership[codes.index("S000"), trade_dates.index("20240102")]
    assert membership[codes.index("S000"), trade_dates.index("20240201")]
    assert not membership[codes.index("S000"), trade_dates.index("20240202")]
    assert membership[codes.index("S300"), trade_dates.index("20240202")]
    assert validate_artifact(result.proof_manifest_path, strict=True).valid is True


def test_universe_proof_rejects_any_bad_snapshot_and_unpinned_lineage(tmp_path):
    _, members_path, calendar_path, lineage_path, _ = _build_universe_fixture(tmp_path)
    rows = [json.loads(line) for line in members_path.read_text(encoding="utf-8").splitlines()]
    _write_jsonl(members_path, rows[:-1])
    lineage_path.write_text(
        json.dumps(
            {
                "index_members_sha256": _sha256(members_path),
                "trade_calendar_sha256": _sha256(calendar_path),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Task052UniverseProofError, match="rejected_snapshot_count_nonzero"):
        Task052HistoricalUniverseProofBuilder().build(
            members_path,
            calendar_path,
            lineage_path,
            tmp_path / "rejected",
        )

    lineage_path.write_text(json.dumps({"index_members_sha256": _sha256(members_path)}), encoding="utf-8")
    with pytest.raises(Task052UniverseProofError, match="trade_calendar_sha256"):
        Task052HistoricalUniverseProofBuilder().build(
            members_path,
            calendar_path,
            lineage_path,
            tmp_path / "unlinked",
        )


def test_governed_freeze_and_strict_matrix_are_content_addressed(tmp_path):
    universe, _, _, lineage_path, trade_dates = _build_universe_fixture(tmp_path)
    bars_path = tmp_path / "raw" / "daily_bars.jsonl"
    bar_rows = [
        {
            "ts_code": "S001",
            "trade_date": date,
            "open": open_price,
            "high": open_price + 1.0,
            "low": open_price - 1.0,
            "close": open_price + 0.5,
            "volume": 1000.0 + position,
            "amount": 10000.0 + position,
        }
        for position, (date, open_price) in enumerate(
            zip(trade_dates[:4], [10.0, 11.0, 12.0, 13.0], strict=True)
        )
    ]
    _write_jsonl(bars_path, bar_rows)
    adjustment_path = tmp_path / "raw" / "adjustment_factors.jsonl"
    _write_jsonl(
        adjustment_path,
        [
            {"ts_code": "S001", "trade_date": "20240102", "adj_factor": 1.2},
            {"ts_code": "S002", "trade_date": "20240103", "adj_factor": 1.1},
        ],
    )
    freeze = create_task052_governed_freeze(
        {"daily_bars": bars_path, "adjustment_factors": adjustment_path},
        tmp_path / "freeze_generations",
        source_lineage_manifest_path=lineage_path,
    )
    replayed_freeze = create_task052_governed_freeze(
        {"adjustment_factors": adjustment_path, "daily_bars": bars_path},
        tmp_path / "freeze_generations",
        source_lineage_manifest_path=lineage_path,
    )
    assert replayed_freeze.generation_id == freeze.generation_id
    assert validate_task052_governed_freeze(freeze.generation_dir)["checked_artifacts"] == 2
    assert validate_artifact(freeze.manifest_path, strict=True).valid is True

    builder = StrictEngineeringPITMatrixBuilder(StrictEngineeringPITMatrixConfig())
    with pytest.raises(StrictEngineeringMatrixError, match="required governed artifact missing: securities"):
        builder.build(
            governed_freeze_dir=freeze.generation_dir,
            historical_universe_dir=universe.generation_dir,
            output_root=tmp_path / "matrix_generations",
        )
def test_governed_freeze_detects_post_publication_drift(tmp_path):
    universe, _, _, lineage_path, _ = _build_universe_fixture(tmp_path)
    source = tmp_path / "raw" / "daily_bars.jsonl"
    _write_jsonl(
        source,
        [
            {
                "ts_code": "S001",
                "trade_date": "20240102",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
                "amount": 1000.0,
                "adj_factor": 1.0,
            }
        ],
    )
    freeze = create_task052_governed_freeze(
        {"daily_bars": source},
        tmp_path / "freeze_generations",
        source_lineage_manifest_path=lineage_path,
    )
    frozen_path = Path(freeze.generation_dir) / "artifacts" / "daily_bars" / "daily_bars.jsonl"
    frozen_path.chmod(0o644)
    frozen_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(GovernedFreezeError, match="drift"):
        validate_task052_governed_freeze(freeze.generation_dir)
    assert Path(universe.generation_dir).exists()
