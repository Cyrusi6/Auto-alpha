"""Fail-closed compact factor materialization from governed tensor artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

from artifact_schema.writer import attach_artifact_metadata
from factor_engine.transforms import preprocess_factor_with_validity
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
    feature_validity_tensor_path: str | None = None
    snapshot_proof_manifest_path: str | None = None
    promotion_allowlist_path: str | None = None
    promotion_denylist_path: str | None = None


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
            "active_mask": _first_existing(matrix_dir / "active.npy", matrix_dir / "active_mask.npy"),
            "pit_available_mask": _first_existing(
                matrix_dir / "signal_eligible_at_close.npy",
                matrix_dir / "pit_available_mask.npy",
            ),
            "index_member_matrix": _first_existing(
                matrix_dir / "membership.npy",
                matrix_dir / "index_membership.npy",
                matrix_dir / "index_member_matrix.npy",
            ),
            "matrix_manifest": _first_existing(matrix_dir / "task_052a_strict_matrix_manifest.json", matrix_dir / "matrix_version_manifest.json"),
            "membership_known": _first_existing(matrix_dir / "membership_known_mask.npy", matrix_dir / "membership_known.npy"),
        }
        validity_path = Path(self.inputs.feature_validity_tensor_path) if self.inputs.feature_validity_tensor_path else required["feature_tensor_path"].with_name("feature_validity_tensor.npy")
        axis_paths["feature_validity"] = validity_path
        missing_axes = [name for name, path in axis_paths.items() if not path.exists()]
        if missing_axes:
            raise MaterializationBlocker(f"missing_matrix_artifact:{','.join(missing_axes)}")
        feature_manifest = load_feature_manifest(required["feature_manifest_path"])
        if str(factor.feature_version) not in {str(feature_manifest.feature_version), str(feature_manifest.feature_set_name)}:
            raise MaterializationBlocker("feature_version_mismatch")
        if str(feature_manifest.operator_version) != str(factor.operator_version):
            raise MaterializationBlocker("operator_version_mismatch")
        vocab = make_formula_vocab_from_manifest(feature_manifest)
        vm = StackVM(vocab)
        valid, reason = vm.validate_with_reason(list(factor.formula_tokens or []))
        if not valid:
            raise MaterializationBlocker(f"invalid_formula:{reason}")
        if list(factor.formula or []) != vm.describe(list(factor.formula_tokens or [])):
            raise MaterializationBlocker("formula_names_tokens_mismatch")
        if int(factor.lookback_days) != int(vm.formula_lookback(list(factor.formula_tokens or []))):
            raise MaterializationBlocker("formula_lookback_mismatch")
        tensor = np.load(required["feature_tensor_path"], mmap_mode="r")
        trade_dates = _read_list(axis_paths["trade_dates"])
        ts_codes = _read_list(axis_paths["ts_codes"])
        expected_shape = (len(ts_codes), int(feature_manifest.feature_count), len(trade_dates))
        if tuple(tensor.shape) != expected_shape:
            raise MaterializationBlocker(f"feature_tensor_shape_mismatch:{tensor.shape}!={expected_shape}")
        if str(tensor.dtype) != "float32":
            raise MaterializationBlocker(f"feature_tensor_dtype_mismatch:{tensor.dtype}")
        feature_validity = np.load(validity_path, mmap_mode="r")
        if tuple(feature_validity.shape) != expected_shape or str(feature_validity.dtype) != "bool":
            raise MaterializationBlocker("feature_validity_tensor_mismatch")
        masks = {name: np.load(path, mmap_mode="r") for name, path in axis_paths.items() if name in {"active_mask", "pit_available_mask", "index_member_matrix"}}
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
            "formula_names": list(factor.formula or []),
            "feature_version": factor.feature_version,
            "lookback_days": factor.lookback_days,
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
            "feature_validity_sha256": _sha256(validity_path),
            "membership_known_sha256": _sha256(axis_paths["membership_known"]),
            "campaign_manifest_sha256": _optional_sha(self.inputs.campaign_manifest_path),
            "snapshot_proof_sha256": _optional_sha(self.inputs.snapshot_proof_manifest_path),
            "promotion_allowlist_sha256": _optional_sha(self.inputs.promotion_allowlist_path),
            "promotion_denylist_sha256": _optional_sha(self.inputs.promotion_denylist_path),
            "transform_inputs": {name: _optional_sha(matrix_dir / filename) for name, filename in {"total_mv": "total_mv.npy", "industry_codes": "industry_codes.npy"}.items()},
            "coverage_policy": {"min": self.min_coverage, "max": self.max_coverage},
            "code_semantic_hash": _code_semantic_hash(),
            "feature_cutoff_mode": self.inputs.feature_cutoff_mode,
            "point_in_time": self.inputs.point_in_time,
        }
        return {
            "feature_manifest": feature_manifest,
            "vm": vm,
            "tensor": tensor,
            "feature_validity": feature_validity,
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
        generation_dir = factor_dir / "generations" / fingerprint
        if generation_dir.is_dir():
            manifest_path = generation_dir / "materialization_manifest.json"
            values_path = generation_dir / "values.npy"
            validity_path = generation_dir / "validity.npy"
        else:
            manifest_path, values_path, validity_path = _current_materialization_paths(factor_dir)
        if not (manifest_path.exists() and values_path.exists() and validity_path.exists()):
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("input_fingerprint") != fingerprint or payload.get("materialization_status") != "success":
            return None
        if self.device.type == "cuda" and not _valid_cuda_formula_evidence(payload.get("cuda_formula_execution")):
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
        if manifest_path.parent == generation_dir:
            _atomic_json(
                factor_dir / "current_materialization.json",
                {
                    "generation_id": fingerprint,
                    "generation_path": str(Path("generations") / fingerprint),
                    "input_fingerprint": fingerprint,
                    "manifest_sha256": _sha256(manifest_path),
                },
            )
        return MaterializationResult(
            factor_id=str(payload["factor_id"]), status="success", cache_hit=True,
            values_path=str(values_path), validity_path=str(validity_path), manifest_path=str(manifest_path),
            input_fingerprint=fingerprint, metrics=dict(payload.get("statistics") or {}),
        )

    def _compute(self, factor: FactorRecord, factor_dir: Path, context: dict[str, Any]) -> MaterializationResult:
        started = time.perf_counter()
        cuda_formula_execution: dict[str, Any] | None = None
        cuda_start = None
        cuda_end = None
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
            torch.cuda.reset_peak_memory_stats(self.device)
            cuda_start = torch.cuda.Event(enable_timing=True)
            cuda_end = torch.cuda.Event(enable_timing=True)
        tensor_np = context["tensor"]
        tensor = torch.tensor(np.asarray(tensor_np), dtype=torch.float32, device=self.device)
        feature_validity = torch.tensor(np.asarray(context["feature_validity"]), dtype=torch.bool, device=self.device)
        if cuda_start is not None:
            cuda_start.record()
        executed = context["vm"].execute_with_validity(list(factor.formula_tokens or []), tensor, feature_validity)
        if executed is None:
            raise MaterializationBlocker("stack_vm_execution_failed")
        raw, formula_validity = executed
        raw_data = self._transform_inputs(context)
        eligible = torch.tensor(
            np.asarray(context["masks"]["active_mask"], dtype=bool)
            & np.asarray(context["masks"]["index_member_matrix"], dtype=bool),
            dtype=torch.bool,
            device=self.device,
        )
        transformed, propagated_validity = preprocess_factor_with_validity(raw, formula_validity, raw_data, factor.transform_method or "raw", eligible)
        if cuda_end is not None and cuda_start is not None:
            cuda_end.record()
            torch.cuda.synchronize(self.device)
            cuda_formula_execution = {
                "evidence_version": "stackvm_cuda_formula_execution_v1",
                "factor_id": factor.factor_id,
                "formula_hash": factor.formula_hash,
                "physical_gpu": _cuda_physical_device(),
                "torch_device": str(self.device),
                "input_tensor_device": str(tensor.device),
                "input_validity_device": str(feature_validity.device),
                "output_tensor_device": str(transformed.device),
                "output_validity_device": str(propagated_validity.device),
                "cuda_event_elapsed_ms": float(cuda_start.elapsed_time(cuda_end)),
                "peak_allocated_bytes": int(torch.cuda.max_memory_allocated(self.device)),
                "input_bytes": int(tensor.numel() * tensor.element_size() + feature_validity.numel() * feature_validity.element_size()),
                "output_bytes": int(transformed.numel() * transformed.element_size() + propagated_validity.numel() * propagated_validity.element_size()),
            }
            if not _valid_cuda_formula_evidence(cuda_formula_execution):
                raise MaterializationBlocker("invalid_cuda_formula_execution_evidence")
        values = transformed.detach().to("cpu", dtype=torch.float32).numpy()
        masks = context["masks"]
        validity = (
            propagated_validity.detach().cpu().numpy()
            & np.isfinite(values)
            & np.asarray(masks["active_mask"], dtype=bool)
            & np.asarray(masks["pit_available_mask"], dtype=bool)
            & np.asarray(masks["index_member_matrix"], dtype=bool)
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
        generations_dir = factor_dir / "generations"
        generation_dir = generations_dir / context["input_fingerprint"]
        generations_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = generations_dir / f".{context['input_fingerprint']}.tmp-{uuid.uuid4().hex}"
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
            "cache_hit": False,
            "shape": list(values.shape),
            "dtype": str(values.dtype),
            "validity_dtype": "bool",
            "stock_axis_hash": context["fingerprint_payload"]["stock_axis_hash"],
            "date_axis_hash": context["fingerprint_payload"]["date_axis_hash"],
            "value_sha256": _sha256(values_path),
            "validity_sha256": _sha256(validity_path),
            "partition_sha256": {"values.npy": _sha256(values_path), "validity.npy": _sha256(validity_path)},
            "statistics": statistics,
            "cuda_formula_execution": cuda_formula_execution,
            "validity_contract": "factor_observation_only_v2",
            "target_excluded_from_factor_validity": True,
            "lineage": {
                "data_freeze_dir": str(self.inputs.data_freeze_dir),
                "matrix_cache_dir": str(self.inputs.matrix_cache_dir),
                "feature_manifest_path": str(self.inputs.feature_manifest_path),
                "feature_tensor_path": str(self.inputs.feature_tensor_path),
                "promotion_policy_path": self.inputs.promotion_policy_path,
                "feature_cutoff_mode": self.inputs.feature_cutoff_mode,
                "point_in_time": self.inputs.point_in_time,
                "campaign_manifest_path": self.inputs.campaign_manifest_path,
                "code_commit_hash": _git_commit(),
                "data_freeze_id": context["freeze_payload"].get("freeze_id") or context["freeze_payload"].get("data_freeze_id"),
                "data_freeze_hash": context["freeze_payload"].get("freeze_hash") or context["freeze_payload"].get("content_hash") or context["fingerprint_payload"]["freeze_manifest_sha256"],
                "matrix_cache_hash": context["matrix_payload"].get("cache_hash") or context["fingerprint_payload"]["matrix_manifest_sha256"],
                "matrix_semantic_hash": context["matrix_payload"].get("semantic_hash"),
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
        if generation_dir.exists():
            existing_manifest = generation_dir / "materialization_manifest.json"
            if not existing_manifest.is_file() or _sha256(existing_manifest) != _sha256(tmp_dir / "materialization_manifest.json"):
                raise MaterializationBlocker("immutable_materialization_generation_conflict")
            for path in tmp_dir.iterdir():
                path.unlink()
            tmp_dir.rmdir()
        else:
            os.replace(tmp_dir, generation_dir)
        _atomic_json(
            factor_dir / "current_materialization.json",
            {
                "generation_id": context["input_fingerprint"],
                "generation_path": str(Path("generations") / context["input_fingerprint"]),
                "input_fingerprint": context["input_fingerprint"],
                "manifest_sha256": _sha256(generation_dir / "materialization_manifest.json"),
            },
        )
        return MaterializationResult(
            factor_id=factor.factor_id, status="success", cache_hit=False,
            values_path=str(generation_dir / "values.npy"), validity_path=str(generation_dir / "validity.npy"),
            manifest_path=str(generation_dir / "materialization_manifest.json"), input_fingerprint=context["input_fingerprint"], metrics=statistics,
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


def _current_materialization_paths(factor_dir: Path) -> tuple[Path, Path, Path]:
    pointer_path = factor_dir / "current_materialization.json"
    if pointer_path.is_file():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        generation_path = Path(str(pointer.get("generation_path") or ""))
        if not generation_path.parts or generation_path.is_absolute() or ".." in generation_path.parts:
            raise MaterializationBlocker("invalid_materialization_generation_pointer")
        generation_dir = factor_dir / generation_path
        manifest_path = generation_dir / "materialization_manifest.json"
        if pointer.get("manifest_sha256") != _sha256(manifest_path):
            raise MaterializationBlocker("materialization_generation_pointer_hash_mismatch")
        return manifest_path, generation_dir / "values.npy", generation_dir / "validity.npy"
    return factor_dir / "materialization_manifest.json", factor_dir / "values.npy", factor_dir / "validity.npy"


def _resolve_device(device: str) -> torch.device:
    value = str(device or "cpu").lower()
    if value.startswith("cuda"):
        if not torch.cuda.is_available():
            raise MaterializationBlocker("cuda_required_but_unavailable")
        return torch.device(value)
    return torch.device("cpu")


def _valid_cuda_formula_evidence(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    physical = payload.get("physical_gpu") or {}
    device_fields = (
        payload.get("torch_device"),
        payload.get("input_tensor_device"),
        payload.get("input_validity_device"),
        payload.get("output_tensor_device"),
        payload.get("output_validity_device"),
    )
    return (
        payload.get("evidence_version") == "stackvm_cuda_formula_execution_v1"
        and bool(payload.get("factor_id"))
        and bool(payload.get("formula_hash"))
        and bool(physical.get("uuid"))
        and bool(physical.get("model") or physical.get("name"))
        and all(str(value).startswith("cuda") for value in device_fields)
        and float(payload.get("cuda_event_elapsed_ms") or 0.0) > 0.0
        and int(payload.get("peak_allocated_bytes") or 0) > 0
        and int(payload.get("input_bytes") or 0) > 0
        and int(payload.get("output_bytes") or 0) > 0
    )


def _cuda_physical_device() -> dict[str, Any]:
    visible = [item.strip() for item in os.getenv("CUDA_VISIBLE_DEVICES", "").split(",") if item.strip()]
    selected = visible[0] if len(visible) == 1 else None
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid,name", "--format=csv,noheader"],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if completed.returncode != 0:
        return {}
    records = []
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", 2)]
        if len(fields) == 3:
            records.append({"physical_index": int(fields[0]), "uuid": fields[1], "model": fields[2]})
    if selected is not None and selected.isdigit():
        return next((row for row in records if row["physical_index"] == int(selected)), {})
    return records[0] if len(records) == 1 else {}


def _optional_sha(value: str | Path | None) -> str | None:
    if not value:
        return None
    path = Path(value)
    return _sha256(path) if path.exists() else None


def _code_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [
        Path(__file__), root / "model_core" / "vm.py", root / "model_core" / "ops.py",
        root / "model_core" / "validity.py", root / "factor_engine" / "transforms.py",
        root / "validation_lab" / "policy.py", root / "validation_lab" / "splits.py",
    ]
    return _hash_json({str(path.relative_to(root)): _sha256(path) for path in paths})


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
