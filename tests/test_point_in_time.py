import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager, LocalAshareStorage
from matrix_store import MatrixStoreReader, build_matrix_cache, validate_matrix_cache
from model_core.data_loader import AShareDataLoader
from point_in_time.security_master import build_active_security_mask, build_security_lifecycle
from point_in_time.validator import validate_point_in_time_data
from point_in_time.run_pit import main as pit_main
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def _write_sample(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    return data_dir


def test_security_lifecycle_and_active_mask_handle_statuses():
    lifecycle = build_security_lifecycle(
        [
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "list_date": "20200101", "list_status": "L"},
            {"ts_code": "000002.SZ", "symbol": "000002", "name": "退市样例", "list_date": "20200101", "delist_date": "20240103", "list_status": "D"},
            {"ts_code": "000003.SZ", "symbol": "000003", "name": "暂停样例", "list_date": "20200101", "list_status": "P"},
            {"ts_code": "000004.SZ", "symbol": "000004", "name": "ST样例", "list_date": "20240103", "list_status": "L", "is_st": True},
        ]
    )
    mask = build_active_security_mask(
        lifecycle,
        ["20240102", "20240103", "20240104"],
        min_listing_days=1,
        exclude_st=True,
        include_paused=False,
    )
    active = {(item.ts_code, item.trade_date): item for item in mask}

    assert active[("000001.SZ", "20240104")].is_active
    assert not active[("000002.SZ", "20240103")].is_active
    assert active[("000002.SZ", "20240103")].reason == "delisted"
    assert not active[("000003.SZ", "20240104")].is_active
    assert active[("000003.SZ", "20240104")].reason == "paused"
    assert not active[("000004.SZ", "20240103")].is_active


def test_run_pit_cli_writes_artifacts_and_current_only_warning(tmp_path, capsys):
    data_dir = _write_sample(tmp_path)
    output_dir = tmp_path / "pit"

    rc = pit_main(
        [
            "validate",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--as-of-date",
            "20240104",
            "--feature-cutoff-mode",
            "next_trade_day_open",
        ]
    )
    capsys.readouterr()

    assert rc == 0
    report = json.loads((output_dir / "pit_validation_report.json").read_text())
    survivor = json.loads((output_dir / "survivorship_bias_report.json").read_text())
    assert report["warning_count"] >= 1
    assert survivor["current_only_security_master"] is True
    assert (output_dir / "active_security_mask.jsonl").exists()
    assert (output_dir / "pit_dataset_contracts.json").exists()


def test_data_loader_and_matrix_cache_point_in_time_masks(tmp_path):
    data_dir = _write_sample(tmp_path)
    pit_report, _, mask = validate_point_in_time_data(
        data_dir,
        start_date="20240102",
        end_date="20240104",
        feature_cutoff_mode="next_trade_day_open",
    )
    mask_path = tmp_path / "active_security_mask.jsonl"
    mask_path.write_text("\n".join(json.dumps(item.to_dict(), sort_keys=True) for item in mask) + "\n")
    build_universe_from_storage(
        LocalAshareStorage(data_dir),
        UniverseBuildConfig(
            universe_name="csi300_sample",
            as_of_date="20240104",
            min_listed_days=0,
            min_amount=0,
            index_code="000300.SH",
            use_index_members=True,
            point_in_time=True,
        ),
    )

    loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        universe_name="csi300_sample",
        point_in_time=True,
        active_security_mask_path=mask_path,
        feature_cutoff_mode="next_trade_day_open",
    ).load_data()
    assert pit_report.active_universe_coverage > 0
    assert {"active_mask", "listing_age_days", "pit_available_mask"} <= set(loader.raw_data_cache)
    assert float(loader.raw_data_cache["pit_available_mask"][:, 0].sum()) == 0.0

    result = build_matrix_cache(
        data_dir,
        output_dir=data_dir / "matrix_cache",
        universe_name="csi300_sample",
        point_in_time=True,
        feature_cutoff_mode="next_trade_day_open",
        active_mask_path=mask_path,
    )
    validation = validate_matrix_cache(result.cache_dir)
    reader = MatrixStoreReader(result.cache_dir)
    raw = reader.to_raw_data_cache(device="cpu")

    assert validation.valid
    assert reader.load_metadata()["point_in_time"] is True
    assert "active_mask" in raw
    assert raw["active_mask"].shape == loader.raw_data_cache["active_mask"].shape
