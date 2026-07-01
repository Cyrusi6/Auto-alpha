import json

from data_backfill.planner import build_backfill_plan
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.dataset_registry import (
    EXPANDED_INDEX_CODES,
    FINANCIAL_STATEMENT_DATASETS,
    FULL_RESEARCH_DATASETS,
    INDEX_INDUSTRY_STATUS_DATASETS,
    DATASET_DEFINITIONS,
)
from data_pipeline.ashare.pipeline import ASHARE_DATASETS
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.storage import DATASET_PRIMARY_KEYS, LocalAshareStorage
from data_source_validation.contracts import DATASET_CONTRACTS
from data_source_validation.fake_tushare import FakeTushareHttpClient
from point_in_time.contracts import PIT_DATASET_CONTRACTS
from real_data_ops.profiles import get_real_data_profile


def test_042c_datasets_are_registered_across_governance_layers():
    assert set(FULL_RESEARCH_DATASETS) <= set(ASHARE_DATASETS)

    for dataset in DATASET_DEFINITIONS:
        assert dataset in DATASET_PRIMARY_KEYS
        assert DATASET_PRIMARY_KEYS[dataset]
        assert dataset in DATASET_CONTRACTS
        assert DATASET_CONTRACTS[dataset].api_name == DATASET_DEFINITIONS[dataset].api_name
        assert dataset in PIT_DATASET_CONTRACTS


def test_fake_tushare_supports_all_expanded_api_contracts():
    client = FakeTushareHttpClient("success")

    for dataset, definition in DATASET_DEFINITIONS.items():
        rows = client.post(definition.api_name, fields=definition.field_string)
        assert rows, dataset
        assert set(definition.primary_key) <= set(rows[0]), dataset


def test_generic_tushare_provider_fetches_expanded_datasets_with_fake_client():
    client = FakeTushareHttpClient("success")
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="test-token",
        start_date="20240102",
        end_date="20240104",
        index_codes=("000300.SH", "000905.SH"),
        ts_code="000001.SZ",
    )

    for dataset in (
        "index_daily_bars",
        "industry_classification",
        "income_statements",
        "moneyflow",
        "holder_number",
    ):
        records = provider.fetch_generic_dataset(dataset, config)
        assert records, dataset
        assert set(DATASET_DEFINITIONS[dataset].primary_key) <= set(records[0]), dataset

    index_call = next(call for call in client.calls if call["api_name"] == "index_daily")
    assert index_call["params"]["ts_code"] in {"000300.SH", "000905.SH"}
    income_call = next(call for call in client.calls if call["api_name"] == "income")
    assert income_call["params"]["ts_code"] == "000001.SZ"


def test_expanded_dataset_storage_append_deduplicates(tmp_path):
    storage = LocalAshareStorage(tmp_path)
    records = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "buy_sm_vol": 1.0},
        {"ts_code": "000001.SZ", "trade_date": "20240102", "buy_sm_vol": 2.0},
    ]

    storage.write_dataset("moneyflow", records, mode="append")
    storage.write_dataset("moneyflow", records, mode="append")

    loaded = storage.read_dataset("moneyflow")
    assert len(loaded) == 1
    assert loaded[0]["buy_sm_vol"] == 2.0


def test_real_data_ops_profiles_cover_042c_groups():
    assert set(get_real_data_profile("index_industry_status").datasets) == set(INDEX_INDUSTRY_STATUS_DATASETS)
    assert set(get_real_data_profile("financial_statements").datasets) == set(FINANCIAL_STATEMENT_DATASETS)
    assert set(get_real_data_profile("full_research_data").datasets) == set(FULL_RESEARCH_DATASETS)
    assert set(EXPANDED_INDEX_CODES) <= set(get_real_data_profile("full_research_data").index_codes)


def test_backfill_plan_can_split_financial_statement_datasets_by_ts_code(tmp_path):
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="test-token",
        data_dir=tmp_path,
        start_date="20240101",
        end_date="20240105",
    )

    plan = build_backfill_plan(
        config,
        datasets=["income_statements", "balance_sheets"],
        financial_ts_codes=["000001.SZ", "000002.SZ"],
        ts_code_split_datasets=["income_statements", "balance_sheets"],
    )

    assert plan.job_count == 4
    assert {job.dataset for job in plan.jobs} == {"income_statements", "balance_sheets"}
    assert {job.ts_code for job in plan.jobs} == {"000001.SZ", "000002.SZ"}
    assert json.dumps(plan.to_dict(), ensure_ascii=False)
