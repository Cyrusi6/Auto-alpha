import json

from data_pipeline.ashare import AShareDataConfig, build_pipeline_plan
from data_pipeline.ashare.dataset_registry import FULL_RESEARCH_DATASETS

EXPECTED_CORE_DATASETS = [
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
    "index_members",
    "corporate_actions",
]


def test_build_pipeline_plan_default_datasets():
    config = AShareDataConfig()
    plan = build_pipeline_plan(config)

    assert plan.provider == "tushare"
    names = [dataset.name for dataset in plan.datasets]
    assert names == list(FULL_RESEARCH_DATASETS)
    assert set(names) >= set(EXPECTED_CORE_DATASETS)
    assert all(dataset.target.startswith("data/ashare/") for dataset in plan.datasets)


def test_pipeline_plan_to_dict_is_json_serializable():
    plan = build_pipeline_plan(AShareDataConfig())

    encoded = json.dumps(plan.to_dict(), sort_keys=True)

    assert json.loads(encoded)["provider"] == "tushare"


def test_pipeline_plan_excludes_old_business_terms():
    plan = build_pipeline_plan(AShareDataConfig())
    payload = json.dumps(plan.to_dict()).lower()

    for forbidden in [
        "address",
        "liquidity",
        "fdv",
        "solana",
        "birdeye",
        "dexscreener",
        "meme",
        "token",
    ]:
        assert forbidden not in payload
