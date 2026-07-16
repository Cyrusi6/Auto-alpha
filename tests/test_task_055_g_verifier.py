from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_a.run import SCENARIO_NAMES
from task_055_g.access import AccessBroker, canonical_hash
from task_055_g.contracts import ACCESS_PLAN_SCHEMA
from task_055_g.verifier import (
    SemanticVerificationError,
    _compare_causal,
    compare_truth_rows,
    rebuild_truth_rows,
    trace_causal_runs,
)


def test_independent_truth_rebuild_matches_exact_rows_and_rejects_forgery(tmp_path: Path) -> None:
    broker, parents = _truth_source_fixture(tmp_path)
    rebuilt = rebuild_truth_rows(parents=parents, broker=broker)
    assert rebuilt["record_count"] == 2
    assert rebuilt["state_counts"] == {
        "TRADED_PRIMARY_BAR": 1,
        "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE": 1,
    }
    resume = rebuilt["records"][0]
    suspended = rebuilt["records"][1]
    assert resume["suspend_type"] == "R"
    assert resume["state"] == "TRADED_PRIMARY_BAR"
    assert suspended["suspend_type"] == "S"
    assert suspended["modeled_stale_candidate"] is True

    producer = {
        "records": json.loads(json.dumps(rebuilt["records"])),
        "record_count": rebuilt["record_count"],
        "key_root": rebuilt["key_root"],
        "state_counts": rebuilt["state_counts"],
        "suspend_type_counts": rebuilt["suspend_type_counts"],
    }
    verified = compare_truth_rows(rebuilt, producer)
    assert verified["exact_rows_match"] is True
    assert verified["exact_rows_root"] == canonical_hash(rebuilt["records"])

    producer["records"][1]["state"] = "DATA_SOURCE_GAP"
    with pytest.raises(SemanticVerificationError, match="producer_truth_exact_row_mismatch"):
        compare_truth_rows(rebuilt, producer)


def test_independent_truth_comparison_accepts_matching_nan_payloads() -> None:
    expected_row = {
        "ts_code": "000001.SZ",
        "trade_date": "20240102",
        "matrix_bar": {"values": {"open": float("nan")}},
    }
    actual_row = {
        "ts_code": "000001.SZ",
        "trade_date": "20240102",
        "matrix_bar": {"values": {"open": float("nan")}},
    }
    independent = {
        "records": [expected_row],
        "record_count": 1,
        "key_root": "key-root",
        "state_counts": {},
        "suspend_type_counts": {},
    }
    producer = {
        "records": [actual_row],
        "record_count": 1,
        "key_root": "key-root",
        "state_counts": {},
        "suspend_type_counts": {},
    }

    verified = compare_truth_rows(independent, producer)

    assert verified["exact_rows_match"] is True


def test_independent_causal_reexecution_runs_exact20x5_and_rejects_forged_terminal() -> None:
    bundle, prepared, surface = _causal_fixture()
    rebuilt = trace_causal_runs(
        bundle=bundle,
        prepared=prepared,
        surface=surface,
        calculator=_ZeroFeeCalculator(),
    )
    assert len(rebuilt["run_rows"]) == 100
    assert len({(row["factor_id"], row["scenario"]) for row in rebuilt["run_rows"]}) == 100
    assert rebuilt["terminal_counts"] == {"causal_trace_completed": 100}
    assert rebuilt["held_rows"]
    assert all(row["shares"] > 0 for row in rebuilt["held_rows"])
    assert rebuilt["round_one_frontier_count"] == 0

    producer = {
        "exact20_ids": list(bundle["manifest"]["exact20_ids"]),
        "run_count": len(rebuilt["run_rows"]),
        "run_rows": json.loads(json.dumps(rebuilt["run_rows"])),
        "held_marks": json.loads(json.dumps(rebuilt["held_rows"])),
        "run_rows_root": rebuilt["run_rows_root"],
        "held_mark_root": rebuilt["held_mark_root"],
        "missing_key_root": rebuilt["missing_key_root"],
        "terminal_counts": rebuilt["terminal_counts"],
        "round_one_frontier_count": rebuilt["round_one_frontier_count"],
        "held_mark_count": rebuilt["held_mark_count"],
        "authorized_modeled_held_mark_count": rebuilt["authorized_modeled_held_mark_count"],
    }
    _compare_causal(producer, rebuilt)

    producer["run_rows"][0]["terminal_state"] = "causal_valuation_blocked"
    with pytest.raises(SemanticVerificationError, match="producer_causal_run_semantics_mismatch"):
        _compare_causal(producer, rebuilt)


def test_independent_causal_comparison_rejects_held_mark_and_frontier_summary_forgery() -> None:
    bundle, prepared, surface = _causal_fixture()
    rebuilt = trace_causal_runs(
        bundle=bundle,
        prepared=prepared,
        surface=surface,
        calculator=_ZeroFeeCalculator(),
    )
    producer = {
        "exact20_ids": list(bundle["manifest"]["exact20_ids"]),
        "run_count": len(rebuilt["run_rows"]),
        "run_rows": rebuilt["run_rows"],
        "held_marks": rebuilt["held_rows"],
        "run_rows_root": rebuilt["run_rows_root"],
        "held_mark_root": "0" * 64,
        "missing_key_root": "1" * 64,
        "terminal_counts": rebuilt["terminal_counts"],
        "round_one_frontier_count": rebuilt["round_one_frontier_count"],
        "held_mark_count": rebuilt["held_mark_count"],
        "authorized_modeled_held_mark_count": rebuilt["authorized_modeled_held_mark_count"],
    }
    with pytest.raises(SemanticVerificationError, match="producer_causal_held_mark_root_mismatch"):
        _compare_causal(producer, rebuilt)


class _ZeroFeeCalculator:
    def calculate(self, **_: object) -> dict[str, float]:
        values = {
            "commission": 0.0,
            "stamp_duty": 0.0,
            "transfer_fee": 0.0,
            "handling_fee": 0.0,
            "securities_management_fee": 0.0,
            "slippage": 0.0,
            "impact": 0.0,
        }
        return values | {"total": 0.0}


def _causal_fixture() -> tuple[dict, dict, dict]:
    factor_ids = [f"factor_{index:02d}" for index in range(20)]
    dates = ["20240527", "20240528", "20240529", "20240530"]
    assets = ["000001.SZ", "600000.SH"]
    date_count, asset_count, signal_count = 4, 2, 2
    open_values = np.asarray(
        [[10.0, 20.0], [10.1, 20.1], [10.2, 20.2], [10.3, 20.3]], dtype=float
    )
    close_values = open_values + 0.05
    prepared = {
        "market": {
            "dates": dates,
            "assets": assets,
            "open": open_values,
            "close": close_values,
            "valuation_open": open_values.copy(),
            "valuation_close": close_values.copy(),
            "adv": np.full((date_count, asset_count), 1_000_000.0),
        },
        "buy": np.ones((date_count, asset_count), dtype=bool),
        "sell": np.ones((date_count, asset_count), dtype=bool),
        "signal_common": np.ones((signal_count, asset_count), dtype=bool),
        "factor_values": {
            factor_id: np.asarray([[2.0 + index, 2.0 + index], [1.0, 1.0]], dtype=float)
            for index, factor_id in enumerate(factor_ids)
        },
        "factor_validity": {
            factor_id: np.ones((asset_count, signal_count), dtype=bool) for factor_id in factor_ids
        },
        "corporate_actions": [],
        "signal_count": signal_count,
    }
    metadata = {}
    for point, method in (("open", "OFFICIAL_OPEN"), ("close", "OFFICIAL_CLOSE")):
        metadata[point] = {
            "method": np.full((date_count, asset_count), method, dtype=object),
            "source_date": np.repeat(np.asarray(dates, dtype=object)[:, None], asset_count, axis=1),
            "stale_age": np.zeros((date_count, asset_count), dtype=np.int32),
            "evidence_id": np.full((date_count, asset_count), f"{point}:evidence", dtype=object),
        }
    surface = {
        "values": {"open": open_values.copy(), "close": close_values.copy()},
        "metadata": metadata,
        "blockers": {},
    }
    bundle = {"manifest": {"exact20_ids": factor_ids}}
    assert tuple(SCENARIO_NAMES) == tuple(
        [
            "baseline",
            "zero_cost_accounting",
            "double_modeled_cost",
            "participation_5_percent",
            "aum_10_million",
        ]
    )
    return bundle, prepared, surface


def _truth_source_fixture(tmp_path: Path) -> tuple[AccessBroker, dict]:
    root = tmp_path / "governed"
    matrix = root / "matrix"
    provenance_root = root / "provenance"
    cache_root = root / "cache"
    for directory in (matrix, provenance_root, cache_root):
        directory.mkdir(parents=True)

    cells = [
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240102",
            "bar_observed": True,
            "lifecycle": {"listed": True, "active": True},
            "membership": True,
            "membership_known": True,
            "corporate_action_validity": True,
            "valuation_closure_domain": True,
        },
        {
            "ts_code": "600000.SH",
            "trade_date": "20240102",
            "bar_observed": False,
            "lifecycle": {"listed": True, "active": True},
            "membership": True,
            "membership_known": True,
            "corporate_action_validity": True,
            "valuation_closure_domain": True,
        },
    ]
    cells_path = root / "inventory_cells.jsonl"
    _write_jsonl(cells_path, cells)
    inventory_semantic = {
        "schema_version": "task055b_security_date_gap_inventory_v1",
        "cell_count": len(cells),
        "partitions": {
            "cells": {
                "path": cells_path.name,
                "sha256": _sha(cells_path),
                "size_bytes": cells_path.stat().st_size,
            }
        },
    }
    inventory_path = root / "inventory.json"
    _write_json(inventory_path, inventory_semantic)

    codes = ["000001.SZ", "600000.SH"]
    dates = ["20240102"]
    _write_json(matrix / "ts_codes.json", codes)
    _write_json(matrix / "trade_dates.json", dates)
    partitions = {
        "ts_codes.json": _sha(matrix / "ts_codes.json"),
        "trade_dates.json": _sha(matrix / "trade_dates.json"),
    }
    for field, values in {
        "open": [10.0, 0.0],
        "high": [10.5, 0.0],
        "low": [9.8, 0.0],
        "close": [10.2, 0.0],
        "pre_close": [10.0, 0.0],
        "volume": [1000.0, 0.0],
        "amount": [10000.0, 0.0],
    }.items():
        value_path = matrix / f"{field}.npy"
        valid_path = matrix / f"{field}_validity.npy"
        np.save(value_path, np.asarray(values, dtype=np.float32).reshape(2, 1), allow_pickle=False)
        np.save(valid_path, np.asarray([True, False], dtype=bool).reshape(2, 1), allow_pickle=False)
        partitions[value_path.name] = _sha(value_path)
        partitions[valid_path.name] = _sha(valid_path)
    matrix_manifest = {
        "content_hash": "m" * 64,
        "partition_sha256": partitions,
    }
    _write_json(matrix / "task_052a_strict_matrix_manifest.json", matrix_manifest)

    provenance_rows: list[dict] = []
    provenance_path = provenance_root / "row_provenance.jsonl"
    _write_jsonl(provenance_path, provenance_rows)
    provenance_manifest = {
        "partitions": {
            "row_provenance": {
                "path": provenance_path.name,
                "sha256": _sha(provenance_path),
                "size_bytes": provenance_path.stat().st_size,
            }
        }
    }
    provenance_manifest_path = provenance_root / "manifest.json"
    _write_json(provenance_manifest_path, provenance_manifest)

    ledger_rows = []
    for code, event_type in (("000001.SZ", "R"), ("600000.SH", "S")):
        cache_name = f"{code}.json"
        request = {
            "api_name": "suspend_d",
            "params": {"ts_code": code, "start_date": "20240102", "end_date": "20240102"},
            "fields": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
        }
        records = [
            {
                "ts_code": code,
                "trade_date": "20240102",
                "suspend_timing": None,
                "suspend_type": event_type,
            }
        ]
        fingerprint = canonical_hash(request)
        envelope = {
            "schema_version": "tushare_cache_envelope.v3",
            "request": request,
            "request_fingerprint": fingerprint,
            "code_semantic_hash": "c" * 64,
            "response": {
                "code": 0,
                "complete": True,
                "item_count": len(records),
                "records_sha256": stable_json_hash(records),
            },
            "records": records,
        }
        cache_path = cache_root / cache_name
        _write_json(cache_path, envelope)
        ledger_rows.append(
            {
                "ts_code": code,
                "status": "success",
                "api_name": "suspend_d",
                "code_semantic_hash": "c" * 64,
                "endpoint": "https://api.tushare.pro",
                "provider_api_version": "v3",
                "slices": [
                    {
                        "start_date": "20240102",
                        "end_date": "20240102",
                        "cache_key": cache_name,
                        "cache_sha256": _sha(cache_path),
                        "normalized_request": request,
                        "request_fingerprint": fingerprint,
                        "endpoint": "https://api.tushare.pro",
                        "provider_api_version": "v3",
                    }
                ],
            }
        )
    ledger_path = root / "suspension_coverage.jsonl"
    _write_jsonl(ledger_path, ledger_rows)

    entries = []
    for path, role, mode, parser in [
        (inventory_path, "security_date_inventory", "json", "inventory"),
        (cells_path, "security_date_inventory_cells", "jsonl", "inventory"),
        (matrix / "task_052a_strict_matrix_manifest.json", "strict_matrix_manifest", "json", "manifest_metadata"),
        (matrix / "ts_codes.json", "strict_matrix_partition:ts_codes.json", "json", "none"),
        (matrix / "trade_dates.json", "strict_matrix_partition:trade_dates.json", "json", "date_axis"),
        (provenance_manifest_path, "task055e_provenance_manifest", "json", "provenance"),
        (provenance_path, "task055e_provenance_partition:row_provenance", "jsonl", "provenance"),
        (ledger_path, "suspension_coverage_ledger", "jsonl", "provenance"),
    ]:
        entries.append(_entry(root, path, role, mode, parser))
    for path in sorted(matrix.glob("*.npy")):
        entries.append(_entry(root, path, f"strict_matrix_partition:{path.name}", "npy", "binary_declared_axis"))
    for path in sorted(cache_root.glob("*.json")):
        entries.append(_entry(root, path, "indexed_suspend_cache", "json", "suspension_envelope"))
    plan_semantic = {
        "schema_version": ACCESS_PLAN_SCHEMA,
        "status": "sealed",
        "plan_scope": "verifier_test",
        "max_allowed_date": "20260630",
        "entry_count": len(entries),
        "entries_root": canonical_hash(entries),
        "entries": entries,
    }
    plan = plan_semantic | {"content_hash": canonical_hash(plan_semantic), "generation_id": "test"}
    plan_path = root / "access_plan.json"
    _write_json(plan_path, plan)
    parents = {
        "inventory_manifest": str(inventory_path.relative_to(root)),
        "inventory_cells": str(cells_path.relative_to(root)),
        "matrix_root": str(matrix.relative_to(root)),
        "matrix_manifest": str((matrix / "task_052a_strict_matrix_manifest.json").relative_to(root)),
        "task055e_provenance_manifest": str(provenance_manifest_path.relative_to(root)),
        "suspension_coverage_ledger": str(ledger_path.relative_to(root)),
        "suspension_cache_root": str(cache_root.relative_to(root)),
    }
    return AccessBroker(root, plan_path), parents


def _entry(root: Path, path: Path, role: str, mode: str, parser: str) -> dict:
    return {
        "relative_path": str(path.relative_to(root)),
        "dataset_role": role,
        "parent_generation": "fixture",
        "expected_sha256": _sha(path),
        "read_mode": mode,
        "date_parser": parser,
        "declared_min_date": None,
        "declared_max_date": "20260630" if parser not in {"none", "binary_declared_axis"} else None,
        "byte_range": None,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
