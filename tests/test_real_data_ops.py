import json

from data_backfill.chunking import PRODUCTION_DAILY_CHUNK_DAYS, dataset_chunk_days_for_strategy
from data_backfill.run_backfill import main as backfill_main
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.rate_limit import RequestRateLimitConfig, SimpleRateLimiter
from real_data_ops.run_real_data import main as real_data_main


FULL_DATASETS = "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions"


def test_rate_limiter_uses_fake_clock_without_sleeping():
    now = {"value": 0.0}
    sleeps: list[float] = []

    def clock() -> float:
        return now["value"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["value"] += seconds

    limiter = SimpleRateLimiter(
        RequestRateLimitConfig(requests_per_minute=120, enabled=True),
        time_func=clock,
        sleep_func=sleep,
    )

    first = limiter.wait("daily")
    second = limiter.wait("daily_basic")

    assert first.waited_seconds == 0
    assert second.waited_seconds == 0.5
    assert sleeps == [0.5]
    assert limiter.summary().rate_limit_event_count == 2


def test_backfill_plan_records_production_chunk_strategy(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "backfill"

    assert backfill_main(
        [
            "plan",
            "--provider",
            "sample",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            "daily_bars,index_members,trade_calendar",
            "--index-codes",
            "000300.SH",
            "--chunk-strategy",
            "production_daily",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    plan = json.loads((output_dir / "backfill_plan.json").read_text(encoding="utf-8"))

    assert payload["job_count"] == len(plan["jobs"])
    assert plan["scope"]["metadata"]["chunk_strategy"] == "production_daily"
    assert plan["scope"]["metadata"]["dataset_chunk_days"]["daily_bars"] == PRODUCTION_DAILY_CHUNK_DAYS["daily_bars"]
    assert dataset_chunk_days_for_strategy("uniform") == {}


def test_real_data_readiness_writes_plan_and_redacts_token(tmp_path, capsys):
    rc = real_data_main(
        [
            "readiness",
            "--profile-name",
            "tushare_online_smoke",
            "--provider",
            "tushare",
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "out"),
            "--datasets",
            "securities,trade_calendar",
            "--index-codes",
            "000300.SH",
            "--max-requests",
            "20",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "planned"
    assert (tmp_path / "out" / "backfill_plan.json").exists()
    assert (tmp_path / "out" / "real_data_readiness_report.json").exists()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "TUSHARE_TOKEN" not in serialized


def test_real_data_sample_smoke_writes_lake_sla_and_matrix(tmp_path, capsys):
    rc = real_data_main(
        [
            "smoke",
            "--data-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "smoke"),
            "--datasets",
            FULL_DATASETS,
            "--index-codes",
            "000300.SH",
            "--chunk-days",
            "2",
            "--cache",
            "--audit",
            "--compact",
            "--snapshot",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] in {"success", "warning"}
    assert (tmp_path / "smoke" / "dataset_version_manifest.json").exists()
    assert (tmp_path / "smoke" / "real_data_sla_report.json").exists()
    assert (tmp_path / "smoke" / "real_data_size_report.json").exists()
    assert (tmp_path / "smoke" / "matrix_refresh" / "matrix_refresh_result.json").exists()
    assert payload["paths"]["real_data_sla_report_path"].endswith("real_data_sla_report.json")


def test_fake_tushare_real_data_run_is_offline(tmp_path, capsys):
    rc = real_data_main(
        [
            "run",
            "--profile-name",
            "fake_tushare_small",
            "--provider",
            "tushare",
            "--fake-tushare-scenario",
            "success",
            "--data-dir",
            str(tmp_path / "fake_data"),
            "--output-dir",
            str(tmp_path / "fake_out"),
            "--datasets",
            "securities,trade_calendar,daily_bars,daily_basic,daily_limits,adjustment_factors,index_members,corporate_actions",
            "--index-codes",
            "000300.SH",
            "--cache",
            "--audit",
            "--validate",
            "--stats",
            "--build-matrix",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] in {"success", "warning"}
    assert (tmp_path / "fake_data" / "api_audit.jsonl").exists()
    assert payload["summary"]["backfill"]["failed_jobs"] == 0
    assert any("matrix_build_skipped_missing_datasets" in item for item in payload["warnings"])


def test_config_reads_tushare_rate_limit_from_env():
    config = AShareDataConfig.from_env({"TUSHARE_TOKEN": "x", "TUSHARE_RATE_LIMIT_PER_MINUTE": "88"})

    assert config.tushare_rate_limit_per_minute == 88
