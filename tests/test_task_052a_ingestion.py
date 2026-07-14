import json
from pathlib import Path

import pytest

from data_backfill.executor import execute_backfill_plan
from data_backfill.planner import build_backfill_plan
from data_backfill.staging import atomic_publish_staging, write_staging_records
from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.cache import (
    TushareCacheCorruptionError,
    TushareCacheSchemaError,
    TushareResponseCache,
)
from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS, FULL_RESEARCH_DATASETS
from data_pipeline.ashare.request_normalization import (
    normalize_tushare_request,
    tushare_code_semantic_hash,
    tushare_request_fingerprint,
)
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.providers.tushare_client import TushareHttpClient, TushareSchemaError


class GenericClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def post(self, api_name, params=None, fields=None):
        self.calls.append({"api_name": api_name, "params": dict(params or {}), "fields": fields})
        return list(self.responses.get(api_name, []))


class BackfillProvider:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0
        self.last_job_metrics = {}

    def fetch_dataset_job(self, job, config, cache=None, auditor=None):
        self.calls += 1
        self.last_job_metrics = {
            "fetched": len(self.rows),
            "rejected": 0,
            "request_fingerprints": [f"fp-{self.calls}"],
            "negative_attestations": [] if self.rows else [{"assertion": "provider_returned_zero_rows", "item_count": 0}],
        }
        return list(self.rows)


def test_task_052a_registry_uses_canonical_status_contracts():
    suspensions = DATASET_DEFINITIONS["suspensions"]
    assert suspensions.api_name == "suspend_d"
    assert suspensions.fields == ("ts_code", "trade_date", "suspend_timing", "suspend_type")
    assert suspensions.primary_key == ("ts_code", "trade_date", "suspend_type")
    assert suspensions.single_date_param == "trade_date"

    stock_st = DATASET_DEFINITIONS["st_status_daily"]
    assert stock_st.api_name == "stock_st"
    assert stock_st.fields == ("ts_code", "name", "trade_date", "type", "type_name")
    assert "st_status_daily" in FULL_RESEARCH_DATASETS

    namechange = DATASET_DEFINITIONS["name_changes"]
    assert namechange.api_name == "namechange"
    assert namechange.fields == ("ts_code", "name", "start_date", "end_date", "ann_date", "change_reason")


def test_request_normalization_fingerprint_and_code_semantic_hash_are_stable():
    first = normalize_tushare_request(" suspend_d ", {"trade_date": " 20240102 ", "x": 1}, "ts_code, trade_date,ts_code")
    second = normalize_tushare_request("suspend_d", {"x": 1, "trade_date": "20240102"}, ["ts_code", "trade_date"])
    assert first == second
    assert tushare_request_fingerprint("suspend_d", first["params"], first["fields"]) == tushare_request_fingerprint(
        "suspend_d", second["params"], second["fields"]
    )
    assert len(tushare_code_semantic_hash()) == 64
    assert "token" not in json.dumps(first)


def test_cache_envelope_negative_attestation_and_fail_closed_validation(tmp_path: Path):
    cache = TushareResponseCache(tmp_path)
    path = cache.write(
        "suspend_d",
        {"trade_date": "20240102"},
        "ts_code,trade_date,suspend_timing,suspend_type",
        [],
        response_fields=["ts_code", "trade_date", "suspend_timing", "suspend_type"],
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["request_fingerprint"]
    assert payload["code_semantic_hash"] == tushare_code_semantic_hash()
    assert payload["negative_attestation"]["assertion"] == "provider_returned_zero_rows"
    assert cache.read("suspend_d", {"trade_date": "20240102"}, "ts_code,trade_date,suspend_timing,suspend_type").hit

    payload["response"]["item_count"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TushareCacheCorruptionError, match="truncated"):
        cache.read("suspend_d", {"trade_date": "20240102"}, "ts_code,trade_date,suspend_timing,suspend_type")

    cache.write("suspend_d", {"trade_date": "20240102"}, "ts_code,trade_date,suspend_timing,suspend_type", [])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["code_semantic_hash"] = "obsolete"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TushareCacheSchemaError, match="semantic hash"):
        cache.read("suspend_d", {"trade_date": "20240102"}, "ts_code,trade_date,suspend_timing,suspend_type")

    path.write_text('{"schema_version":', encoding="utf-8")
    with pytest.raises(TushareCacheCorruptionError, match="unreadable"):
        cache.read("suspend_d", {"trade_date": "20240102"}, "ts_code,trade_date,suspend_timing,suspend_type")


def test_tushare_response_row_width_is_fail_closed():
    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"code": 0, "data": {"fields": ["ts_code", "trade_date"], "items": [["000001.SZ"]]}}).encode()

    config = AShareDataConfig(provider="tushare", tushare_token="token")
    client = TushareHttpClient(config, urlopen=lambda *args, **kwargs: Response())
    with pytest.raises(TushareSchemaError, match="row width"):
        client.post("suspend_d", {"trade_date": "20240102"}, "ts_code,trade_date")


def test_provider_normalizes_suspensions_and_stock_st_without_legacy_fields():
    client = GenericClient(
        {
            "suspend_d": [
                {"ts_code": "000001.SZ", "trade_date": "20240102", "suspend_timing": "09:30", "suspend_type": "s"},
                {"ts_code": "000002.SZ", "trade_date": "20240102", "suspend_timing": None, "suspend_type": "X"},
            ],
            "stock_st": [{"ts_code": "000001.SZ", "name": "ST样例", "trade_date": "20240102", "type": "S", "type_name": "ST"}],
        }
    )
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(provider="tushare", tushare_token="token", start_date="20240102", end_date="20240102")

    suspensions = provider.fetch_generic_dataset("suspensions", config)
    stock_st = provider.fetch_generic_dataset("st_status_daily", config)

    assert suspensions == [{"ts_code": "000001.SZ", "trade_date": "20240102", "suspend_timing": "09:30", "suspend_type": "S"}]
    assert set(stock_st[0]) == set(DATASET_DEFINITIONS["st_status_daily"].fields)
    assert client.calls[0]["params"] == {"trade_date": "20240102"}


def test_atomic_publish_metrics_negative_attestation_and_resume_miss(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "run"
    staging_dir = tmp_path / "staging"
    config = AShareDataConfig(provider="sample", data_dir=data_dir, start_date="20240102", end_date="20240102")
    plan = build_backfill_plan(config, datasets=["suspensions"], chunk_days=1)
    rows = [{"ts_code": "000001.SZ", "trade_date": "20240102", "suspend_timing": "09:30", "suspend_type": "S"}]
    provider = BackfillProvider(rows)
    monkeypatch.setattr("data_backfill.executor._provider", lambda *args, **kwargs: provider)

    first = execute_backfill_plan(plan, config, data_dir, output_dir, staging_dir=staging_dir)
    job = first.jobs[0]
    assert (job.requested, job.fetched, job.written, job.dedup, job.rejected, job.dataset_total) == (1, 1, 1, 0, 0, 1)
    assert job.publish_receipt_path and Path(job.publish_receipt_path).exists()
    assert first.summary["dataset_total"] == {"suspensions": 1}

    Path(job.publish_receipt_path).unlink()
    resumed = execute_backfill_plan(plan, config, data_dir, output_dir, staging_dir=staging_dir, resume=True)
    assert provider.calls == 2
    assert resumed.jobs[0].metadata["resume_miss"] is True
    assert resumed.jobs[0].written == 0
    assert resumed.jobs[0].dedup == 1

    empty_provider = BackfillProvider([])
    monkeypatch.setattr("data_backfill.executor._provider", lambda *args, **kwargs: empty_provider)
    empty_dir = tmp_path / "empty"
    empty_report = execute_backfill_plan(plan, config, empty_dir / "data", empty_dir / "run", staging_dir=empty_dir / "staging")
    empty_job = empty_report.jobs[0]
    assert empty_job.fetched == 0
    assert empty_job.negative_attestation_path
    assert Path(empty_job.negative_attestation_path).exists()


def test_atomic_publish_preserves_existing_dataset_when_replace_fails(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    target = data_dir / "suspensions" / "records.jsonl"
    target.parent.mkdir(parents=True)
    original = {"ts_code": "000001.SZ", "trade_date": "20240102", "suspend_timing": "09:30", "suspend_type": "S"}
    target.write_text(json.dumps(original) + "\n", encoding="utf-8")
    config = AShareDataConfig(provider="sample", start_date="20240103", end_date="20240103")
    job = build_backfill_plan(config, datasets=["suspensions"], chunk_days=1).jobs[0]
    staging_path, _, _ = write_staging_records(
        tmp_path / "staging",
        job,
        [{"ts_code": "000002.SZ", "trade_date": "20240103", "suspend_timing": None, "suspend_type": "R"}],
    )
    monkeypatch.setattr("data_backfill.staging.os.replace", lambda *args: (_ for _ in ()).throw(OSError("publish failed")))
    with pytest.raises(OSError, match="publish failed"):
        atomic_publish_staging(data_dir, staging_path, job)
    assert target.read_text(encoding="utf-8") == json.dumps(original) + "\n"
