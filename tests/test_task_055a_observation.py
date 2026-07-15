import json
from pathlib import Path

import pytest

from task_055_a.contracts import CONTAMINATED_END_DATE, CONTAMINATED_START_DATE
from task_055_a.observation import (
    publish_observation_boundary_seal,
    recompute_observation_boundary,
    validate_observation_boundary_seal,
)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path, *, trading_dates: list[str]) -> tuple[Path, Path]:
    metadata = tmp_path / "metadata"
    state = tmp_path / "state"
    _write_json(
        metadata / "strict_matrix_manifest.json",
        {
            "max_legal_signal_date": "20260626",
            "source": {"observed_end_date": "20260630"},
            "max_observed_target_date": "20260630",
            "partitions": [
                {
                    "partition_id": "daily/202606",
                    "first_seen": "2026-07-01T09:00:00+08:00",
                    "acquired_at": "2026-07-01T09:05:00+08:00",
                    "content_hash": "a" * 64,
                    "revision": "r3",
                }
            ],
        },
    )
    _write_json(metadata / "trade_calendar_raw_index.json", {"trade_dates": trading_dates})
    _write_jsonl(
        metadata / "observation_ledger.jsonl",
        [{"source_endpoint_date": "20260627"}, {"signal_endpoint_date": "20260625"}],
    )
    (metadata / "feature_tensor.npy").write_bytes(b"not a real numpy file")
    _write_jsonl(metadata / "daily_bars" / "records.jsonl", [{"trade_date": "20991231"}])
    _write_jsonl(state / "certification_queue.jsonl", [{"id": "q1"}, {}, {"id": "q2"}])
    _write_json(state / "model_registry.json", [{"id": "m1"}, {"id": "m2"}])
    _write_jsonl(state / "portfolio_store.jsonl", [])
    return metadata, state


def test_metadata_only_scan_recomputes_endpoints_lineage_and_physical_counts(tmp_path: Path):
    metadata, state = _fixture(tmp_path, trading_dates=["20260630", "20260716", "20260717"])

    observation = recompute_observation_boundary(roots=[metadata], state_roots=[state])

    assert observation["max_observed_signal_endpoint"] == "20260626"
    assert observation["max_observed_source_endpoint"] == "20260630"
    assert observation["max_observed_target_endpoint"] == "20260630"
    assert observation["max_observed_endpoint"] == "20260630"
    assert observation["partition_lineage"] == [
        {
            "partition_id": "daily/202606",
            "source_path": str((metadata / "strict_matrix_manifest.json").resolve()),
            "first_seen": "2026-07-01T09:00:00+08:00",
            "acquired_at": "2026-07-01T09:05:00+08:00",
            "content_hash": "a" * 64,
            "revision": "r3",
        }
    ]
    assert observation["physical_nonempty_record_count"] == 4
    assert all(not row["path"].endswith(".npy") for row in observation["metadata_files"])
    assert all(not row["path"].endswith("records.jsonl") for row in observation["metadata_files"])
    assert observation["contaminated_period"] == {
        "start_date": CONTAMINATED_START_DATE,
        "end_date": CONTAMINATED_END_DATE,
        "status": "contaminated",
        "clean_holdout": False,
    }


def test_content_addressed_seal_uses_first_provable_post_seal_trade_date(tmp_path: Path):
    metadata, state = _fixture(tmp_path, trading_dates=["20260630", "20260715", "20260716", "20260717"])
    observation = recompute_observation_boundary(roots=[metadata], state_roots=[state])

    sealed = publish_observation_boundary_seal(
        observation,
        output_dir=tmp_path / "seals",
        effective_at="2026-07-15T12:00:00+08:00",
    )

    assert Path(sealed["seal_path"]).name == f"{sealed['content_hash']}.json"
    assert sealed["effective_timezone"] == "Asia/Shanghai"
    assert sealed["prospective_holdout"]["earliest_holdout_trade_date"] == "20260716"
    assert sealed["prospective_holdout"]["strictly_after_max_endpoint"] is True
    assert validate_observation_boundary_seal(sealed["seal_path"])["content_hash"] == sealed["content_hash"]
    repeated = publish_observation_boundary_seal(
        observation,
        output_dir=tmp_path / "seals",
        effective_at="2026-07-15T12:00:00+08:00",
    )
    assert repeated["seal_path"] == sealed["seal_path"]
    assert len(list((tmp_path / "seals").glob("*.json"))) == 1


def test_seal_waits_for_future_data_and_validator_detects_source_change(tmp_path: Path):
    metadata, state = _fixture(tmp_path, trading_dates=["20260629", "20260630"])
    sealed = publish_observation_boundary_seal(
        output_dir=tmp_path / "seals",
        roots=[metadata],
        state_roots=[state],
        effective_at="2026-07-15T08:30:00+08:00",
    )

    assert sealed["status"] == "sealed_waiting_for_future_data"
    assert sealed["prospective_holdout"] == {
        "status": "waiting_for_future_data",
        "earliest_holdout_trade_date": None,
        "earliest_prospective_holdout_date": None,
        "strictly_after_max_endpoint": True,
        "strictly_after_seal_effective_date": True,
        "reason": "waiting_for_future_added_data_no_next_provable_trading_day",
    }
    queue = state / "certification_queue.jsonl"
    queue.write_text(queue.read_text(encoding="utf-8") + json.dumps({"id": "late"}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="source_file_changed"):
        validate_observation_boundary_seal(sealed["seal_path"])


def test_validator_rejects_seal_tampering(tmp_path: Path):
    metadata, state = _fixture(tmp_path, trading_dates=["20260716"])
    sealed = publish_observation_boundary_seal(
        output_dir=tmp_path / "seals",
        roots=[metadata],
        state_roots=[state],
        effective_at="2026-07-15T09:00:00+08:00",
    )
    seal_path = Path(sealed["seal_path"])
    payload = json.loads(seal_path.read_text(encoding="utf-8"))
    payload["prospective_holdout"]["earliest_holdout_trade_date"] = "20260715"
    seal_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="seal_hash_invalid"):
        validate_observation_boundary_seal(seal_path, rescan=False)
