from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from auto_alpha.platform.artifacts.schema.validator import validate_artifact
from auto_alpha.research.discovery.factory_models import AlphaCampaignConfig
from auto_alpha.research.discovery.factory_runner import _resolve_data_dir
from auto_alpha.research.discovery.factory_runner import _validate_production_research_config
from auto_alpha.data.lake.store.canonical_freeze import (
    REQUIRED_DATASETS,
    CanonicalFreezeConfig,
    CanonicalFreezeError,
    PhysicalResearchDataView,
    _canonical_hash,
    _dataset_contract,
    audit_canonical_freeze_sources,
    build_canonical_research_freeze,
    validate_canonical_research_freeze,
    validate_physical_research_view,
)
from auto_alpha.data.lake.store.validator import validate_research_input


def test_canonical_freeze_is_deterministic_and_physically_hides_later_periods(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    first = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze_a"), batch_rows=64, sample_size=32)
    )
    second = build_canonical_research_freeze(
        CanonicalFreezeConfig(
            str(governed),
            str(tmp_path / "freeze_b"),
            batch_rows=64,
            sample_size=32,
            workers=2,
        )
    )

    assert first["content_hash"] == second["content_hash"]
    assert first["partition_root"] == second["partition_root"]
    assert first["search_partition_root"] == second["search_partition_root"]
    assert first["alpha_search_authorized"] is True
    assert first["sealed_holdout"]["historically_observed"] is True
    assert first["sealed_holdout"]["untouched"] is False
    assert validate_research_input(data_freeze_dir=first["generation_dir"], require_freeze=True).status == "passed"
    assert validate_artifact(first["manifest_path"], strict=True).valid is True
    assert validate_artifact(first["search_view_manifest_path"], strict=True).valid is True

    view = PhysicalResearchDataView(first["search_view_manifest_path"])
    dates = [row["trade_date"] for row in view.iter_observable_records("daily_bars")]
    assert dates == ["20111230", "20190102"]
    assert "20200102" not in dates
    assert "20230103" not in dates
    assert "20250102" not in dates
    for partition in view.dataset_partitions("daily_bars"):
        assert "raw_json" not in pq.read_schema(partition).names

    security = next(view.iter_observable_records("securities"))
    assert security["ts_code"] == "000001.SZ"
    assert security["delist_date"] == "20181231"
    assert "name" not in security
    assert "is_st" not in security
    assert "list_status" not in security


def test_financial_availability_uses_conservative_announcement_endpoint(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    income = governed / "data" / "income_statements" / "records.jsonl"
    rows = _read_jsonl(income)
    rows[0]["ann_date"] = "20191231"
    rows[0]["f_ann_date"] = "20200103"
    _write_jsonl(income, rows)
    _refresh_raw_index(governed)

    freeze = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze"), batch_rows=64)
    )
    manifest = validate_canonical_research_freeze(freeze["manifest_path"])
    income_partitions = [row for row in manifest["partitions"] if row["dataset"] == "income_statements"]
    assert {row["period"] for row in income_partitions} == {"validation"}
    view = PhysicalResearchDataView(manifest["search_view_manifest_path"])
    with pytest.raises(CanonicalFreezeError, match="research dataset unavailable"):
        view.dataset_partitions("income_statements")


def test_missing_event_sources_and_legacy_suspension_fail_closed(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    index_path = _raw_index_path(governed)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["datasets"] = [row for row in index["datasets"] if row["dataset"] != "st_status_daily"]
    index["dataset_count"] = len(index["datasets"])
    suspension = governed / "data" / "suspensions" / "records.jsonl"
    _write_jsonl(
        suspension,
        [{"ts_code": "000001.SZ", "suspend_date": None, "resume_date": None, "ann_date": None}],
    )
    for row in index["datasets"]:
        if row["dataset"] == "suspensions":
            _refresh_index_row(row, suspension)
            row["primary_key_fields"] = ["ts_code"]
    index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")

    preflight = audit_canonical_freeze_sources(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "preflight"))
    )
    assert preflight["alpha_search_authorized"] is False
    assert "st_status_daily:dataset_missing_from_reviewed_raw_index" in preflight["blockers"]
    assert any("legacy_unusable_suspension_contract" in blocker for blocker in preflight["blockers"])


def test_search_partition_tampering_and_axis_drift_are_rejected(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    freeze = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze"), batch_rows=64)
    )
    view = validate_physical_research_view(freeze["search_view_manifest_path"])
    partition = Path(view["view_root"]) / view["partitions"][0]["view_relative_path"]
    partition.chmod(0o640)
    with partition.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(CanonicalFreezeError, match="partition hash mismatch"):
        validate_physical_research_view(freeze["search_view_manifest_path"])

    governed_b = _governed_fixture(tmp_path / "lake_b")
    date_axis = governed_b / "governance" / "canonical_derived" / "generations" / "derived" / "trade_dates.json"
    date_axis.write_text(json.dumps(["20190102", "20190103", "20250102"]), encoding="utf-8")
    _refresh_derived_bundle(governed_b)
    with pytest.raises(CanonicalFreezeError, match="post-research dates"):
        audit_canonical_freeze_sources(CanonicalFreezeConfig(str(governed_b), str(tmp_path / "blocked")))


def test_source_mutation_after_publication_does_not_change_freeze(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    freeze = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze"), batch_rows=64)
    )
    view = PhysicalResearchDataView(freeze["search_view_manifest_path"])
    before = list(view.iter_observable_records("daily_bars"))
    source = governed / "data" / "daily_bars" / "records.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    assert list(PhysicalResearchDataView(freeze["search_view_manifest_path"]).iter_observable_records("daily_bars")) == before


def test_production_research_rejects_noncanonical_or_unbounded_inputs(tmp_path: Path) -> None:
    old_freeze = tmp_path / "old_freeze"
    old_freeze.mkdir()
    config = AlphaCampaignConfig(
        campaign_name="blocked",
        data_dir=str(tmp_path / "raw"),
        output_dir=str(tmp_path / "out"),
        factor_store_dir=str(tmp_path / "factors"),
        data_freeze_dir=str(old_freeze),
        require_data_freeze=True,
        production_research=True,
        provider="tushare",
        device="cuda",
        use_batch_eval=True,
        batch_eval_device="cuda",
        point_in_time=True,
        matrix_cache_dir=str(tmp_path / "matrix"),
        feature_set_manifest_path=str(tmp_path / "feature.json"),
        canonical_feature_tensor_path=str(tmp_path / "values.npy"),
        canonical_feature_validity_tensor_path=str(tmp_path / "validity.npy"),
        canonical_research_view_manifest_path=None,
    )
    with pytest.raises(RuntimeError, match="canonical_freeze_manifest_required"):
        _validate_production_research_config(config)


def test_production_research_accepts_only_artifacts_inside_bounded_view(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    freeze = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze"), batch_rows=64)
    )
    view_root = Path(freeze["search_view"]["view_root"])
    config = AlphaCampaignConfig(
        campaign_name="bounded",
        data_dir=str(tmp_path / "raw_must_not_be_used"),
        output_dir=str(tmp_path / "out"),
        factor_store_dir=str(tmp_path / "factors"),
        data_freeze_dir=freeze["generation_dir"],
        require_data_freeze=True,
        production_research=True,
        provider="tushare",
        device="cuda",
        use_batch_eval=True,
        batch_eval_device="cuda",
        point_in_time=True,
        matrix_cache_dir=str(view_root / "derived" / "strict_matrix_manifest"),
        feature_set_manifest_path=str(view_root / "derived" / "feature_manifest" / "feature_set_manifest.json"),
        canonical_feature_tensor_path=str(view_root / "derived" / "feature_values" / "feature_tensor.npy"),
        canonical_feature_validity_tensor_path=str(
            view_root / "derived" / "feature_validity" / "feature_validity_tensor.npy"
        ),
        canonical_research_view_manifest_path=freeze["search_view_manifest_path"],
        research_end_date="20191231",
        holdout_start_date="20200101",
        label_horizon=2,
    )
    _validate_production_research_config(config)
    assert _resolve_data_dir(
        config.data_dir,
        config.data_freeze_dir,
        config.canonical_research_view_manifest_path,
        production_research=True,
    ) == str(view_root / "data")


def test_post_cutoff_rows_are_hashed_but_physically_excluded(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    margin = governed / "data" / "margin_summary" / "records.jsonl"
    row = _read_jsonl(margin)[0]
    row["trade_date"] = "20260701"
    _write_jsonl(margin, [row])
    _refresh_raw_index(governed)
    report = audit_canonical_freeze_sources(CanonicalFreezeConfig(str(governed), str(tmp_path / "preflight")))
    assert "margin_summary:source_requires_post_cutoff_filter" in report["warnings"]
    freeze = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze"), batch_rows=64)
    )
    quality = json.loads((Path(freeze["generation_dir"]) / "quality_report.json").read_text(encoding="utf-8"))
    result = next(item for item in quality["datasets"] if item["dataset"] == "margin_summary")
    assert result["source_record_count"] == 1
    assert result["record_count"] == 0
    assert result["excluded_post_cutoff_count"] == 1
    assert len(result["excluded_post_cutoff_row_hash_root"]) == 64


def test_duplicate_keys_and_price_anomalies_are_reported_and_block_core(tmp_path: Path) -> None:
    governed = _governed_fixture(tmp_path / "lake")
    daily = governed / "data" / "daily_bars" / "records.jsonl"
    rows = _read_jsonl(daily)
    duplicate = dict(rows[1])
    duplicate["open"] = -1.0
    rows.append(duplicate)
    _write_jsonl(daily, rows)
    _refresh_raw_index(governed)
    freeze = build_canonical_research_freeze(
        CanonicalFreezeConfig(str(governed), str(tmp_path / "freeze"), batch_rows=64)
    )
    quality = json.loads((Path(freeze["generation_dir"]) / "quality_report.json").read_text(encoding="utf-8"))
    result = next(row for row in quality["datasets"] if row["dataset"] == "daily_bars")
    assert result["duplicate_primary_key_count"] == 1
    assert result["anomaly_counts"]["invalid_price"] == 1
    assert "daily_bars:duplicate_primary_keys:1" in freeze["blockers"]
    assert freeze["alpha_search_authorized"] is False


def _governed_fixture(root: Path) -> Path:
    data = root / "data"
    rows_by_dataset: dict[str, list[dict[str, object]]] = {}
    for dataset in REQUIRED_DATASETS:
        rows_by_dataset[dataset] = [_generic_row(dataset, "20190102")]

    securities = []
    for index in range(300):
        code = f"{index + 1:06d}.SZ"
        securities.append(
            {
                "ts_code": code,
                "symbol": f"{index + 1:06d}",
                "exchange": "SZSE",
                "market": "主板",
                "board": "主板",
                "name": f"CURRENT-{index}",
                "raw_name": f"CURRENT-{index}",
                "area": "深圳",
                "industry": "CURRENT",
                "is_st": False,
                "list_status": "D" if index == 0 else "L",
                "list_date": "20100104",
                "delist_date": "20181231" if index == 0 else None,
            }
        )
    rows_by_dataset["securities"] = securities
    rows_by_dataset["trade_calendar"] = [
        {"trade_date": date, "is_open": 1}
        for date in ("20111230", "20190102", "20190103", "20200102", "20230103", "20250102")
    ]
    rows_by_dataset["daily_bars"] = [
        _daily_bar("000001.SZ", date, 10.0 + index)
        for index, date in enumerate(("20111230", "20190102", "20200102", "20230103", "20250102"))
    ]
    rows_by_dataset["daily_basic"] = [{"ts_code": "000001.SZ", "trade_date": "20190102", "total_mv": 1_000.0}]
    rows_by_dataset["daily_limits"] = [
        {"ts_code": "000001.SZ", "trade_date": "20190102", "up_limit": 11.0, "down_limit": 9.0}
    ]
    rows_by_dataset["adjustment_factors"] = [
        {"ts_code": "000001.SZ", "trade_date": "20190102", "adj_factor": 1.0}
    ]
    rows_by_dataset["st_status_daily"] = [
        {"ts_code": "000001.SZ", "name": "A", "trade_date": "20190102", "type": "ST", "type_name": "ST"}
    ]
    rows_by_dataset["suspensions"] = [
        {
            "ts_code": "000001.SZ",
            "trade_date": "20190102",
            "suspend_timing": "09:30-15:00",
            "suspend_type": "S",
        }
    ]
    rows_by_dataset["name_changes"] = [
        {
            "ts_code": "000001.SZ",
            "name": "A",
            "start_date": "20120101",
            "end_date": "20181231",
            "ann_date": "20111230",
            "change_reason": "name",
        }
    ]
    rows_by_dataset["industry_members"] = [
        {
            "ts_code": "000001.SZ",
            "l1_code": "I1",
            "l1_name": "one",
            "l2_code": "I11",
            "l2_name": "one-one",
            "l3_code": "I111",
            "l3_name": "one-one-one",
            "name": "A",
            "in_date": "20120103",
            "out_date": "20171229",
            "is_new": "N",
        },
        {
            "ts_code": "000001.SZ",
            "l1_code": "I2",
            "l1_name": "two",
            "l2_code": "I22",
            "l2_name": "two-two",
            "l3_code": "I222",
            "l3_name": "two-two-two",
            "name": "A",
            "in_date": "20180102",
            "out_date": "20191231",
            "is_new": "Y",
        },
    ]
    rows_by_dataset["index_members"] = [
        {
            "index_code": "000300.SH",
            "ts_code": f"{index + 1:06d}.SZ",
            "trade_date": "20190102",
            "weight": 100.0 / 300.0,
        }
        for index in range(300)
    ]
    rows_by_dataset["corporate_actions"] = [
        {
            "ts_code": "000001.SZ",
            "ann_date": "20190102",
            "end_date": "20181231",
            "ex_date": "20190103",
            "div_proc": "实施",
            "cash_div": 0.1,
            "cash_div_tax": 0.1,
            "stk_bo_rate": 0.0,
            "stk_co_rate": 0.0,
            "stk_div": 0.0,
            "record_date": "20190102",
            "pay_date": "20190104",
            "div_listdate": None,
        }
    ]

    index_rows = []
    for dataset, rows in rows_by_dataset.items():
        path = data / dataset / "records.jsonl"
        _write_jsonl(path, rows)
        index_row = {
            "dataset": dataset,
            "status": "fresh",
            "primary_key_fields": _dataset_contract(dataset)["primary_key"],
            "ts_code_count": len({str(row.get("ts_code")) for row in rows if row.get("ts_code")}),
        }
        _refresh_index_row(index_row, path)
        index_rows.append(index_row)
    report_dir = root / "reports" / "raw_index_fixture"
    report_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "status": "fresh",
        "reviewed_status": "fresh",
        "built_at": "2026-06-30T00:00:00Z",
        "profile_name": "task056c_fixture",
        "data_dir": str(data.resolve()),
        "dataset_count": len(index_rows),
        "datasets": index_rows,
        "index_hash": _canonical_hash(index_rows),
    }
    (report_dir / "raw_data_index_manifest.reviewed_fresh.json").write_text(
        json.dumps(index, sort_keys=True), encoding="utf-8"
    )
    _write_source_coverage(root, len(securities))
    _write_derived_bundle(root, len(securities))
    return root


def _generic_row(dataset: str, date: str) -> dict[str, object]:
    contract = _dataset_contract(dataset)
    fields = set(contract["required_fields"]) | set(contract["primary_key"])
    payload: dict[str, object] = {}
    for field in fields:
        if field in {"trade_date", "ann_date", "f_ann_date", "announce_date", "start_date", "begin_date", "list_date", "in_date"}:
            payload[field] = date
        elif field in {"end_date", "report_period", "close_date", "out_date", "effective_date", "ex_date", "float_date"}:
            payload[field] = "20181231"
        elif field == "ts_code":
            payload[field] = "000001.SZ"
        elif field == "index_code":
            payload[field] = "000300.SH"
        elif field in {"open", "high", "low", "close", "pre_close", "up_limit", "down_limit", "adj_factor", "weight"}:
            payload[field] = 10.0
        elif field in {"volume", "vol", "amount"}:
            payload[field] = 100.0
        elif field == "is_open":
            payload[field] = 1
        elif field == "suspend_type":
            payload[field] = "S"
        elif field == "suspend_timing":
            payload[field] = "09:30-15:00"
        elif field == "update_flag":
            payload[field] = "1"
        elif field == "list_status":
            payload[field] = "L"
        else:
            payload[field] = f"{dataset}-{field}"
    return payload


def _daily_bar(code: str, date: str, price: float) -> dict[str, object]:
    return {
        "ts_code": code,
        "trade_date": date,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price + 0.5,
        "pre_close": price - 0.5,
        "volume": 100.0,
        "amount": 1_000.0,
    }


def _write_source_coverage(root: Path, security_count: int) -> None:
    generation = root / "governance" / "canonical_source_coverage" / "generations" / "coverage"
    generation.mkdir(parents=True, exist_ok=True)
    semantic = {
        "schema_version": "canonical_source_coverage_manifest_v1",
        "datasets": [
            {
                "dataset": dataset,
                "complete": True,
                "security_count": security_count,
                "start_date": "20120101",
                "end_date": "20260630",
                "coverage_root": hashlib.sha256(dataset.encode()).hexdigest(),
                "negative_attestation_count": security_count - 1,
            }
            for dataset in ("st_status_daily", "suspensions", "name_changes")
        ],
    }
    manifest = semantic | {"content_hash": _canonical_hash(semantic)}
    manifest_path = generation / "canonical_source_coverage_manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    pointer = {
        "schema_version": "canonical_source_coverage_pointer_v1",
        "manifest": "generations/coverage/canonical_source_coverage_manifest.json",
        "content_hash": manifest["content_hash"],
    }
    pointer_path = root / "governance" / "canonical_source_coverage" / "current.json"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(json.dumps(pointer, sort_keys=True), encoding="utf-8")


def _write_derived_bundle(root: Path, stock_count: int) -> None:
    generation = root / "governance" / "canonical_derived" / "generations" / "derived"
    generation.mkdir(parents=True, exist_ok=True)
    stocks = [f"{index + 1:06d}.SZ" for index in range(stock_count)]
    dates = ["20190102", "20190103", "20190104"]
    features = ["RET_1D", "VOL_2D"]
    (generation / "ts_codes.json").write_text(json.dumps(stocks), encoding="utf-8")
    (generation / "trade_dates.json").write_text(json.dumps(dates), encoding="utf-8")
    (generation / "feature_names.json").write_text(json.dumps(features), encoding="utf-8")
    np.save(generation / "feature_tensor.npy", np.ones((stock_count, len(features), len(dates)), dtype=np.float32))
    np.save(
        generation / "feature_validity_tensor.npy",
        np.ones((stock_count, len(features), len(dates)), dtype=np.bool_),
    )
    np.save(generation / "target_available_mask.npy", np.ones((stock_count, len(dates)), dtype=np.bool_))
    (generation / "feature_set_manifest.json").write_text(
        json.dumps({"feature_names": features, "feature_axis_hash": _canonical_hash(features)}),
        encoding="utf-8",
    )
    matrix = {
        "shape": [stock_count, len(dates)],
        "stock_axis_hash": _canonical_hash(stocks),
        "date_axis_hash": _canonical_hash(dates),
        "partition_sha256": {
            "target_available_mask.npy": _sha256(generation / "target_available_mask.npy")
        },
    }
    (generation / "task_052a_strict_matrix_manifest.json").write_text(
        json.dumps(matrix, sort_keys=True), encoding="utf-8"
    )
    _refresh_derived_bundle(root)


def _refresh_derived_bundle(root: Path) -> None:
    generation = root / "governance" / "canonical_derived" / "generations" / "derived"
    roles = {
        "strict_matrix_manifest": "task_052a_strict_matrix_manifest.json",
        "stock_axis": "ts_codes.json",
        "date_axis": "trade_dates.json",
        "feature_axis": "feature_names.json",
        "feature_manifest": "feature_set_manifest.json",
        "feature_values": "feature_tensor.npy",
        "feature_validity": "feature_validity_tensor.npy",
        "target_availability": "target_available_mask.npy",
    }
    artifacts = []
    for role, filename in roles.items():
        path = generation / filename
        row = {
            "role": role,
            "relative_path": filename,
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        if role.endswith("axis"):
            row["axis_hash"] = _canonical_hash(json.loads(path.read_text(encoding="utf-8")))
        artifacts.append(row)
    semantic = {"schema_version": "canonical_ashare_derived_bundle_v1", "artifacts": artifacts}
    bundle = semantic | {"content_hash": _canonical_hash(semantic)}
    manifest = generation / "canonical_derived_bundle_manifest.json"
    manifest.write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")
    pointer = {
        "schema_version": "canonical_derived_pointer_v1",
        "manifest": "generations/derived/canonical_derived_bundle_manifest.json",
        "content_hash": bundle["content_hash"],
    }
    current = root / "governance" / "canonical_derived" / "current.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(json.dumps(pointer, sort_keys=True), encoding="utf-8")


def _refresh_raw_index(root: Path) -> None:
    path = _raw_index_path(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload["datasets"]:
        source = root / "data" / row["dataset"] / "records.jsonl"
        _refresh_index_row(row, source)
    payload["index_hash"] = _canonical_hash(payload["datasets"])
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _refresh_index_row(row: dict[str, object], source: Path) -> None:
    records = _read_jsonl(source)
    dates = []
    for record in records:
        for field in ("trade_date", "ann_date", "announce_date", "start_date", "in_date", "list_date", "end_date"):
            value = record.get(field)
            if isinstance(value, str) and len(value) == 8 and value.isdigit():
                dates.append(value)
                break
    row.update(
        {
            "records_path": str(source.resolve()),
            "records_sha256": _sha256(source),
            "file_size_bytes": source.stat().st_size,
            "record_count": len(records),
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
        }
    )


def _raw_index_path(root: Path) -> Path:
    return root / "reports" / "raw_index_fixture" / "raw_data_index_manifest.reviewed_fresh.json"


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
