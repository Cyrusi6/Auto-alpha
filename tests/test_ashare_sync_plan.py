import json

from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.sync_plan import build_sync_plan, split_date_windows


def test_split_date_windows_by_calendar_days():
    assert split_date_windows("20240101", "20240105", chunk_days=2) == [
        ("20240101", "20240102"),
        ("20240103", "20240104"),
        ("20240105", "20240105"),
    ]


def test_build_sync_plan_stable_and_json_serializable():
    config = AShareDataConfig(
        provider="sample",
        start_date="20240101",
        end_date="20240103",
        index_codes=("000300.SH", "000905.SH"),
    )

    plan_a = build_sync_plan(config, datasets=["daily_bars", "index_members"], chunk_days=1)
    plan_b = build_sync_plan(config, datasets=["daily_bars", "index_members"], chunk_days=1)
    payload = plan_a.to_dict()

    assert plan_a.plan_id == plan_b.plan_id
    assert len([job for job in plan_a.jobs if job.dataset == "daily_bars"]) == 3
    assert len([job for job in plan_a.jobs if job.dataset == "index_members"]) == 6
    assert {job.index_code for job in plan_a.jobs if job.dataset == "index_members"} == {
        "000300.SH",
        "000905.SH",
    }
    assert json.loads(json.dumps(payload))["plan_id"] == plan_a.plan_id
