import json

from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.audit import ApiRequestAuditor
from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.sync_plan import SyncJob


class CountingFakeClient:
    def __init__(self):
        self.calls = []

    def post(self, api_name, params=None, fields=None):
        self.calls.append({"api_name": api_name, "params": dict(params or {}), "fields": fields})
        if api_name == "daily":
            return [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": params["start_date"],
                    "open": 9.0,
                    "high": 9.8,
                    "low": 8.9,
                    "close": 9.5,
                    "pre_close": 9.1,
                    "vol": 1000,
                    "amount": 9500,
                }
            ]
        if api_name == "index_weight":
            return [
                {
                    "index_code": params["index_code"],
                    "con_code": "000001.SZ",
                    "trade_date": params["start_date"],
                    "weight": 0.5,
                }
            ]
        raise AssertionError(api_name)


def test_tushare_fetch_dataset_job_uses_window_params_cache_and_audit(tmp_path):
    client = CountingFakeClient()
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="test-token",
        data_dir=tmp_path,
        start_date="20240101",
        end_date="20240103",
    )
    job = SyncJob(
        job_id="job_daily",
        dataset="daily_bars",
        provider="tushare",
        start_date="20240102",
        end_date="20240102",
    )
    cache = TushareResponseCache(tmp_path)
    auditor = ApiRequestAuditor(tmp_path / "api_audit.jsonl")

    first = provider.fetch_dataset_job(job, config, cache=cache, auditor=auditor)
    second = provider.fetch_dataset_job(job, config, cache=cache, auditor=auditor)

    assert first[0].trade_date == "20240102"
    assert second[0].trade_date == "20240102"
    assert len(client.calls) == 1
    assert client.calls[0]["params"]["start_date"] == "20240102"
    assert client.calls[0]["params"]["end_date"] == "20240102"
    lines = [json.loads(line) for line in (tmp_path / "api_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["cache_hit"] for line in lines] == [False, True]
    assert all(line["status"] == "success" for line in lines)


def test_tushare_fetch_index_member_job_uses_index_code(tmp_path):
    client = CountingFakeClient()
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(provider="tushare", tushare_token="test-token", data_dir=tmp_path)
    job = SyncJob(
        job_id="job_index",
        dataset="index_members",
        provider="tushare",
        start_date="20240102",
        end_date="20240102",
        index_code="000905.SH",
    )

    records = provider.fetch_dataset_job(job, config)

    assert records[0].index_code == "000905.SH"
    assert client.calls[0]["params"]["index_code"] == "000905.SH"
    assert client.calls[0]["params"]["start_date"] == "20240102"
    assert client.calls[0]["params"]["end_date"] == "20240102"
