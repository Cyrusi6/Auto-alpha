"""Build governed v3 feature validity tensors."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .builder import _coerce_manifest, _definition_from_payload, _compute_raw_feature, _infer_device, _infer_shape
from .contracts import contract_from_definition
from .extended_builder import _rolling_z


def build_feature_validity_tensor(
    feature_manifest,
    feature_tensor: np.ndarray,
    source_validity: dict[str, np.ndarray],
    eligible_mask: np.ndarray,
    output_dir: str | Path,
) -> dict[str, Any]:
    shape = tuple(feature_tensor.shape)
    if len(shape) != 3 or eligible_mask.shape != (shape[0], shape[2]):
        raise ValueError("feature/eligible axis mismatch")
    validity = np.zeros(shape, dtype=np.bool_)
    summaries = []
    definitions = list(feature_manifest.feature_definitions)
    if len(definitions) != shape[1]:
        raise ValueError("feature definition count mismatch")
    for feature_index, definition in enumerate(definitions):
        contract = contract_from_definition(definition)
        valid, missing_fields = _contract_validity_numpy(contract, source_validity, eligible_mask.shape)
        valid &= eligible_mask
        valid &= np.isfinite(feature_tensor[:, feature_index, :])
        validity[:, feature_index, :] = valid
        valid_values = feature_tensor[:, feature_index, :][valid]
        standard_deviation = float(np.std(valid_values, dtype=np.float64)) if valid_values.size else 0.0
        blocker = _feature_blocker(missing_fields, int(valid.sum()), standard_deviation)
        denominator = int(eligible_mask.sum())
        summaries.append({
            "feature_name": definition.get("feature_name"),
            "lookback": contract.effective_lookback,
            "valid_count": int(valid.sum()),
            "eligible_count": denominator,
            "valid_coverage": float(valid.sum() / denominator) if denominator else 0.0,
            "nonzero_coverage": float(((feature_tensor[:, feature_index, :] != 0) & valid).sum() / denominator) if denominator else 0.0,
            "max_breadth": int(valid.sum(axis=0).max(initial=0)),
            "standard_deviation": standard_deviation,
            "validity_dependencies": [item.to_dict() for item in contract.dependencies],
            "missing_validity_dependencies": missing_fields,
            "blocker": blocker,
        })
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    tensor_path = output / "feature_validity_tensor.npy"
    _atomic_npy(tensor_path, validity)
    payload = {
        "artifact_type": "feature_validity_manifest",
        "shape": list(validity.shape),
        "dtype": str(validity.dtype),
        "feature_manifest_hash": str(feature_manifest.content_hash),
        "feature_tensor_sha256": _sha256_array_file(Path(output_dir).parent / "feature_tensor.npy") if (Path(output_dir).parent / "feature_tensor.npy").exists() else None,
        "validity_sha256": _sha256_array_file(tensor_path),
        "feature_summaries": summaries,
        "invalid_values_stored_as_zero": True,
    }
    manifest_path = output / "feature_validity_manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {**payload, "tensor_path": str(tensor_path), "manifest_path": str(manifest_path)}


def build_feature_values_and_validity(
    loader,
    feature_manifest,
    *,
    eligible_mask: torch.Tensor | np.ndarray | None = None,
) -> tuple[torch.Tensor, torch.Tensor, list[dict[str, Any]]]:
    """Build values and validity together so invalid cells never enter statistics."""
    manifest = _coerce_manifest(feature_manifest)
    definitions = [_definition_from_payload(item) for item in manifest.feature_definitions]
    raw = loader.raw_data_cache
    source_validity = dict(getattr(loader, "raw_validity_cache", {}) or {})
    source_validity.update(dict(getattr(loader, "feature_source_validity", {}) or {}))
    shape = _infer_shape(raw)
    device = _infer_device(raw)
    if eligible_mask is None:
        for key in ("signal_eligible_at_close", "signal_eligible", "pit_available_mask"):
            if key in raw:
                eligible_mask = raw[key]
                break
    if eligible_mask is None:
        raise ValueError("joint feature build requires an explicit signal-eligible mask")
    eligible = torch.as_tensor(eligible_mask, dtype=torch.bool, device=device)
    if tuple(eligible.shape) != tuple(shape):
        raise ValueError("feature eligible axis mismatch")

    matrices: list[torch.Tensor] = []
    validity_matrices: list[torch.Tensor] = []
    summaries: list[dict[str, Any]] = []
    for definition in definitions:
        contract = contract_from_definition(definition)
        missing: list[str] = []
        sanitized = dict(raw)
        dependency_masks: list[torch.Tensor] = []
        for dependency in contract.dependencies:
            field = dependency.field
            raw_key = _resolve_raw_key(raw, field)
            mask = _resolve_validity(source_validity, raw, field, raw_key)
            if field == "adjusted_close" and mask is None:
                mask = _combined_validity(source_validity, raw, ("close", "adj_factor"), shape, device)
            if raw_key is None or mask is None:
                missing.append(field)
                continue
            mask = torch.as_tensor(mask, dtype=torch.bool, device=device)
            if tuple(mask.shape) != tuple(shape):
                raise ValueError(f"source validity axis mismatch: {definition.feature_name}:{field}")
            if dependency.price_basis != "not_applicable":
                source_values = raw[raw_key]
                mask = mask & torch.isfinite(source_values) & (source_values > 0)
            dependency_masks.append(_dependency_validity_torch(mask, dependency.offsets, dependency.history))
            sanitized[raw_key] = torch.where(mask, raw[raw_key], torch.zeros_like(raw[raw_key]))

        source_mask = torch.stack(dependency_masks).all(dim=0) if dependency_masks and not missing else torch.zeros(shape, dtype=torch.bool, device=device)
        valid = source_mask.clone()
        valid &= eligible
        base = _compute_raw_feature(sanitized, definition.feature_name)
        if base is None:
            base = torch.zeros(shape, dtype=torch.float32, device=device)
            valid.zero_()
            missing.append("computed_feature_source")
        base = base.to(dtype=torch.float32)
        valid &= torch.isfinite(base)
        clean = torch.where(valid, base, torch.zeros_like(base))
        if definition.transform == "identity":
            values = clean
        elif definition.transform == "time_series_zscore":
            source_clean = torch.where(source_mask, base, torch.zeros_like(base))
            values = _rolling_z(source_clean, max(2, int(definition.lookback or 1)))
        else:
            values = _masked_robust_zscore(clean, valid)
        valid &= torch.isfinite(values)
        values = torch.where(valid, values, torch.zeros_like(values)).to(torch.float32)
        valid_values = values[valid]
        standard_deviation = float(valid_values.std(unbiased=False).item()) if valid_values.numel() else 0.0
        blocker = _feature_blocker(missing, int(valid.sum().item()), standard_deviation)
        matrices.append(values)
        validity_matrices.append(valid)
        denominator = int(eligible.sum().item())
        summaries.append(
            {
                "feature_name": definition.feature_name,
                "valid_count": int(valid.sum().item()),
                "eligible_count": denominator,
                "valid_coverage": float(valid.sum().item() / denominator) if denominator else 0.0,
                "nonzero_coverage": float(((values != 0) & valid).sum().item() / denominator) if denominator else 0.0,
                "max_breadth": int(valid.sum(dim=0).max().item()) if valid.numel() else 0,
                "standard_deviation": standard_deviation,
                "source_fields": list(contract.source_fields),
                "dependency_graph": contract.to_dict(),
                "effective_lookback": contract.effective_lookback,
                "price_basis": contract.price_basis,
                "pit_availability": contract.pit_availability,
                "validity_rule": contract.validity_rule,
                "missing_validity_dependencies": sorted(set(missing)),
                "blocker": blocker,
            }
        )
    if not matrices:
        raise ValueError("feature set manifest contains no feature definitions")
    return torch.stack(matrices, dim=1), torch.stack(validity_matrices, dim=1), summaries


def _resolve_raw_key(raw: dict[str, torch.Tensor], field: str) -> str | None:
    candidates = (field, field.rsplit(".", 1)[-1], field.lower(), field.rsplit(".", 1)[-1].lower())
    return next((candidate for candidate in candidates if candidate in raw), None)


def _feature_blocker(missing: list[str], valid_count: int, standard_deviation: float) -> str | None:
    if missing:
        return "missing_validity_dependency"
    if valid_count <= 0:
        return "zero_valid_coverage"
    if not np.isfinite(standard_deviation) or standard_deviation <= 1e-12:
        return "zero_variance"
    return None


def _combined_validity(source_validity, raw, fields, shape, device):
    masks = []
    for field in fields:
        raw_key = _resolve_raw_key(raw, field)
        mask = _resolve_validity(source_validity, raw, field, raw_key)
        if raw_key is None or mask is None:
            return None
        tensor = torch.as_tensor(mask, dtype=torch.bool, device=device)
        if tuple(tensor.shape) != tuple(shape):
            raise ValueError(f"source validity axis mismatch: {field}")
        masks.append(tensor)
    return torch.stack(masks).all(dim=0)


def _dependency_validity_torch(mask: torch.Tensor, offsets: tuple[int, ...], history: int) -> torch.Tensor:
    if offsets:
        shifted = [_shift_validity_torch(mask, offset) for offset in offsets]
        return torch.stack(shifted).all(dim=0)
    return _rolling_all_torch(mask, max(1, int(history)))


def _shift_validity_torch(mask: torch.Tensor, offset: int) -> torch.Tensor:
    result = torch.zeros_like(mask, dtype=torch.bool)
    if offset == 0:
        return mask.bool()
    if offset < 0:
        periods = -offset
        if mask.shape[1] > periods:
            result[:, periods:] = mask[:, :-periods]
        return result
    if mask.shape[1] > offset:
        result[:, :-offset] = mask[:, offset:]
    return result


def _contract_validity_numpy(contract, source_validity, shape):
    dependency_masks = []
    missing = []
    for dependency in contract.dependencies:
        mask = source_validity.get(dependency.field)
        if mask is None and dependency.field == "adjusted_close":
            close_mask = source_validity.get("close")
            adjustment_mask = source_validity.get("adj_factor")
            if close_mask is not None and adjustment_mask is not None:
                mask = np.asarray(close_mask, dtype=np.bool_) & np.asarray(adjustment_mask, dtype=np.bool_)
        if mask is None:
            missing.append(dependency.field)
            continue
        mask = np.asarray(mask, dtype=np.bool_)
        if mask.shape != shape:
            raise ValueError(f"source validity axis mismatch: {contract.feature_name}:{dependency.field}")
        if dependency.offsets:
            parts = [_shift_validity_numpy(mask, offset) for offset in dependency.offsets]
            dependency_masks.append(np.logical_and.reduce(parts))
        else:
            dependency_masks.append(_rolling_valid(mask, dependency.history))
    valid = np.logical_and.reduce(dependency_masks) if dependency_masks and not missing else np.zeros(shape, dtype=np.bool_)
    return valid, missing


def _shift_validity_numpy(mask: np.ndarray, offset: int) -> np.ndarray:
    result = np.zeros_like(mask, dtype=np.bool_)
    if offset == 0:
        return mask.copy()
    if offset < 0:
        periods = -offset
        if mask.shape[1] > periods:
            result[:, periods:] = mask[:, :-periods]
        return result
    if mask.shape[1] > offset:
        result[:, :-offset] = mask[:, offset:]
    return result


def _resolve_validity(source_validity, raw, field: str, raw_key: str | None):
    candidates = [field, field.rsplit(".", 1)[-1]]
    if raw_key:
        candidates.extend([raw_key, f"{raw_key}_validity", f"{raw_key}_valid"])
    for candidate in candidates:
        if candidate in source_validity:
            return source_validity[candidate]
        if candidate in raw and str(candidate).endswith(("_validity", "_valid")):
            return raw[candidate]
    return None


def _rolling_all_torch(mask: torch.Tensor, window: int) -> torch.Tensor:
    result = torch.zeros_like(mask, dtype=torch.bool)
    if window <= 1:
        return mask.bool()
    if mask.shape[1] >= window:
        result[:, window - 1 :] = mask.to(torch.int16).unfold(1, window, 1).sum(dim=-1) == window
    return result


def _masked_robust_zscore(values: torch.Tensor, validity: torch.Tensor, limit: float = 5.0) -> torch.Tensor:
    result = torch.zeros_like(values)
    for date_index in range(values.shape[1]):
        mask = validity[:, date_index]
        if not mask.any():
            continue
        eligible = values[mask, date_index]
        median = eligible.median()
        centered = eligible - median
        scale = centered.abs().median()
        if not torch.isfinite(scale) or float(scale.item()) < 1e-6:
            scale = torch.ones_like(scale)
        result[mask, date_index] = torch.clamp(centered / scale, -limit, limit)
    return result


def _rolling_valid(mask: np.ndarray, window: int) -> np.ndarray:
    result = np.zeros_like(mask, dtype=np.bool_)
    if mask.shape[1] >= window:
        cumulative = np.cumsum(mask.astype(np.int32), axis=1)
        counts = cumulative[:, window - 1 :].copy()
        if window < mask.shape[1]:
            counts[:, 1:] -= cumulative[:, :-window]
        result[:, window - 1 :] = counts == window
    return result


def _atomic_npy(path: Path, value: np.ndarray) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent); os.close(fd)
    temp = Path(name)
    try:
        with temp.open("wb") as handle: np.save(handle, value, allow_pickle=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _sha256_array_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()
