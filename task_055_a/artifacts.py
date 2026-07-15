"""Immutable, content-addressed artifacts for Task 055-A simulation runs."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .models import SimulationResult


RUN_ARTIFACT_SCHEMA = "task055a_simulation_run_v1"
BLOCKED_RUN_ARTIFACT_SCHEMA = "task055a_blocked_simulation_run_v1"
RUN_POINTER_SCHEMA = "task055a_simulation_run_pointer_v1"
MONEY_TOLERANCE_CNY = 0.01


class SimulationArtifactError(RuntimeError):
    """Raised when immutable run artifacts cannot be safely published."""


class ResumeDriftError(SimulationArtifactError):
    """Raised when resume inputs differ from the published immutable run."""


def publish_blocked_simulation_run(
    *, output_root: str | Path, spec: Mapping[str, Any], input_lineage: Mapping[str, Any], blocker: Mapping[str, Any]
) -> dict[str, Any]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    spec_payload = _jsonable(dict(spec))
    lineage_payload = _jsonable(dict(input_lineage))
    blocker_payload = _jsonable(dict(blocker))
    spec_hash = canonical_hash(spec_payload)
    input_hash = canonical_hash(lineage_payload)
    truth_hash = canonical_hash({"terminal_state": "data_blocked", "blocker": blocker_payload})
    staging = Path(tempfile.mkdtemp(prefix=".task055a_blocked.", dir=root))
    try:
        _write_json(staging / "spec.json", spec_payload)
        _write_json(staging / "input_lineage.json", lineage_payload)
        _write_json(staging / "blocker.json", blocker_payload)
        partitions = {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in sorted(staging.iterdir()) if path.is_file()
        }
        semantic = {
            "schema_version": BLOCKED_RUN_ARTIFACT_SCHEMA,
            "spec_hash": spec_hash,
            "input_lineage_hash": input_hash,
            "truth_hash": truth_hash,
            "blocker_hash": canonical_hash(blocker_payload),
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"blocked_simulation_run_{content_hash[:24]}"
        manifest = semantic | {
            "content_hash": content_hash,
            "generation_id": generation_id,
            "status": "data_blocked",
        }
        _write_json(staging / "manifest.json", manifest)
        generation_root = root / "generations" / generation_id
        generation_root.parent.mkdir(parents=True, exist_ok=True)
        if generation_root.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, generation_root)
        _atomic_write_json(root / "current.json", {
            "schema_version": RUN_POINTER_SCHEMA,
            "generation_id": generation_id,
            "manifest": f"generations/{generation_id}/manifest.json",
            "content_hash": content_hash,
            "spec_hash": spec_hash,
            "input_lineage_hash": input_hash,
        })
        return manifest | {"root": str(generation_root), "manifest_path": str(generation_root / "manifest.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_simulation_run(
    *,
    output_root: str | Path,
    result: SimulationResult | Mapping[str, Any],
    spec: Mapping[str, Any],
    input_lineage: Mapping[str, Any],
    market: Mapping[str, Any],
    benchmark: Mapping[str, Any] | None = None,
    initial_positions: Mapping[str, int] | None = None,
    allow_resume: bool = False,
) -> dict[str, Any]:
    """Publish one immutable run and atomically advance its current pointer.

    Resume is accepted only when both the complete simulation specification and
    immutable input lineage hashes match the existing generation. Any drift is
    rejected rather than silently creating a misleading resume hit.
    """

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    spec_payload = _jsonable(dict(spec))
    lineage_payload = _jsonable(dict(input_lineage))
    spec_hash = canonical_hash(spec_payload)
    input_hash = canonical_hash(lineage_payload)
    pointer_path = root / "current.json"
    if pointer_path.exists() and allow_resume:
        return resume_simulation_run(
            root,
            expected_spec_hash=spec_hash,
            expected_input_lineage_hash=input_hash,
        )

    payload = result.to_dict() if isinstance(result, SimulationResult) else _jsonable(dict(result))
    dates = [str(item) for item in payload.get("dates", ())]
    assets = [str(item) for item in payload.get("assets", ())]
    if not dates or not assets:
        raise SimulationArtifactError("simulation_axes_empty")
    open_prices = _market_array(market, ("open", "open_price", "open_prices"), dates, assets)
    close_prices = _market_array(market, ("close", "close_price", "close_prices"), dates, assets)
    valuation_open = _market_array(market, ("valuation_open", "open", "open_price", "open_prices"), dates, assets)
    valuation_close = _market_array(market, ("valuation_close", "close", "close_price", "close_prices"), dates, assets)
    benchmark_dates, benchmark_open = _benchmark_view(benchmark, dates)

    staging = Path(tempfile.mkdtemp(prefix=".task055a_run.", dir=root))
    try:
        _write_json(staging / "spec.json", spec_payload)
        _write_json(staging / "input_lineage.json", lineage_payload)
        _write_json(staging / "axes.json", {"dates": dates, "assets": assets})
        _write_json(staging / "final_state.json", {
            "final_cash": payload.get("final_cash") or {},
            "final_positions": payload.get("final_positions") or {},
            "initial_positions": {str(key): int(value) for key, value in (initial_positions or {}).items()},
        })
        table_names = (
            "orders",
            "fills",
            "rejections",
            "settlements",
            "corporate_actions",
            "nav",
            "event_ledger",
        )
        for name in table_names:
            _write_jsonl(staging / f"{name}.jsonl", payload.get(name) or ())
        _write_jsonl(staging / "target_positions.jsonl", _target_positions(payload.get("orders") or ()))
        _write_jsonl(staging / "cash_ledger.jsonl", _cash_ledger(payload.get("event_ledger") or ()))
        positions, lot_events = _position_artifacts(
            dates,
            payload.get("fills") or (),
            payload.get("settlements") or (),
            payload.get("corporate_actions") or (),
            initial_positions or {},
        )
        _write_jsonl(staging / "positions.jsonl", positions)
        _write_jsonl(staging / "lots.jsonl", lot_events)
        np.savez_compressed(
            staging / "verification_view.npz",
            open=open_prices.astype(np.float64, copy=False),
            close=close_prices.astype(np.float64, copy=False),
            valuation_open=valuation_open.astype(np.float64, copy=False),
            valuation_close=valuation_close.astype(np.float64, copy=False),
            benchmark_dates=np.asarray(benchmark_dates, dtype="U16"),
            benchmark_open=benchmark_open.astype(np.float64, copy=False),
        )

        from .verifier import recompute_run_truth

        summary = recompute_run_truth(staging)
        _write_json(staging / "summary.json", summary)
        partitions = {
            path.name: {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(staging.iterdir())
            if path.is_file()
        }
        semantic = {
            "schema_version": RUN_ARTIFACT_SCHEMA,
            "spec_hash": spec_hash,
            "input_lineage_hash": input_hash,
            "truth_hash": summary["truth_hash"],
            "dates_hash": canonical_hash(dates),
            "assets_hash": canonical_hash(assets),
            "record_counts": {
                name: len(payload.get(name) or ()) for name in table_names
            },
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"simulation_run_{content_hash[:24]}"
        manifest = {
            **semantic,
            "content_hash": content_hash,
            "generation_id": generation_id,
            "status": "complete",
            "money_tolerance_cny": MONEY_TOLERANCE_CNY,
        }
        _write_json(staging / "manifest.json", manifest)
        generation_root = root / "generations" / generation_id
        generation_root.parent.mkdir(parents=True, exist_ok=True)
        if generation_root.exists():
            existing = json.loads((generation_root / "manifest.json").read_text())
            if existing.get("content_hash") != content_hash:
                raise SimulationArtifactError("content_address_collision")
            shutil.rmtree(staging)
        else:
            os.replace(staging, generation_root)
        pointer = {
            "schema_version": RUN_POINTER_SCHEMA,
            "generation_id": generation_id,
            "manifest": f"generations/{generation_id}/manifest.json",
            "content_hash": content_hash,
            "spec_hash": spec_hash,
            "input_lineage_hash": input_hash,
        }
        _atomic_write_json(pointer_path, pointer)
        return {
            **manifest,
            "root": str(generation_root),
            "manifest_path": str(generation_root / "manifest.json"),
            "resume_hit": False,
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def resume_simulation_run(
    output_root: str | Path,
    *,
    expected_spec_hash: str,
    expected_input_lineage_hash: str,
) -> dict[str, Any]:
    """Validate and resume an immutable generation, rejecting any drift."""

    from .verifier import verify_simulation_run

    root = Path(output_root)
    pointer_path = root / "current.json"
    if not pointer_path.is_file():
        raise SimulationArtifactError("resume_pointer_missing")
    pointer = json.loads(pointer_path.read_text())
    if pointer.get("schema_version") != RUN_POINTER_SCHEMA:
        raise SimulationArtifactError("resume_pointer_schema_mismatch")
    if pointer.get("spec_hash") != expected_spec_hash:
        raise ResumeDriftError("resume_spec_drift")
    if pointer.get("input_lineage_hash") != expected_input_lineage_hash:
        raise ResumeDriftError("resume_input_lineage_drift")
    verified = verify_simulation_run(root)
    return {**verified, "resume_hit": True}


def _target_positions(orders: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "decision_index": int(row["decision_index"]),
            "execution_index": int(row["execution_index"]),
            "asset": str(row["asset"]),
            "target_shares": int(row["target_shares"]),
            "target_weight": float(row["target_weight"]),
            "order_id": str(row["order_id"]),
        }
        for row in orders
    ]


def _cash_ledger(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    keep = {"cash_frozen", "fill", "settlement", "corporate_action"}
    return [dict(row) for row in events if str(row.get("type")) in keep]


def _position_artifacts(
    dates: Sequence[str],
    fills: Sequence[Mapping[str, Any]],
    settlements: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    initial_positions: Mapping[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lots: dict[str, list[dict[str, Any]]] = {}
    lot_events: list[dict[str, Any]] = []
    for asset, shares in sorted(initial_positions.items()):
        lot = {"lot_id": f"initial:{asset}", "shares": int(shares), "available_index": 0}
        lots.setdefault(str(asset), []).append(lot)
        lot_events.append({"type": "initial", "index": 0, "asset": str(asset), **lot})
    settlement_by_source = {str(row.get("source_id")): row for row in settlements}
    actions_by_index: dict[int, list[Mapping[str, Any]]] = {}
    fills_by_index: dict[int, list[Mapping[str, Any]]] = {}
    for row in actions:
        actions_by_index.setdefault(int(row["effective_index"]), []).append(row)
    for row in fills:
        fills_by_index.setdefault(int(row["execution_index"]), []).append(row)
    positions: list[dict[str, Any]] = []
    for index, date in enumerate(dates):
        for action in sorted(actions_by_index.get(index, ()), key=lambda row: str(row["action_id"])):
            asset = str(action["asset"])
            ratio = float(action["share_ratio"])
            if ratio != 1.0:
                for lot in lots.get(asset, ()):
                    before = int(lot["shares"])
                    lot["shares"] = int(np.floor(before * ratio + 1e-12))
                    lot_events.append({
                        "type": "corporate_action",
                        "index": index,
                        "asset": asset,
                        "lot_id": lot["lot_id"],
                        "shares_before": before,
                        "shares_after": int(lot["shares"]),
                        "action_id": str(action["action_id"]),
                    })
        for fill in fills_by_index.get(index, ()):
            asset = str(fill["asset"])
            shares = int(fill["filled_shares"])
            if str(fill["side"]) == "BUY":
                settlement = settlement_by_source.get(str(fill["fill_id"])) or {}
                lot = {
                    "lot_id": f"lot:{fill['fill_id']}",
                    "shares": shares,
                    "available_index": int(settlement.get("settle_index", index + 1)),
                }
                lots.setdefault(asset, []).append(lot)
                lot_events.append({"type": "buy", "index": index, "asset": asset, **lot})
            else:
                remaining = shares
                for lot in lots.get(asset, ()):
                    if int(lot["available_index"]) > index:
                        continue
                    consumed = min(int(lot["shares"]), remaining)
                    if consumed:
                        lot["shares"] = int(lot["shares"]) - consumed
                        remaining -= consumed
                        lot_events.append({
                            "type": "sell",
                            "index": index,
                            "asset": asset,
                            "lot_id": lot["lot_id"],
                            "shares": consumed,
                            "fill_id": str(fill["fill_id"]),
                        })
                    if remaining == 0:
                        break
                if remaining:
                    raise SimulationArtifactError(f"position_artifact_sell_shortfall:{fill['fill_id']}")
        for asset, asset_lots in sorted(lots.items()):
            total = sum(int(lot["shares"]) for lot in asset_lots)
            if total <= 0:
                continue
            available = sum(
                int(lot["shares"]) for lot in asset_lots if int(lot["available_index"]) <= index
            )
            positions.append({
                "index": index,
                "date": str(date),
                "asset": asset,
                "total_shares": total,
                "available_shares": available,
                "unsettled_shares": total - available,
            })
    return positions, lot_events


def _market_array(
    market: Mapping[str, Any], keys: Sequence[str], dates: Sequence[str], assets: Sequence[str]
) -> np.ndarray:
    value = next((market[key] for key in keys if key in market), None)
    if value is None and "close" in keys:
        value = next((market[key] for key in ("open", "open_price", "open_prices") if key in market), None)
    if value is None:
        raise SimulationArtifactError(f"verification_market_field_missing:{keys[0]}")
    array = np.asarray(value, dtype=float)
    expected = (len(dates), len(assets))
    if array.shape != expected:
        raise SimulationArtifactError(f"verification_market_shape_mismatch:{keys[0]}:{array.shape}:{expected}")
    return array


def _benchmark_view(
    benchmark: Mapping[str, Any] | None, dates: Sequence[str]
) -> tuple[list[str], np.ndarray]:
    if benchmark is None:
        return [], np.asarray([], dtype=float)
    benchmark_dates = [str(item) for item in benchmark.get("dates", dates)]
    values = benchmark.get("open", benchmark.get("open_price", benchmark.get("open_prices")))
    if values is None:
        raise SimulationArtifactError("benchmark_open_missing")
    array = np.asarray(values, dtype=float).reshape(-1)
    if len(benchmark_dates) != len(array):
        raise SimulationArtifactError("benchmark_axis_mismatch")
    return benchmark_dates, array


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_jsonable(value), sort_keys=True, indent=2) + "\n")


def _write_jsonl(path: Path, rows: Sequence[Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_jsonable(row), sort_keys=True, separators=(",", ":")) + "\n")


def _atomic_write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    _write_json(temporary, value)
    os.replace(temporary, path)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


publish_run_artifacts = publish_simulation_run
resume_run_artifacts = resume_simulation_run
