import json
from pathlib import Path

import pytest

from data_pipeline.ashare.cache import TushareCacheSchemaError, TushareResponseCache
from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS
from data_pipeline.ashare.providers.tushare_client import TushareResponseEnvelope
from data_pipeline.ashare.request_normalization import stable_json_hash
from task_052_a.backfill import GovernedBackfillConfig, _run_dataset


FIELDS = DATASET_DEFINITIONS["suspensions"].fields


class FakeClient:
    api_url = "https://api.tushare.pro"

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def post_with_metadata(self, api_name, params=None, fields=None):
        params = dict(params or {})
        self.calls.append((api_name, params))
        key = (api_name, params.get("ts_code"), params.get("start_date"), params.get("end_date"))
        if key not in self.responses:
            raise RuntimeError("network disabled")
        rows, response_fields = self.responses[key]
        return TushareResponseEnvelope(
            api_name=api_name,
            params_without_token=params,
            requested_fields=",".join(fields),
            response_code=0,
            response_message="",
            response_fields=list(response_fields),
            records=list(rows),
            item_count=len(rows),
            duration_seconds=0.01,
            endpoint=self.api_url,
        )


def _config(tmp_path: Path, *, end_date="20260630"):
    union = tmp_path / "union.jsonl"
    securities = tmp_path / "securities.jsonl"
    union.write_text('{"ts_code":"000001.SZ"}\n{"ts_code":"000002.SZ"}\n', encoding="utf-8")
    securities.write_text(
        '{"ts_code":"000001.SZ","list_date":"19910403"}\n'
        '{"ts_code":"000002.SZ","list_date":"19910129"}\n',
        encoding="utf-8",
    )
    return GovernedBackfillConfig(
        union_path=union,
        securities_path=securities,
        output_root=tmp_path / "output",
        observed_end_date=end_date,
        datasets=("suspensions",),
    )


def _seed_cache(cache: TushareResponseCache, end_date="20260630"):
    positive_params = {"ts_code": "000001.SZ", "start_date": "20150101", "end_date": end_date}
    empty_params = {"ts_code": "000002.SZ", "start_date": "20150101", "end_date": end_date}
    cache.write(
        "suspend_d",
        positive_params,
        FIELDS,
        [{"ts_code": "000001.SZ", "trade_date": "20240102", "suspend_timing": None, "suspend_type": "S"}],
        response_fields=FIELDS,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
    )
    empty = cache.write(
        "suspend_d",
        empty_params,
        FIELDS,
        [],
        response_fields=FIELDS,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
    )
    return positive_params, empty_params, empty


def test_legacy_empty_cache_requires_matching_positive_endpoint_schema_proof(tmp_path: Path):
    cache = TushareResponseCache(tmp_path)
    _, empty_params, empty_path = _seed_cache(cache)
    payload = json.loads(empty_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "tushare_cache_envelope.v2"
    payload.pop("source_code_hash")
    payload.pop("provider")
    payload["response"].pop("fields_observed")
    payload["negative_attestation"].pop("response_fields_observed")
    empty_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TushareCacheSchemaError, match="endpoint schema proof"):
        cache.read("suspend_d", empty_params, FIELDS)

    proof = cache.build_endpoint_schema_proof("suspend_d", FIELDS)
    result = cache.read("suspend_d", empty_params, FIELDS, endpoint_schema_proof=proof, allow_legacy_source_semantics=True)
    assert result and result.hit and result.records == []
    assert proof and proof["proof_hash"] == stable_json_hash({key: value for key, value in proof.items() if key != "proof_hash"})


def test_governed_generation_preserves_null_and_validates_resume_evidence(tmp_path: Path):
    config = _config(tmp_path)
    cache = TushareResponseCache(config.output_root / "request_cache")
    _seed_cache(cache)
    client = FakeClient()
    list_dates = {"000001.SZ": "19910403", "000002.SZ": "19910129"}

    first = _run_dataset("suspensions", ["000001.SZ", "000002.SZ"], list_dates, config, client, cache)
    assert first["pointer_updated"] is True
    assert first["covered_stock_count"] == 2
    assert client.calls == []
    records = [json.loads(line) for line in (Path(first["generation_path"]) / "records.jsonl").read_text().splitlines()]
    assert records[0]["suspend_timing"] is None
    assert records[0]["timing_parse_status"] == "raw_null"
    assert records[0]["canonical_interval"] is None
    assert records[0]["conservative_open_excluded"] is True

    stock = config.output_root / "staging" / "suspensions" / "stocks" / "000001.SZ.jsonl"
    stock.write_text(stock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    replay = _run_dataset("suspensions", ["000001.SZ", "000002.SZ"], list_dates, config, client, cache)
    assert replay["pointer_updated"] is True
    assert any(item["ts_code"] == "000001.SZ" and "SHA mismatch" in item["reason"] for item in replay["resume_misses"])
    assert client.calls == []


def test_incomplete_generation_never_updates_current_pointer(tmp_path: Path):
    config = _config(tmp_path)
    cache = TushareResponseCache(config.output_root / "request_cache")
    _seed_cache(cache)
    list_dates = {"000001.SZ": "19910403", "000002.SZ": "19910129"}
    first = _run_dataset("suspensions", ["000001.SZ", "000002.SZ"], list_dates, config, FakeClient(), cache)
    pointer_path = config.output_root / "current_suspensions.json"
    pointer_before = pointer_path.read_bytes()

    changed = _config(tmp_path, end_date="20260701")
    blocked = _run_dataset("suspensions", ["000001.SZ", "000002.SZ"], list_dates, changed, FakeClient(), cache)
    assert blocked["pointer_updated"] is False
    assert blocked["covered_stock_count"] == 0
    assert blocked["failure_count"] == 2
    assert pointer_path.read_bytes() == pointer_before
    manifest = json.loads((Path(blocked["generation_path"]) / "coverage_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "incomplete"
