from pathlib import Path

import pytest

from data_pipeline.ashare.config import AShareDataConfig


def test_from_env_defaults(monkeypatch):
    for key in [
        "ASHARE_PROVIDER",
        "TUSHARE_TOKEN",
        "TUSHARE_API_URL",
        "TUSHARE_TIMEOUT_SECONDS",
        "TUSHARE_RETRY_COUNT",
        "ASHARE_DATABASE_URL",
        "DATABASE_URL",
        "ASHARE_DATA_DIR",
        "ASHARE_START_DATE",
        "ASHARE_END_DATE",
        "ASHARE_ADJUST",
        "ASHARE_UNIVERSE",
        "ASHARE_INDEX_CODES",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = AShareDataConfig.from_env()

    assert config.provider == "tushare"
    assert config.tushare_token is None
    assert config.tushare_api_url == "http://api.tushare.pro"
    assert config.tushare_timeout_seconds == 30
    assert config.tushare_retry_count == 3
    assert config.database_url is None
    assert config.data_dir == Path("data/ashare")
    assert config.start_date == "20150101"
    assert config.end_date is None
    assert config.adjust == "qfq"
    assert config.universe == "all_a"
    assert config.index_codes == ("000300.SH",)


def test_from_env_reads_tushare_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")

    config = AShareDataConfig.from_env()

    assert config.tushare_token == "test-token"


def test_from_env_reads_tushare_http_settings(monkeypatch):
    monkeypatch.setenv("TUSHARE_API_URL", "http://example.test/pro")
    monkeypatch.setenv("TUSHARE_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("TUSHARE_RETRY_COUNT", "1")

    config = AShareDataConfig.from_env()

    assert config.tushare_api_url == "http://example.test/pro"
    assert config.tushare_timeout_seconds == 5
    assert config.tushare_retry_count == 1


def test_from_env_reads_index_codes(monkeypatch):
    monkeypatch.setenv("ASHARE_INDEX_CODES", "000300.SH,000905.SH")

    config = AShareDataConfig.from_env()

    assert config.index_codes == ("000300.SH", "000905.SH")


def test_from_env_rejects_invalid_adjust(monkeypatch):
    monkeypatch.setenv("ASHARE_ADJUST", "invalid")

    with pytest.raises(ValueError, match="adjust"):
        AShareDataConfig.from_env()


def test_from_env_rejects_invalid_start_date(monkeypatch):
    monkeypatch.setenv("ASHARE_START_DATE", "20150230")

    with pytest.raises(ValueError, match="start_date"):
        AShareDataConfig.from_env()


def test_from_env_empty_end_date_is_none(monkeypatch):
    monkeypatch.setenv("ASHARE_END_DATE", "")

    config = AShareDataConfig.from_env()

    assert config.end_date is None
