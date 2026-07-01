from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.providers.factory import create_ashare_provider
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.validators import validate_daily_bar


class FakeTushareClient:
    def __init__(self):
        self.calls = []
        self.responses = {
            "stock_basic": [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "exchange": "SZSE",
                    "list_date": "19910403",
                    "industry": "银行",
                    "market": "主板",
                }
            ],
            "trade_cal": [
                {"cal_date": "20240102", "is_open": 1, "pretrade_date": "20231229"},
                {"cal_date": "20240106", "is_open": 0, "pretrade_date": ""},
            ],
            "daily": [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "open": "9.40",
                    "high": "9.58",
                    "low": "9.32",
                    "close": "9.50",
                    "pre_close": "9.39",
                    "vol": "123456.7",
                    "amount": "837451.2",
                }
            ],
            "daily_basic": [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "turnover_rate": "0.45",
                    "volume_ratio": "",
                    "pe_ttm": "4.80",
                    "pb": "0.52",
                    "ps_ttm": "1.18",
                    "total_mv": "1843200",
                    "circ_mv": "1840000",
                }
            ],
            "fina_indicator": [
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20230930",
                    "ann_date": "20231025",
                    "roe": "9.6",
                    "roa": "0.75",
                    "grossprofit_margin": "",
                    "or_yoy": "-7.5",
                    "netprofit_yoy": "8.7",
                    "debt_to_assets": "91.8",
                    "ocfps": "1.28",
                },
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20230930",
                    "ann_date": "",
                    "roe": "99",
                },
            ],
            "stk_limit": [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "up_limit": "10.33",
                    "down_limit": "8.45",
                    "pre_close": "9.39",
                }
            ],
            "adj_factor": [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "adj_factor": "1.02",
                }
            ],
            "index_weight": [
                {
                    "index_code": "000300.SH",
                    "con_code": "000001.SZ",
                    "trade_date": "20240102",
                    "weight": "0.42",
                }
            ],
            "dividend": [
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20231231",
                    "ann_date": "20240102",
                    "div_proc": "实施",
                    "stk_div": "0",
                    "stk_bo_rate": "0.05",
                    "stk_co_rate": "0.02",
                    "cash_div": "0.12",
                    "cash_div_tax": "0.10",
                    "record_date": "20240102",
                    "ex_date": "20240103",
                    "pay_date": "20240104",
                    "div_listdate": "20240104",
                    "imp_ann_date": "20240102",
                    "base_date": "20231231",
                    "base_share": "1000000",
                }
            ],
        }

    def post(self, api_name, params=None, fields=None):
        self.calls.append({"api_name": api_name, "params": params, "fields": fields})
        if api_name == "dividend" and (params or {}).get("ex_date") != "20240102":
            return []
        return self.responses[api_name]


def test_tushare_provider_maps_all_dataset_types():
    client = FakeTushareClient()
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="test-token",
        start_date="20240101",
        end_date="20240103",
        index_codes=("000300.SH",),
    )

    securities = provider.fetch_securities(config)
    calendar = provider.fetch_trade_calendar(config)
    bars = provider.fetch_daily_bars(config)
    basic = provider.fetch_daily_basic(config)
    financial = provider.fetch_financial_features(config)
    limits = provider.fetch_daily_limits(config)
    factors = provider.fetch_adjustment_factors(config)
    members = provider.fetch_index_members(config)
    actions = provider.fetch_corporate_actions(config)

    assert securities[0].ts_code == "000001.SZ"
    assert securities[0].board == "主板"
    assert calendar[0].trade_date == "20240102"
    assert calendar[0].is_open is True
    assert calendar[0].prev_trade_date == "20231229"
    assert calendar[1].is_open is False
    assert calendar[1].prev_trade_date is None
    assert bars[0].volume == 123456.7
    validate_daily_bar(bars[0])
    assert basic[0].volume_ratio is None
    assert financial[0].report_period == "20230930"
    assert financial[0].announce_date == "20231025"
    assert financial[0].gross_margin is None
    assert financial[0].revenue_yoy == -7.5
    assert financial[0].net_profit_yoy == 8.7
    assert financial[0].debt_to_asset == 91.8
    assert financial[0].operating_cashflow == 1.28
    assert len(financial) == 1
    assert limits[0].up_limit == 10.33
    assert limits[0].down_limit == 8.45
    assert factors[0].adj_factor == 1.02
    assert members[0].index_code == "000300.SH"
    assert members[0].ts_code == "000001.SZ"
    assert members[0].weight == 0.42
    assert actions[0].cash_div == 0.12
    assert actions[0].stk_bo_rate == 0.05
    assert actions[0].stk_co_rate == 0.02
    assert actions[0].source == "tushare"


def test_tushare_provider_uses_expected_api_names_and_params():
    client = FakeTushareClient()
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="test-token",
        start_date="20240101",
        end_date="20240103",
        index_codes=("000300.SH",),
    )

    provider.fetch_securities(config)
    provider.fetch_trade_calendar(config)
    provider.fetch_daily_bars(config)
    provider.fetch_daily_basic(config)
    provider.fetch_financial_features(config)
    provider.fetch_daily_limits(config)
    provider.fetch_adjustment_factors(config)
    provider.fetch_index_members(config)
    provider.fetch_corporate_actions(config)

    assert [call["api_name"] for call in client.calls[:8]] == [
        "stock_basic",
        "trade_cal",
        "daily",
        "daily_basic",
        "fina_indicator",
        "stk_limit",
        "adj_factor",
        "index_weight",
    ]
    assert [call["api_name"] for call in client.calls[8:]] == ["dividend", "dividend", "dividend"]
    assert client.calls[0]["params"] == {"list_status": "L"}
    assert client.calls[1]["params"] == {
        "start_date": "20240101",
        "exchange": "SSE",
        "end_date": "20240103",
    }
    assert client.calls[8]["params"] == {"ex_date": "20240101"}


def test_tushare_financial_features_can_use_ts_code_param():
    client = FakeTushareClient()
    provider = TushareAShareDataProvider(client=client)
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="test-token",
        start_date="20240101",
        end_date="20240103",
        ts_code="000001.SZ",
    )

    records = provider.fetch_financial_features(config)

    assert records
    assert client.calls[-1]["api_name"] == "fina_indicator"
    assert client.calls[-1]["params"]["ts_code"] == "000001.SZ"


def test_factory_returns_tushare_provider():
    provider = create_ashare_provider(AShareDataConfig(provider="tushare"))

    assert isinstance(provider, TushareAShareDataProvider)
