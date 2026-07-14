"""Deterministic lineage contracts for alpha screening caches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import torch


def build_loader_lineage(loader, *, stage: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    feature_manifest_path = getattr(loader, "feature_set_manifest_path", None)
    matrix_cache_dir = getattr(loader, "matrix_cache_dir", None)
    matrix_manifest = _matrix_manifest_path(matrix_cache_dir)
    matrix_payload = json.loads(matrix_manifest.read_text(encoding="utf-8")) if matrix_manifest is not None else {}
    source_dates = list(getattr(loader, "firewall_source_trade_dates", None) or loader.trade_dates)
    firewall = getattr(loader, "date_firewall", None)
    payload = {
        "stage": stage,
        "stock_axis_hash": _hash_lines(list(loader.ts_codes)),
        "source_date_axis_hash": _hash_lines(source_dates),
        "eligible_date_hash": _hash_lines(list(loader.trade_dates)),
        "feature_manifest_sha256": _optional_sha(feature_manifest_path),
        "matrix_manifest_sha256": _optional_sha(matrix_manifest),
        "matrix_semantic_hash": matrix_payload.get("semantic_hash"),
        "target_contract": dict(matrix_payload.get("target_contract") or {}),
        "feature_shape": list(loader.feat_tensor.shape),
        "feature_tensor_hash": _tensor_hash(loader.feat_tensor),
        "feature_validity_hash": _tensor_hash(getattr(loader, "feature_validity", None)),
        "target_shape": list(loader.target_ret.shape),
        "target_tensor_hash": _tensor_hash(loader.target_ret),
        "target_available_hash": _tensor_hash(getattr(loader, "target_available", None)),
        "target_return_mode": getattr(loader, "target_return_mode", None),
        "label_horizon": int(getattr(loader, "label_horizon", 1)),
        "firewall": firewall.fingerprint_payload(source_dates) if firewall is not None else None,
        "extra": extra or {},
        "evaluation_code_semantic_hash": _code_semantic_hash(),
    }
    payload["lineage_hash"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def _hash_lines(values: list[str]) -> str:
    return hashlib.sha256("\n".join(str(value) for value in values).encode()).hexdigest()


def _optional_sha(path_value) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matrix_manifest_path(matrix_cache_dir) -> Path | None:
    if not matrix_cache_dir:
        return None
    root = Path(matrix_cache_dir)
    for name in ("task_052a_strict_matrix_manifest.json", "metadata.json", "matrix_version_manifest.json"):
        path = root / name
        if path.exists():
            return path
    return None


def _tensor_hash(value) -> str | None:
    if value is None or not isinstance(value, torch.Tensor):
        return None
    array = value.detach().cpu().contiguous().numpy()
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode())
    digest.update(json.dumps(list(array.shape)).encode())
    digest.update(array.tobytes())
    return digest.hexdigest()


def _code_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "model_core" / "vm.py",
        root / "model_core" / "validity.py",
        root / "alpha_factory" / "proxy_eval.py",
        root / "formula_batch_eval" / "evaluator.py",
        root / "research_firewall" / "firewall.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
