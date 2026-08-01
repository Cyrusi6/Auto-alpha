"""Canonical feature dependency and validity contracts."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .semantics import build_feature_semantics


CONTRACT_VERSION = "ashare_v3_feature_contract_v1"


@dataclass(frozen=True)
class FeatureInputDependency:
    field: str
    offsets: tuple[int, ...] = (0,)
    history: int = 1
    price_basis: str = "not_applicable"
    pit_availability: str = "same_trade_date"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["offsets"] = list(self.offsets)
        return payload


@dataclass(frozen=True)
class FeatureComputationContract:
    version: str
    feature_name: str
    source_fields: tuple[str, ...]
    dependencies: tuple[FeatureInputDependency, ...]
    effective_lookback: int
    price_basis: str
    pit_availability: str
    validity_rule: str
    computation: str
    transform: str
    max_raw_lag: int
    required_observations: int
    inner_operations: tuple[dict[str, Any], ...]
    outer_transforms: tuple[dict[str, Any], ...]
    longest_dependency_path: tuple[dict[str, Any], ...]
    feature_implementation_source_hash: str
    operator_implementation_source_hash: str
    semantics_hash: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_fields"] = list(self.source_fields)
        payload["dependencies"] = [item.to_dict() for item in self.dependencies]
        payload["inner_operations"] = [dict(item) for item in self.inner_operations]
        payload["outer_transforms"] = [dict(item) for item in self.outer_transforms]
        payload["longest_dependency_path"] = [dict(item) for item in self.longest_dependency_path]
        return payload


def build_feature_contract(
    feature_name: str,
    source_fields: Iterable[str],
    *,
    lookback: int = 1,
    transform: str = "identity",
    availability_field: str | None = None,
    pit_safety: str = "pit_safe",
    feature_version: str = "",
) -> FeatureComputationContract:
    name = str(feature_name).upper()
    fields = tuple(str(field) for field in source_fields)
    lookback = max(1, int(lookback or 1))
    semantics = build_feature_semantics(
        {
            "feature_name": name,
            "source_fields": fields,
            "lookback": lookback,
            "transform": transform,
            "availability_field": availability_field,
            "pit_safety": pit_safety,
            "feature_version": feature_version,
        }
    )
    expanded_precomputed = feature_version == "ashare_features_v3"
    if expanded_precomputed:
        dependencies = (
            FeatureInputDependency(
                field=name.lower(),
                offsets=(0,),
                history=1,
                price_basis=semantics.price_basis,
                pit_availability=semantics.pit_availability,
            ),
        )
    else:
        contiguous = "contiguous" in semantics.validity_rule or "rolling" in semantics.validity_rule
        dependencies = tuple(
            FeatureInputDependency(
                field=field,
                offsets=() if contiguous else ((0, -semantics.max_raw_lag) if semantics.max_raw_lag else (0,)),
                history=semantics.required_observations,
                price_basis=semantics.price_basis,
                pit_availability=semantics.pit_availability,
            )
            for field in semantics.raw_dependencies
        )
    computation = "+".join(str(item["name"]) for item in semantics.inner_operations) or "direct_observation"
    return FeatureComputationContract(
        version=CONTRACT_VERSION,
        feature_name=name,
        source_fields=semantics.raw_dependencies,
        dependencies=dependencies,
        effective_lookback=semantics.required_observations,
        price_basis=semantics.price_basis,
        pit_availability=semantics.pit_availability,
        validity_rule=semantics.validity_rule,
        computation=computation,
        transform=transform,
        max_raw_lag=semantics.max_raw_lag,
        required_observations=semantics.required_observations,
        inner_operations=semantics.inner_operations,
        outer_transforms=semantics.outer_transforms,
        longest_dependency_path=tuple(item.to_dict() for item in semantics.longest_dependency_path),
        feature_implementation_source_hash=semantics.feature_implementation_source_hash,
        operator_implementation_source_hash=semantics.operator_implementation_source_hash,
        semantics_hash=semantics.semantics_hash,
    )


def contract_from_definition(definition: Mapping[str, Any] | Any) -> FeatureComputationContract:
    payload = definition if isinstance(definition, Mapping) else definition.to_dict()
    stored = payload.get("dependency_graph") or payload.get("feature_contract")
    if stored:
        return _contract_from_payload(dict(stored))
    return build_feature_contract(
        str(payload["feature_name"]),
        payload.get("source_fields", ()),
        lookback=int(payload.get("lookback", 1) or 1),
        transform=str(payload.get("transform", "identity")),
        availability_field=payload.get("availability_field"),
        pit_safety=str(payload.get("pit_safety", "pit_safe")),
        feature_version=str(payload.get("feature_version", "")),
    )


def intersect_candidate_feature_blockers(
    candidates: Iterable[Mapping[str, Any]],
    feature_summaries: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    blocker_by_feature = {
        str(row.get("feature_name")): str(row.get("blocker"))
        for row in feature_summaries
        if row.get("feature_name") and row.get("blocker")
    }
    result: dict[str, list[dict[str, str]]] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("factor_id") or candidate.get("candidate_id") or "")
        names = candidate.get("formula_names") or candidate.get("formula") or candidate.get("canonical_names") or ()
        dependencies = sorted({str(name) for name in names if str(name) in blocker_by_feature})
        if dependencies:
            result[candidate_id] = [
                {"feature_name": name, "reason_code": blocker_by_feature[name]}
                for name in dependencies
            ]
    return result


def feature_semantic_source_hash(extra_sources: Sequence[str | Path] = ()) -> str:
    import sys

    from . import builder, catalog, extended_builder, validity
    from model_core import data_loader, ops, vocab
    from model_core import validity as vm_validity
    from model_core import vm

    modules = (sys.modules[__name__], catalog, builder, extended_builder, validity, data_loader, ops, vocab, vm_validity, vm)
    digest = hashlib.sha256(CONTRACT_VERSION.encode("utf-8"))
    for module in modules:
        path = Path(inspect.getsourcefile(module) or "")
        if not path.is_file():
            raise RuntimeError(f"semantic source unavailable: {module.__name__}")
        digest.update(module.__name__.encode("utf-8"))
        digest.update(path.read_bytes())
    for source in sorted(Path(item) for item in extra_sources):
        digest.update(str(source.name).encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest()


def build_tensor_content_fingerprint(
    *,
    values_sha256: str,
    validity_sha256: str,
    matrix_sha256: str,
    freeze_sha256: str,
    universe_sha256: str,
    feature_manifest_sha256: str,
    stock_axis_hash: str,
    date_axis_hash: str,
    feature_axis_hash: str,
    target_contract_hash: str,
    time_contract_hash: str,
    semantic_source_hash: str,
) -> str:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "values_sha256": values_sha256,
        "validity_sha256": validity_sha256,
        "matrix_sha256": matrix_sha256,
        "freeze_sha256": freeze_sha256,
        "universe_sha256": universe_sha256,
        "feature_manifest_sha256": feature_manifest_sha256,
        "stock_axis_hash": stock_axis_hash,
        "date_axis_hash": date_axis_hash,
        "feature_axis_hash": feature_axis_hash,
        "target_contract_hash": target_contract_hash,
        "time_contract_hash": time_contract_hash,
        "semantic_source_hash": semantic_source_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _contract_from_payload(payload: dict[str, Any]) -> FeatureComputationContract:
    semantics = build_feature_semantics(payload)
    return FeatureComputationContract(
        version=str(payload.get("version", CONTRACT_VERSION)),
        feature_name=str(payload["feature_name"]),
        source_fields=tuple(str(item) for item in payload.get("source_fields", ())),
        dependencies=tuple(
            FeatureInputDependency(
                field=str(item["field"]),
                offsets=tuple(int(offset) for offset in item.get("offsets", ())),
                history=max(1, int(item.get("history", 1))),
                price_basis=str(item.get("price_basis", "not_applicable")),
                pit_availability=str(item.get("pit_availability", "same_trade_date")),
            )
            for item in payload.get("dependencies", ())
        ),
        effective_lookback=max(1, int(payload.get("effective_lookback", 1))),
        price_basis=str(payload.get("price_basis", "not_applicable")),
        pit_availability=str(payload.get("pit_availability", "same_trade_date")),
        validity_rule=str(payload.get("validity_rule", "all_sources_valid_for_required_history")),
        computation=str(payload.get("computation", "direct_or_derived")),
        transform=str(payload.get("transform", "identity")),
        max_raw_lag=int(payload.get("max_raw_lag", semantics.max_raw_lag)),
        required_observations=int(payload.get("required_observations", semantics.required_observations)),
        inner_operations=tuple(dict(item) for item in payload.get("inner_operations", semantics.inner_operations)),
        outer_transforms=tuple(dict(item) for item in payload.get("outer_transforms", semantics.outer_transforms)),
        longest_dependency_path=tuple(dict(item) for item in payload.get("longest_dependency_path", [item.to_dict() for item in semantics.longest_dependency_path])),
        feature_implementation_source_hash=str(payload.get("feature_implementation_source_hash", semantics.feature_implementation_source_hash)),
        operator_implementation_source_hash=str(payload.get("operator_implementation_source_hash", semantics.operator_implementation_source_hash)),
        semantics_hash=str(payload.get("semantics_hash", semantics.semantics_hash)),
    )
