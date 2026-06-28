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
) -> MatrixCacheBuildResult:
    """Build a deterministic local matrix cache from JSONL datasets."""

    data_path = Path(data_dir)
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
    cache_hash = _stable_cache_hash(
        loader.ts_codes,
        loader.trade_dates,
        requested_fields,
        source_manifest_hash,
        quality_report_hash,
    )
    metadata = {
        "data_dir": str(data_path),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "n_stocks": len(loader.ts_codes),
        "n_dates": len(loader.trade_dates),
        "fields": requested_fields,
        "source_manifest_hash": source_manifest_hash,
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
