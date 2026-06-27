from pathlib import Path

import pytest

from data_pipeline.ashare.config import AShareDataConfig


def test_from_env_defaults(monkeypatch):
    for key in [
        "ASHARE_PROVIDER",
        "TUSHARE_TOKEN",
        "ASHARE_DATABASE_URL",
        "DATABASE_URL",
        "ASHARE_DATA_DIR",
        "ASHARE_START_DATE",
        "ASHARE_END_DATE",
        "ASHARE_ADJUST",
        "ASHARE_UNIVERSE",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = AShareDataConfig.from_env()

    assert config.provider == "tushare"
    assert config.tushare_token is None
    assert config.database_url is None
    assert config.data_dir == Path("data/ashare")
    assert config.start_date == "20150101"
    assert config.end_date is None
    assert config.adjust == "qfq"
    assert config.universe == "all_a"


def test_from_env_reads_tushare_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")

    config = AShareDataConfig.from_env()

    assert config.tushare_token == "test-token"


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
