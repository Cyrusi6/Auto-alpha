"""Audited Task 055-A bundle validation and loading for Task 055-G."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from task_055_a.bundle import (
    DERIVED_EXECUTION_MASKS,
    EXECUTION_CUTOFF,
    EXECUTION_MASKS,
    EXECUTION_METADATA,
    RAW_FIELDS,
    SIGNAL_CUTOFF,
    SIGNAL_MASKS,
    SIMULATION_BUNDLE_SCHEMA,
)

from .access import AccessBroker, canonical_hash


class AuditedBundleError(RuntimeError):
    pass


def load_audited_simulation_bundle(
    *, manifest_path: str | Path, broker: AccessBroker
) -> dict[str, Any]:
    """Validate every registered byte and return the native loader shape."""

    relative_manifest = broker._relative(manifest_path)
    manifest = broker.read_json(
        relative_manifest,
        principal="task055g_bundle_loader",
        expected_role="task055a_simulation_bundle_manifest",
    )
    _validate_manifest_identity(manifest, relative_manifest)
    root = Path(relative_manifest).parent
    artifacts = dict(manifest.get("artifacts") or {})
    if not artifacts:
        raise AuditedBundleError("simulation_bundle_artifact_registry_empty")

    arrays: dict[str, Any] = {}
    payloads: dict[str, Any] = {}
    registered_paths: set[str] = set()
    for name, entry in sorted(artifacts.items()):
        relative = (root / str(entry.get("path") or "")).as_posix()
        registered_paths.add(relative)
        if relative.endswith(".npy"):
            value = broker.load_npy(
                relative,
                component="task055g_bundle_loader",
                dataset=f"simulation_bundle:{entry.get('role') or name}",
            )
            if list(value.shape) != list(entry.get("shape") or ()):
                raise AuditedBundleError(f"simulation_bundle_shape_mismatch:{name}")
            if str(value.dtype) != str(entry.get("dtype") or ""):
                raise AuditedBundleError(f"simulation_bundle_dtype_mismatch:{name}")
            arrays[name] = value
            continue
        if relative.endswith(".jsonl"):
            payloads[name] = broker.read_jsonl(
                relative,
                principal="task055g_bundle_loader",
            )
        elif relative.endswith(".json"):
            payloads[name] = broker.read_json(
                relative,
                principal="task055g_bundle_loader",
            )
        else:
            broker.read_bytes(relative, principal="task055g_bundle_loader")
        if int(entry.get("size_bytes") or -1) != int(broker.rows[-1].get("size_bytes") or -2):
            raise AuditedBundleError(f"simulation_bundle_size_mismatch:{name}")

    physical_root = broker.governed_root / root
    observed = {
        candidate.resolve().relative_to(broker.governed_root).as_posix()
        for candidate in physical_root.rglob("*")
        if candidate.is_file() and candidate.name != Path(relative_manifest).name
    }
    if observed != registered_paths:
        raise AuditedBundleError("simulation_bundle_unregistered_or_missing_file")

    exact_ids = [str(value) for value in manifest.get("exact20_ids") or ()]
    if len(exact_ids) != 20 or len(set(exact_ids)) != 20:
        raise AuditedBundleError("simulation_bundle_exact20_invalid")
    return {
        "manifest": manifest,
        "trade_dates": _payload(payloads, "signal_trade_dates"),
        "execution_dates": _payload(payloads, "execution_trade_dates"),
        "ts_codes": _payload(payloads, "ts_codes"),
        "factor_values": {
            factor_id: _array(arrays, f"factor:{factor_id}:values")
            for factor_id in exact_ids
        },
        "factor_validity": {
            factor_id: _array(arrays, f"factor:{factor_id}:validity")
            for factor_id in exact_ids
        },
        "strict_masks": {
            Path(name).stem: _array(arrays, f"mask:{name}") for name in SIGNAL_MASKS
        },
        "execution_masks": {
            Path(name).stem: _array(arrays, f"execution_mask:{name}")
            for name in EXECUTION_MASKS + DERIVED_EXECUTION_MASKS
        },
        "execution_metadata": {
            Path(name).stem: _array(arrays, f"execution_metadata:{name}")
            for name in EXECUTION_METADATA
        },
        "raw": {field: _array(arrays, f"raw:{field}") for field in RAW_FIELDS},
        "raw_validity": {
            field: _array(arrays, f"raw:{field}:validity") for field in RAW_FIELDS
        },
        "benchmark_index_bars": _payload(payloads, "benchmark_index_bars"),
        "corporate_actions": _payload(payloads, "corporate_actions"),
        "unit_contract": _payload(payloads, "unit_contract"),
    }


def _validate_manifest_identity(manifest: Mapping[str, Any], relative_path: str) -> None:
    if manifest.get("schema_version") != SIMULATION_BUNDLE_SCHEMA:
        raise AuditedBundleError("simulation_bundle_schema_invalid")
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise AuditedBundleError("simulation_bundle_content_hash_mismatch")
    expected_generation = f"simulation_bundle_{str(manifest.get('content_hash') or '')[:24]}"
    if manifest.get("generation_id") != expected_generation:
        raise AuditedBundleError("simulation_bundle_generation_identity_mismatch")
    if Path(relative_path).parent.name != expected_generation:
        raise AuditedBundleError("simulation_bundle_directory_identity_mismatch")
    if (
        manifest.get("status") != "ready"
        or manifest.get("blockers")
        or manifest.get("fallback_allowed") is not False
    ):
        raise AuditedBundleError("simulation_bundle_status_invalid")
    if (
        manifest.get("signal_cutoff") != SIGNAL_CUTOFF
        or manifest.get("execution_cutoff") != EXECUTION_CUTOFF
        or manifest.get("valuation_cutoff") != EXECUTION_CUTOFF
    ):
        raise AuditedBundleError("simulation_bundle_time_contract_mismatch")


def _payload(values: Mapping[str, Any], key: str) -> Any:
    if key not in values:
        raise AuditedBundleError(f"simulation_bundle_payload_missing:{key}")
    return values[key]


def _array(values: Mapping[str, Any], key: str) -> Any:
    if key not in values:
        raise AuditedBundleError(f"simulation_bundle_array_missing:{key}")
    return values[key]
