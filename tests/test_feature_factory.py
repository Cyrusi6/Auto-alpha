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
from feature_factory.run_features import main as run_features_main
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
