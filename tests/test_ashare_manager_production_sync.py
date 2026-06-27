import json
from pathlib import Path

import pytest

from data_pipeline.ashare import AShareDataConfig, AShareDataManager, LocalAshareStorage
from data_pipeline.ashare.schema import DailyBar


def test_manager_use_plan_sample_writes_governance_artifacts(tmp_path):
    config = AShareDataConfig(
        provider="sample",
        data_dir=tmp_path,
        start_date="20240102",
        end_date="20240104",
        index_codes=("000300.SH",),
    )
    result = AShareDataManager(config).sync(
        mode="append",
        validate=True,
        use_plan=True,
        chunk_days=1,
        audit_enabled=True,
        write_stats=True,
        compact_after_sync=True,
        snapshot_after_sync=True,
        snapshot_name="snap_test",
    )
    payload = result.to_dict()

    assert payload["has_errors"] is False
    assert Path(payload["plan_path"]).exists()
    assert Path(payload["stats_path"]).exists()
    assert Path(payload["snapshot_path"]).name == "snap_test"
    assert payload["compaction_summary"]
    for dataset in payload["datasets"]:
        assert Path(dataset["path"]).exists()


def test_manager_resume_skips_successful_jobs(tmp_path):
    config = AShareDataConfig(
        provider="sample",
        data_dir=tmp_path,
        start_date="20240102",
        end_date="20240104",
    )
    manager = AShareDataManager(config)

    first = manager.sync(datasets=["daily_bars"], mode="append", use_plan=True, chunk_days=1)
    second = manager.sync(datasets=["daily_bars"], mode="append", use_plan=True, chunk_days=1, resume=True)

    assert first.datasets[0].records == 6
    assert second.datasets[0].records == 6
    state = json.loads((tmp_path / "pipeline_state.json").read_text(encoding="utf-8"))
    assert len(state["datasets"]["daily_bars"]["successful_job_ids"]) == 3


class FailingProvider:
    def fetch_daily_bars(self, config):
        raise RuntimeError("planned fetch failed")


def test_manager_failed_job_is_recorded_in_state(tmp_path):
    config = AShareDataConfig(
        provider="sample",
        data_dir=tmp_path,
        start_date="20240102",
        end_date="20240102",
    )
    manager = AShareDataManager(config, provider=FailingProvider())

    with pytest.raises(RuntimeError):
        manager.sync(datasets=["daily_bars"], use_plan=True, chunk_days=1)

    state = json.loads((tmp_path / "pipeline_state.json").read_text(encoding="utf-8"))
    dataset_state = state["datasets"]["daily_bars"]
    assert dataset_state["failed_job_ids"]
    assert dataset_state["last_error"] == "planned fetch failed"


def test_sync_result_quality_gate_marks_errors(tmp_path):
    storage = LocalAshareStorage(tmp_path)
    storage.write_dataset(
        "daily_bars",
        [
            DailyBar(
                trade_date="20240102",
                ts_code="000001.SZ",
                open=-1,
                high=1,
                low=2,
                close=1,
                pre_close=1,
                volume=1,
                amount=1,
            )
        ],
    )
    config = AShareDataConfig(provider="sample", data_dir=tmp_path)
    result = AShareDataManager(config).sync(datasets=["securities"], validate=True, fail_on_quality_error=True)

    assert result.has_errors is True
    assert result.quality_summary["quality_gate"] == "failed"
