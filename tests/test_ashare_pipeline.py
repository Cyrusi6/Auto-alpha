import json

from data_pipeline.ashare import AShareDataConfig, build_pipeline_plan

EXPECTED_DATASETS = [
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
    "index_members",
]


def test_build_pipeline_plan_default_datasets():
    config = AShareDataConfig()
    plan = build_pipeline_plan(config)

    assert plan.provider == "tushare"
    assert [dataset.name for dataset in plan.datasets] == EXPECTED_DATASETS
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
