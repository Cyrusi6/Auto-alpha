from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from auto_alpha.data.lake.store.local_development_bundle import (
    LocalDevelopmentBundleLoader,
    LocalDevelopmentScope,
    build_local_development_bundle,
)
from auto_alpha.data.lake.store.source_freeze import (
    SourceFreezeConfig,
    build_source_freeze_generation,
)
from auto_alpha.portfolio.simulator.fixed_factor_replay import (
    FixedFactorReplayError,
    main as fixed_replay_cli_main,
    run_fixed_factor_replay,
    validate_fixed_factor_replay_evidence,
)
from auto_alpha.platform.artifacts.storage import canonical_hash, sha256_file
from auto_alpha.platform.artifacts.schema.validator import validate_artifact
from tests.data.test_source_freeze import (
    _governed_fixture,
    _read_jsonl,
    _refresh_raw_index,
    _write_jsonl,
)


def test_local_bundle_loader_preserves_frozen_axes_and_feature_meaning(
    replay_bundle: dict[str, object],
) -> None:
    loader = LocalDevelopmentBundleLoader(replay_bundle["manifest_path"])

    assert loader.manifest["content_hash"] == replay_bundle["content_hash"]
    assert loader.feature_names == (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume",
        "amount",
        "turnover_rate",
        "volume_ratio",
        "total_mv",
    )
    assert loader.feature_names.index("volume_ratio") == 8
    values = loader.load_array("feature_values", dtype=np.float32)
    validity = loader.load_array("feature_validity", dtype=np.bool_)
    assert values.shape == validity.shape
    assert values.shape[0] == len(loader.stock_ids)
    assert values.shape[2] == len(loader.trade_dates)
    assert not loader.manifest["data_admission_eligible"]
    assert not loader.manifest["alpha_search_authorized"]
    assert not loader.manifest["lifecycle_publication_allowed"]


def test_fixed_factor_replay_runs_close_to_next_open_and_never_promotes(
    replay_bundle: dict[str, object],
    tmp_path: Path,
) -> None:
    output = tmp_path / "fixed_replay"

    built = run_fixed_factor_replay(
        replay_bundle["manifest_path"],
        output,
    )
    validated = validate_fixed_factor_replay_evidence(
        built["manifest_path"],
        trusted_bundle_manifest=replay_bundle["manifest_path"],
    )
    schema = validate_artifact(built["manifest_path"], strict=True)

    assert schema.valid is True
    assert schema.artifact_type == "fixed_factor_replay_evidence"
    assert validated["terminal_status"] == "diagnostic_completed"
    assert validated["mode"] == "development_replay"
    assert validated["replay_contract"]["factor_id"] == "volume_ratio_cs_rank_v1"
    assert validated["replay_contract"]["formula_names"] == [
        "volume_ratio",
        "CS_RANK",
    ]
    assert validated["replay_contract"]["target_used_for_signal"] is False
    assert validated["replay_contract"]["signal_timing"] == "close_t"
    assert validated["replay_contract"]["execution_timing"] == "next_open"
    assert validated["data_admission_eligible"] is False
    assert validated["alpha_search_authorized"] is False
    assert validated["validation_candidate_eligible"] is False
    assert validated["lifecycle_publication_allowed"] is False
    assert validated["holdout_accessed"] is False
    assert validated["network_accessed"] is False
    assert validated["blockers"] == replay_bundle["blockers"]
    assert validated["backtest_summary"]["baseline"]["fill_count"] > 0
    assert validated["factor_diagnostics"]["valid_observation_count"] > 0

    artifacts = {row["role"]: row for row in validated["artifacts"]}
    generation = Path(validated["manifest_path"]).parent
    factor = np.load(generation / artifacts["factor_values"]["relative_path"], allow_pickle=False)
    factor_valid = np.load(
        generation / artifacts["factor_validity"]["relative_path"],
        allow_pickle=False,
    )
    loader = LocalDevelopmentBundleLoader(replay_bundle["manifest_path"])
    membership = loader.load_array("pit_universe_membership", dtype=np.bool_)
    membership_known = loader.load_array("membership_known", dtype=np.bool_)
    assert not factor_valid[:, 0].any()
    assert not np.any(factor_valid & ~(membership & membership_known))
    volume_ratio = loader.load_array("raw_volume_ratio", dtype=np.float32)
    source_valid = loader.load_array("raw_volume_ratio_validity", dtype=np.bool_)
    date_index = next(
        index
        for index in range(len(loader.trade_dates))
        if int(factor_valid[:, index].sum()) >= 20
    )
    eligible = source_valid[:, date_index] & membership[:, date_index]
    expected_order = np.argsort(volume_ratio[eligible, date_index], kind="stable")
    expected = np.empty(len(expected_order), dtype=np.float32)
    expected[expected_order] = np.arange(len(expected_order), dtype=np.float32) / (
        len(expected_order) - 1
    )
    np.testing.assert_allclose(factor[eligible, date_index], expected, rtol=0, atol=1e-6)

    orders = _read_jsonl(generation / artifacts["baseline_orders"]["relative_path"])
    fills = _read_jsonl(generation / artifacts["baseline_fills"]["relative_path"])
    assert orders and fills
    first_fill = fills[0]
    source_order = next(row for row in orders if row["order_id"] == first_fill["order_id"])
    assert first_fill["execution_index"] == source_order["decision_index"] + 1
    assert first_fill["price"] == pytest.approx(
        float(
            loader.load_array("raw_open", dtype=np.float32)[
                loader.stock_ids.index(first_fill["asset"]),
                first_fill["execution_index"],
            ]
        )
    )

    forbidden = {
        "factors.jsonl",
        "alpha_validation_candidate_pool.jsonl",
        "shadow_candidate.json",
        "paper_account.json",
        "live_orders.jsonl",
    }
    assert not forbidden & {path.name for path in generation.rglob("*") if path.is_file()}


def test_fixed_factor_replay_is_content_deterministic_resumable_and_tamper_evident(
    replay_bundle: dict[str, object],
    tmp_path: Path,
) -> None:
    first = run_fixed_factor_replay(
        replay_bundle["manifest_path"],
        tmp_path / "first",
    )
    cached = run_fixed_factor_replay(
        replay_bundle["manifest_path"],
        tmp_path / "first",
    )
    sibling = run_fixed_factor_replay(
        replay_bundle["manifest_path"],
        tmp_path / "sibling",
    )

    assert first["cache_hit"] is False
    assert cached["cache_hit"] is True
    for key in ("generation_id", "content_hash", "simulation_truth_hash"):
        assert first[key] == cached[key] == sibling[key]

    source = Path(first["manifest_path"]).parent
    forged = tmp_path / "forged" / source.name
    shutil.copytree(source, forged)
    event_path = forged / "baseline_event_ledger.jsonl"
    event_path.chmod(0o600)
    event_path.write_text(event_path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(FixedFactorReplayError):
        validate_fixed_factor_replay_evidence(
            forged / "fixed_factor_replay_evidence.json",
            trusted_bundle_manifest=replay_bundle["manifest_path"],
        )


def test_fixed_factor_replay_rejects_resealed_governance_and_policy_forgery(
    replay_bundle: dict[str, object],
    tmp_path: Path,
) -> None:
    built = run_fixed_factor_replay(
        replay_bundle["manifest_path"],
        tmp_path / "evidence",
    )
    source = Path(built["manifest_path"]).parent

    washed = _reseal_replay_evidence(source, tmp_path / "washed", "wash_blockers")
    with pytest.raises(FixedFactorReplayError):
        validate_fixed_factor_replay_evidence(washed)

    policy = _reseal_replay_evidence(source, tmp_path / "policy", "change_policy")
    with pytest.raises(FixedFactorReplayError):
        validate_fixed_factor_replay_evidence(policy)


def test_fixed_factor_replay_rejects_output_symlink_and_cli_reports_blocked(
    replay_bundle: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked_output"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(FixedFactorReplayError, match="symlink"):
        run_fixed_factor_replay(replay_bundle["manifest_path"], link)

    exit_code = fixed_replay_cli_main(
        ["validate", "--manifest", str(tmp_path / "missing.json"), "--pretty"]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.err)["status"] == "blocked"


@pytest.fixture
def replay_bundle(tmp_path: Path) -> dict[str, object]:
    governed = _governed_fixture(tmp_path / "lake")
    _install_replay_market(governed)
    source = build_source_freeze_generation(
        SourceFreezeConfig(
            governed_root=str(governed),
            output_root=str(tmp_path / "source"),
            batch_rows=128,
            sample_size=64,
            workers=1,
        )
    )
    return build_local_development_bundle(
        source["manifest_path"],
        tmp_path / "bundle",
        scope=LocalDevelopmentScope("20190102", "20190109", "000300.SH"),
        workers=1,
    )


def _install_replay_market(governed: Path) -> None:
    data = governed / "data"
    dates = ("20190102", "20190103", "20190104", "20190107", "20190108", "20190109")
    assets = tuple(f"{index:06d}.SZ" for index in range(1, 26))

    calendar_path = data / "trade_calendar" / "records.jsonl"
    calendar = {
        str(row["trade_date"]): row for row in _read_jsonl(calendar_path)
    }
    calendar.update({date: {"trade_date": date, "is_open": 1} for date in dates})
    _write_jsonl(calendar_path, [calendar[key] for key in sorted(calendar)])

    bars = [
        row
        for row in _read_jsonl(data / "daily_bars" / "records.jsonl")
        if not (str(row.get("ts_code")) in assets and str(row.get("trade_date")) in dates)
    ]
    basics = [
        row
        for row in _read_jsonl(data / "daily_basic" / "records.jsonl")
        if not (str(row.get("ts_code")) in assets and str(row.get("trade_date")) in dates)
    ]
    limits = [
        row
        for row in _read_jsonl(data / "daily_limits" / "records.jsonl")
        if not (str(row.get("ts_code")) in assets and str(row.get("trade_date")) in dates)
    ]
    adjustments = [
        row
        for row in _read_jsonl(data / "adjustment_factors" / "records.jsonl")
        if not (str(row.get("ts_code")) in assets and str(row.get("trade_date")) in dates)
    ]
    prior_close = {asset: 10.0 + ordinal * 0.05 for ordinal, asset in enumerate(assets)}
    for date_index, date in enumerate(dates):
        for asset_index, asset in enumerate(assets):
            pre_close = prior_close[asset]
            open_price = pre_close * (1.0 + ((asset_index + date_index) % 5 - 2) * 0.001)
            close_price = open_price * (1.0 + ((asset_index * 3 + date_index) % 7 - 3) * 0.001)
            high = max(open_price, close_price) * 1.01
            low = min(open_price, close_price) * 0.99
            volume_lots = 100_000.0 + asset_index * 1_000.0
            bars.append(
                {
                    "ts_code": asset,
                    "trade_date": date,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "pre_close": pre_close,
                    "volume": volume_lots,
                    "amount": close_price * volume_lots / 10.0,
                }
            )
            basics.append(
                {
                    "ts_code": asset,
                    "trade_date": date,
                    "turnover_rate": 1.0 + asset_index / 100.0,
                    "volume_ratio": float((asset_index + date_index) % len(assets) + 1),
                    "total_mv": 1_000_000.0 + asset_index * 10_000.0,
                }
            )
            limits.append(
                {
                    "ts_code": asset,
                    "trade_date": date,
                    "pre_close": pre_close,
                    "up_limit": pre_close * 1.10,
                    "down_limit": pre_close * 0.90,
                }
            )
            adjustments.append(
                {"ts_code": asset, "trade_date": date, "adj_factor": 1.0}
            )
            prior_close[asset] = close_price
    _write_jsonl(data / "daily_bars" / "records.jsonl", bars)
    _write_jsonl(data / "daily_basic" / "records.jsonl", basics)
    _write_jsonl(data / "daily_limits" / "records.jsonl", limits)
    _write_jsonl(data / "adjustment_factors" / "records.jsonl", adjustments)
    _refresh_raw_index(governed)


def _reseal_replay_evidence(
    source: Path,
    output: Path,
    mutation: str,
) -> Path:
    working = output / "working"
    shutil.copytree(source, working)
    for path in working.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    working.chmod(0o700)
    manifest_path = working / "fixed_factor_replay_evidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_role: str
    if mutation == "wash_blockers":
        manifest["blockers"] = []
        manifest["input_bundle"]["blockers"] = []
        changed_role = "input_lineage"
        (working / "input_lineage.json").write_text(
            json.dumps(manifest["input_bundle"], sort_keys=True),
            encoding="utf-8",
        )
    elif mutation == "change_policy":
        manifest["replay_contract"]["scenario_policies"]["baseline"][
            "commission_rate"
        ] = 0.0
        manifest["replay_contract_hash"] = canonical_hash(
            manifest["replay_contract"]
        )
        changed_role = "replay_contract"
        (working / "replay_contract.json").write_text(
            json.dumps(manifest["replay_contract"], sort_keys=True),
            encoding="utf-8",
        )
    else:  # pragma: no cover - helper contract
        raise AssertionError(mutation)
    changed_path = working / next(
        row["relative_path"]
        for row in manifest["artifacts"]
        if row["role"] == changed_role
    )
    for row in manifest["artifacts"]:
        if row["role"] == changed_role:
            row["sha256"] = sha256_file(changed_path)
            row["size_bytes"] = changed_path.stat().st_size
    manifest["artifact_root"] = canonical_hash(manifest["artifacts"])
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"generation_id", "content_hash"}
    }
    manifest["content_hash"] = canonical_hash(semantic)
    manifest["generation_id"] = (
        f"fixed_factor_replay_{manifest['content_hash'][:24]}"
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    generation = output / manifest["generation_id"]
    working.rename(generation)
    for path in sorted(generation.rglob("*"), reverse=True):
        path.chmod(0o550 if path.is_dir() else 0o440)
    generation.chmod(0o550)
    return generation / "fixed_factor_replay_evidence.json"
