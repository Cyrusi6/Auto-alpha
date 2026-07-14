"""Canonical recursive semantics for A-share features and formulas."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SEMANTICS_VERSION = "ashare_feature_formula_semantics_v1"


@dataclass(frozen=True)
class DependencyPathStep:
    node: str
    node_type: str
    lag_increment: int
    cumulative_raw_lag: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureSemantics:
    feature_name: str
    raw_dependencies: tuple[str, ...]
    max_raw_lag: int
    required_observations: int
    inner_operations: tuple[dict[str, Any], ...]
    outer_transforms: tuple[dict[str, Any], ...]
    validity_rule: str
    min_periods: int
    price_basis: str
    pit_availability: str
    longest_dependency_path: tuple[DependencyPathStep, ...]
    feature_implementation_source_hash: str
    operator_implementation_source_hash: str
    semantics_hash: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_dependencies"] = list(self.raw_dependencies)
        payload["inner_operations"] = [dict(item) for item in self.inner_operations]
        payload["outer_transforms"] = [dict(item) for item in self.outer_transforms]
        payload["longest_dependency_path"] = [item.to_dict() for item in self.longest_dependency_path]
        return payload


@dataclass(frozen=True)
class FormulaSemantics:
    formula_names: tuple[str, ...]
    max_raw_lag: int
    required_observations: int
    longest_dependency_path: tuple[DependencyPathStep, ...]
    feature_semantics_hashes: tuple[str, ...]
    operator_implementation_source_hash: str
    semantics_hash: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["formula_names"] = list(self.formula_names)
        payload["longest_dependency_path"] = [item.to_dict() for item in self.longest_dependency_path]
        payload["feature_semantics_hashes"] = list(self.feature_semantics_hashes)
        return payload


def build_feature_semantics(
    definition: Mapping[str, Any] | Any,
    *,
    feature_source_hash: str | None = None,
    operator_source_hash: str | None = None,
) -> FeatureSemantics:
    payload = definition if isinstance(definition, Mapping) else definition.to_dict()
    name = str(payload.get("feature_name") or "").upper()
    if not name:
        raise ValueError("feature contract missing feature_name")
    raw_dependencies = tuple(str(item) for item in payload.get("source_fields", ()) if str(item))
    if not raw_dependencies:
        raise ValueError(f"feature contract missing raw dependencies: {name}")
    transform = str(payload.get("transform") or "identity")
    declared_lookback = max(1, int(payload.get("lookback", 1) or 1))
    pit_availability = _pit_availability(payload.get("availability_field"), str(payload.get("pit_safety", "pit_safe")))
    recipe = _feature_recipe(name, raw_dependencies, declared_lookback, transform)
    feature_source_hash = feature_source_hash or feature_implementation_source_hash()
    operator_source_hash = operator_source_hash or operator_implementation_source_hash()
    path = _build_path(recipe["path"])
    max_raw_lag = path[-1].cumulative_raw_lag
    core = {
        "version": SEMANTICS_VERSION,
        "feature_name": name,
        "raw_dependencies": list(recipe["raw_dependencies"]),
        "max_raw_lag": max_raw_lag,
        "required_observations": max_raw_lag + 1,
        "inner_operations": recipe["inner_operations"],
        "outer_transforms": recipe["outer_transforms"],
        "validity_rule": recipe["validity_rule"],
        "min_periods": int(recipe["min_periods"]),
        "price_basis": recipe["price_basis"],
        "pit_availability": pit_availability,
        "longest_dependency_path": [item.to_dict() for item in path],
        "feature_implementation_source_hash": feature_source_hash,
        "operator_implementation_source_hash": operator_source_hash,
    }
    semantics_hash = _stable_hash(core)
    return FeatureSemantics(
        feature_name=name,
        raw_dependencies=tuple(recipe["raw_dependencies"]),
        max_raw_lag=max_raw_lag,
        required_observations=max_raw_lag + 1,
        inner_operations=tuple(dict(item) for item in recipe["inner_operations"]),
        outer_transforms=tuple(dict(item) for item in recipe["outer_transforms"]),
        validity_rule=str(recipe["validity_rule"]),
        min_periods=int(recipe["min_periods"]),
        price_basis=str(recipe["price_basis"]),
        pit_availability=pit_availability,
        longest_dependency_path=path,
        feature_implementation_source_hash=feature_source_hash,
        operator_implementation_source_hash=operator_source_hash,
        semantics_hash=semantics_hash,
    )


def build_feature_semantics_map(manifest_or_definitions: Any) -> dict[str, FeatureSemantics]:
    if hasattr(manifest_or_definitions, "feature_definitions"):
        definitions = manifest_or_definitions.feature_definitions
    elif isinstance(manifest_or_definitions, Mapping):
        definitions = manifest_or_definitions.get("feature_definitions") or manifest_or_definitions.get("features")
    else:
        definitions = manifest_or_definitions
    if not isinstance(definitions, Iterable) or isinstance(definitions, (str, bytes)):
        raise ValueError("feature semantics require feature definitions")
    feature_hash = feature_implementation_source_hash()
    operator_hash = operator_implementation_source_hash()
    result: dict[str, FeatureSemantics] = {}
    for definition in definitions:
        semantics = build_feature_semantics(
            definition,
            feature_source_hash=feature_hash,
            operator_source_hash=operator_hash,
        )
        if semantics.feature_name in result:
            raise ValueError(f"duplicate feature contract: {semantics.feature_name}")
        result[semantics.feature_name] = semantics
    if not result:
        raise ValueError("feature semantics map is empty")
    return result


def feature_semantics_contract_hash(feature_semantics: Mapping[str, FeatureSemantics]) -> str:
    if not feature_semantics:
        raise ValueError("feature semantics map is empty")
    return _stable_hash(
        {
            "version": SEMANTICS_VERSION,
            "features": {name: feature_semantics[name].to_dict() for name in sorted(feature_semantics)},
        }
    )


def calculate_formula_semantics(
    formula_names: Sequence[str],
    feature_semantics: Mapping[str, FeatureSemantics],
    *,
    operator_arities: Mapping[str, int],
    operator_windows: Mapping[str, int],
) -> FormulaSemantics:
    if not feature_semantics:
        raise ValueError("formula semantics require an explicit feature dependency map")
    stack: list[tuple[int, tuple[DependencyPathStep, ...], tuple[str, ...]]] = []
    used_feature_hashes: list[str] = []
    for raw_name in formula_names:
        name = str(raw_name)
        if name in feature_semantics:
            semantics = feature_semantics[name]
            stack.append((semantics.max_raw_lag, semantics.longest_dependency_path, (semantics.semantics_hash,)))
            used_feature_hashes.append(semantics.semantics_hash)
            continue
        if name not in operator_arities:
            raise ValueError(f"missing canonical feature contract: {name}")
        arity = int(operator_arities[name])
        if len(stack) < arity:
            raise ValueError(f"stack underflow at operator: {name}")
        inputs = stack[-arity:]
        del stack[-arity:]
        base = max(inputs, key=lambda item: item[0])
        increment = _operator_lag_increment(name, int(operator_windows[name]))
        cumulative = base[0] + increment
        path = base[1] + (DependencyPathStep(name, "formula_operator", increment, cumulative),)
        hashes = tuple(item for entry in inputs for item in entry[2])
        stack.append((cumulative, path, hashes))
    if len(stack) != 1:
        raise ValueError(f"formula semantics require one output, got {len(stack)}")
    max_raw_lag, path, feature_hashes = stack[0]
    operator_hash = operator_implementation_source_hash()
    core = {
        "version": SEMANTICS_VERSION,
        "formula_names": list(formula_names),
        "max_raw_lag": max_raw_lag,
        "required_observations": max_raw_lag + 1,
        "longest_dependency_path": [item.to_dict() for item in path],
        "feature_semantics_hashes": list(feature_hashes),
        "operator_implementation_source_hash": operator_hash,
    }
    return FormulaSemantics(
        formula_names=tuple(str(item) for item in formula_names),
        max_raw_lag=max_raw_lag,
        required_observations=max_raw_lag + 1,
        longest_dependency_path=path,
        feature_semantics_hashes=feature_hashes,
        operator_implementation_source_hash=operator_hash,
        semantics_hash=_stable_hash(core),
    )


def feature_implementation_source_hash() -> str:
    from . import builder, extended_builder, validity

    return _modules_hash((builder, extended_builder, validity), extra_files=(Path(__file__),))


def operator_implementation_source_hash() -> str:
    from model_core import ops, validity, vm

    return _modules_hash((ops, validity, vm))


def _feature_recipe(name: str, fields: tuple[str, ...], lookback: int, transform: str) -> dict[str, Any]:
    raw_dependencies = fields
    price_basis = "not_applicable"
    inner: list[dict[str, Any]] = []
    path: list[tuple[str, str, int]] = [(fields[0], "raw_field", 0)]
    validity_rule = "all_required_sources_valid"
    min_periods = 1

    if name.startswith("RET_") and name.endswith("D"):
        horizon = _suffix_horizon(name)
        raw_dependencies = ("adjusted_close",)
        price_basis = "adjusted_close"
        inner = [_operation("endpoint_log_return", horizon, horizon + 1)]
        path = [("adjusted_close", "raw_field", 0), (f"RET_{horizon}D", "inner_operation", horizon)]
        validity_rule = "both_adjusted_price_endpoints_valid"
        min_periods = horizon + 1
    elif name in {"VOLATILITY_5D", "VOLATILITY_20D", "DOWNSIDE_VOL_20D"}:
        horizon = _suffix_horizon(name)
        raw_dependencies = ("adjusted_close",)
        price_basis = "adjusted_close"
        inner = [_operation("one_day_log_return", 1, 2), _operation("rolling_std", horizon - 1, horizon)]
        path = [("adjusted_close", "raw_field", 0), ("one_day_return", "inner_operation", 1), (f"rolling_{horizon}", "inner_operation", horizon - 1)]
        validity_rule = "all_contiguous_adjusted_price_endpoints_valid"
        min_periods = horizon + 1
    elif name in {"INTRADAY_RETURN", "GAP_RETURN", "AMPLITUDE"}:
        price_basis = "raw_intraday_ohlc" if name != "GAP_RETURN" else "raw_gap_ohlc"
        inner = [_operation(name.lower(), 0, 1)]
        validity_rule = "all_same_day_raw_endpoints_valid"
    elif name.startswith("INDEX_RETURN_") and name.endswith("D"):
        horizon = _suffix_horizon(name)
        raw_dependencies = ("index_daily_bars.close",)
        price_basis = "index_close"
        inner = [_operation("endpoint_log_return", horizon, horizon + 1)]
        path = [(raw_dependencies[0], "raw_field", 0), (f"INDEX_RETURN_{horizon}D", "inner_operation", horizon)]
        validity_rule = "all_index_price_endpoints_valid"
        min_periods = horizon + 1
    elif name == "INDEX_VOLATILITY_20D":
        raw_dependencies = ("index_daily_bars.close",)
        price_basis = "index_close"
        inner = [_operation("one_day_log_return", 1, 2), _operation("rolling_std", 19, 20)]
        path = [(raw_dependencies[0], "raw_field", 0), ("one_day_return", "inner_operation", 1), ("rolling_std_20", "inner_operation", 19)]
        validity_rule = "all_contiguous_index_price_endpoints_valid"
        min_periods = 21
    elif name.startswith("BENCHMARK_RELATIVE_RETURN_"):
        horizon = _suffix_horizon(name)
        raw_dependencies = ("adjusted_close", "index_daily_bars.close")
        price_basis = "adjusted_stock_close_vs_index_close"
        inner = [_operation("stock_endpoint_log_return", horizon, horizon + 1), _operation("index_endpoint_log_return", horizon, horizon + 1), _operation("subtract", 0, 1)]
        path = [("adjusted_close", "raw_field", 0), (f"stock_return_{horizon}", "inner_operation", horizon), ("relative_return", "inner_operation", 0)]
        validity_rule = "stock_and_index_endpoints_valid"
        min_periods = horizon + 1
    elif name.startswith("INDUSTRY_RELATIVE_RETURN_") or name == "INDUSTRY_MOMENTUM":
        horizon = 20 if name == "INDUSTRY_MOMENTUM" else _suffix_horizon(name)
        raw_dependencies = ("industry_members", "adjusted_close")
        price_basis = "adjusted_close"
        inner = [_operation("endpoint_log_return", horizon, horizon + 1), _operation("pit_industry_group_mean", 0, 1), _operation("subtract", 0, 1)]
        path = [("adjusted_close", "raw_field", 0), (f"return_{horizon}", "inner_operation", horizon), ("pit_industry_relative", "inner_operation", 0)]
        validity_rule = "price_endpoints_and_pit_industry_valid"
        min_periods = horizon + 1
    elif name == "MARKET_REGIME_UP_DOWN_FLAG":
        raw_dependencies = ("index_daily_bars.close",)
        price_basis = "index_close"
        inner = [_operation("endpoint_log_return", 20, 21), _operation("positive_flag", 0, 1)]
        path = [(raw_dependencies[0], "raw_field", 0), ("index_return_20", "inner_operation", 20)]
        validity_rule = "index_return_endpoints_valid"
        min_periods = 21
    elif name in {"AMOUNT_Z20", "TURNOVER_Z20", "MONEYFLOW_Z20", "MARGIN_CROWDING_Z20", "HK_HOLDING_Z20"}:
        inner = [_operation("rolling_zscore", 19, 20)]
        path.append(("rolling_zscore_20", "inner_operation", 19))
        validity_rule = "all_rolling_observations_valid"
        min_periods = 20
    elif name in {"RECENT_SUSPENSION_COUNT_20D", "MONEYFLOW_TREND_20D"}:
        inner = [_operation("rolling_window", 19, 20)]
        path.append(("rolling_20", "inner_operation", 19))
        validity_rule = "all_rolling_observations_valid"
        min_periods = 20
    elif name == "MONEYFLOW_TREND_5D":
        inner = [_operation("rolling_mean", 4, 5)]
        path.append(("rolling_mean_5", "inner_operation", 4))
        validity_rule = "all_rolling_observations_valid"
        min_periods = 5
    elif name in {"HK_HOLDING_CHANGE_5D", "HK_HOLDING_CHANGE_20D"}:
        horizon = _suffix_horizon(name)
        inner = [_operation("delta", horizon, horizon + 1)]
        path.append((f"delta_{horizon}", "inner_operation", horizon))
        validity_rule = "current_and_delayed_observations_valid"
        min_periods = horizon + 1
    elif name in {"MARGIN_BALANCE_CHANGE", "SHORT_SELL_BALANCE_CHANGE", "HOLDER_NUMBER_CHANGE"}:
        inner = [_operation("delta", 1, 2)]
        path.append(("delta_1", "inner_operation", 1))
        validity_rule = "current_and_previous_observations_valid"
        min_periods = 2
    elif lookback > 1:
        inner = [_operation("rolling_window", lookback - 1, lookback)]
        path.append((f"rolling_{lookback}", "inner_operation", lookback - 1))
        validity_rule = "all_rolling_observations_valid"
        min_periods = lookback

    outer: list[dict[str, Any]] = []
    if transform == "time_series_zscore":
        window = lookback
        outer.append(_operation("time_series_zscore", window - 1, window))
        path.append((f"time_series_zscore_{window}", "outer_transform", window - 1))
        min_periods = max(min_periods, sum(step[2] for step in path) + 1)
        validity_rule = f"{validity_rule}_and_outer_window_valid"
    elif transform not in {"identity", "raw", "robust_zscore"}:
        raise ValueError(f"unsupported canonical feature transform: {name}:{transform}")
    elif transform == "robust_zscore":
        outer.append(_operation("eligible_cross_section_robust_zscore", 0, 1))

    return {
        "raw_dependencies": raw_dependencies,
        "inner_operations": inner,
        "outer_transforms": outer,
        "validity_rule": validity_rule,
        "min_periods": min_periods,
        "price_basis": price_basis,
        "path": path,
    }


def _operation(name: str, lag_increment: int, required_observations: int) -> dict[str, Any]:
    return {
        "name": name,
        "lag_increment": int(lag_increment),
        "required_observations": int(required_observations),
    }


def _build_path(specs: Sequence[tuple[str, str, int]]) -> tuple[DependencyPathStep, ...]:
    cumulative = 0
    result: list[DependencyPathStep] = []
    for node, node_type, increment in specs:
        cumulative += int(increment)
        result.append(DependencyPathStep(str(node), str(node_type), int(increment), cumulative))
    return tuple(result)


def _operator_lag_increment(name: str, window: int) -> int:
    if name.startswith(("DELAY", "DELTA")):
        return window
    if name.startswith("TS_"):
        return max(0, window - 1)
    return 0


def _suffix_horizon(name: str) -> int:
    suffix = name.rsplit("_", 1)[-1]
    if not suffix.endswith("D") or not suffix[:-1].isdigit():
        raise ValueError(f"feature horizon cannot be derived: {name}")
    return int(suffix[:-1])


def _pit_availability(availability_field: Any, pit_safety: str) -> str:
    if availability_field:
        return f"available_after_{availability_field}"
    if pit_safety == "event_date_only":
        return "available_on_event_date_if_source_coverage_proven"
    if pit_safety == "weak_pit":
        return "availability_timestamp_required"
    return "same_trade_date_after_source_observation"


def _modules_hash(modules: Sequence[Any], *, extra_files: Sequence[Path] = ()) -> str:
    digest = hashlib.sha256(SEMANTICS_VERSION.encode("utf-8"))
    paths: list[Path] = []
    for module in modules:
        source = Path(inspect.getsourcefile(module) or "")
        if not source.is_file():
            raise RuntimeError(f"semantic source unavailable: {module.__name__}")
        paths.append(source)
    paths.extend(extra_files)
    for path in sorted(set(paths), key=lambda item: str(item)):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
