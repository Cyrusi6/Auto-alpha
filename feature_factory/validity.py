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
        fields = list(definition.get("source_fields") or [])
        missing_fields = [field for field in fields if field not in source_validity]
        masks = [np.asarray(source_validity[field], dtype=np.bool_) for field in fields if field in source_validity]
        if any(mask.shape != eligible_mask.shape for mask in masks):
            raise ValueError(f"source validity axis mismatch: {definition.get('feature_name')}")
        base = np.logical_and.reduce(masks) if masks and not missing_fields else np.zeros_like(eligible_mask, dtype=np.bool_)
        lookback = max(1, int(definition.get("lookback") or 1))
        valid = _rolling_valid(base, lookback) if lookback > 1 else base.copy()
        valid &= eligible_mask
        valid &= np.isfinite(feature_tensor[:, feature_index, :])
        validity[:, feature_index, :] = valid
        denominator = int(eligible_mask.sum())
        summaries.append({
            "feature_name": definition.get("feature_name"),
            "lookback": lookback,
            "valid_count": int(valid.sum()),
            "eligible_count": denominator,
            "valid_coverage": float(valid.sum() / denominator) if denominator else 0.0,
            "nonzero_coverage": float(((feature_tensor[:, feature_index, :] != 0) & valid).sum() / denominator) if denominator else 0.0,
            "max_breadth": int(valid.sum(axis=0).max(initial=0)),
            "validity_dependencies": fields,
            "missing_validity_dependencies": missing_fields,
            "blocker": "missing_validity_dependency" if missing_fields else (None if valid.any() else "zero_valid_coverage"),
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
        fields = list(definition.source_fields)
        masks: list[torch.Tensor] = []
        missing: list[str] = []
        sanitized = dict(raw)
        for field in fields:
            raw_key = _resolve_raw_key(raw, field)
            mask = _resolve_validity(source_validity, raw, field, raw_key)
            if raw_key is None or mask is None:
                missing.append(field)
                continue
            mask = torch.as_tensor(mask, dtype=torch.bool, device=device)
            if tuple(mask.shape) != tuple(shape):
                raise ValueError(f"source validity axis mismatch: {definition.feature_name}:{field}")
            masks.append(mask)
            sanitized[raw_key] = torch.where(mask, raw[raw_key], torch.zeros_like(raw[raw_key]))

        source_mask = torch.stack(masks).all(dim=0) if masks and not missing else torch.zeros(shape, dtype=torch.bool, device=device)
        lookback = max(1, int(definition.lookback or 1))
        valid = _rolling_all_torch(source_mask, lookback) if lookback > 1 else source_mask
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
            values = _rolling_z(source_clean, max(2, lookback))
        else:
            values = _masked_robust_zscore(clean, valid)
        valid &= torch.isfinite(values)
        values = torch.where(valid, values, torch.zeros_like(values)).to(torch.float32)
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
                "missing_validity_dependencies": sorted(set(missing)),
                "blocker": "missing_validity_dependency" if missing else (None if valid.any() else "zero_valid_coverage"),
            }
        )
    if not matrices:
        raise ValueError("feature set manifest contains no feature definitions")
    return torch.stack(matrices, dim=1), torch.stack(validity_matrices, dim=1), summaries


def _resolve_raw_key(raw: dict[str, torch.Tensor], field: str) -> str | None:
    candidates = (field, field.rsplit(".", 1)[-1], field.lower(), field.rsplit(".", 1)[-1].lower())
    return next((candidate for candidate in candidates if candidate in raw), None)


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
