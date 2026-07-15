"""Independent ledger and accounting verifier for Task 055-A runs."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .artifacts import (
    BLOCKED_RUN_ARTIFACT_SCHEMA,
    MONEY_TOLERANCE_CNY,
    RUN_ARTIFACT_SCHEMA,
    RUN_POINTER_SCHEMA,
    canonical_hash,
    sha256_file,
)


class SimulationVerificationError(RuntimeError):
    """Raised when independently reconstructed simulation truth does not close."""


def recompute_run_truth(root: str | Path) -> dict[str, Any]:
    """Recompute accounting, positions, metrics, and invariant status from rows."""

    generation = Path(root)
    spec = _read_json(generation / "spec.json")
    axes = _read_json(generation / "axes.json")
    final_state = _read_json(generation / "final_state.json")
    dates = [str(item) for item in axes["dates"]]
    assets = [str(item) for item in axes["assets"]]
    asset_index = {asset: index for index, asset in enumerate(assets)}
    view = np.load(generation / "verification_view.npz", allow_pickle=False)
    open_prices = np.asarray(view["open"], dtype=float)
    close_prices = np.asarray(view["close"], dtype=float)
    valuation_open = np.asarray(view["valuation_open"] if "valuation_open" in view.files else open_prices, dtype=float)
    valuation_close = np.asarray(view["valuation_close"] if "valuation_close" in view.files else close_prices, dtype=float)
    expected_shape = (len(dates), len(assets))
    if any(array.shape != expected_shape for array in (open_prices, close_prices, valuation_open, valuation_close)):
        raise SimulationVerificationError("verification_view_shape_mismatch")

    orders = _read_jsonl(generation / "orders.jsonl")
    fills = _read_jsonl(generation / "fills.jsonl")
    rejections = _read_jsonl(generation / "rejections.jsonl")
    settlements = _read_jsonl(generation / "settlements.jsonl")
    actions = _read_jsonl(generation / "corporate_actions.jsonl")
    nav = _read_jsonl(generation / "nav.jsonl")
    events = _read_jsonl(generation / "event_ledger.jsonl")
    stored_positions = _read_jsonl(generation / "positions.jsonl")
    _read_jsonl(generation / "lots.jsonl")
    issues: list[str] = []

    _verify_unique_ids(orders, "order_id", "duplicate_order_id", issues)
    _verify_unique_ids(fills, "fill_id", "duplicate_fill_id", issues)
    _verify_unique_ids(settlements, "event_id", "duplicate_settlement_id", issues)
    _verify_order_closure(orders, fills, rejections, issues)
    _verify_fill_costs(fills, issues)
    _verify_fill_prices(fills, open_prices, asset_index, issues)
    _verify_settlement_closure(fills, actions, settlements, issues)

    initial_cash = _initial_cash(spec)
    cash = {
        "available": initial_cash,
        "frozen": 0.0,
        "unsettled_receivable": 0.0,
        "unsettled_payable": 0.0,
        "withdrawable": initial_cash,
    }
    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for asset, shares in (final_state.get("initial_positions") or {}).items():
        lots[str(asset)].append({"shares": int(shares), "available_index": 0})
    settlement_by_source = {str(row.get("source_id")): row for row in settlements}
    nav_by_index = {int(row["index"]): row for row in nav}
    daily_positions: dict[int, dict[str, dict[str, int]]] = {}
    daily_cash: dict[int, dict[str, float]] = {}
    event_sequences = [int(row.get("sequence", 0)) for row in events]
    if event_sequences != list(range(1, len(events) + 1)):
        issues.append("event_sequence_not_contiguous")

    current_index = -1
    pretrade_shares: dict[str, int] = {}
    pretrade_cash = 0.0
    trade_started = False
    for event in events:
        index = int(event.get("index", -1))
        if index < current_index or index < 0 or index >= len(dates):
            issues.append("event_index_invalid_or_nonmonotonic")
            continue
        if index != current_index:
            current_index = index
            pretrade_shares = _total_shares(lots)
            pretrade_cash = _cash_total(cash)
            trade_started = False
        kind = str(event.get("type", ""))
        if kind == "settlement":
            amount = float(event.get("cash_amount", 0.0))
            event_type = str(event.get("event_type", ""))
            created = int(event.get("created_index", index))
            if event_type in {"sell_cash_available", "cash_dividend_available"}:
                if event_type == "cash_dividend_available" and created == index:
                    cash["available"] += amount
                    cash["withdrawable"] += amount
                else:
                    cash["unsettled_receivable"] -= amount
                    cash["available"] += amount
                    cash["withdrawable"] += amount
            if not trade_started:
                pretrade_cash = _cash_total(cash)
        elif kind == "corporate_action_shares":
            asset = str(event["asset"])
            ratio = float(event["share_ratio"])
            for lot in lots.get(asset, ()):
                lot["shares"] = int(math.floor(int(lot["shares"]) * ratio + 1e-12))
            if _asset_shares(lots, asset) != int(event["post_shares"]):
                issues.append(f"corporate_action_share_mismatch:{event.get('action_id')}")
            if not trade_started:
                pretrade_shares = _total_shares(lots)
        elif kind == "corporate_action":
            action_id = str(event.get("action_id"))
            settlement = settlement_by_source.get(action_id)
            if settlement and int(settlement.get("settle_index", index)) > index:
                cash["unsettled_receivable"] += float(settlement.get("cash_amount", 0.0))
            if not trade_started:
                pretrade_cash = _cash_total(cash)
        elif kind == "cash_frozen":
            trade_started = True
            amount = float(event.get("amount", 0.0))
            cash["available"] -= amount
            cash["withdrawable"] -= amount
        elif kind == "fill":
            trade_started = True
            side = str(event.get("side"))
            asset = str(event.get("asset"))
            shares = int(event.get("filled_shares", 0))
            if side == "BUY":
                settlement = settlement_by_source.get(str(event.get("fill_id")), {})
                available_index = int(settlement.get("settle_index", index + 1))
                lots[asset].append({"shares": shares, "available_index": available_index})
            elif side == "SELL":
                if shares > _available_shares(lots, asset, index):
                    issues.append(f"sell_exceeds_available_shares:{event.get('fill_id')}")
                _consume_lots(lots, asset, shares, index, issues)
                cash["unsettled_receivable"] += float(event["notional"]) - float(event["total_cost"])
        elif kind == "nav":
            row = nav_by_index.get(index)
            if row is None:
                issues.append(f"nav_row_missing:{index}")
                continue
            post_shares = _total_shares(lots)
            positions_open_pre = _position_value(pretrade_shares, valuation_open[index], asset_index)
            positions_open_post = _position_value(post_shares, valuation_open[index], asset_index)
            positions_close = _position_value(post_shares, valuation_close[index], asset_index)
            current_cash = _cash_total(cash)
            _money_equal(float(row["open_pre"]), pretrade_cash + positions_open_pre, f"nav_open_pre:{index}", issues)
            _money_equal(float(row["open_post"]), current_cash + positions_open_post, f"nav_open_post:{index}", issues)
            _money_equal(float(row["close"]), current_cash + positions_close, f"nav_close:{index}", issues)
            _money_equal(float(row["cash_total"]), current_cash, f"cash_total:{index}", issues)
            if cash["available"] < -MONEY_TOLERANCE_CNY or cash["unsettled_receivable"] < -MONEY_TOLERANCE_CNY:
                issues.append(f"negative_cash_bucket:{index}")
            daily_positions[index] = {
                asset: {
                    "total": total,
                    "available": _available_shares(lots, asset, index),
                }
                for asset, total in post_shares.items()
            }
            daily_cash[index] = dict(cash)

    final_cash = final_state.get("final_cash") or {}
    for key in ("available", "frozen", "unsettled_receivable", "unsettled_payable", "withdrawable"):
        _money_equal(float(final_cash.get(key, 0.0)), float(cash[key]), f"final_cash:{key}", issues)
    calculated_positions = _total_shares(lots)
    expected_positions = final_state.get("final_positions") or {}
    for asset in sorted(set(calculated_positions) | set(expected_positions)):
        expected = int((expected_positions.get(asset) or {}).get("total", 0))
        if calculated_positions.get(asset, 0) != expected:
            issues.append(f"final_position_mismatch:{asset}")
    reconstructed_position_rows = []
    for index in sorted(daily_positions):
        for asset, snapshot in sorted(daily_positions[index].items()):
            total = snapshot["total"]
            available = snapshot["available"]
            reconstructed_position_rows.append({
                "index": index,
                "date": dates[index],
                "asset": asset,
                "total_shares": total,
                "available_shares": available,
                "unsettled_shares": total - available,
            })
    if stored_positions != reconstructed_position_rows:
        issues.append("daily_position_artifact_mismatch")

    metrics = _metrics(nav, fills, orders, rejections, view, dates, issues)
    summary_core = {
        "status": "passed" if not issues else "blocked",
        "record_counts": {
            "orders": len(orders),
            "fills": len(fills),
            "rejections": len(rejections),
            "settlements": len(settlements),
            "corporate_actions": len(actions),
            "nav": len(nav),
            "events": len(events),
        },
        "metrics": metrics,
        "final_cash": {key: float(cash[key]) for key in cash},
        "final_positions": {asset: calculated_positions[asset] for asset in sorted(calculated_positions)},
        "issues": sorted(set(issues)),
    }
    summary_core["truth_hash"] = canonical_hash(summary_core)
    return summary_core


def verify_simulation_run(
    path: str | Path,
    *,
    expected_spec_hash: str | None = None,
    expected_input_lineage_hash: str | None = None,
) -> dict[str, Any]:
    """Validate partitions and independently reconstruct the complete run."""

    generation, manifest = _resolve_generation(path)
    if manifest.get("schema_version") == BLOCKED_RUN_ARTIFACT_SCHEMA:
        return _verify_blocked_run(generation, manifest, expected_spec_hash, expected_input_lineage_hash)
    if manifest.get("schema_version") != RUN_ARTIFACT_SCHEMA or manifest.get("status") != "complete":
        raise SimulationVerificationError("run_manifest_schema_or_status_invalid")
    if expected_spec_hash and manifest.get("spec_hash") != expected_spec_hash:
        raise SimulationVerificationError("run_spec_hash_mismatch")
    if expected_input_lineage_hash and manifest.get("input_lineage_hash") != expected_input_lineage_hash:
        raise SimulationVerificationError("run_input_lineage_hash_mismatch")
    for name, entry in (manifest.get("partitions") or {}).items():
        artifact = generation / name
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise SimulationVerificationError(f"run_partition_mismatch:{name}")
    if canonical_hash(_read_json(generation / "spec.json")) != manifest.get("spec_hash"):
        raise SimulationVerificationError("run_spec_content_drift")
    if canonical_hash(_read_json(generation / "input_lineage.json")) != manifest.get("input_lineage_hash"):
        raise SimulationVerificationError("run_input_lineage_content_drift")
    recomputed = recompute_run_truth(generation)
    stored = _read_json(generation / "summary.json")
    if recomputed != stored:
        raise SimulationVerificationError("run_summary_recomputation_mismatch")
    if recomputed["status"] != "passed":
        raise SimulationVerificationError("run_accounting_invariants_blocked:" + ",".join(recomputed["issues"][:5]))
    if recomputed["truth_hash"] != manifest.get("truth_hash"):
        raise SimulationVerificationError("run_truth_hash_mismatch")
    semantic = {
        key: manifest[key]
        for key in (
            "schema_version", "spec_hash", "input_lineage_hash", "truth_hash",
            "dates_hash", "assets_hash", "record_counts", "partitions",
        )
    }
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise SimulationVerificationError("run_content_hash_mismatch")
    return {
        **manifest,
        "root": str(generation),
        "manifest_path": str(generation / "manifest.json"),
        "verification": recomputed,
    }


def _verify_blocked_run(
    generation: Path,
    manifest: Mapping[str, Any],
    expected_spec_hash: str | None,
    expected_input_lineage_hash: str | None,
) -> dict[str, Any]:
    if manifest.get("status") != "data_blocked":
        raise SimulationVerificationError("blocked_run_status_invalid")
    if expected_spec_hash and manifest.get("spec_hash") != expected_spec_hash:
        raise SimulationVerificationError("run_spec_hash_mismatch")
    if expected_input_lineage_hash and manifest.get("input_lineage_hash") != expected_input_lineage_hash:
        raise SimulationVerificationError("run_input_lineage_hash_mismatch")
    for name, entry in (manifest.get("partitions") or {}).items():
        artifact = generation / name
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise SimulationVerificationError(f"run_partition_mismatch:{name}")
    spec = _read_json(generation / "spec.json")
    lineage = _read_json(generation / "input_lineage.json")
    blocker = _read_json(generation / "blocker.json")
    if canonical_hash(spec) != manifest.get("spec_hash") or canonical_hash(lineage) != manifest.get("input_lineage_hash"):
        raise SimulationVerificationError("blocked_run_lineage_drift")
    if canonical_hash(blocker) != manifest.get("blocker_hash"):
        raise SimulationVerificationError("blocked_run_blocker_drift")
    truth_hash = canonical_hash({"terminal_state": "data_blocked", "blocker": blocker})
    if truth_hash != manifest.get("truth_hash"):
        raise SimulationVerificationError("blocked_run_truth_hash_mismatch")
    semantic = {key: manifest[key] for key in (
        "schema_version", "spec_hash", "input_lineage_hash", "truth_hash", "blocker_hash", "partitions"
    )}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise SimulationVerificationError("blocked_run_content_hash_mismatch")
    return {**manifest, "root": str(generation), "manifest_path": str(generation / "manifest.json"), "blocker": blocker}


def compare_replay_truth(primary: str | Path, sibling: str | Path) -> dict[str, Any]:
    """Verify two uncached runs and require identical semantic truth hashes."""

    first = verify_simulation_run(primary)
    second = verify_simulation_run(sibling)
    if first["truth_hash"] != second["truth_hash"]:
        raise SimulationVerificationError("replay_truth_hash_mismatch")
    if first["spec_hash"] != second["spec_hash"] or first["input_lineage_hash"] != second["input_lineage_hash"]:
        raise SimulationVerificationError("replay_lineage_mismatch")
    return {
        "status": "passed",
        "truth_hash": first["truth_hash"],
        "primary_content_hash": first["content_hash"],
        "sibling_content_hash": second["content_hash"],
    }


def _metrics(
    nav: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
    rejections: Sequence[Mapping[str, Any]],
    view: Any,
    dates: Sequence[str],
    issues: list[str],
) -> dict[str, Any]:
    if len(nav) != len(dates):
        issues.append("nav_axis_length_mismatch")
    equity = np.asarray([float(row["open_pre"]) for row in nav], dtype=float)
    costs = np.asarray([float(row["daily_cost"]) for row in nav], dtype=float)
    if equity.size == 0 or not np.all(np.isfinite(equity)) or np.any(equity <= 0):
        issues.append("equity_curve_invalid")
        net_return = drawdown = float("nan")
    else:
        net_return = float(equity[-1] / equity[0] - 1.0)
        peaks = np.maximum.accumulate(equity)
        drawdown = float(np.min(equity / peaks - 1.0))
    total_notional = float(sum(float(row["notional"]) for row in fills))
    mean_nav = float(np.mean(equity)) if equity.size else float("nan")
    turnover = total_notional / mean_nav if mean_nav > 0 else float("nan")
    total_cost = float(sum(float(row["total_cost"]) for row in fills))
    if abs(total_cost - float(np.sum(costs))) > MONEY_TOLERANCE_CNY:
        issues.append("daily_cost_total_mismatch")
    gross_equity = equity + np.cumsum(costs) if equity.size else equity
    gross_return = float(gross_equity[-1] / gross_equity[0] - 1.0) if equity.size else float("nan")
    requested = sum(int(row["requested_shares"]) for row in orders)
    filled = sum(int(row["filled_shares"]) for row in fills)

    benchmark_dates = [str(item) for item in view["benchmark_dates"].tolist()]
    benchmark_open = np.asarray(view["benchmark_open"], dtype=float)
    benchmark: dict[str, Any]
    if not benchmark_dates or benchmark_open.size == 0:
        issues.append("benchmark_missing")
        benchmark = {"status": "blocked"}
    elif benchmark_dates != list(dates) or benchmark_open.shape != (len(dates),):
        issues.append("benchmark_date_axis_mismatch")
        benchmark = {"status": "blocked"}
    elif not np.all(np.isfinite(benchmark_open)) or np.any(benchmark_open <= 0):
        issues.append("benchmark_values_invalid")
        benchmark = {"status": "blocked"}
    else:
        benchmark_return = float(benchmark_open[-1] / benchmark_open[0] - 1.0)
        benchmark = {
            "status": "available",
            "total_return": benchmark_return,
            "excess_return": net_return - benchmark_return,
            "active_curve_hash": canonical_hash((equity / equity[0] - benchmark_open / benchmark_open[0]).tolist()),
        }
    return {
        "net_return": net_return,
        "gross_accounting_return": gross_return,
        "total_cost": total_cost,
        "turnover": float(turnover),
        "max_drawdown": drawdown,
        "requested_shares": requested,
        "filled_shares": filled,
        "fill_ratio": float(filled / requested) if requested else 0.0,
        "rejection_count": len(rejections),
        "partial_fill_count": sum(str(row.get("status")) == "PARTIAL" for row in fills),
        "benchmark": benchmark,
        "equity_curve_hash": canonical_hash(equity.tolist()),
        "gross_equity_curve_hash": canonical_hash(gross_equity.tolist()),
    }


def _verify_order_closure(orders, fills, rejections, issues) -> None:
    outcomes: dict[str, int] = defaultdict(int)
    order_ids = {str(row.get("order_id")) for row in orders}
    for row in fills:
        order_id = str(row.get("order_id"))
        outcomes[order_id] += 1
        if order_id not in order_ids:
            issues.append(f"fill_without_order:{order_id}")
        if int(row.get("filled_shares", 0)) <= 0 or int(row.get("filled_shares", 0)) > int(row.get("requested_shares", 0)):
            issues.append(f"fill_share_bounds:{row.get('fill_id')}")
    for row in rejections:
        order_id = str(row.get("order_id"))
        outcomes[order_id] += 1
        if order_id not in order_ids:
            issues.append(f"rejection_without_order:{order_id}")
    for order_id in sorted(order_ids):
        if outcomes[order_id] != 1:
            issues.append(f"order_outcome_count:{order_id}:{outcomes[order_id]}")


def _verify_fill_costs(fills, issues) -> None:
    for row in fills:
        components = sum(float(row[key]) for key in ("commission", "stamp_duty", "transfer_fee", "slippage", "impact"))
        _money_equal(float(row["total_cost"]), components, f"fill_cost_components:{row.get('fill_id')}", issues)
        _money_equal(float(row["notional"]), float(row["price"]) * int(row["filled_shares"]), f"fill_notional:{row.get('fill_id')}", issues)


def _verify_fill_prices(fills, open_prices, asset_index, issues) -> None:
    for row in fills:
        asset = str(row.get("asset"))
        index = int(row.get("execution_index", -1))
        if asset not in asset_index or index < 0 or index >= open_prices.shape[0]:
            issues.append(f"fill_reference_axis_invalid:{row.get('fill_id')}")
            continue
        _money_equal(float(row["price"]), float(open_prices[index, asset_index[asset]]), f"fill_reference_open:{row.get('fill_id')}", issues)


def _verify_settlement_closure(fills, actions, settlements, issues) -> None:
    by_source = defaultdict(list)
    for row in settlements:
        by_source[str(row.get("source_id"))].append(row)
    for fill in fills:
        rows = by_source.get(str(fill.get("fill_id")), ())
        expected_type = "buy_shares_available" if fill.get("side") == "BUY" else "sell_cash_available"
        if len(rows) != 1 or rows[0].get("event_type") != expected_type:
            issues.append(f"fill_settlement_closure:{fill.get('fill_id')}")
    action_ids = {str(row.get("action_id")) for row in actions if float(row.get("cash_dividend_per_share", 0.0)) != 0.0}
    for action_id in action_ids:
        rows = by_source.get(action_id, ())
        if len(rows) != 1 or rows[0].get("event_type") != "cash_dividend_available":
            issues.append(f"dividend_settlement_closure:{action_id}")


def _verify_unique_ids(rows, key, code, issues) -> None:
    values = [str(row.get(key)) for row in rows]
    if len(values) != len(set(values)):
        issues.append(code)


def _initial_cash(spec: Mapping[str, Any]) -> float:
    candidates = (
        spec.get("initial_cash"), spec.get("initial_aum"),
        (spec.get("policy") or {}).get("initial_aum") if isinstance(spec.get("policy"), Mapping) else None,
    )
    for value in candidates:
        if value is not None:
            result = float(value)
            if math.isfinite(result) and result >= 0:
                return result
    raise SimulationVerificationError("initial_cash_missing_from_spec")


def _total_shares(lots: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, int]:
    return {
        asset: sum(max(0, int(lot["shares"])) for lot in rows)
        for asset, rows in lots.items()
        if sum(max(0, int(lot["shares"])) for lot in rows) > 0
    }


def _asset_shares(lots, asset) -> int:
    return sum(max(0, int(row["shares"])) for row in lots.get(asset, ()))


def _available_shares(lots, asset, index) -> int:
    return sum(max(0, int(row["shares"])) for row in lots.get(asset, ()) if int(row["available_index"]) <= index)


def _consume_lots(lots, asset, shares, index, issues) -> None:
    remaining = int(shares)
    for lot in lots.get(asset, ()):
        if int(lot["available_index"]) > index:
            continue
        taken = min(int(lot["shares"]), remaining)
        lot["shares"] = int(lot["shares"]) - taken
        remaining -= taken
        if remaining == 0:
            break
    if remaining:
        issues.append(f"lot_consumption_shortfall:{asset}:{index}:{remaining}")


def _position_value(shares, prices, asset_index) -> float:
    total = 0.0
    for asset, count in shares.items():
        price = float(prices[asset_index[asset]])
        if count and (not math.isfinite(price) or price <= 0):
            raise SimulationVerificationError(f"position_mark_invalid:{asset}")
        total += count * price
    return float(total)


def _cash_total(cash) -> float:
    return float(cash["available"] + cash["frozen"] + cash["unsettled_receivable"] - cash["unsettled_payable"])


def _money_equal(actual, expected, code, issues) -> None:
    if not math.isfinite(actual) or not math.isfinite(expected) or abs(actual - expected) > MONEY_TOLERANCE_CNY + 1e-9:
        issues.append(code)


def _resolve_generation(path: str | Path) -> tuple[Path, dict[str, Any]]:
    target = Path(path)
    if target.is_file():
        if target.name == "current.json":
            root = target.parent
            pointer = _read_json(target)
            if pointer.get("schema_version") != RUN_POINTER_SCHEMA:
                raise SimulationVerificationError("run_pointer_schema_mismatch")
            manifest_path = root / str(pointer["manifest"])
        else:
            manifest_path = target
            root = target.parent.parent.parent if target.parent.name.startswith("simulation_run_") else target.parent
    elif (target / "current.json").is_file():
        root = target
        pointer = _read_json(target / "current.json")
        if pointer.get("schema_version") != RUN_POINTER_SCHEMA:
            raise SimulationVerificationError("run_pointer_schema_mismatch")
        manifest_path = root / str(pointer["manifest"])
    elif (target / "manifest.json").is_file():
        manifest_path = target / "manifest.json"
        root = target
    else:
        raise SimulationVerificationError("run_manifest_not_found")
    manifest = _read_json(manifest_path)
    generation = manifest_path.parent
    if (target / "current.json").is_file():
        pointer = _read_json(target / "current.json")
        if pointer.get("content_hash") != manifest.get("content_hash"):
            raise SimulationVerificationError("run_pointer_content_hash_mismatch")
    return generation, manifest


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SimulationVerificationError(f"artifact_missing:{path.name}")
    return json.loads(path.read_text())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SimulationVerificationError(f"artifact_missing:{path.name}")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


validate_simulation_run = verify_simulation_run
verify_replay_pair = compare_replay_truth


def verify_task055a_orchestration(output_root: str | Path) -> dict[str, Any]:
    """Independently verify the final Task055-A DAG and every terminal run."""

    root = Path(output_root)
    final_pointer = _read_json(root / "final" / "current.json")
    final_path = root / "final" / str(final_pointer.get("manifest") or "")
    result = _read_json(final_path)
    expected_result_hash = canonical_hash({
        key: value for key, value in result.items() if key != "result_hash"
    })
    if result.get("result_hash") != expected_result_hash:
        raise SimulationVerificationError("task055a_final_result_hash_mismatch")
    generation_results: dict[str, Any] = {}
    for role in ("primary", "sibling"):
        pointer = _read_json(root / role / "current.json")
        manifest_path = root / role / str(pointer.get("manifest") or "")
        manifest = _read_json(manifest_path)
        semantic = {key: value for key, value in manifest.items() if key != "content_hash"}
        if canonical_hash(semantic) != manifest.get("content_hash"):
            raise SimulationVerificationError(f"task055a_{role}_generation_hash_mismatch")
        rows = manifest.get("runs") or []
        if len(rows) != 100 or len({(row.get("factor_id"), row.get("scenario")) for row in rows}) != 100:
            raise SimulationVerificationError(f"task055a_{role}_terminal_set_invalid")
        verified_rows = []
        for row in rows:
            run_root = manifest_path.parents[2] / str(row["path"])
            verified = verify_simulation_run(
                run_root,
                expected_spec_hash=row.get("spec_hash"),
                expected_input_lineage_hash=row.get("input_lineage_hash"),
            )
            if verified.get("truth_hash") != row.get("run_hash") or verified.get("content_hash") != row.get("content_hash"):
                raise SimulationVerificationError(f"task055a_{role}_run_hash_mismatch")
            verified_rows.append(verified)
        truth_hash = canonical_hash([row["run_hash"] for row in rows])
        if truth_hash != manifest.get("truth_hash"):
            raise SimulationVerificationError(f"task055a_{role}_truth_root_mismatch")
        generation_results[role] = {
            "truth_hash": truth_hash,
            "terminal_count": len(rows),
            "data_blocked_count": sum(row.get("terminal_state") == "data_blocked" for row in rows),
            "verified_artifact_count": len(verified_rows),
        }
    if generation_results["primary"]["truth_hash"] != generation_results["sibling"]["truth_hash"]:
        raise SimulationVerificationError("task055a_ab_truth_mismatch")
    if result.get("primary_truth_hash") != generation_results["primary"]["truth_hash"]:
        raise SimulationVerificationError("task055a_final_primary_truth_mismatch")
    if result.get("sibling_truth_hash") != generation_results["sibling"]["truth_hash"]:
        raise SimulationVerificationError("task055a_final_sibling_truth_mismatch")
    if result.get("resume_truth_hash") != generation_results["primary"]["truth_hash"] or result.get("immutable_resume_hit") is not True:
        raise SimulationVerificationError("task055a_final_resume_invalid")
    from .run import BLOCKED_STATUS, SUCCESS_STATUS, inspect_physical_states

    inventory = result.get("physical_state_inventory") or {}
    roots = {name: row.get("root") for name, row in inventory.items()}
    if inspect_physical_states(roots) != inventory:
        raise SimulationVerificationError("task055a_physical_state_inventory_drift")
    if any(int(row.get("record_count", -1)) != 0 for row in inventory.values()):
        raise SimulationVerificationError("task055a_downstream_queue_nonempty")
    readiness = result.get("readiness") or {}
    if any(readiness.get(name) is not False for name in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
        raise SimulationVerificationError("task055a_readiness_boundary_invalid")
    if result.get("status") not in {SUCCESS_STATUS, BLOCKED_STATUS}:
        raise SimulationVerificationError("task055a_final_status_invalid")
    if generation_results["primary"]["data_blocked_count"] and result.get("status") != BLOCKED_STATUS:
        raise SimulationVerificationError("task055a_data_blocked_promoted")
    payload = {
        "schema_version": "task055a_independent_final_verification_v1",
        "status": "verified",
        "top_status": result["status"],
        "final_result_hash": result["result_hash"],
        "primary": generation_results["primary"],
        "sibling": generation_results["sibling"],
        "immutable_resume_hit": True,
        "queues_verified_empty": True,
        "certification_blocked": True,
    }
    payload["verification_hash"] = canonical_hash(payload)
    return payload
