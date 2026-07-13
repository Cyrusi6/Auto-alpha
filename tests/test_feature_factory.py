import json
import shutil

import numpy as np
import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from feature_factory import (
    FEATURE_SET_V1,
    FEATURE_SET_V2,
    FEATURE_SET_V3,
    build_feature_set_manifest,
    build_feature_tensor,
    build_feature_tensor_artifacts,
    load_feature_manifest,
)
from feature_factory.builder import build_feature_matrix
from feature_factory.extended_builder import (
    _index_market_feature,
    _days_since_event,
    _days_to_event,
    _pit_field_matrices,
    _records,
    _rolling_mean,
    _rolling_std,
    _rolling_sum,
    _rolling_z,
)
from feature_factory.models import FeatureDefinition
from feature_factory.run_features import _resolve_data_dir, main as run_features_main
from model_core.data_loader import AShareDataLoader
from model_core.vocab import FEATURE_NAMES


def _prepare_sample_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    return data_dir


def test_feature_set_v1_matches_model_core_default_features(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    manifest = build_feature_set_manifest(FEATURE_SET_V1, created_at="2026-01-01T00:00:00Z")
    tensor, warnings = build_feature_tensor(loader, manifest)

    assert [item["feature_name"] for item in manifest.feature_definitions] == list(FEATURE_NAMES)
    assert manifest.feature_count == len(FEATURE_NAMES)
    assert tensor.shape == loader.feat_tensor.shape
    assert torch.isfinite(tensor).all()
    assert warnings == []


def test_feature_set_v2_builds_artifacts_and_loader_can_opt_in(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    result = build_feature_tensor_artifacts(loader, tmp_path / "features", feature_set_name=FEATURE_SET_V2)
    manifest = load_feature_manifest(result.manifest_path)
    opt_in_loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        feature_set_name=FEATURE_SET_V2,
        feature_set_manifest_path=result.manifest_path,
    ).load_data()

    assert result.feature_count > len(FEATURE_NAMES)
    assert manifest.feature_set_name == FEATURE_SET_V2
    assert opt_in_loader.feat_tensor.shape[1] == result.feature_count
    assert np.load(result.tensor_path).shape == tuple(opt_in_loader.feat_tensor.shape)
    assert (tmp_path / "features" / "feature_coverage_report.json").exists()
    assert (tmp_path / "features" / "feature_coverage_report.md").exists()
    assert (tmp_path / "features" / "feature_values_summary.json").exists()


def test_feature_builder_derives_corporate_action_flags_from_matrix_fields(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    loader.raw_data_cache["cash_dividend"] = torch.tensor(
        [[0.0, 1.0, 0.0], [0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        dtype=torch.float32,
    )
    loader.raw_data_cache["stock_distribution_ratio"] = torch.tensor(
        [[0.0, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 0.0]],
        dtype=torch.float32,
    )

    result = build_feature_tensor_artifacts(loader, tmp_path / "features_v2_actions", feature_set_name=FEATURE_SET_V2)

    assert "missing source for feature CASH_DIVIDEND_FLAG" not in result.warnings
    assert "missing source for feature STOCK_DISTRIBUTION_FLAG" not in result.warnings


def test_feature_manifest_hash_is_stable_for_same_inputs():
    left = build_feature_set_manifest(FEATURE_SET_V2, created_at="2026-01-01T00:00:00Z")
    right = build_feature_set_manifest(FEATURE_SET_V2, created_at="2026-01-02T00:00:00Z")

    assert left.content_hash == right.content_hash


def test_run_features_cli_build_and_validate(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    output_dir = tmp_path / "features_cli"

    assert (
        run_features_main(
            [
                "build",
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(output_dir),
                "--feature-set-name",
                FEATURE_SET_V2,
                "--device",
                "cpu",
                "--pretty",
            ]
        )
        == 0
    )
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["feature_set_name"] == FEATURE_SET_V2

    assert (
        run_features_main(
            [
                "validate",
                "--output-dir",
                str(output_dir),
                "--feature-set-manifest-path",
                str(output_dir / "feature_set_manifest.json"),
                "--pretty",
            ]
        )
        == 0
    )
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["feature_count"] == build_payload["feature_count"]


def test_feature_factory_resolves_manifest_only_freeze_source_data(tmp_path):
    data_dir = tmp_path / "governed_data"
    freeze_dir = tmp_path / "freeze"
    data_dir.mkdir()
    freeze_dir.mkdir()
    (freeze_dir / "freeze_manifest.json").write_text(
        json.dumps({"mode": "manifest_only_candidate", "source_data_dir": str(data_dir)}),
        encoding="utf-8",
    )

    assert _resolve_data_dir(None, str(freeze_dir)) == str(data_dir)


def test_feature_set_v3_manifest_extends_v2_with_pit_contracts():
    v2 = build_feature_set_manifest(FEATURE_SET_V2, created_at="2026-01-01T00:00:00Z")
    v3 = build_feature_set_manifest(FEATURE_SET_V3, created_at="2026-01-01T00:00:00Z")

    assert v3.feature_count > v2.feature_count
    for item in v3.feature_definitions:
        if item.get("feature_set_name") != FEATURE_SET_V3:
            continue
        assert item.get("family")
        assert "required_datasets" in item
        assert "pit_safety" in item
    weak = [item for item in v3.feature_definitions if item.get("pit_safety") == "weak_pit"]
    assert weak
    assert all(item.get("default_enabled") is False for item in weak)


def test_feature_set_v3_market_features_use_time_series_transforms():
    manifest = build_feature_set_manifest(FEATURE_SET_V3, created_at="2026-01-01T00:00:00Z")
    definitions = {item["feature_name"]: item for item in manifest.feature_definitions}

    for name in [
        "INDEX_RETURN_1D",
        "INDEX_RETURN_5D",
        "INDEX_RETURN_20D",
        "INDEX_VOLATILITY_20D",
        "INDEX_VALUATION_PE",
        "INDEX_VALUATION_PB",
    ]:
        assert definitions[name]["transform"] == "time_series_zscore"
        assert definitions[name]["lookback"] == 60
    assert definitions["MARKET_REGIME_UP_DOWN_FLAG"]["transform"] == "identity"


def test_time_series_zscore_preserves_market_regime_signal_across_dates():
    values = torch.tensor([[1.0, 2.0, 4.0, 3.0, 6.0], [1.0, 2.0, 4.0, 3.0, 6.0]])
    definition = FeatureDefinition(
        feature_name="INDEX_RETURN_1D",
        feature_version=FEATURE_SET_V3,
        family="index_market",
        source_fields=["index_daily_bars.close"],
        tensor_key="index_return_1d",
        transform="time_series_zscore",
        lookback=3,
    )

    matrix, warnings = build_feature_matrix({"close": torch.ones_like(values), "index_return_1d": values}, definition)

    assert warnings == []
    assert float(matrix.abs().sum()) > 0.0
    torch.testing.assert_close(matrix[0], matrix[1])


def test_index_market_feature_selects_csi300_series_from_multi_index_data(tmp_path):
    loader = type(
        "Loader",
        (),
        {
            "universe_name": "csi300_20260630",
            "ts_codes": ["000001.SZ", "000002.SZ"],
            "trade_dates": ["20240102", "20240103", "20240104"],
            "raw_data_cache": {"close": torch.ones((2, 3))},
        },
    )()
    records = [
        {"ts_code": "000300.SH", "trade_date": "20240102", "close": 100.0},
        {"ts_code": "000905.SH", "trade_date": "20240102", "close": 100.0},
        {"ts_code": "000300.SH", "trade_date": "20240103", "close": 110.0},
        {"ts_code": "000905.SH", "trade_date": "20240103", "close": 200.0},
        {"ts_code": "000300.SH", "trade_date": "20240104", "close": 121.0},
        {"ts_code": "000905.SH", "trade_date": "20240104", "close": 400.0},
    ]

    matrix = _index_market_feature(loader, "INDEX_RETURN_1D", {"index_daily_bars": records}, tmp_path)

    assert 0.09 < float(matrix[0, 1]) < 0.10
    torch.testing.assert_close(matrix[0], matrix[1])


def test_feature_set_v3_missing_expanded_dataset_warns_without_crashing(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    shutil.rmtree(data_dir / "moneyflow", ignore_errors=True)
    shutil.rmtree(data_dir / "margin_detail", ignore_errors=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    result = build_feature_tensor_artifacts(loader, tmp_path / "features_v3_missing", feature_set_name=FEATURE_SET_V3)

    payload = json.loads((tmp_path / "features_v3_missing" / "feature_family_readiness.json").read_text(encoding="utf-8"))
    assert result.feature_count > len(FEATURE_NAMES)
    assert (tmp_path / "features_v3_missing" / "feature_pit_alignment_report.json").exists()
    assert (tmp_path / "features_v3_missing" / "feature_build_warnings.jsonl").exists()
    assert payload["summary"]["insufficient_data_family_count"] >= 1


def test_feature_set_v3_fake_expanded_datasets_build_nonzero_matrices(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    _write_fake_expanded_records(data_dir)
    manifest = build_feature_set_manifest(FEATURE_SET_V3, created_at="2026-01-01T00:00:00Z")
    loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        feature_set_name=FEATURE_SET_V3,
        feature_set_manifest_path=tmp_path / "missing_manifest.json",
    )
    manifest_path = tmp_path / "feature_set_manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False), encoding="utf-8")
    loader.feature_set_manifest_path = manifest_path
    loader.load_data()

    assert loader.feat_tensor.shape[1] == manifest.feature_count
    for key in ["moneyflow_net_ratio", "margin_buy_ratio", "roa"]:
        assert key in loader.raw_data_cache
        assert torch.isfinite(loader.raw_data_cache[key]).all()
        assert float(loader.raw_data_cache[key].abs().sum().item()) > 0.0


def test_v3_vectorized_rolling_features_match_reference_implementation():
    values = torch.tensor(
        [
            [1.0, 2.0, 4.0, 8.0, 16.0, 32.0],
            [3.0, -1.0, 5.0, 0.0, 2.0, 7.0],
        ],
        dtype=torch.float32,
    )

    for window in (1, 3, 10):
        expected_sum = _rolling_reference(values, window, "sum")
        expected_mean = _rolling_reference(values, window, "mean")
        expected_std = _rolling_reference(values, window, "std")
        expected_z = (values - expected_mean) / torch.clamp(expected_std, min=1e-6)

        torch.testing.assert_close(_rolling_sum(values, window), expected_sum)
        torch.testing.assert_close(_rolling_mean(values, window), expected_mean)
        torch.testing.assert_close(_rolling_std(values, window), expected_std, atol=1e-6, rtol=1e-6)
        torch.testing.assert_close(_rolling_z(values, window), expected_z, atol=1e-5, rtol=1e-5)


def test_v3_vectorized_rolling_features_limit_nonfinite_values_to_active_window():
    values = torch.tensor([[1.0, float("nan"), 3.0, 4.0, 5.0]], dtype=torch.float32)
    result = _rolling_mean(values, 2)

    assert torch.isfinite(result[:, :1]).all()
    assert torch.isnan(result[:, 1:3]).all()
    torch.testing.assert_close(result[:, 3:], torch.tensor([[3.5, 4.5]]))


def test_v3_expanded_records_stream_and_filter_to_loaded_universe(tmp_path):
    dataset_dir = tmp_path / "moneyflow"
    dataset_dir.mkdir()
    rows = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "net_mf_amount": 1},
        {"ts_code": "000002.SZ", "trade_date": "20240102", "net_mf_amount": 2},
        {"ts_code": "000001.SZ", "trade_date": "20250102", "net_mf_amount": 3},
    ]
    (dataset_dir / "records.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    loader = type("Loader", (), {"ts_codes": ["000001.SZ"], "trade_dates": ["20240102"]})()

    records = _records(loader, {}, tmp_path, "moneyflow")

    assert records == [rows[0]]


def test_v3_event_distance_features_use_nearest_available_event():
    loader = type(
        "Loader",
        (),
        {
            "ts_codes": ["000001.SZ"],
            "trade_dates": ["20240102", "20240103", "20240104", "20240105"],
            "raw_data_cache": {"close": torch.ones((1, 4))},
        },
    )()
    records = [
        {"ts_code": "000001.SZ", "event_date": "20240103"},
        {"ts_code": "000001.SZ", "event_date": "20240105"},
    ]

    torch.testing.assert_close(_days_to_event(loader, records, "event_date"), torch.tensor([[1.0, 0.0, 1.0, 0.0]]))
    torch.testing.assert_close(_days_since_event(loader, records, "event_date"), torch.tensor([[0.0, 0.0, 1.0, 0.0]]))


def test_v3_pit_alignment_keeps_non_trading_day_announcements():
    loader = type(
        "Loader",
        (),
        {
            "ts_codes": ["000001.SZ"],
            "trade_dates": ["20240105", "20240108", "20240109"],
            "raw_data_cache": {"close": torch.ones((1, 3))},
        },
    )()
    records = [{"ts_code": "000001.SZ", "ann_date": "20240106", "value": 12.0}]

    matrix = _pit_field_matrices(loader, records, "ann_date", {"metric": ("value",)})["metric"]

    torch.testing.assert_close(matrix, torch.tensor([[0.0, 12.0, 12.0]]))


def _rolling_reference(values: torch.Tensor, window: int, operation: str) -> torch.Tensor:
    columns = []
    for idx in range(values.shape[1]):
        current = values[:, max(0, idx - window + 1) : idx + 1]
        if operation == "sum":
            columns.append(current.sum(dim=1))
        elif operation == "mean":
            columns.append(current.mean(dim=1))
        else:
            columns.append(current.std(dim=1, unbiased=False))
    return torch.stack(columns, dim=1)


def _write_fake_expanded_records(data_dir):
    moneyflow = data_dir / "moneyflow"
    margin = data_dir / "margin_detail"
    income = data_dir / "income_statements"
    balance = data_dir / "balance_sheets"
    cashflow = data_dir / "cashflow_statements"
    for path in [moneyflow, margin, income, balance, cashflow]:
        path.mkdir(parents=True, exist_ok=True)
    stocks = ["000001.SZ", "000002.SZ", "000003.SZ"]
    dates = ["20240102", "20240103", "20240104"]
    with (moneyflow / "records.jsonl").open("w", encoding="utf-8") as handle:
        for si, ts_code in enumerate(stocks):
            for di, trade_date in enumerate(dates):
                handle.write(json.dumps({"ts_code": ts_code, "trade_date": trade_date, "net_mf_amount": (si + 1) * (di + 1) * 10000}) + "\n")
    with (margin / "records.jsonl").open("w", encoding="utf-8") as handle:
        for si, ts_code in enumerate(stocks):
            for di, trade_date in enumerate(dates):
                handle.write(json.dumps({"ts_code": ts_code, "trade_date": trade_date, "rzye": 1000 + si * 100 + di * 50, "rzmre": 200 + si * 20 + di * 10}) + "\n")
    for directory, payload in [
        (income, {"revenue": 1000, "net_profit": 100, "oper_cost": 600}),
        (balance, {"total_assets": 2000, "total_liab": 800, "total_cur_assets": 900, "total_cur_liab": 300}),
        (cashflow, {"net_cash_flows_oper_act": 120, "c_pay_acq_const_fiolta": 20}),
    ]:
        with (directory / "records.jsonl").open("w", encoding="utf-8") as handle:
            for si, ts_code in enumerate(stocks):
                row = {"ts_code": ts_code, "ann_date": "20240102", "end_date": "20231231"}
                row.update({key: value + si * 10 for key, value in payload.items()})
                handle.write(json.dumps(row) + "\n")
