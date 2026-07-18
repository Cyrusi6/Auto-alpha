from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from data_lake.task052_freeze import create_task052_governed_freeze
from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareResponseEnvelope,
)
from data_pipeline.ashare.request_normalization import tushare_code_semantic_hash
from factor_store.hash import make_factor_id, stable_formula_hash
from factor_store.models import FactorRecord
from factor_store.storage import LocalFactorStore
from feature_factory.catalog import FEATURE_SET_V3, build_feature_set_manifest
from feature_factory.semantics import build_feature_semantics_map
from feature_factory.vocab_adapter import make_formula_vocab_from_manifest
from matrix_store.strict_engineering import StrictEngineeringPITMatrixBuilder, StrictEngineeringPITMatrixConfig
from model_core.vm import StackVM
from task_053_a.orchestrator import build_v3_tensor_generation
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file
from universe.task052 import Task052HistoricalUniverseProofBuilder

from .application import (
    _materialize_exact20,
    apply_rehearsal_canary_response,
    apply_rehearsal_suspend_response,
    validate_native_response_application,
)
from .contracts import CANARY, REHEARSAL_SCHEMA, RUNTIME_AUTHORITY_SCHEMA
from .executor import (
    Task055IExecutionError,
    execute_canary_rehearsal,
    verify_and_accept_canary_rehearsal,
)
from .ledger import HashChainLedger, count_events


class Task055IRehearsalError(RuntimeError):
    pass


_PLAN_LINEAGE = {
    "access_plan_content_hash": "cd7049d6d5a68ac782079546b96505c94b7149a769c7d10830304484b6dd9a1f",
    "builder_code_hash": "13f9173fad540fab56afdc89568018ed96b4a4bedec2594542147ff384b12522",
    "fee_schedule_content_hash": "2540a60e0c9103135aa104bbdabfe8e2b371e5f317b31d8348dcff5445cc9a75",
    "frontier_root": "fd7e9a1468d8b5960767c2c3e4877c6cfa646a9051b8a6b2ba95f5573fb77b6f",
    "key_root": "42d8fe6a9e8cf4801ca4d93814b9801ba9d884670f4c01389b6eb678e05e5b57",
    "matrix_content_hash": "73699526ca22815ce0f0aabc8ded0adc1301d0e4f885a0698f50af0a02bf3a7f",
    "parent_lineage_content_hash": "610d70cde74031ce42252fa78a99bc15f142b6f8a69e870d416cc06cdb0ee823",
    "parent_task055g_plan_hash": "397ac8d5190ab492c65d5f947df69e845db517b0358330c95db365186aec1e6a",
    "simulation_bundle_content_hash": "0be5ee96c4fddbed4202f1f4de1124dd87cb7871d18ef5be44f5859b93454e58",
    "truth_v2_content_hash": "d4fe2a51ca9119ee9318a1d34eb2f46972a4e7123b5defe6e21f07e94e5e3ac9",
}


class _SyntheticFeeCalculator:
    def calculate(
        self,
        *,
        date: str,
        market: str,
        side: str,
        notional: float,
        shares: int,
        zero_all_costs: bool,
        modeled_multiplier: float,
    ) -> dict[str, float]:
        if zero_all_costs:
            return {name: 0.0 for name in (
                "commission", "stamp_duty", "transfer_fee", "handling_fee",
                "securities_management_fee", "slippage", "impact", "total",
            )}
        statutory = {
            "stamp_duty": notional * (0.0005 if side == "SELL" else 0.0),
            "transfer_fee": notional * 0.00001,
            "handling_fee": notional * 0.0000341,
            "securities_management_fee": notional * 0.00002,
        }
        modeled = {
            "commission": max(5.0, notional * 0.0003) * modeled_multiplier,
            "slippage": notional * 0.0005 * modeled_multiplier,
            "impact": notional * 0.0005 * modeled_multiplier,
        }
        result = statutory | modeled
        result["total"] = float(sum(result.values()))
        return result


def run_native_application_rehearsal(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    fixture = _build_fixture(root / "fixture")

    positive_authority = _publish_rehearsal_authority(root / "positive_authority", fixture)
    positive_execution = execute_canary_rehearsal(
        runtime_authority=positive_authority["manifest_path"],
        reviewed_authority_hash=positive_authority["content_hash"],
        transport=lambda request: _envelope(request, [fixture["canary_row"]]),
    )
    positive_acceptance = verify_and_accept_canary_rehearsal(
        runtime_authority=positive_authority["manifest_path"],
        reviewed_authority_hash=positive_authority["content_hash"],
    )
    positive_application = apply_rehearsal_canary_response(
        runtime_authority=positive_authority["manifest_path"],
        canary_acceptance=positive_acceptance["manifest_path"],
        context=fixture,
        output_root=root / "positive_application",
    )
    validate_native_response_application(positive_application["manifest_path"])

    empty_authority = _publish_rehearsal_authority(root / "empty_authority", fixture)
    execute_canary_rehearsal(
        runtime_authority=empty_authority["manifest_path"],
        reviewed_authority_hash=empty_authority["content_hash"],
        transport=lambda request: _envelope(request, []),
    )
    empty_acceptance = verify_and_accept_canary_rehearsal(
        runtime_authority=empty_authority["manifest_path"],
        reviewed_authority_hash=empty_authority["content_hash"],
    )
    empty_application = apply_rehearsal_canary_response(
        runtime_authority=empty_authority["manifest_path"],
        canary_acceptance=empty_acceptance["manifest_path"],
        context=fixture,
        output_root=root / "empty_application",
    )
    empty_payload = validate_native_response_application(empty_application["manifest_path"])
    l2_request = _load_dynamic_l2(Path(empty_application["stage_root"]))
    suspend_cases = {}
    for name, records in {
        "s": [{"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"], "suspend_type": "S", "suspend_timing": None}],
        "r": [{"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"], "suspend_type": "R", "suspend_timing": None}],
        "empty": [],
    }.items():
        suspend_cases[name] = apply_rehearsal_suspend_response(
            request=l2_request,
            records=records,
            parent_application=empty_application["manifest_path"],
            output_root=root / "suspend_cases" / name,
        )

    negative = {
        "cache_corruption": _exercise_cache_corruption(root / "negative_cache", fixture),
        "crash_after_cache": _exercise_crash_recovery(root / "negative_crash", fixture),
        "concurrent_single_flight": _exercise_concurrency(root / "negative_concurrency", fixture),
        "fresh_root_budget_reset": _exercise_fresh_root_rejection(root / "negative_fresh_root", fixture),
        "forged_first_key": _exercise_forged_key(root / "negative_forged_key", fixture),
        "forged_authority": _exercise_forged_authority(root / "negative_forged_authority", fixture),
        "source_drift": _exercise_source_drift(root / "negative_source_drift", fixture),
        "legacy_entrypoints": _legacy_entrypoint_contract(),
    }
    if not all(row.get("passed") is True for row in negative.values()):
        raise Task055IRehearsalError(
            "task055i_negative_rehearsal_failed:" + json.dumps(negative, sort_keys=True)
        )

    positive_payload = validate_native_response_application(positive_application["manifest_path"])
    artifacts = {
        "fixture_freeze": fixture["freeze_content_hash"],
        "fixture_matrix": fixture["matrix_content_hash"],
        "fixture_tensor": fixture["tensor_content_hash"],
        "positive_execution": positive_execution["content_hash"],
        "positive_acceptance": positive_acceptance["content_hash"],
        "positive_application": positive_payload["content_hash"],
        "positive_raw_repair": positive_payload["stage_outputs"]["raw_repair"],
        "positive_repaired_freeze": positive_payload["stage_outputs"]["freeze"],
        "positive_repaired_matrix": positive_payload["stage_outputs"]["matrix"],
        "positive_repaired_tensor": positive_payload["stage_outputs"]["tensor"],
        "positive_firewall_sentinel": positive_payload["stage_outputs"]["firewall_sentinel"],
        "positive_exact20_materialization": positive_payload["stage_outputs"]["exact20_materialization"],
        "positive_exact20_x5": positive_payload["stage_outputs"]["fee_aware_exact20_x5"],
        "empty_application": empty_payload["content_hash"],
        "empty_dynamic_l2": empty_payload["stage_outputs"]["dynamic_l2"],
        "s_apply": suspend_cases["s"]["content_hash"],
        "r_apply": suspend_cases["r"]["content_hash"],
        "empty_suspend_apply": suspend_cases["empty"]["content_hash"],
    }
    semantic = {
        "schema_version": REHEARSAL_SCHEMA,
        "status": "passed",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "positive_chain_complete": True,
        "positive_terminal_pair_count": 100,
        "positive_terminal_counts": positive_payload["terminal_counts"],
        "empty_dynamic_l2_generated": True,
        "s_outcome": validate_native_response_application(suspend_cases["s"]["manifest_path"])["action"]["outcome"],
        "r_outcome": validate_native_response_application(suspend_cases["r"]["manifest_path"])["action"]["outcome"],
        "empty_suspend_outcome": validate_native_response_application(suspend_cases["empty"]["manifest_path"])["action"]["outcome"],
        "negative_case_count": len(negative),
        "negative_cases": negative,
        "artifact_hashes": artifacts,
        "artifact_root": canonical_hash(artifacts),
        "real_network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": False,
        },
    }
    result = publish_generation(
        root / "report",
        prefix="native_application_rehearsal",
        manifest_name="rehearsal_manifest.json",
        semantic=semantic,
    )
    return result


def _build_fixture(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True)
    dates = _business_dates(date(2016, 1, 4), date(2016, 8, 31))
    if CANARY["trade_date"] not in dates:
        raise Task055IRehearsalError("task055i_fixture_canary_date_missing")
    codes = [f"{index:06d}.SZ" for index in range(1, 300)] + [CANARY["ts_code"]]
    codes = sorted(set(codes))
    if len(codes) != 300:
        raise Task055IRehearsalError("task055i_fixture_stock_count_invalid")
    source = root / "source"
    calendar = source / "trade_calendar.jsonl"
    members = source / "index_members.jsonl"
    _write_jsonl(calendar, ({"trade_date": value, "is_open": True} for value in dates))
    snapshots = []
    monthly_snapshots = []
    seen_months: set[str] = set()
    for trade_date in dates:
        month = trade_date[:6]
        if month not in seen_months:
            seen_months.add(month)
            monthly_snapshots.append(trade_date)
    for snapshot in monthly_snapshots:
        snapshots.extend({
            "index_code": "000300.SH",
            "trade_date": snapshot,
            "ts_code": code,
            "weight": 100.0 / 300.0,
        } for code in codes)
    _write_jsonl(members, snapshots)
    lineage = source / "lineage.json"
    lineage.write_text(json.dumps({
        "index_members_sha256": sha256_file(members),
        "trade_calendar_sha256": sha256_file(calendar),
        "evidence_scope": "synthetic_rehearsal_only",
    }, sort_keys=True) + "\n", encoding="utf-8")
    universe = Task052HistoricalUniverseProofBuilder().build(members, calendar, lineage, root / "universe")

    bars = source / "daily_bars.jsonl"
    adjustments = source / "adjustment_factors.jsonl"
    limits = source / "daily_limits.jsonl"
    securities = source / "securities.jsonl"
    suspensions = source / "suspensions.jsonl"
    suspension_coverage = source / "suspension_coverage.jsonl"
    stock_st = source / "stock_st.jsonl"
    stock_st_coverage = source / "stock_st_coverage.jsonl"
    daily_basic = source / "daily_basic.jsonl"
    bar_rows = []
    adjustment_rows = []
    limit_rows = []
    basic_rows = []
    canary_row = None
    for stock_index, code in enumerate(codes):
        for date_index, trade_date in enumerate(dates):
            trend = 8.0 + stock_index * 0.013 + date_index * (0.006 + (stock_index % 7) * 0.0002)
            wave = math_sin((date_index + stock_index % 13) / 7.0) * 0.12
            raw_open = round(trend + wave, 4)
            raw_close = round(raw_open * (1.0 + ((stock_index % 11) - 5) * 0.0004 + (date_index % 5 - 2) * 0.0003), 4)
            row = {
                "ts_code": code,
                "trade_date": trade_date,
                "open": raw_open,
                "high": round(max(raw_open, raw_close) + 0.18, 4),
                "low": round(min(raw_open, raw_close) - 0.18, 4),
                "close": raw_close,
                "pre_close": round(raw_open * 0.997, 4),
                "vol": float(800000 + stock_index * 1000 + date_index * 300),
                "amount": float((800000 + stock_index * 1000 + date_index * 300) * raw_close),
            }
            if code == CANARY["ts_code"] and trade_date == CANARY["trade_date"]:
                canary_row = row
            else:
                bar_rows.append(row)
            adjustment_rows.append({"ts_code": code, "trade_date": trade_date, "adj_factor": 1.0})
            limit_rows.append({
                "ts_code": code,
                "trade_date": trade_date,
                "up_limit": round(raw_open * 1.1, 4),
                "down_limit": round(raw_open * 0.9, 4),
            })
            basic_rows.append({
                "ts_code": code,
                "trade_date": trade_date,
                "turnover_rate": 1.0 + stock_index % 9,
                "volume_ratio": 0.8 + date_index % 5 * 0.1,
                "total_mv": 1_000_000.0 + stock_index * 10_000.0,
                "pb": 1.0 + stock_index % 10 * 0.1,
                "pe_ttm": 8.0 + stock_index % 30,
            })
    if canary_row is None:
        raise Task055IRehearsalError("task055i_fixture_canary_row_missing")
    _write_jsonl(bars, bar_rows)
    _write_jsonl(adjustments, adjustment_rows)
    _write_jsonl(limits, limit_rows)
    _write_jsonl(daily_basic, basic_rows)
    _write_jsonl(securities, ({"ts_code": code, "list_date": "20100101", "delist_date": None} for code in codes))
    _write_jsonl(suspensions, [])
    coverage = [{"ts_code": code, "start_date": dates[0], "end_date": dates[-1], "validated": True} for code in codes]
    _write_jsonl(suspension_coverage, coverage)
    _write_jsonl(stock_st, [])
    _write_jsonl(stock_st_coverage, coverage)
    artifacts = {
        "daily_bars": bars,
        "adjustment_factors": adjustments,
        "daily_limits": limits,
        "daily_basic": daily_basic,
        "securities": securities,
        "suspensions": suspensions,
        "suspension_coverage_ledger": suspension_coverage,
        "stock_st": stock_st,
        "stock_st_coverage_ledger": stock_st_coverage,
    }
    freeze = create_task052_governed_freeze(artifacts, root / "freeze", source_lineage_manifest_path=lineage)
    matrix = StrictEngineeringPITMatrixBuilder(StrictEngineeringPITMatrixConfig(
        min_cross_section_breadth=30,
        research_observable_cutoff="20240530",
    )).build(
        governed_freeze_dir=freeze.generation_dir,
        historical_universe_dir=universe.generation_dir,
        output_root=root / "matrix",
    )
    feature_manifest = build_feature_set_manifest(
        FEATURE_SET_V3,
        corporate_action_aware=True,
        point_in_time=True,
        target_return_mode="target_open_t1_t2",
        created_at="1970-01-01T00:00:00Z",
    )
    feature_manifest_path = root / "feature_set_manifest.json"
    feature_manifest_path.write_text(json.dumps(feature_manifest.to_dict(), sort_keys=True) + "\n", encoding="utf-8")
    tensor = build_v3_tensor_generation(
        matrix_dir=matrix.generation_dir,
        feature_manifest_path=feature_manifest_path,
        output_root=root / "tensor",
    )
    promotion = root / "promotion_policy.json"
    promotion.write_text(json.dumps({
        "policy_id": "synthetic_rehearsal_promotion_v1",
        "feature_set_name": FEATURE_SET_V3,
        "feature_set_hash": feature_manifest.content_hash,
        "alpha_eligible_features": [row["feature_name"] for row in feature_manifest.feature_definitions],
        "evidence_scope": "synthetic_rehearsal_only",
    }, sort_keys=True) + "\n", encoding="utf-8")
    factors = _build_factors(feature_manifest)
    factor_store = root / "normalized_store"
    store = LocalFactorStore(factor_store)
    for factor in factors:
        store.save_factor(factor)
    exact_root = canonical_hash([as_factor_identity(factor) for factor in factors])
    parent_materializations = _materialize_exact20(
        factors=factors,
        freeze_root=Path(freeze.generation_dir),
        matrix_root=Path(matrix.generation_dir),
        tensor_root=Path(tensor["generation_dir"]),
        feature_manifest=feature_manifest_path,
        promotion_policy=promotion,
        output_root=root / "parent_materializations",
        research_cutoff="20240530",
    )
    return {
        "freeze_root": freeze.generation_dir,
        "freeze_content_hash": freeze.content_hash,
        "universe_root": universe.generation_dir,
        "matrix_root": matrix.generation_dir,
        "matrix_content_hash": matrix.content_hash,
        "tensor_root": tensor["generation_dir"],
        "tensor_content_hash": tensor["content_hash"],
        "feature_manifest": str(feature_manifest_path),
        "promotion_policy": str(promotion),
        "factors": factors,
        "factor_store": str(factor_store),
        "parent_materializations": parent_materializations,
        "exact20_identity_root": exact_root,
        "research_cutoff": "20240530",
        "truth_content_hash": canonical_hash(["synthetic_parent_truth", CANARY]),
        "fee_calculator": _SyntheticFeeCalculator(),
        "canary_row": canary_row,
    }


def _publish_rehearsal_authority(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("network_ledger", "transport_spend", "cache_data", "executions", "acceptance", "runtime_authority"):
        (root / name).mkdir(exist_ok=True)
    (root / "single_flight.lock").touch()
    network = HashChainLedger(root / "network_ledger", name="network")
    spend = HashChainLedger(root / "transport_spend", name="transport")
    network.append({"event_id": "authority-initialized", "event": "authority_initialized"})
    spend.append({"event_id": "transport-initialized", "event": "transport_authority_initialized"})
    plan = _single_request_plan()
    fixture_root = canonical_hash([
        fixture["freeze_content_hash"], fixture["matrix_content_hash"], fixture["tensor_content_hash"]
    ])
    fixture_binding = root / "fixture_binding.json"
    fixture_binding.write_text(
        json.dumps({"application_fixture_root": fixture_root}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    semantic = {
        "schema_version": RUNTIME_AUTHORITY_SCHEMA,
        "status": "sealed_synthetic_rehearsal",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "authority_root": str(root),
        "root_identities": {
            role: _root_identity(path)
            for role, path in {
                "authority": root,
                "state": root / "network_ledger",
                "cache": root / "cache_data",
                "spend": root / "transport_spend",
            }.items()
        },
        "canonical_subroots": {
            "network_ledger": "network_ledger",
            "transport_spend": "transport_spend",
            "cache_data": "cache_data",
            "executions": "executions",
            "acceptance": "acceptance",
            "single_flight_lock": "single_flight.lock",
        },
        "canary": dict(CANARY),
        "single_request_plan": plan,
        "single_request_plan_hash": plan["plan_hash"],
        "reviewed_plan_hash": plan["plan_hash"],
        "initial_network_ledger": {"sequence": len(network.rows()), "root": network.root_hash()},
        "initial_transport_spend": {"sequence": len(spend.rows()), "root": spend.root_hash()},
        "budgets": {"physical_attempts": 0, "limits": {"physical_attempts": 160}},
        "resume_authorized": False,
        "batch_authorized": False,
        "application_fixture_root": fixture_root,
        "fixture_binding_sha256": sha256_file(fixture_binding),
    }
    return publish_generation(
        root / "runtime_authority",
        prefix="runtime_authority",
        manifest_name="runtime_authority.json",
        semantic=semantic,
    )


def _single_request_plan() -> dict[str, Any]:
    request = {
        "api_name": "daily",
        "params": {"ts_code": CANARY["ts_code"], "trade_date": CANARY["trade_date"]},
        "fields": list(CANARY["fields"]),
        "ts_code": CANARY["ts_code"],
        "trade_date": CANARY["trade_date"],
        "transport_hash": CANARY["transport_hash"],
        "evidence_use_hash": CANARY["evidence_use_hash"],
        "round_id": 1,
        "stage": "L1",
    }
    return {
        "schema_version": "task055g_dynamic_network_plan_v1",
        "status": "sealed_single_exact_daily_canary_only",
        "stage": "L1",
        "round_id": 1,
        "frontier_root": _PLAN_LINEAGE["frontier_root"],
        "parent_apply_hash": None,
        "lineage": dict(_PLAN_LINEAGE),
        "requests": [request],
        "limits": {"unique_security_dates": 64, "logical_requests": 128, "physical_attempts": 160},
        "must_stop_after_canary": True,
        "batch_authorized": False,
        "plan_hash": "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae",
    }


def _envelope(request: Mapping[str, Any], rows: list[dict[str, Any]]) -> TushareResponseEnvelope:
    return TushareResponseEnvelope(
        api_name=request["api_name"],
        params_without_token=dict(request["params"]),
        requested_fields=",".join(request["fields"]),
        response_code=0,
        response_message="",
        response_fields=list(request["fields"]),
        records=[dict(row) for row in rows],
        item_count=len(rows),
        duration_seconds=0.001,
        request_fingerprint=request["transport_hash"],
        code_semantic_hash=tushare_code_semantic_hash(),
        endpoint=CANONICAL_ORIGIN,
        provider_api_version=TUSHARE_PROVIDER_API_VERSION,
        response_payload_hash=canonical_hash(rows),
    )


def _exercise_cache_corruption(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root, fixture)
    execution = execute_canary_rehearsal(
        runtime_authority=authority["manifest_path"],
        reviewed_authority_hash=authority["content_hash"],
        transport=lambda request: _envelope(request, [fixture["canary_row"]]),
    )
    cache = root / execution["cache_relative_path"]
    cache.write_bytes(cache.read_bytes() + b"\n")
    try:
        verify_and_accept_canary_rehearsal(
            runtime_authority=authority["manifest_path"],
            reviewed_authority_hash=authority["content_hash"],
        )
    except Exception:
        return {"passed": True, "blocker": "cache_drift_rejected"}
    return {"passed": False}


def _exercise_crash_recovery(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root, fixture)
    calls = {"count": 0}
    def transport(request: Mapping[str, Any]) -> TushareResponseEnvelope:
        calls["count"] += 1
        return _envelope(request, [fixture["canary_row"]])
    try:
        execute_canary_rehearsal(
            runtime_authority=authority["manifest_path"],
            reviewed_authority_hash=authority["content_hash"],
            transport=transport,
            crash_after_cache=True,
        )
    except Exception:
        pass
    recovered = execute_canary_rehearsal(
        runtime_authority=authority["manifest_path"],
        reviewed_authority_hash=authority["content_hash"],
        transport=transport,
    )
    spend = HashChainLedger(root / "transport_spend", name="transport").rows()
    return {
        "passed": calls["count"] == 1 and recovered.get("crash_recovered") is True and count_events(spend, "physical_post_started") == 1,
        "post_calls": calls["count"],
    }


def _exercise_concurrency(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root, fixture)
    row_path = root / "canary_row.json"
    row_path.write_text(json.dumps(fixture["canary_row"], sort_keys=True), encoding="utf-8")
    call_path = root / "transport_calls.jsonl"
    context = mp.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_worker,
            args=(authority["manifest_path"], authority["content_hash"], row_path, call_path, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
    results = [queue.get(timeout=5) for _ in processes]
    call_count = len(call_path.read_text(encoding="utf-8").splitlines()) if call_path.exists() else 0
    return {
        "passed": call_count == 1 and sum(row["status"] == "completed" for row in results) == 1,
        "transport_calls": call_count,
        "results": results,
    }


def _concurrent_worker(runtime: str, reviewed: str, row_path: Path, call_path: Path, queue: Any) -> None:
    row = json.loads(row_path.read_text(encoding="utf-8"))
    def transport(request: Mapping[str, Any]) -> TushareResponseEnvelope:
        with call_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        time.sleep(0.3)
        return _envelope(request, [row])
    try:
        result = execute_canary_rehearsal(
            runtime_authority=runtime,
            reviewed_authority_hash=reviewed,
            transport=transport,
        )
        queue.put({"status": result["status"]})
    except Exception as exc:
        queue.put({"status": "blocked", "error": str(exc)})


def _exercise_fresh_root_rejection(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root / "original", fixture)
    copied = root / "copied"
    shutil.copytree(root / "original", copied)
    copied_manifest = copied / "runtime_authority" / Path(authority["manifest_path"]).relative_to(root / "original" / "runtime_authority")
    try:
        execute_canary_rehearsal(
            runtime_authority=copied_manifest,
            reviewed_authority_hash=authority["content_hash"],
            transport=lambda request: _envelope(request, [fixture["canary_row"]]),
        )
    except Exception:
        return {"passed": True, "blocker": "root_identity_mismatch"}
    return {"passed": False}


def _exercise_forged_key(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root, fixture)
    payload = read_json(authority["manifest_path"])
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    semantic["single_request_plan"] = dict(semantic["single_request_plan"])
    semantic["single_request_plan"]["requests"] = [dict(semantic["single_request_plan"]["requests"][0], ts_code="000001.SZ")]
    forged = publish_generation(root / "forged", prefix="runtime_authority", manifest_name="runtime_authority.json", semantic=semantic)
    try:
        execute_canary_rehearsal(
            runtime_authority=forged["manifest_path"],
            reviewed_authority_hash=forged["content_hash"],
            transport=lambda request: _envelope(request, [fixture["canary_row"]]),
        )
    except Exception:
        return {"passed": True, "blocker": "forged_first_key_rejected"}
    return {"passed": False}


def _exercise_forged_authority(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root, fixture)
    try:
        execute_canary_rehearsal(
            runtime_authority=authority["manifest_path"],
            reviewed_authority_hash="0" * 64,
            transport=lambda request: _envelope(request, [fixture["canary_row"]]),
        )
    except Exception:
        return {"passed": True, "blocker": "reviewed_hash_rejected"}
    return {"passed": False}


def _exercise_source_drift(root: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    authority = _publish_rehearsal_authority(root, fixture)
    payload = read_json(authority["manifest_path"])
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    semantic["application_fixture_root"] = "f" * 64
    forged = publish_generation(root / "drifted", prefix="runtime_authority", manifest_name="runtime_authority.json", semantic=semantic)
    try:
        execute_canary_rehearsal(
            runtime_authority=forged["manifest_path"],
            reviewed_authority_hash=forged["content_hash"],
            transport=lambda request: _envelope(request, [fixture["canary_row"]]),
        )
    except Exception:
        return {"passed": True, "blocker": "source_or_root_drift_rejected"}
    return {"passed": False}


def _legacy_entrypoint_contract() -> dict[str, Any]:
    from task_055_g.network_cli import _dispatch
    from task_055_g.network_state import Task055GNetworkStateError
    import argparse
    try:
        _dispatch(argparse.Namespace(command="l1-canary", allow_network=False, sealed_plan_hash=None), {})
    except Task055GNetworkStateError as exc:
        return {"passed": str(exc) == "superseded_by_task055j", "blocker": str(exc)}
    return {"passed": False}


def _load_dynamic_l2(stage_root: Path) -> dict[str, Any]:
    pointer = read_json(stage_root / "dynamic_l2" / "current.json")
    plan = read_json(stage_root / "dynamic_l2" / pointer["manifest"])
    requests = list(plan.get("requests") or ())
    if len(requests) != 1:
        raise Task055IRehearsalError("task055i_dynamic_l2_request_invalid")
    return requests[0]


def _build_factors(manifest: Any) -> list[FactorRecord]:
    vocab = make_formula_vocab_from_manifest(manifest)
    vm = StackVM(vocab)
    semantics = build_feature_semantics_map(manifest)
    formulas = [
        ["RET_1D"], ["RET_3D"], ["RET_5D"], ["RET_10D"], ["RET_20D"],
        ["AMPLITUDE"], ["INTRADAY_RETURN"], ["GAP_RETURN"], ["VOLATILITY_5D"], ["VOLATILITY_20D"],
        ["RET_1D", "TS_MEAN3"], ["RET_1D", "TS_MEAN5"], ["RET_5D", "TS_MEAN3"],
        ["RET_5D", "TS_STD3"], ["RET_10D", "TS_ZSCORE3"], ["AMPLITUDE", "TS_MEAN5"],
        ["INTRADAY_RETURN", "TS_RANK5"], ["GAP_RETURN", "NEG"],
        ["RET_1D", "RET_5D", "ADD"], ["RET_10D", "RET_20D", "SUB"],
    ]
    result = []
    for index, names in enumerate(formulas):
        tokens = [vocab.encode_name(name) for name in names]
        formula_semantics = vm.formula_semantics(tokens, semantics)
        formula_hash = stable_formula_hash(tokens, names, manifest.feature_version, manifest.operator_version)
        result.append(FactorRecord(
            factor_id=make_factor_id(formula_hash),
            formula=names,
            formula_tokens=tokens,
            formula_hash=formula_hash,
            feature_version=manifest.feature_version,
            operator_version=manifest.operator_version,
            lookback_days=formula_semantics.max_raw_lag,
            created_at="1970-01-01T00:00:00Z",
            status="historical_probe",
            transform_method="raw",
            metadata={
                "complexity": vm.formula_complexity(tokens),
                "required_observations": formula_semantics.required_observations,
                "evidence_scope": "synthetic_rehearsal_only",
                "ordinal": index,
            },
        ))
    return result


def as_factor_identity(factor: FactorRecord) -> dict[str, Any]:
    return {
        "factor_id": factor.factor_id,
        "formula_hash": factor.formula_hash,
        "formula": factor.formula,
        "tokens": factor.formula_tokens,
        "lookback": factor.lookback_days,
    }


def _root_identity(path: Path) -> str:
    metadata = path.stat()
    return canonical_hash([str(path.resolve()), metadata.st_dev, metadata.st_ino])


def _business_dates(start: date, end: date) -> list[str]:
    values = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            values.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return values


def math_sin(value: float) -> float:
    import math
    return math.sin(value)


def _write_jsonl(path: Path, rows: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n")
