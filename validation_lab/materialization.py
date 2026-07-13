"""Fail-closed compact factor materialization from governed tensor artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

from artifact_schema.writer import attach_artifact_metadata
from factor_engine.transforms import preprocess_factor
from factor_store.models import FactorRecord
from feature_factory.builder import load_feature_manifest
from feature_factory.vocab_adapter import make_formula_vocab_from_manifest
from model_core.vm import StackVM


_FILE_HASH_CACHE: dict[tuple[str, int, int], str] = {}


class MaterializationBlocker(RuntimeError):
    """Raised when governed factor materialization cannot be trusted."""


@dataclass(frozen=True)
class MaterializationInputs:
    data_freeze_dir: str
    matrix_cache_dir: str
    feature_manifest_path: str
    feature_tensor_path: str
    promotion_policy_path: str | None = None
    target_return_mode: str = "adjusted_close"
    feature_cutoff_mode: str = "next_open"
    point_in_time: bool = True
    campaign_manifest_path: str | None = None


@dataclass(frozen=True)
class MaterializationResult:
    factor_id: str
    status: str
    cache_hit: bool
    values_path: str | None
    validity_path: str | None
    manifest_path: str
    input_fingerprint: str
    blocker: str | None = None
    metrics: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class FactorMaterializer:
    """The single formal path for materializing metadata-only factors."""

    def __init__(
        self,
        inputs: MaterializationInputs,
        output_dir: str | Path,
        *,
        device: str = "cpu",
        min_coverage: float = 0.01,
        max_coverage: float = 1.0,
    ):
        self.inputs = inputs
        self.output_dir = Path(output_dir)
        self.device = _resolve_device(device)
        self.min_coverage = float(min_coverage)
        self.max_coverage = float(max_coverage)

    def materialize(self, factor: FactorRecord) -> MaterializationResult:
        factor_dir = self.output_dir / factor.factor_id
        manifest_path = factor_dir / "materialization_manifest.json"
        try:
            context = self._load_context(factor)
            fingerprint = context["input_fingerprint"]
            cached = self._load_cached(factor_dir, fingerprint, context)
            if cached is not None:
                return cached
            return self._compute(factor, factor_dir, context)
        except Exception as exc:
            blocker = str(exc)
            factor_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "factor_id": factor.factor_id,
                "materialization_status": "blocked",
                "blocker": blocker,
                "formula_hash": factor.formula_hash,
                "formula_tokens": factor.formula_tokens,
                "operator_version": factor.operator_version,
                "transform_method": factor.transform_method,
            }
            _atomic_json(manifest_path, attach_artifact_metadata(payload, "factor_materialization_manifest", "validation_lab"))
            return MaterializationResult(
                factor_id=factor.factor_id,
                status="blocked",
                cache_hit=False,
                values_path=None,
                validity_path=None,
                manifest_path=str(manifest_path),
                input_fingerprint="",
                blocker=blocker,
            )

    def _load_context(self, factor: FactorRecord) -> dict[str, Any]:
        required = {
            "data_freeze_dir": Path(self.inputs.data_freeze_dir),
            "matrix_cache_dir": Path(self.inputs.matrix_cache_dir),
            "feature_manifest_path": Path(self.inputs.feature_manifest_path),
            "feature_tensor_path": Path(self.inputs.feature_tensor_path),
        }
        missing = [name for name, path in required.items() if not path.exists()]
        if missing:
            raise MaterializationBlocker(f"missing_required_artifact:{','.join(missing)}")
        matrix_dir = required["matrix_cache_dir"]
        axis_paths = {
            "trade_dates": matrix_dir / "trade_dates.json",
            "ts_codes": matrix_dir / "ts_codes.json",
            "active_mask": matrix_dir / "active_mask.npy",
            "pit_available_mask": matrix_dir / "pit_available_mask.npy",
            "index_member_matrix": matrix_dir / "index_member_matrix.npy",
            "target": matrix_dir / _target_filename(self.inputs.target_return_mode),
            "matrix_manifest": matrix_dir / "matrix_version_manifest.json",
        }
        missing_axes = [name for name, path in axis_paths.items() if not path.exists()]
        if missing_axes:
            raise MaterializationBlocker(f"missing_matrix_artifact:{','.join(missing_axes)}")
        feature_manifest = load_feature_manifest(required["feature_manifest_path"])
        if str(feature_manifest.operator_version) != str(factor.operator_version):
            raise MaterializationBlocker("operator_version_mismatch")
        vocab = make_formula_vocab_from_manifest(feature_manifest)
        vm = StackVM(vocab)
        valid, reason = vm.validate_with_reason(list(factor.formula_tokens or []))
        if not valid:
            raise MaterializationBlocker(f"invalid_formula:{reason}")
        tensor = np.load(required["feature_tensor_path"], mmap_mode="r")
        trade_dates = _read_list(axis_paths["trade_dates"])
        ts_codes = _read_list(axis_paths["ts_codes"])
        expected_shape = (len(ts_codes), int(feature_manifest.feature_count), len(trade_dates))
        if tuple(tensor.shape) != expected_shape:
            raise MaterializationBlocker(f"feature_tensor_shape_mismatch:{tensor.shape}!={expected_shape}")
        if str(tensor.dtype) != "float32":
            raise MaterializationBlocker(f"feature_tensor_dtype_mismatch:{tensor.dtype}")
        masks = {name: np.load(path, mmap_mode="r") for name, path in axis_paths.items() if name in {"active_mask", "pit_available_mask", "index_member_matrix", "target"}}
        for name, matrix in masks.items():
            if tuple(matrix.shape) != (len(ts_codes), len(trade_dates)):
                raise MaterializationBlocker(f"{name}_shape_mismatch:{matrix.shape}")
        freeze_manifest = _first_existing(required["data_freeze_dir"] / "freeze_manifest.json", required["data_freeze_dir"] / "dataset_version_manifest.json")
        freeze_payload = json.loads(freeze_manifest.read_text(encoding="utf-8"))
        matrix_payload = json.loads(axis_paths["matrix_manifest"].read_text(encoding="utf-8"))
        promotion_path = Path(self.inputs.promotion_policy_path) if self.inputs.promotion_policy_path else None
        promotion_payload = json.loads(promotion_path.read_text(encoding="utf-8")) if promotion_path and promotion_path.exists() else {}
        fingerprint_payload = {
            "factor_id": factor.factor_id,
            "formula_hash": factor.formula_hash,
            "formula_tokens": list(factor.formula_tokens or []),
            "operator_version": factor.operator_version,
            "transform_method": factor.transform_method,
            "feature_manifest_sha256": _sha256(required["feature_manifest_path"]),
            "feature_tensor_sha256": _sha256(required["feature_tensor_path"]),
            "matrix_manifest_sha256": _sha256(axis_paths["matrix_manifest"]),
            "freeze_manifest_sha256": _sha256(freeze_manifest),
            "promotion_policy_sha256": _sha256(promotion_path) if promotion_path and promotion_path.exists() else None,
            "stock_axis_hash": _hash_list(ts_codes),
            "date_axis_hash": _hash_list(trade_dates),
            "active_mask_sha256": _sha256(axis_paths["active_mask"]),
            "pit_available_mask_sha256": _sha256(axis_paths["pit_available_mask"]),
            "index_member_matrix_sha256": _sha256(axis_paths["index_member_matrix"]),
            "target_sha256": _sha256(axis_paths["target"]),
            "target_return_mode": self.inputs.target_return_mode,
            "feature_cutoff_mode": self.inputs.feature_cutoff_mode,
            "point_in_time": self.inputs.point_in_time,
        }
        return {
            "feature_manifest": feature_manifest,
            "vm": vm,
            "tensor": tensor,
            "trade_dates": trade_dates,
            "ts_codes": ts_codes,
            "masks": masks,
            "axis_paths": axis_paths,
            "fingerprint_payload": fingerprint_payload,
            "input_fingerprint": _hash_json(fingerprint_payload),
            "freeze_payload": freeze_payload,
            "matrix_payload": matrix_payload,
            "promotion_payload": promotion_payload,
        }

    def _load_cached(self, factor_dir: Path, fingerprint: str, context: dict[str, Any]) -> MaterializationResult | None:
        manifest_path = factor_dir / "materialization_manifest.json"
        values_path = factor_dir / "values.npy"
        validity_path = factor_dir / "validity.npy"
        if not (manifest_path.exists() and values_path.exists() and validity_path.exists()):
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("input_fingerprint") != fingerprint or payload.get("materialization_status") != "success":
            return None
        if payload.get("value_sha256") != _sha256(values_path) or payload.get("validity_sha256") != _sha256(validity_path):
            return None
        values = np.load(values_path, mmap_mode="r")
        validity = np.load(validity_path, mmap_mode="r")
        expected = (len(context["ts_codes"]), len(context["trade_dates"]))
        if tuple(values.shape) != expected or tuple(validity.shape) != expected:
            return None
        if not payload.get("artifact_metadata"):
            payload = attach_artifact_metadata(payload, "factor_materialization_manifest", "validation_lab")
            _atomic_json(manifest_path, payload)
        return MaterializationResult(
            factor_id=str(payload["factor_id"]), status="success", cache_hit=True,
            values_path=str(values_path), validity_path=str(validity_path), manifest_path=str(manifest_path),
            input_fingerprint=fingerprint, metrics=dict(payload.get("statistics") or {}),
        )

    def _compute(self, factor: FactorRecord, factor_dir: Path, context: dict[str, Any]) -> MaterializationResult:
        started = time.perf_counter()
        tensor_np = context["tensor"]
        tensor = torch.tensor(np.asarray(tensor_np), dtype=torch.float32, device=self.device)
        raw = context["vm"].execute(list(factor.formula_tokens or []), tensor)
        if raw is None:
            raise MaterializationBlocker("stack_vm_execution_failed")
        raw_data = self._transform_inputs(context)
        transformed = preprocess_factor(raw, raw_data, factor.transform_method or "raw")
        values = transformed.detach().to("cpu", dtype=torch.float32).numpy()
        masks = context["masks"]
        validity = (
            np.isfinite(values)
            & np.asarray(masks["active_mask"], dtype=bool)
            & np.asarray(masks["pit_available_mask"], dtype=bool)
            & np.asarray(masks["index_member_matrix"], dtype=bool)
            & np.isfinite(np.asarray(masks["target"]))
        )
        values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
        valid_values = values[validity]
        if valid_values.size == 0:
            raise MaterializationBlocker("no_valid_values")
        coverage = float(validity.mean())
        std = float(valid_values.std())
        nonzero_ratio = float(np.count_nonzero(valid_values) / valid_values.size)
        if coverage < self.min_coverage or coverage > self.max_coverage:
            raise MaterializationBlocker(f"abnormal_coverage:{coverage:.8f}")
        if std <= 1e-8:
            raise MaterializationBlocker("zero_variance_or_constant_factor")
        if nonzero_ratio <= 1e-8:
            raise MaterializationBlocker("all_zero_factor")
        tmp_dir = factor_dir.with_name(f".{factor_dir.name}.tmp-{uuid.uuid4().hex}")
        tmp_dir.mkdir(parents=True, exist_ok=False)
        values_path = tmp_dir / "values.npy"
        validity_path = tmp_dir / "validity.npy"
        np.save(values_path, values, allow_pickle=False)
        np.save(validity_path, validity.astype(np.bool_), allow_pickle=False)
        statistics = {
            "valid_count": int(validity.sum()),
            "coverage": coverage,
            "nonzero_ratio": nonzero_ratio,
            "standard_deviation": std,
        }
        payload = {
            "factor_id": factor.factor_id,
            "formula": list(factor.formula or []),
            "formula_tokens": list(factor.formula_tokens or []),
            "formula_hash": factor.formula_hash,
            "operator_version": factor.operator_version,
            "feature_version": factor.feature_version,
            "transform_method": factor.transform_method,
            "materialization_status": "success",
            "shape": list(values.shape),
            "dtype": str(values.dtype),
            "validity_dtype": "bool",
            "stock_axis_hash": context["fingerprint_payload"]["stock_axis_hash"],
            "date_axis_hash": context["fingerprint_payload"]["date_axis_hash"],
            "value_sha256": _sha256(values_path),
            "validity_sha256": _sha256(validity_path),
            "partition_sha256": {"values.npy": _sha256(values_path), "validity.npy": _sha256(validity_path)},
            "statistics": statistics,
            "lineage": {
                "data_freeze_dir": str(self.inputs.data_freeze_dir),
                "matrix_cache_dir": str(self.inputs.matrix_cache_dir),
                "feature_manifest_path": str(self.inputs.feature_manifest_path),
                "feature_tensor_path": str(self.inputs.feature_tensor_path),
                "promotion_policy_path": self.inputs.promotion_policy_path,
                "target_return_mode": self.inputs.target_return_mode,
                "feature_cutoff_mode": self.inputs.feature_cutoff_mode,
                "point_in_time": self.inputs.point_in_time,
                "campaign_manifest_path": self.inputs.campaign_manifest_path,
                "code_commit_hash": _git_commit(),
                "data_freeze_id": context["freeze_payload"].get("freeze_id") or context["freeze_payload"].get("data_freeze_id"),
                "data_freeze_hash": context["freeze_payload"].get("freeze_hash") or context["freeze_payload"].get("content_hash") or context["fingerprint_payload"]["freeze_manifest_sha256"],
                "matrix_cache_hash": context["matrix_payload"].get("cache_hash") or context["fingerprint_payload"]["matrix_manifest_sha256"],
                "feature_manifest_hash": getattr(context["feature_manifest"], "content_hash", None) or context["fingerprint_payload"]["feature_manifest_sha256"],
                "promotion_policy_hash": context["promotion_payload"].get("policy_hash") or context["fingerprint_payload"].get("promotion_policy_sha256"),
                "universe_name": context["matrix_payload"].get("effective_universe_name") or context["matrix_payload"].get("universe_name"),
            },
            "cache_input_fingerprint": context["fingerprint_payload"],
            "input_fingerprint": context["input_fingerprint"],
            "device": str(self.device),
            "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES", ""),
            "elapsed_seconds": float(time.perf_counter() - started),
        }
        _atomic_json(tmp_dir / "materialization_manifest.json", attach_artifact_metadata(payload, "factor_materialization_manifest", "validation_lab"))
        if factor_dir.exists():
            shutil.rmtree(factor_dir)
        os.replace(tmp_dir, factor_dir)
        return MaterializationResult(
            factor_id=factor.factor_id, status="success", cache_hit=False,
            values_path=str(factor_dir / "values.npy"), validity_path=str(factor_dir / "validity.npy"),
            manifest_path=str(factor_dir / "materialization_manifest.json"), input_fingerprint=context["input_fingerprint"], metrics=statistics,
        )

    def _transform_inputs(self, context: dict[str, Any]) -> dict[str, torch.Tensor]:
        matrix_dir = Path(self.inputs.matrix_cache_dir)
        raw: dict[str, torch.Tensor] = {}
        for key, filename in {"log_mkt_cap": "total_mv.npy", "industry_codes": "industry_codes.npy"}.items():
            path = matrix_dir / filename
            if path.exists():
                value = torch.from_numpy(np.array(np.load(path, mmap_mode="r"), copy=True)).to(self.device)
                raw[key] = torch.log1p(torch.clamp(value, min=0.0)) if key == "log_mkt_cap" else value
        return raw


def load_materialized_factor(manifest_path: str | Path) -> tuple[torch.Tensor, torch.Tensor, dict[str, Any]]:
    path = Path(manifest_path)
    if not path.exists():
        raise MaterializationBlocker("materialization_manifest_missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("materialization_status") != "success":
        raise MaterializationBlocker(str(payload.get("blocker") or "materialization_not_successful"))
    values_path = path.parent / "values.npy"
    validity_path = path.parent / "validity.npy"
    if not values_path.exists() or not validity_path.exists():
        raise MaterializationBlocker("compact_factor_files_missing")
    if payload.get("value_sha256") != _sha256(values_path) or payload.get("validity_sha256") != _sha256(validity_path):
        raise MaterializationBlocker("compact_factor_hash_mismatch")
    values = np.load(values_path, mmap_mode="r")
    validity = np.load(validity_path, mmap_mode="r")
    if list(values.shape) != payload.get("shape") or validity.shape != values.shape:
        raise MaterializationBlocker("compact_factor_shape_mismatch")
    return torch.tensor(np.asarray(values), dtype=torch.float32), torch.tensor(np.asarray(validity, dtype=np.bool_), dtype=torch.bool), payload


def _resolve_device(device: str) -> torch.device:
    value = str(device or "cpu").lower()
    if value.startswith("cuda"):
        if not torch.cuda.is_available():
            raise MaterializationBlocker("cuda_required_but_unavailable")
        return torch.device(value)
    return torch.device("cpu")


def _target_filename(mode: str) -> str:
    return "total_return.npy" if mode == "corporate_action_total_return" else "adjusted_close.npy"


def _read_list(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise MaterializationBlocker(f"invalid_axis_file:{path.name}")
    return [str(item) for item in payload]


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise MaterializationBlocker("freeze_manifest_missing")


def _hash_list(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _hash_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256(path: Path | None) -> str:
    if path is None or not path.exists():
        raise MaterializationBlocker(f"hash_input_missing:{path}")
    stat = path.stat()
    key = (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))
    if key in _FILE_HASH_CACHE:
        return _FILE_HASH_CACHE[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    _FILE_HASH_CACHE[key] = value
    return value


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"
