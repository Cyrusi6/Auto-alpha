"""Build numpy matrix caches from governed JSONL A-share data."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import torch

from model_core.data_loader import AShareDataLoader
from data_lake import validate_research_input
from feature_factory import build_feature_set_manifest, load_feature_manifest

from .models import MatrixCacheBuildResult, MatrixFieldInfo


DEFAULT_MATRIX_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "total_mv",
    "roe",
    "revenue_yoy",
    "adj_factor",
    "up_limit",
    "down_limit",
    "limit_up_flag",
    "limit_down_flag",
    "is_suspended",
    "industry_codes",
    "index_member_matrix",
    "active_mask",
    "listing_age_days",
    "pit_available_mask",
    "cash_dividend",
    "cash_dividend_tax",
    "stock_distribution_ratio",
    "corporate_action_flag",
    "total_return_close",
    "total_return",
)


def build_matrix_cache(
    data_dir: str | Path,
    output_dir: str | Path | None = None,
    universe_file: str | Path | None = None,
    universe_name: str | None = None,
    fields: Iterable[str] | None = None,
    point_in_time: bool = False,
    feature_cutoff_mode: str = "same_day_after_close",
    min_listing_days: int = 0,
    exclude_st: bool = False,
    active_mask_path: str | Path | None = None,
    corporate_action_aware: bool = False,
    target_return_mode: str = "adjusted_close",
    corporate_action_dir: str | Path | None = None,
    data_freeze_dir: str | Path | None = None,
    data_freeze_id: str | None = None,
    data_version_manifest_path: str | Path | None = None,
    require_data_freeze: bool = False,
    feature_set_name: str = "ashare_features_v1",
    feature_set_manifest_path: str | Path | None = None,
    raw_data_index_manifest_path: str | Path | None = None,
) -> MatrixCacheBuildResult:
    """Build a deterministic local matrix cache from JSONL datasets."""

    freeze_report = validate_research_input(data_dir=data_dir, data_freeze_dir=data_freeze_dir, require_freeze=require_data_freeze)
    if freeze_report.error_count > 0:
        raise RuntimeError(f"data freeze validation failed: {freeze_report.status}")
    data_path = Path(data_freeze_dir) / "data" if data_freeze_dir is not None else Path(data_dir)
    cache_dir = Path(output_dir) if output_dir is not None else data_path / "matrix_cache"
    requested_fields = list(fields or DEFAULT_MATRIX_FIELDS)
    cache_dir.mkdir(parents=True, exist_ok=True)
    effective_universe_name = universe_name
    universe_missing_fallback = False
    if universe_file is None and universe_name is not None:
        expected_universe_path = data_path / "universe" / f"{universe_name}.jsonl"
        if not expected_universe_path.exists():
            effective_universe_name = None
            universe_missing_fallback = True

    loader = AShareDataLoader(
        data_dir=data_path,
        device="cpu",
        universe_file=universe_file,
        universe_name=effective_universe_name,
        point_in_time=point_in_time,
        feature_cutoff_mode=feature_cutoff_mode,
        min_listing_days=min_listing_days,
        exclude_st=exclude_st,
        active_security_mask_path=active_mask_path,
        corporate_action_aware=corporate_action_aware,
        target_return_mode=target_return_mode,
        corporate_action_dir=corporate_action_dir,
        feature_set_name=feature_set_name,
        feature_set_manifest_path=feature_set_manifest_path,
    ).load_data()

    raw = dict(loader.raw_data_cache)
    if "ps_ttm" not in raw:
        raw["ps_ttm"] = torch.zeros_like(raw["close"])

    field_infos: list[MatrixFieldInfo] = []
    for field in requested_fields:
        if field == "industry_codes":
            values = loader.industry_codes if loader.industry_codes is not None else raw.get("industry_codes")
        else:
            values = raw.get(field)
        if values is None:
            values = torch.zeros_like(raw["close"])
        array = _tensor_to_array(values, field)
        path = cache_dir / f"{field}.npy"
        np.save(path, array)
        field_infos.append(
            MatrixFieldInfo(
                name=field,
                path=str(path),
                shape=list(array.shape),
                dtype=str(array.dtype),
            )
        )

    ts_codes_path = cache_dir / "ts_codes.json"
    trade_dates_path = cache_dir / "trade_dates.json"
    fields_path = cache_dir / "fields.json"
    ts_codes_path.write_text(json.dumps(loader.ts_codes, ensure_ascii=False, indent=2), encoding="utf-8")
    trade_dates_path.write_text(json.dumps(loader.trade_dates, ensure_ascii=False, indent=2), encoding="utf-8")
    fields_path.write_text(
        json.dumps([info.__dict__ for info in field_infos], ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    source_manifest_hash = _file_hash(data_path / "manifest.json")
    quality_report_hash = _file_hash(data_path / "quality_report.json")
    feature_manifest = (
        load_feature_manifest(feature_set_manifest_path)
        if feature_set_manifest_path is not None and Path(feature_set_manifest_path).exists()
        else build_feature_set_manifest(feature_set_name, point_in_time=point_in_time, corporate_action_aware=corporate_action_aware, target_return_mode=target_return_mode)
    )
    feature_defs = list(feature_manifest.feature_definitions)
    family_names = {str(item.get("family")) for item in feature_defs if isinstance(item, dict)}
    weak_pit_count = sum(1 for item in feature_defs if isinstance(item, dict) and item.get("pit_safety") != "pit_safe")
    disabled_count = sum(1 for item in feature_defs if isinstance(item, dict) and not item.get("default_enabled", True))
    manifest_payload = _feature_manifest_payload(feature_set_manifest_path)
    promotion_summary = dict(manifest_payload.get("feature_promotion_summary", {}))
    promotion_hash = manifest_payload.get("feature_promotion_policy_hash")
    raw_index_payload = _raw_data_index_payload(raw_data_index_manifest_path)
    raw_index_hash = _fresh_raw_index_hash(raw_index_payload)
    cache_hash = _stable_cache_hash(
        loader.ts_codes,
        loader.trade_dates,
        requested_fields,
        raw_index_hash or source_manifest_hash,
        quality_report_hash,
    )
    metadata = {
        "data_dir": str(data_path),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "n_stocks": len(loader.ts_codes),
        "n_dates": len(loader.trade_dates),
        "fields": requested_fields,
        "source_manifest_hash": source_manifest_hash,
        "raw_data_index_manifest_path": str(raw_data_index_manifest_path) if raw_data_index_manifest_path else None,
        "raw_data_index_hash": raw_index_hash,
        "source_dataset_indexes": _source_dataset_indexes(raw_index_payload),
        "quality_report_hash": quality_report_hash,
        "cache_hash": cache_hash,
        "universe_file": str(universe_file) if universe_file is not None else None,
        "universe_name": universe_name,
        "effective_universe_name": effective_universe_name,
        "universe_missing_fallback": universe_missing_fallback,
        "security_metadata": loader.security_metadata,
        "point_in_time": bool(point_in_time),
        "feature_cutoff_mode": feature_cutoff_mode,
        "active_mask_included": all(field in requested_fields for field in ("active_mask", "pit_available_mask")),
        "min_listing_days": int(min_listing_days),
        "exclude_st": bool(exclude_st),
        "pit_contract_version": "1.0",
        "corporate_action_aware": bool(corporate_action_aware),
        "target_return_mode": target_return_mode,
        "corporate_action_dir": str(corporate_action_dir) if corporate_action_dir is not None else None,
        "corporate_action_contract_version": "1.0",
        "corporate_action_event_count": int(raw.get("corporate_action_flag", torch.zeros_like(raw["close"])).sum().item())
        if corporate_action_aware
        else 0,
        "feature_set_name": feature_manifest.feature_set_name,
        "feature_set_version": feature_manifest.feature_set_version,
        "feature_set_hash": feature_manifest.content_hash,
        "feature_family_count": len(family_names),
        "feature_count_enabled": sum(1 for item in feature_defs if isinstance(item, dict) and item.get("default_enabled", True)),
        "weak_pit_feature_count": weak_pit_count,
        "disabled_feature_count": disabled_count,
        "feature_pit_alignment_status": "warning" if weak_pit_count else "ok",
        "feature_promotion_policy_hash": promotion_hash,
        "alpha_eligible_feature_count": int(promotion_summary.get("alpha_eligible_feature_count", 0) or 0),
        "blocked_feature_count": int(promotion_summary.get("blocked_feature_count", 0) or 0),
        "weak_pit_promoted_count": int(promotion_summary.get("weak_pit_promoted_count", 0) or 0),
        "dataset_version_id": _dataset_version_id(data_version_manifest_path),
        "data_freeze_id": data_freeze_id or freeze_report.freeze_id,
        "data_freeze_hash": freeze_report.content_hash,
        "source_content_hash": raw_index_hash or _source_content_hash(data_version_manifest_path) or freeze_report.content_hash,
        "freeze_validation_status": freeze_report.status,
        "source_data_hashes": _source_hashes(data_version_manifest_path),
    }
    metadata_path = cache_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    return MatrixCacheBuildResult(
        cache_dir=str(cache_dir),
        metadata_path=str(metadata_path),
        fields_path=str(fields_path),
        ts_codes_path=str(ts_codes_path),
        trade_dates_path=str(trade_dates_path),
        fields=requested_fields,
        n_stocks=len(loader.ts_codes),
        n_dates=len(loader.trade_dates),
        cache_hash=cache_hash,
    )


def _tensor_to_array(values: torch.Tensor, field: str) -> np.ndarray:
    if hasattr(values, "detach"):
        tensor = values.detach().cpu()
    else:
        tensor = torch.as_tensor(values)
    if field == "industry_codes":
        return tensor.to(dtype=torch.int64).numpy()
    return tensor.to(dtype=torch.float32).numpy()


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _dataset_version_id(path: str | Path | None) -> str | None:
    if path is None:
        return None
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload.get("dataset_version_id")


def _source_hashes(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        str(item.get("dataset")): str(item.get("sha256") or "")
        for item in payload.get("dataset_fingerprints", [])
        if isinstance(item, dict)
    }


def _feature_manifest_payload(path: str | Path | None) -> dict:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _source_content_hash(path: str | Path | None) -> str | None:
    if path is None:
        return None
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    content_hash = payload.get("content_hash")
    return str(content_hash) if content_hash else None


def _raw_data_index_payload(path: str | Path | None) -> dict:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _fresh_raw_index_hash(payload: dict) -> str | None:
    if payload.get("status") != "fresh":
        return None
    value = payload.get("index_hash")
    return str(value) if value else None


def _source_dataset_indexes(payload: dict) -> list[dict[str, object]]:
    rows = payload.get("datasets", []) if isinstance(payload, dict) else []
    out: list[dict[str, object]] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "dataset": item.get("dataset"),
                "records_sha256": item.get("records_sha256"),
                "record_count": item.get("record_count"),
                "file_size_bytes": item.get("file_size_bytes"),
                "status": item.get("status"),
            }
        )
    return out


def _stable_cache_hash(
    ts_codes: list[str],
    trade_dates: list[str],
    fields: list[str],
    source_manifest_hash: str | None,
    quality_report_hash: str | None,
) -> str:
    payload = {
        "ts_codes": ts_codes,
        "trade_dates": trade_dates,
        "fields": fields,
        "source_manifest_hash": source_manifest_hash,
        "quality_report_hash": quality_report_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
