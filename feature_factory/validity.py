"""Build governed v3 feature validity tensors."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


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
        masks = [source_validity[field] for field in fields if field in source_validity]
        base = np.logical_and.reduce(masks) if masks else np.zeros_like(eligible_mask, dtype=np.bool_)
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
            "blocker": None if valid.any() else "zero_valid_coverage",
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
