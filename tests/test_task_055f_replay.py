from __future__ import annotations

from pathlib import Path

import numpy as np

import task_055_f.fees as fee_module
import task_055_f.replay as replay_module
from task_055_f.fees import acquire_official_fee_documents, publish_fee_schedule_v2
from task_055_f.replay import _execute_generation, verify_native_replay_tree
from task_055_f.valuation import publish_valuation_projection


def _rules(clause: str) -> list[dict]:
    official = {
        "stamp_duty": (0.001, "0.1%", "卖方"),
        "transfer_fee": (0.00002, "0.002%", "过户费"),
        "handling_fee": (0.0000487, "0.00487%", "经手费"),
        "securities_management_fee": (0.00002, "0.002%", "证管费"),
    }
    rows = []
    for market in ("SSE", "SZSE"):
        for side in ("BUY", "SELL"):
            for component, (rate, rate_text, direction) in official.items():
                zero = component == "stamp_duty" and side == "BUY"
                rows.append(
                    {
                        "rule_id": f"{component}:{market}:{side}",
                        "component": component,
                        "market": market,
                        "side": side,
                        "effective_start": "20240527",
                        "effective_end": "20240530",
                        "rate": 0.0 if zero else rate,
                        "basis": "notional",
                        "rounding": "cent_half_up",
                        "minimum_cny": 0.0,
                        "explicit_zero": zero,
                        "evidence_class": "governed_official",
                        "document_id": "official_fee",
                        "page_or_clause": "正文",
                        "clause_text": clause,
                        "rate_text": "不征收" if zero else rate_text,
                        "effective_date_text": "2024年5月27日",
                        "direction_text": "买方" if zero else direction,
                    }
                )
            for component, rate, minimum in (
                ("commission", 0.0003, 5.0),
                ("slippage", 0.0005, 0.0),
                ("impact", 0.0005, 0.0),
            ):
                rows.append(
                    {
                        "rule_id": f"{component}:{market}:{side}",
                        "component": component,
                        "market": market,
                        "side": side,
                        "effective_start": "20240527",
                        "effective_end": "20240530",
                        "rate": rate,
                        "basis": "notional",
                        "rounding": "cent_half_up",
                        "minimum_cny": minimum,
                        "explicit_zero": False,
                        "evidence_class": "modeled",
                        "model_name": component,
                        "model_version": "v1",
                        "calibration_status": "uncalibrated_modeled",
                        "inclusion_contract": (
                            "exclusive_of_statutory_components"
                            if component == "commission"
                            else "not_a_fee_component"
                        ),
                    }
                )
    return rows


def _fee(tmp_path: Path, monkeypatch) -> dict:
    clause = "自2024年5月27日起，卖方印花税税率为0.1%，买方不征收；过户费率为0.002%；经手费率为0.00487%；证管费率为0.002%。"
    body = ("<html><body>官方收费通知" + clause * 20 + "</body></html>").encode()

    monkeypatch.setattr(
        fee_module,
        "_fetch_https_document",
        lambda url: {
            "body": body,
            "final_url": url,
            "redirect_chain": [url],
            "http_status": 200,
            "tls_verified": True,
            "hostname_verified": True,
            "peer_certificate_sha256": "a" * 64,
            "retrieved_at": "2026-07-16T00:00:00+08:00",
            "response_headers": {"content-type": "text/html"},
        },
    )
    acquisition = acquire_official_fee_documents(
        output_root=tmp_path / "fee_documents",
        documents=[
            {
                "document_id": "official_fee",
                "publisher": "上海证券交易所",
                "request_url": "https://www.sse.com.cn/official-fee",
            }
        ],
        allow_network=True,
    )
    return publish_fee_schedule_v2(
        output_root=tmp_path / "fee_schedule",
        document_acquisition_manifest=acquisition["manifest_path"],
        rules=_rules(clause),
        simulation_start="20240527",
        simulation_end="20240530",
        policy_seal_hash="p" * 64,
        builder_code_hash="c" * 64,
    )


def _loaded_bundle() -> tuple[list[str], dict]:
    ids = [f"factor_{index:02d}" for index in range(20)]
    signal_dates = ["20240527", "20240528"]
    execution_dates = signal_dates + ["20240529", "20240530"]
    assets = ["000001.SZ", "600000.SH"]
    signal_shape = (2, 2)
    execution_shape = (2, 4)
    signal_masks = {
        "signal_candidate_cells": np.ones(signal_shape, dtype=bool),
        "membership": np.ones(signal_shape, dtype=bool),
        "membership_known": np.ones(signal_shape, dtype=bool),
        "active": np.ones(signal_shape, dtype=bool),
        "listed": np.ones(signal_shape, dtype=bool),
        "st_effective": np.zeros(signal_shape, dtype=bool),
        "st_status_known": np.ones(signal_shape, dtype=bool),
        "st_information_available": np.ones(signal_shape, dtype=bool),
        "signal_eligible_at_close": np.ones(signal_shape, dtype=bool),
        "unexplained_data_gap": np.zeros(signal_shape, dtype=bool),
    }
    execution_masks = {
        "membership": np.ones(execution_shape, dtype=bool),
        "membership_known": np.ones(execution_shape, dtype=bool),
        "active": np.ones(execution_shape, dtype=bool),
        "listed": np.ones(execution_shape, dtype=bool),
        "open_execution_known": np.ones(execution_shape, dtype=bool),
        "open_execution_value": np.ones(execution_shape, dtype=bool),
        "buyable_at_open": np.ones(execution_shape, dtype=bool),
        "sellable_at_open": np.ones(execution_shape, dtype=bool),
        "suspension_source_covered": np.ones(execution_shape, dtype=bool),
        "suspension_event_present": np.zeros(execution_shape, dtype=bool),
        "suspension_associated_bar_absence": np.zeros(execution_shape, dtype=bool),
        "conservative_open_excluded": np.zeros(execution_shape, dtype=bool),
        "unexplained_data_gap": np.zeros(execution_shape, dtype=bool),
        "corporate_action_validity": np.ones(execution_shape, dtype=bool),
    }
    manifest = {
        "content_hash": "b" * 64,
        "exact20_ids": ids,
        "artifacts": {
            f"factor:{factor_id}:{kind}": {"sha256": ("c" if kind == "values" else "d") * 64}
            for factor_id in ids
            for kind in ("values", "validity")
        },
    }
    loaded = {
        "manifest": manifest,
        "trade_dates": signal_dates,
        "execution_dates": execution_dates,
        "ts_codes": assets,
        "factor_values": {
            factor_id: np.asarray([[index + 2.0, index + 2.0], [1.0, 1.0]])
            for index, factor_id in enumerate(ids)
        },
        "factor_validity": {factor_id: np.ones(signal_shape, dtype=bool) for factor_id in ids},
        "strict_masks": signal_masks,
        "execution_masks": execution_masks,
        "raw": {
            "open": np.asarray([[10.0, 10.1, 10.2, 10.3], [20.0, 20.1, 20.2, 20.3]]),
            "close": np.asarray([[10.05, 10.15, 10.25, 10.35], [20.05, 20.15, 20.25, 20.35]]),
            "vol": np.full(execution_shape, 100_000.0),
            "amount": np.full(execution_shape, 1_000_000.0),
        },
        "raw_validity": {name: np.ones(execution_shape, dtype=bool) for name in ("open", "close", "vol", "amount")},
        "corporate_actions": [],
        "benchmark_index_bars": [
            {"trade_date": date, "open": 4000.0 + index}
            for index, date in enumerate(execution_dates)
        ],
    }
    return ids, loaded


def test_native_replay_full_20x5_primary_sibling_resume(tmp_path, monkeypatch):
    output = tmp_path / "task055f"
    fee = _fee(output, monkeypatch)
    ids, loaded = _loaded_bundle()
    prepared = replay_module.prepare_simulation_inputs(loaded)
    shape = (len(prepared["market"]["dates"]), len(prepared["market"]["assets"]))
    surface = {
        "values": {
            "open": np.asarray(prepared["market"]["open"], dtype=float),
            "close": np.asarray(prepared["market"]["close"], dtype=float),
        },
        "metadata": {
            "open": {
                "method": np.full(shape, "OFFICIAL_OPEN", dtype=object),
                "source_date": np.repeat(np.asarray(prepared["market"]["dates"], dtype=object)[:, None], shape[1], axis=1),
                "stale_age": np.zeros(shape, dtype=np.int32),
                "evidence_id": np.full(shape, "e" * 64, dtype=object),
            },
            "close": {
                "method": np.full(shape, "OFFICIAL_CLOSE", dtype=object),
                "source_date": np.repeat(np.asarray(prepared["market"]["dates"], dtype=object)[:, None], shape[1], axis=1),
                "stale_age": np.zeros(shape, dtype=np.int32),
                "evidence_id": np.full(shape, "f" * 64, dtype=object),
            },
        },
        "blockers": {},
    }
    projection = publish_valuation_projection(
        output_root=output / "valuation_projection",
        dates=prepared["market"]["dates"],
        assets=prepared["market"]["assets"],
        surface=surface,
        truth_v2_content_hash="t" * 64,
        matrix_content_hash="m" * 64,
        builder_code_hash="c" * 64,
    )
    causal = {
        "content_hash": "a" * 64,
        "lineage": {
            "simulation_bundle_content_hash": loaded["manifest"]["content_hash"],
            "fee_schedule_content_hash": fee["content_hash"],
        },
        "valuation_projection": {"content_hash": projection["content_hash"]},
    }
    monkeypatch.setattr(replay_module, "load_simulation_bundle", lambda _: loaded)
    primary = _execute_generation(
        root=output / "replay" / "primary",
        role="primary_uncached",
        exact_ids=ids,
        causal=causal,
        bundle_manifest=tmp_path / "bundle.json",
        fee_manifest=fee["manifest_path"],
        projection_manifest=projection["manifest_path"],
        require_uncached=True,
    )
    sibling = _execute_generation(
        root=output / "replay" / "sibling",
        role="sibling_uncached",
        exact_ids=ids,
        causal=causal,
        bundle_manifest=tmp_path / "bundle.json",
        fee_manifest=fee["manifest_path"],
        projection_manifest=projection["manifest_path"],
        require_uncached=True,
    )
    assert primary["truth_root"] == sibling["truth_root"]
    resumed = _execute_generation(
        root=output / "replay" / "primary",
        role="primary_uncached",
        exact_ids=ids,
        causal=causal,
        bundle_manifest=tmp_path / "bundle.json",
        fee_manifest=fee["manifest_path"],
        projection_manifest=projection["manifest_path"],
        require_uncached=False,
    )
    assert resumed["resume_hit_count"] == 100
    verified = verify_native_replay_tree(output)
    assert verified["status"] == "verified"
    assert verified["terminal_count"] == 100
    assert verified["resume_hit_count"] == 100
