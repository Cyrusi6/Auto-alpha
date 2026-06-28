import json

import numpy as np
import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from feature_factory import (
    FEATURE_SET_V1,
    FEATURE_SET_V2,
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
