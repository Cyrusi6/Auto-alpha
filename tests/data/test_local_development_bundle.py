from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from auto_alpha.data.lake.store import local_development_bundle as local_bundle_module
from auto_alpha.data.lake.store.local_development_bundle import (
    LocalDevelopmentBundleError,
    LocalDevelopmentScope,
    build_local_development_bundle,
    validate_local_development_bundle,
)
from auto_alpha.data.lake.store.run_local_development_bundle import (
    main as local_bundle_cli_main,
)
from auto_alpha.data.lake.store.source_freeze import (
    SourceFreezeConfig,
    build_source_freeze_generation,
)
from auto_alpha.platform.artifacts.storage import canonical_hash
from auto_alpha.platform.artifacts.schema.validator import validate_artifact
from tests.data.test_source_freeze import (
    _daily_bar,
    _governed_fixture,
    _read_jsonl,
    _refresh_raw_index,
    _write_jsonl,
)


def test_local_development_bundle_is_self_contained_and_keeps_unknown_evidence_invalid(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    scope = LocalDevelopmentScope("20120101", "20191231", "000300.SH")

    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=scope,
        workers=1,
    )
    manifest_path = Path(built["manifest_path"])
    validated = validate_local_development_bundle(manifest_path)
    schema_validation = validate_artifact(manifest_path, strict=True)

    assert validated["content_hash"] == built["content_hash"]
    assert schema_validation.valid is True
    assert schema_validation.artifact_type == "local_development_bundle"
    assert len(validated["builder_semantic_hash"]) == 64
    assert validated["mode"] == "development_replay"
    assert validated["data_admission_eligible"] is False
    assert validated["evidence_flags"] == {
        "adjustment_revision_proven": False,
        "corporate_action_lineage_proven": False,
        "provider_coverage_proven": False,
        "pit_membership_proven": False,
        "st_status_proven": False,
        "suspension_state_proven": False,
    }
    assert {
        "adjustment_revision_history_unproven",
        "corporate_action_lineage_unproven",
    } <= set(validated["blockers"])

    bundle_root = manifest_path.parent.resolve()
    assert not bundle_root.stat().st_mode & 0o222
    assert not manifest_path.stat().st_mode & 0o222
    artifacts = {row["role"]: row for row in validated["artifacts"]}
    for row in artifacts.values():
        relative_path = Path(row["relative_path"])
        assert not relative_path.is_absolute()
        assert relative_path.parts[0] in {"development_matrix", "source_evidence"}
        artifact_path = (bundle_root / relative_path).resolve()
        assert artifact_path.is_relative_to(bundle_root)
        assert artifact_path.is_file()

    binding = json.loads(
        (
            bundle_root
            / artifacts["source_identity_binding_evidence"]["relative_path"]
        ).read_text(encoding="utf-8")
    )
    assert binding["source_manifest_raw_embedded"] is False
    assert binding["search_view_manifest_raw_embedded"] is True
    assert binding["holdout_locator_exposed"] is False
    assert not (bundle_root / "source_evidence" / "source_freeze_manifest.json").exists()
    assert not (bundle_root / "source_evidence" / "canonical_freeze_manifest.json").exists()
    for json_path in bundle_root.rglob("*.json"):
        text = json_path.read_text(encoding="utf-8").lower().replace("\\", "/")
        assert "sealed_holdout" not in text
        assert "retrospective_test" not in text
        assert "period=validation" not in text
        assert "/validation/" not in text

    dates = _load_json_axis(bundle_root / artifacts["date_axis"]["relative_path"])
    membership_known = np.load(
        bundle_root / artifacts["membership_known"]["relative_path"],
        allow_pickle=False,
    )
    unknown_history = [
        index for index, date in enumerate(dates) if "20120101" <= date <= "20151231"
    ]
    assert unknown_history
    assert not membership_known[..., unknown_history].any()
    stale_snapshot_date = dates.index("20190102")
    assert not membership_known[..., stale_snapshot_date].any()

    target_values = np.load(
        bundle_root / artifacts["target_values"]["relative_path"],
        allow_pickle=False,
    )
    target_available = np.load(
        bundle_root / artifacts["target_availability"]["relative_path"],
        allow_pickle=False,
    )
    assert target_available.dtype == np.bool_
    assert target_values.shape == target_available.shape
    invalid = ~target_available
    assert invalid.any()
    assert np.isnan(target_values[invalid]).all()


def test_local_development_bundle_is_deterministic_cached_and_source_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    source_root = Path(source["generation_dir"])
    source_before = _file_snapshot(source_root)
    scope = LocalDevelopmentScope("20120101", "20191231", "000300.SH")

    single_worker = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "bundle_single",
        scope=scope,
        workers=1,
    )
    four_workers = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "bundle_four",
        scope=scope,
        workers=4,
    )
    monkeypatch.setattr(
        local_bundle_module,
        "_write_npy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit must skip matrix publication")
        ),
    )
    cached = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "bundle_single",
        scope=scope,
        workers=4,
    )

    assert single_worker["cache_hit"] is False
    assert four_workers["cache_hit"] is False
    assert cached["cache_hit"] is True
    for key in ("content_hash", "generation_id", "artifact_root"):
        assert single_worker[key] == four_workers[key] == cached[key]

    validated_single = validate_local_development_bundle(single_worker["manifest_path"])
    validated_four = validate_local_development_bundle(four_workers["manifest_path"])
    validated_cached = validate_local_development_bundle(cached["manifest_path"])
    assert _validated_semantic(validated_single) == _validated_semantic(validated_four)
    assert _validated_semantic(validated_single) == _validated_semantic(validated_cached)
    assert Path(validated_single["manifest_path"]) != Path(validated_four["manifest_path"])
    assert _file_snapshot(source_root) == source_before


def test_local_development_bundle_is_offline_token_independent_and_tamper_evident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("TUSHARE_") or name == "RUN_TUSHARE_ONLINE_BACKFILL":
            monkeypatch.delenv(name, raising=False)

    def network_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("network forbidden during local development replay")

    # The governed Tushare gateway executes through build_opener(...).open;
    # urlopen is blocked as well so an older direct-client path cannot escape.
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", network_forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", network_forbidden)

    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    scope = LocalDevelopmentScope("20120101", "20191231", "000300.SH")

    without_token = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "bundle_without_token",
        scope=scope,
        workers=1,
    )
    monkeypatch.setenv("TUSHARE_TOKEN", "must-not-affect-local-replay")
    with_token = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "bundle_with_token",
        scope=scope,
        workers=1,
    )
    validated_without = validate_local_development_bundle(without_token["manifest_path"])
    validated_with = validate_local_development_bundle(with_token["manifest_path"])

    for key in ("content_hash", "generation_id", "artifact_root"):
        assert without_token[key] == with_token[key]
    assert _validated_semantic(validated_without) == _validated_semantic(validated_with)

    valid_generation = Path(without_token["manifest_path"]).parent
    tampered_manifest_root = tmp_path / "tampered_manifest" / valid_generation.name
    shutil.copytree(valid_generation, tampered_manifest_root)
    tampered_manifest_path = tampered_manifest_root / "local_development_bundle.json"
    tampered_manifest_path.chmod(0o600)
    tampered_manifest = json.loads(tampered_manifest_path.read_text(encoding="utf-8"))
    tampered_manifest["mode"] = "governed_research"
    tampered_manifest_path.write_text(
        json.dumps(tampered_manifest, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(tampered_manifest_path)

    tampered_artifact_root = tmp_path / "tampered_artifact" / valid_generation.name
    shutil.copytree(valid_generation, tampered_artifact_root)
    target_row = next(
        row for row in validated_without["artifacts"] if row["role"] == "target_values"
    )
    tampered_target = tampered_artifact_root / target_row["relative_path"]
    tampered_target.chmod(0o600)
    tampered_target.write_bytes(tampered_target.read_bytes() + b"tampered")
    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(
            tampered_artifact_root / "local_development_bundle.json"
        )


def test_local_development_bundle_accepts_legacy_source_without_upgrading_its_evidence(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "new_source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    legacy_manifest = _as_legacy_source_generation(
        Path(source["manifest_path"]),
        tmp_path / "legacy_source_freeze",
    )

    built = build_local_development_bundle(
        legacy_manifest,
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    validated = validate_local_development_bundle(built["manifest_path"])

    assert validated["mode"] == "development_replay"
    assert validated["data_admission_eligible"] is False
    assert validated["source_evidence_grade"] == "legacy_unproven"
    assert {
        "adjustment_revision_history_unproven",
        "corporate_action_lineage_unproven",
        "legacy_provider_coverage_unproven",
        "legacy_source_artifact_root_unavailable",
        "pit_membership_publication_unproven",
        "st_status_unproven",
        "suspension_state_unproven",
    } <= set(validated["blockers"])


@pytest.mark.parametrize("output_relative", (".", "search_view"))
def test_local_development_bundle_rejects_output_inside_its_immutable_source_before_writing(
    tmp_path: Path,
    output_relative: str,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    source_root = Path(source["generation_dir"])
    output_root = source_root if output_relative == "." else source_root / output_relative
    source_before = _file_snapshot(source_root)
    assert not (output_root / "current.json").exists()
    assert not (output_root / "generations").exists()

    with pytest.raises(LocalDevelopmentBundleError):
        build_local_development_bundle(
            source["manifest_path"],
            output_root,
            scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
            workers=1,
        )

    assert _file_snapshot(source_root) == source_before
    assert not (output_root / "current.json").exists()
    assert not (output_root / "generations").exists()


def test_local_development_bundle_invalidates_incoherent_limits_and_keeps_controls_out_of_features(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    _inject_incoherent_limit_contract(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    validated = validate_local_development_bundle(built["manifest_path"])
    bundle_root = Path(validated["manifest_path"]).parent
    artifacts = {row["role"]: row for row in validated["artifacts"]}
    stocks = _load_json_axis(bundle_root / artifacts["stock_axis"]["relative_path"])
    dates = _load_json_axis(bundle_root / artifacts["date_axis"]["relative_path"])
    stock_position = stocks.index("000002.SZ")
    limit_date_position = dates.index("20190103")

    up_validity = np.load(
        bundle_root / artifacts["raw_up_limit_validity"]["relative_path"],
        allow_pickle=False,
    )
    down_validity = np.load(
        bundle_root / artifacts["raw_down_limit_validity"]["relative_path"],
        allow_pickle=False,
    )
    assert not bool(up_validity[stock_position, limit_date_position])
    assert not bool(down_validity[stock_position, limit_date_position])

    quality = json.loads(
        (bundle_root / artifacts["quality_report"]["relative_path"]).read_text(
            encoding="utf-8"
        )
    )
    assert quality["limit_anomaly_counts"]["positive_limit_order_violation"] >= 1
    assert "invalid_limit_contract" not in quality["limit_anomaly_counts"]
    assert quality["limit_anomaly_counts"]["cross_source_pre_close_mismatch"] >= 1

    diagnostic_roles = {
        f"{dataset}_{kind}_positions"
        for dataset in (
            "daily_bars",
            "daily_basic",
            "daily_limits",
            "adjustment_factors",
        )
        for kind in ("observed", "duplicate")
    } | {
        "limit_required_field_unusable_positions",
        "positive_limit_order_violation_positions",
        "cross_source_pre_close_mismatch_positions",
    }
    assert diagnostic_roles <= set(artifacts)
    positive_violation = np.load(
        bundle_root
        / artifacts["positive_limit_order_violation_positions"]["relative_path"],
        allow_pickle=False,
    )
    mismatch = np.load(
        bundle_root
        / artifacts["cross_source_pre_close_mismatch_positions"]["relative_path"],
        allow_pickle=False,
    )
    assert bool(positive_violation[stock_position, limit_date_position])
    assert mismatch.any()

    target_available = np.load(
        bundle_root / artifacts["target_availability"]["relative_path"],
        allow_pickle=False,
    )
    signal_date_position = dates.index("20190102")
    assert not bool(target_available[stock_position, signal_date_position])

    feature_axis = _load_json_axis(
        bundle_root / artifacts["feature_axis"]["relative_path"]
    )
    assert {"up_limit", "down_limit", "adj_factor"}.isdisjoint(feature_axis)


def test_local_development_bundle_seeds_membership_from_last_qualified_pre_scope_snapshot(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _configure_pre_scope_seed_fixture(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20170101", "20171231", "000300.SH"),
        workers=1,
    )
    validated = validate_local_development_bundle(built["manifest_path"])
    bundle_root = Path(validated["manifest_path"]).parent
    artifacts = {row["role"]: row for row in validated["artifacts"]}
    stocks = _load_json_axis(bundle_root / artifacts["stock_axis"]["relative_path"])
    dates = _load_json_axis(bundle_root / artifacts["date_axis"]["relative_path"])
    membership = np.load(
        bundle_root / artifacts["pit_universe_membership"]["relative_path"],
        allow_pickle=False,
    )
    membership_known = np.load(
        bundle_root / artifacts["membership_known"]["relative_path"],
        allow_pickle=False,
    )

    assert dates[0] == "20170103"
    assert len(stocks) == 300
    assert {"000001.SZ", "000300.SZ"} <= set(stocks)
    assert membership_known[:, 0].all()
    assert membership[:, 0].all()

    quality = json.loads(
        (bundle_root / artifacts["quality_report"]["relative_path"]).read_text(
            encoding="utf-8"
        )
    )
    seed = next(
        row
        for row in quality["effective_snapshots"]
        if row["snapshot_date"] == "20161230"
    )
    assert seed["effective_trade_date"] == "20170103"
    assert seed["member_count"] == 300
    assert seed["is_pre_scope_seed"] is True


@pytest.mark.parametrize("tampered_name", ("quality_report.json", "source_catalog.json"))
def test_local_development_bundle_revalidates_the_complete_new_source_freeze_before_building(
    tmp_path: Path,
    tampered_name: str,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    tampered_path = Path(source["generation_dir"]) / tampered_name
    tampered_path.chmod(0o644)
    payload = json.loads(tampered_path.read_text(encoding="utf-8"))
    payload["post_publish_tamper"] = True
    tampered_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    output_root = tmp_path / "development_bundles"

    with pytest.raises(LocalDevelopmentBundleError):
        build_local_development_bundle(
            source["manifest_path"],
            output_root,
            scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
            workers=1,
        )

    assert not output_root.exists()


@pytest.mark.parametrize("forged_role", ("target_values", "feature_values"))
def test_local_development_bundle_rejects_self_consistent_derived_array_forgery(
    tmp_path: Path,
    forged_role: str,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )

    forged = _forge_derived_array_and_reseal(
        Path(built["manifest_path"]),
        tmp_path / f"forged_{forged_role}",
        forged_role,
    )

    with pytest.raises(
        LocalDevelopmentBundleError,
        match="local development derived semantics invalid",
    ):
        validate_local_development_bundle(forged)


def test_local_development_bundle_rejects_unregistered_generation_payload(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    original = Path(built["manifest_path"]).parent
    copied = tmp_path / "extra_payload" / original.name
    shutil.copytree(original, copied)
    _make_tree_writable(copied)
    (copied / "development_matrix" / "unregistered_payload.bin").write_bytes(b"hidden")
    _make_tree_readonly(copied)

    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(copied / "local_development_bundle.json")


def test_local_development_bundle_rejects_negative_constituent_weight(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    members_path = governed_root / "data" / "index_members" / "records.jsonl"
    members = _read_jsonl(members_path)
    snapshot = [row for row in members if row.get("trade_date") == "20160104"]
    snapshot[0]["weight"] = -1.0
    snapshot[1]["weight"] = float(snapshot[1]["weight"]) + 4.0 / 3.0
    _write_jsonl(members_path, snapshot)
    _refresh_raw_index(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )

    with pytest.raises(LocalDevelopmentBundleError, match="no accepted index snapshots"):
        build_local_development_bundle(
            source["manifest_path"],
            tmp_path / "development_bundles",
            scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
            workers=1,
        )


def test_local_development_target_rejects_open_outside_observed_price_band_proxy(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _configure_pre_scope_seed_fixture(governed_root)
    bars_path = governed_root / "data" / "daily_bars" / "records.jsonl"
    bars = _read_jsonl(bars_path)
    for row in bars:
        if row.get("ts_code") != "000002.SZ":
            continue
        date = str(row.get("trade_date") or "")
        if date in {"20170103", "20170104", "20170105"}:
            row["pre_close"] = float({"20170103": 10, "20170104": 11, "20170105": 12}[date])
        if date == "20170104":
            row.update({"open": 9.0, "high": 10.0, "low": 8.0, "close": 9.5})
    _write_jsonl(bars_path, bars)
    _refresh_raw_index(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20170101", "20171231", "000300.SH"),
        workers=1,
    )
    validated = validate_local_development_bundle(built["manifest_path"])
    root = Path(validated["manifest_path"]).parent
    artifacts = {row["role"]: row for row in validated["artifacts"]}
    stocks = _load_json_axis(root / artifacts["stock_axis"]["relative_path"])
    dates = _load_json_axis(root / artifacts["date_axis"]["relative_path"])
    available = np.load(
        root / artifacts["target_availability"]["relative_path"],
        allow_pickle=False,
    )
    contract = json.loads(
        (root / artifacts["target_contract"]["relative_path"]).read_text(
            encoding="utf-8"
        )
    )
    quality = json.loads(
        (root / artifacts["quality_report"]["relative_path"]).read_text(
            encoding="utf-8"
        )
    )

    assert not bool(available[stocks.index("000002.SZ"), dates.index("20170103")])
    assert contract["observed_price_band_proxy_checked"] is True
    assert contract["legal_price_band_proven"] is False
    attrition = quality["observed_proxy_target_attrition"]
    assert attrition["mode"] == "ordered_incremental"
    assert attrition["attrition_order"] == [
        "missing_entry_adjusted_open",
        "missing_exit_adjusted_open",
        "missing_observed_price_band_proxy_evidence",
        "entry_open_outside_observed_price_band_proxy",
        "exit_open_outside_observed_price_band_proxy",
        "entry_open_at_observed_up_limit_proxy",
        "exit_open_at_observed_down_limit_proxy",
        "nonfinite_observed_proxy_return",
    ]
    assert set(attrition["attrition_order"]) == set(
        attrition["ordered_incremental_attrition_counts"]
    )


def test_local_development_reconciliation_is_per_approved_field_and_distinguishes_observation(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _configure_pre_scope_seed_fixture(governed_root)
    basic_path = governed_root / "data" / "daily_basic" / "records.jsonl"
    rows = _read_jsonl(basic_path)
    target = next(
        row
        for row in rows
        if row.get("ts_code") == "000002.SZ"
        and row.get("trade_date") == "20170103"
    )
    target["volume_ratio"] = None
    _write_jsonl(basic_path, rows)
    _refresh_raw_index(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20170101", "20171231", "000300.SH"),
        workers=1,
    )
    validated = validate_local_development_bundle(built["manifest_path"])
    root = Path(validated["manifest_path"]).parent
    artifacts = {row["role"]: row for row in validated["artifacts"]}
    reconciliation = json.loads(
        (root / artifacts["reconciliation_report"]["relative_path"]).read_text(
            encoding="utf-8"
        )
    )
    rows_by_field = {
        (row["right_dataset"], row["right_field"]): row
        for row in reconciliation["field_reconciliation"]
    }

    assert reconciliation["proxy_member_observed_daily_bar_position_count"] >= 1
    assert reconciliation["proxy_member_valid_open_close_bar_cell_count"] >= 1
    assert (
        rows_by_field[("daily_basic", "volume_ratio")][
            "missing_right_field_on_proxy_member_valid_open_close_bar_count"
        ]
        >= 1
    )
    assert (
        rows_by_field[("daily_basic", "total_mv")][
            "missing_right_field_on_proxy_member_valid_open_close_bar_count"
        ]
        == 0
    )


def test_local_development_bundle_rejects_self_consistent_evidence_forgery(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    for forged_role in (
        "quality_report",
        "reconciliation_report",
        "source_to_derived_lineage",
    ):
        forged = _forge_json_evidence_and_reseal(
            Path(built["manifest_path"]),
            tmp_path / f"forged_{forged_role}",
            forged_role,
        )
        with pytest.raises(
            LocalDevelopmentBundleError,
            match="local development derived semantics invalid",
        ):
            validate_local_development_bundle(forged)


def test_local_development_bundle_rejects_fully_resealed_empty_source_lineage(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    working = tmp_path / "forged_lineage" / "working"
    shutil.copytree(Path(built["manifest_path"]).parent, working)
    _make_tree_writable(working)
    manifest_path = working / "local_development_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    lineage_path = working / artifacts["source_to_derived_lineage"]["relative_path"]
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    empty_root = canonical_hash([])
    lineage["source_partitions"] = []
    lineage["source_partition_selection_root"] = empty_root
    manifest["source_partition_selection_root"] = empty_root
    lineage_path.write_text(json.dumps(lineage, sort_keys=True), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    forged = _fully_reseal_working_generation(
        working,
        tmp_path / "forged_lineage",
    )

    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(forged)


def test_local_development_bundle_rejects_fully_resealed_duplicate_mask_with_valid_raw(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    working = tmp_path / "forged_duplicate" / "working"
    shutil.copytree(Path(built["manifest_path"]).parent, working)
    _make_tree_writable(working)
    manifest = json.loads(
        (working / "local_development_bundle.json").read_text(encoding="utf-8")
    )
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    validity = np.load(
        working / artifacts["raw_open_validity"]["relative_path"],
        allow_pickle=False,
    )
    duplicate_path = (
        working / artifacts["daily_bars_duplicate_positions"]["relative_path"]
    )
    duplicate = np.load(duplicate_path, allow_pickle=False)
    position = tuple(int(value) for value in np.argwhere(validity)[0])
    duplicate[position] = True
    with duplicate_path.open("wb") as handle:
        np.save(handle, duplicate, allow_pickle=False)
    quality_path = working / artifacts["quality_report"]["relative_path"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["alignment_counts"]["daily_bars"]["duplicate_position_count"] += 1
    quality_path.write_text(json.dumps(quality, sort_keys=True), encoding="utf-8")
    forged = _fully_reseal_working_generation(
        working,
        tmp_path / "forged_duplicate",
    )

    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(forged)


def test_local_development_bundle_rejects_fully_resealed_overlapping_limit_reasons(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    working = tmp_path / "forged_limit_reasons" / "working"
    shutil.copytree(Path(built["manifest_path"]).parent, working)
    _make_tree_writable(working)
    manifest = json.loads(
        (working / "local_development_bundle.json").read_text(encoding="utf-8")
    )
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    unusable = np.load(
        working
        / artifacts["limit_required_field_unusable_positions"]["relative_path"],
        allow_pickle=False,
    )
    mismatch_path = (
        working
        / artifacts["cross_source_pre_close_mismatch_positions"]["relative_path"]
    )
    mismatch = np.load(mismatch_path, allow_pickle=False)
    position = tuple(int(value) for value in np.argwhere(unusable & ~mismatch)[0])
    mismatch[position] = True
    with mismatch_path.open("wb") as handle:
        np.save(handle, mismatch, allow_pickle=False)

    quality_path = working / artifacts["quality_report"]["relative_path"]
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["limit_anomaly_counts"]["cross_source_pre_close_mismatch"] += 1
    quality["limit_alignment_counts"]["comparable_pre_close_position_count"] += 1
    quality_path.write_text(json.dumps(quality, sort_keys=True), encoding="utf-8")
    forged = _fully_reseal_working_generation(
        working,
        tmp_path / "forged_limit_reasons",
    )

    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(forged)


def test_local_development_bundle_rejects_fully_resealed_top_level_locator(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    working = tmp_path / "forged_top_locator" / "working"
    shutil.copytree(Path(built["manifest_path"]).parent, working)
    _make_tree_writable(working)
    manifest_path = working / "local_development_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unapproved_locator"] = "s3://x/sealed_holdout/part-00000.parquet"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    forged = _fully_reseal_working_generation(
        working,
        tmp_path / "forged_top_locator",
    )

    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(forged)


def test_local_development_bundle_trusted_source_rejects_forged_identity_receipt(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    working = tmp_path / "forged_source_identity" / "working"
    shutil.copytree(Path(built["manifest_path"]).parent, working)
    _make_tree_writable(working)
    manifest_path = working / "local_development_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest_sha256"] = "0" * 64
    binding_path = working / next(
        row["relative_path"]
        for row in manifest["artifacts"]
        if row["role"] == "source_identity_binding_evidence"
    )
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["source_manifest_sha256"] = "0" * 64
    binding_path.write_text(json.dumps(binding, sort_keys=True), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    forged = _fully_reseal_working_generation(
        working,
        tmp_path / "forged_source_identity",
    )

    # The self-contained receipt is intentionally only a consistency proof;
    # the caller-supplied immutable source is the authenticity anchor.
    validate_local_development_bundle(forged)
    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(
            forged,
            trusted_source_freeze_manifest=source["manifest_path"],
        )


def test_local_development_bundle_trusted_source_rejects_field_array_swap(
    tmp_path: Path,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    built = build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "development_bundles",
        scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
        workers=1,
    )
    working = tmp_path / "forged_field_swap" / "working"
    shutil.copytree(Path(built["manifest_path"]).parent, working)
    _make_tree_writable(working)
    manifest = json.loads(
        (working / "local_development_bundle.json").read_text(encoding="utf-8")
    )
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    volume_path = working / artifacts["raw_volume"]["relative_path"]
    amount_path = working / artifacts["raw_amount"]["relative_path"]
    volume = np.load(volume_path, allow_pickle=False)
    amount = np.load(amount_path, allow_pickle=False)
    with volume_path.open("wb") as handle:
        np.save(handle, amount, allow_pickle=False)
    with amount_path.open("wb") as handle:
        np.save(handle, volume, allow_pickle=False)
    feature_path = working / artifacts["feature_values"]["relative_path"]
    feature = np.load(feature_path, allow_pickle=False)
    feature[:, [5, 6], :] = feature[:, [6, 5], :]
    with feature_path.open("wb") as handle:
        np.save(handle, feature, allow_pickle=False)
    feature_validity_path = working / artifacts["feature_validity"]["relative_path"]
    feature_validity = np.load(feature_validity_path, allow_pickle=False)
    feature_validity[:, [5, 6], :] = feature_validity[:, [6, 5], :]
    with feature_validity_path.open("wb") as handle:
        np.save(handle, feature_validity, allow_pickle=False)
    forged = _fully_reseal_working_generation(
        working,
        tmp_path / "forged_field_swap",
    )

    validate_local_development_bundle(forged)
    with pytest.raises(LocalDevelopmentBundleError):
        validate_local_development_bundle(
            forged,
            trusted_source_freeze_manifest=source["manifest_path"],
        )


@pytest.mark.parametrize("second_is_open", (True, False))
def test_local_development_trade_date_axis_rejects_duplicate_or_conflicting_calendar_rows(
    second_is_open: bool,
) -> None:
    class DuplicateCalendarView:
        def iter_observable_records(self, dataset: str) -> list[dict[str, object]]:
            assert dataset == "trade_calendar"
            return [
                {"trade_date": "20170103", "is_open": True},
                {"trade_date": "20170103", "is_open": second_is_open},
            ]

    with pytest.raises(
        LocalDevelopmentBundleError,
        match="trade calendar duplicate date",
    ):
        local_bundle_module._trade_date_axis(
            DuplicateCalendarView(),  # type: ignore[arg-type]
            LocalDevelopmentScope("20170101", "20171231", "000300.SH"),
        )


def test_local_development_trade_date_axis_ignores_out_of_scope_calendar_conflict() -> None:
    class OutOfScopeConflictView:
        def iter_observable_records(self, dataset: str) -> list[dict[str, object]]:
            assert dataset == "trade_calendar"
            return [
                {"trade_date": "20160104", "is_open": True},
                {"trade_date": "20160104", "is_open": False},
                {"trade_date": "20170103", "is_open": True},
            ]

    assert local_bundle_module._trade_date_axis(
        OutOfScopeConflictView(),  # type: ignore[arg-type]
        LocalDevelopmentScope("20170101", "20171231", "000300.SH"),
    ) == ["20170103"]


def test_local_development_research_partition_path_is_physical_and_scoped() -> None:
    assert local_bundle_module._valid_research_partition_path(
        "daily_bars",
        "research",
        "data/daily_bars/period=research/part-00000.parquet",
    )
    assert not local_bundle_module._valid_research_partition_path(
        "daily_bars",
        "research",
        "data/controlled_validation_secret/part.parquet",
    )
    assert not local_bundle_module._valid_research_partition_path(
        "daily_bars",
        "research",
        "data/daily_bars/period=validation/part-00000.parquet",
    )


def test_local_development_partition_date_bounds_are_pit_scoped() -> None:
    assert local_bundle_module._valid_partition_date_bounds(
        period="research",
        min_date="20120101",
        max_date="20191231",
        record_count=1,
    )
    assert not local_bundle_module._valid_partition_date_bounds(
        period="research",
        min_date="20190104",
        max_date="20120103",
        record_count=1,
    )
    assert not local_bundle_module._valid_partition_date_bounds(
        period="bootstrap",
        min_date="20120103",
        max_date="20120104",
        record_count=1,
    )
    assert not local_bundle_module._valid_partition_date_bounds(
        period="research",
        min_date=None,
        max_date="20190104",
        record_count=1,
    )


def test_local_development_cli_returns_structured_blocked_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = local_bundle_cli_main(
        ["validate", "--manifest", str(tmp_path / "missing.json")]
    )

    assert exit_code == 2
    assert capsys.readouterr().err == (
        '{"error": "local_development_bundle_error", '
        '"message": "local development bundle identity invalid", '
        '"status": "blocked"}\n'
    )


def test_local_development_bundle_rejects_source_drift_during_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governed_root = _governed_fixture(tmp_path / "legacy_lake")
    _add_development_replay_dates(governed_root)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed_root),
            output_root=str(tmp_path / "source_freeze"),
            batch_rows=64,
            sample_size=32,
            workers=1,
        )
    )
    partition = next(
        (Path(source["generation_dir"]) / "search_view").rglob("*.parquet")
    )
    real_align = local_bundle_module._aligned_observations

    def align_then_drift(*args: object, **kwargs: object) -> object:
        result = real_align(*args, **kwargs)
        partition.chmod(0o640)
        partition.write_bytes(partition.read_bytes() + b"drift")
        return result

    monkeypatch.setattr(
        local_bundle_module,
        "_aligned_observations",
        align_then_drift,
    )

    with pytest.raises(LocalDevelopmentBundleError):
        build_local_development_bundle(
            source["manifest_path"],
            tmp_path / "development_bundles",
            scope=LocalDevelopmentScope("20120101", "20191231", "000300.SH"),
            workers=1,
        )

    assert not (tmp_path / "development_bundles").exists()


def _add_development_replay_dates(governed_root: Path) -> None:
    data_root = governed_root / "data"
    replay_dates = ("20120103", "20150105", "20160104", "20190102", "20190103", "20190104")

    calendar_path = data_root / "trade_calendar" / "records.jsonl"
    calendar = _read_jsonl(calendar_path)
    existing_calendar_dates = {str(row["trade_date"]) for row in calendar}
    calendar.extend(
        {"trade_date": date, "is_open": 1}
        for date in replay_dates
        if date not in existing_calendar_dates
    )
    _write_jsonl(calendar_path, calendar)

    bars_path = data_root / "daily_bars" / "records.jsonl"
    bars = _read_jsonl(bars_path)
    bars.extend(
        _daily_bar("000002.SZ", date, 10.0 + ordinal)
        for ordinal, date in enumerate(replay_dates)
    )
    _write_jsonl(bars_path, bars)

    daily_basic_path = data_root / "daily_basic" / "records.jsonl"
    daily_basic = _read_jsonl(daily_basic_path)
    daily_basic.extend(
        {
            "ts_code": "000002.SZ",
            "trade_date": date,
            "turnover_rate": 1.0,
            "volume_ratio": 1.0,
            "total_mv": 1_000.0,
        }
        for date in replay_dates
    )
    _write_jsonl(daily_basic_path, daily_basic)

    limits_path = data_root / "daily_limits" / "records.jsonl"
    limits = _read_jsonl(limits_path)
    limits.extend(
        {
            "ts_code": "000002.SZ",
            "trade_date": date,
            "pre_close": 10.0,
            "up_limit": 11.0,
            "down_limit": 9.0,
        }
        for date in replay_dates
    )
    _write_jsonl(limits_path, limits)

    adjustment_path = data_root / "adjustment_factors" / "records.jsonl"
    adjustment_factors = _read_jsonl(adjustment_path)
    adjustment_factors.extend(
        {"ts_code": "000002.SZ", "trade_date": date, "adj_factor": 1.0}
        for date in replay_dates
    )
    _write_jsonl(adjustment_path, adjustment_factors)

    members_path = data_root / "index_members" / "records.jsonl"
    members = _read_jsonl(members_path)
    members.extend(
        {
            "index_code": "000300.SH",
            "ts_code": f"{index + 1:06d}.SZ",
            "trade_date": "20160104",
            "weight": 100.0 / 300.0,
        }
        for index in range(300)
    )
    _write_jsonl(members_path, members)
    _refresh_raw_index(governed_root)


def _inject_incoherent_limit_contract(governed_root: Path) -> None:
    bars_path = governed_root / "data" / "daily_bars" / "records.jsonl"
    bars = _read_jsonl(bars_path)
    bar = next(
        row
        for row in bars
        if row.get("ts_code") == "000002.SZ" and row.get("trade_date") == "20190103"
    )
    bar["pre_close"] = 13.0
    _write_jsonl(bars_path, bars)

    limits_path = governed_root / "data" / "daily_limits" / "records.jsonl"
    limits = _read_jsonl(limits_path)
    limit = next(
        row
        for row in limits
        if row.get("ts_code") == "000002.SZ" and row.get("trade_date") == "20190103"
    )
    limit.update({"pre_close": 20.0, "up_limit": 19.0, "down_limit": 21.0})
    _write_jsonl(limits_path, limits)
    _refresh_raw_index(governed_root)


def _configure_pre_scope_seed_fixture(governed_root: Path) -> None:
    data_root = governed_root / "data"
    scope_dates = ("20170103", "20170104", "20170105")

    calendar_path = data_root / "trade_calendar" / "records.jsonl"
    calendar = _read_jsonl(calendar_path)
    existing_dates = {str(row["trade_date"]) for row in calendar}
    calendar.extend(
        {"trade_date": date, "is_open": 1}
        for date in ("20161230", *scope_dates)
        if date not in existing_dates
    )
    _write_jsonl(calendar_path, calendar)

    bars_path = data_root / "daily_bars" / "records.jsonl"
    bars = _read_jsonl(bars_path)
    bars.extend(
        _daily_bar("000002.SZ", date, 10.0 + ordinal)
        for ordinal, date in enumerate(scope_dates)
    )
    _write_jsonl(bars_path, bars)

    daily_basic_path = data_root / "daily_basic" / "records.jsonl"
    daily_basic = _read_jsonl(daily_basic_path)
    daily_basic.extend(
        {
            "ts_code": "000002.SZ",
            "trade_date": date,
            "turnover_rate": 1.0,
            "volume_ratio": 1.0,
            "total_mv": 1_000.0,
        }
        for date in scope_dates
    )
    _write_jsonl(daily_basic_path, daily_basic)

    limits_path = data_root / "daily_limits" / "records.jsonl"
    limits = _read_jsonl(limits_path)
    limits.extend(
        {
            "ts_code": "000002.SZ",
            "trade_date": date,
            "pre_close": 10.0 + ordinal,
            "up_limit": 11.0 + ordinal,
            "down_limit": 9.0 + ordinal,
        }
        for ordinal, date in enumerate(scope_dates)
    )
    _write_jsonl(limits_path, limits)

    adjustment_path = data_root / "adjustment_factors" / "records.jsonl"
    adjustment_factors = _read_jsonl(adjustment_path)
    adjustment_factors.extend(
        {"ts_code": "000002.SZ", "trade_date": date, "adj_factor": 1.0}
        for date in scope_dates
    )
    _write_jsonl(adjustment_path, adjustment_factors)

    members_path = data_root / "index_members" / "records.jsonl"
    _write_jsonl(
        members_path,
        [
            {
                "index_code": "000300.SH",
                "ts_code": f"{index + 1:06d}.SZ",
                "trade_date": "20161230",
                "weight": 100.0 / 300.0,
            }
            for index in range(300)
        ],
    )
    _refresh_raw_index(governed_root)


def _load_json_axis(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return [str(item) for item in payload]


def _validated_semantic(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "manifest_path"}


def _file_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        snapshot[path.relative_to(root).as_posix()] = (
            path.stat().st_size,
            digest.hexdigest(),
        )
    return snapshot


def _as_legacy_source_generation(source_manifest: Path, output_root: Path) -> Path:
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    legacy_core_keys = (
        "schema_version",
        "source_catalog_hash",
        "period_policy",
        "partition_root",
        "search_partition_root",
        "period_coverage",
        "dataset_quality_root",
        "cross_source_reconciliation_hash",
        "source_semantic_hash",
        "strict_derived_bundle",
        "blockers",
        "warnings",
    )
    legacy = {
        key: value
        for key, value in source.items()
        if key not in {"source_artifact_root", "admission_evidence", "admission_evidence_root"}
    }
    legacy["schema_version"] = "canonical_ashare_research_freeze_v1"
    legacy_core = {key: legacy[key] for key in legacy_core_keys}
    legacy_hash = canonical_hash(legacy_core)
    legacy_id = f"ashare_freeze_{legacy_hash[:24]}"
    legacy["content_hash"] = legacy_hash
    legacy["generation_id"] = legacy_id

    target = output_root / "generations" / legacy_id
    shutil.copytree(source_manifest.parent, target)
    _make_tree_writable(target)

    search_path = target / "search_view" / "research_view_manifest.json"
    search = json.loads(search_path.read_text(encoding="utf-8"))
    search["schema_version"] = "physical_ashare_research_view_v1"
    search["generation_id"] = legacy_id
    search["freeze_content_hash"] = legacy_hash
    search_semantic = {key: value for key, value in search.items() if key != "content_hash"}
    search["content_hash"] = canonical_hash(search_semantic)
    search_path.write_text(json.dumps(search, sort_keys=True), encoding="utf-8")
    legacy["search_view_manifest_sha256"] = _sha256_path(search_path)

    (target / "source_freeze_manifest.json").unlink()
    legacy_manifest = target / "canonical_freeze_manifest.json"
    legacy_manifest.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")
    return legacy_manifest


def _make_tree_writable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _forge_derived_array_and_reseal(
    source_manifest: Path,
    output_root: Path,
    role: str,
) -> Path:
    working = output_root / "working"
    shutil.copytree(source_manifest.parent, working)
    _make_tree_writable(working)
    manifest_path = working / "local_development_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = {row["role"]: row for row in manifest["artifacts"]}

    array_path = working / artifacts[role]["relative_path"]
    array = np.load(array_path, allow_pickle=False)
    array.flat[0] = 123.0
    with array_path.open("wb") as handle:
        np.save(handle, array, allow_pickle=False)
    validity_role = {
        "target_values": "target_availability",
        "feature_values": "feature_validity",
    }[role]
    validity_path = working / artifacts[validity_role]["relative_path"]
    validity = np.load(validity_path, allow_pickle=False)
    validity.flat[0] = True
    with validity_path.open("wb") as handle:
        np.save(handle, validity, allow_pickle=False)
    return _fully_reseal_working_generation(working, output_root)


def _forge_json_evidence_and_reseal(
    source_manifest: Path,
    output_root: Path,
    role: str,
) -> Path:
    working = output_root / "working"
    shutil.copytree(source_manifest.parent, working)
    _make_tree_writable(working)
    manifest_path = working / "local_development_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    evidence_path = working / artifacts[role]["relative_path"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["self_consistent_forgery"] = True
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    return _fully_reseal_working_generation(working, output_root)


def _fully_reseal_working_generation(working: Path, output_root: Path) -> Path:
    manifest_path = working / "local_development_bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = {row["role"]: row for row in manifest["artifacts"]}
    matrix_row = artifacts["development_matrix_manifest"]
    matrix_path = working / matrix_row["relative_path"]
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["partition_sha256"] = {
        Path(row["relative_path"]).name: _sha256_path(
            working / row["relative_path"]
        )
        for role, row in artifacts.items()
        if role != "development_matrix_manifest"
        and Path(row["relative_path"]).parts[0] == "development_matrix"
    }
    matrix_path.write_text(json.dumps(matrix, sort_keys=True), encoding="utf-8")
    for row in manifest["artifacts"]:
        path = working / row["relative_path"]
        row["sha256"] = _sha256_path(path)
        row["size_bytes"] = path.stat().st_size
        if path.suffix == ".npy":
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            row["shape"] = list(array.shape)
            row["dtype"] = str(array.dtype)
    manifest["artifact_root"] = canonical_hash(manifest["artifacts"])
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"local_development_bundle_{content_hash[:24]}"
    manifest["content_hash"] = content_hash
    manifest["generation_id"] = generation_id
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    target = output_root / generation_id
    working.rename(target)
    _make_tree_readonly(target)
    return target / "local_development_bundle.json"


def _make_tree_readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o550 if path.is_dir() else 0o440)
    root.chmod(0o550)
