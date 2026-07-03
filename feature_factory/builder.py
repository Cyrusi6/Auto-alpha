"""Build versioned feature tensors from an AShareDataLoader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch

from artifact_schema.writer import write_json_artifact
from model_core.factors import robust_cross_section_zscore

from .catalog import FEATURE_SET_V3, build_feature_set_manifest, manifest_from_payload
from .coverage import build_feature_coverage_report
from .extended_builder import attach_extended_feature_matrices, write_extended_feature_reports
from .models import FeatureDefinition, FeatureSetManifest, FeatureTensorBuildResult


def build_feature_tensor(loader, feature_set_manifest: FeatureSetManifest | dict[str, Any]) -> tuple[torch.Tensor, list[str]]:
    manifest = _coerce_manifest(feature_set_manifest)
    extended_summary = (
        attach_extended_feature_matrices(loader, manifest)
        if manifest.feature_set_name == FEATURE_SET_V3 and _needs_extended_attach(loader.raw_data_cache, manifest)
        else None
    )
    definitions = [_definition_from_payload(item) for item in manifest.feature_definitions]
    matrices: list[torch.Tensor] = []
    warnings: list[str] = []
    if extended_summary:
        warnings.extend(str(item.get("message", item)) for item in extended_summary.get("warnings", []))
    for definition in definitions:
        matrix, feature_warnings = build_feature_matrix(loader.raw_data_cache, definition)
        matrices.append(matrix)
        warnings.extend(feature_warnings)
    if not matrices:
        raise ValueError("feature set manifest contains no feature definitions")
    tensor = torch.stack(matrices, dim=1).to(dtype=torch.float32)
    if manifest.point_in_time and "pit_available_mask" in loader.raw_data_cache:
        tensor = tensor * loader.raw_data_cache["pit_available_mask"].unsqueeze(1)
    return tensor, warnings


def build_feature_matrix(raw: dict[str, torch.Tensor], definition: FeatureDefinition) -> tuple[torch.Tensor, list[str]]:
    warnings: list[str] = []
    base = _compute_raw_feature(raw, definition.feature_name)
    if base is None:
        shape = _infer_shape(raw)
        base = torch.zeros(shape, dtype=torch.float32, device=_infer_device(raw))
        warnings.append(f"missing source for feature {definition.feature_name}")
    base = torch.nan_to_num(base.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if definition.transform == "identity":
        return base, warnings
    return robust_cross_section_zscore(base), warnings


def build_feature_tensor_artifacts(
    loader,
    output_dir: str | Path,
    *,
    feature_set_name: str = "ashare_features_v1",
    feature_set_version: str = "1.0",
    data_freeze_id: str | None = None,
    data_freeze_hash: str | None = None,
    point_in_time: bool = False,
    corporate_action_aware: bool = False,
    target_return_mode: str = "adjusted_close",
) -> FeatureTensorBuildResult:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    manifest = build_feature_set_manifest(
        feature_set_name,
        feature_set_version,
        data_freeze_id=data_freeze_id,
        data_freeze_hash=data_freeze_hash,
        point_in_time=point_in_time,
        corporate_action_aware=corporate_action_aware,
        target_return_mode=target_return_mode,
    )
    extended_summary = attach_extended_feature_matrices(loader, manifest) if manifest.feature_set_name == FEATURE_SET_V3 else None
    tensor, warnings = build_feature_tensor(loader, manifest)
    if extended_summary:
        warnings = [*warnings, *[str(item.get("message", item)) for item in extended_summary.get("warnings", [])]]
    tensor_path = target / "feature_tensor.npy"
    np.save(tensor_path, tensor.detach().cpu().numpy())
    manifest_path = write_json_artifact(
        target / "feature_set_manifest.json",
        manifest.to_dict(),
        "feature_set_manifest",
        "feature_factory",
    )
    coverage = build_feature_coverage_report(manifest, tensor, warnings)
    coverage_path = write_json_artifact(
        target / "feature_coverage_report.json",
        coverage.to_dict(),
        "feature_coverage_report",
        "feature_factory",
    )
    values_summary = _values_summary(manifest, tensor)
    values_summary_path = write_json_artifact(
        target / "feature_values_summary.json",
        values_summary,
        "feature_values_summary",
        "feature_factory",
    )
    (target / "feature_coverage_report.md").write_text(_coverage_markdown(coverage.to_dict()), encoding="utf-8")
    result = FeatureTensorBuildResult(
        feature_set_name=manifest.feature_set_name,
        feature_set_version=manifest.feature_set_version,
        feature_count=manifest.feature_count,
        n_stocks=int(tensor.shape[0]),
        n_dates=int(tensor.shape[2]),
        tensor_path=str(tensor_path),
        manifest_path=str(manifest_path),
        coverage_report_path=str(coverage_path),
        values_summary_path=str(values_summary_path),
        warnings=warnings,
    )
    result_payload = result.to_dict()
    if extended_summary:
        result_payload.update(
            {
                "feature_family_count": len(extended_summary.get("feature_family_readiness", [])),
                "enabled_feature_count": extended_summary.get("enabled_feature_count", 0),
                "weak_pit_feature_count": extended_summary.get("weak_pit_feature_count", 0),
                "disabled_feature_count": extended_summary.get("disabled_feature_count", 0),
                "feature_pit_alignment_status": extended_summary.get("feature_pit_alignment_status", "unknown"),
            }
        )
        result_payload["extended_feature_paths"] = write_extended_feature_reports(target, extended_summary)
    write_json_artifact(target / "feature_tensor_build_result.json", result_payload, "feature_tensor_build_result", "feature_factory")
    return result


def load_feature_manifest(path: str | Path) -> FeatureSetManifest:
    return manifest_from_payload(json.loads(Path(path).read_text(encoding="utf-8")))


def _compute_raw_feature(raw: dict[str, torch.Tensor], name: str) -> torch.Tensor | None:
    close = raw.get("close")
    if close is None:
        return None
    direct = raw.get(name.lower())
    if direct is not None:
        if direct.ndim == 1:
            direct = direct.unsqueeze(1).expand(-1, close.shape[1])
        return direct
    if name.startswith("RET_") and name.endswith("D"):
        days = int(name.removeprefix("RET_").removesuffix("D"))
        return _log_return(close, days)
    if name == "AMPLITUDE":
        return (raw.get("high", close) - raw.get("low", close)) / torch.clamp(raw.get("pre_close", close), min=1e-6)
    if name == "INTRADAY_RETURN":
        return torch.log(torch.clamp(close, min=1e-6) / torch.clamp(raw.get("open", close), min=1e-6))
    if name == "GAP_RETURN":
        return torch.log(torch.clamp(raw.get("open", close), min=1e-6) / torch.clamp(raw.get("pre_close", close), min=1e-6))
    if name == "LOG_AMOUNT":
        return torch.log1p(torch.clamp(raw.get("amount", torch.zeros_like(close)), min=0.0))
    if name == "AMOUNT_Z20":
        return _rolling_z(torch.log1p(torch.clamp(raw.get("amount", torch.zeros_like(close)), min=0.0)), 20)
    if name == "TURNOVER_Z20":
        return _rolling_z(raw.get("turnover_rate", torch.zeros_like(close)), 20)
    if name == "VOLATILITY_5D":
        return _rolling_std(_log_return(close, 1), 5)
    if name == "VOLATILITY_20D":
        return _rolling_std(_log_return(close, 1), 20)
    if name == "DOWNSIDE_VOL_20D":
        ret = torch.minimum(_log_return(close, 1), torch.zeros_like(close))
        return _rolling_std(ret, 20)
    mapping = {
        "TURNOVER_RATE": "turnover_rate",
        "VOLUME_RATIO": "volume_ratio",
        "LOG_MKT_CAP": "log_mkt_cap",
        "PB": "pb",
        "PE_TTM": "pe_ttm",
        "PS_TTM": "ps_ttm",
        "ROE": "roe",
        "REVENUE_YOY": "revenue_yoy",
        "LIMIT_UP_FLAG": "limit_up_flag",
        "LIMIT_DOWN_FLAG": "limit_down_flag",
        "SUSPENSION_FLAG": "is_suspended",
        "INDEX_MEMBER_FLAG": "index_member_matrix",
        "ACTIVE_MASK": "active_mask",
        "LISTING_AGE_DAYS": "listing_age_days",
        "CASH_DIVIDEND_FLAG": "cash_dividend_flag",
        "STOCK_DISTRIBUTION_FLAG": "stock_distribution_flag",
    }
    key = mapping.get(name)
    if key is None:
        return None
    value = raw.get(key)
    if value is None:
        return None
    if value.ndim == 1:
        value = value.unsqueeze(1).expand(-1, close.shape[1])
    return value


def _log_return(x: torch.Tensor, periods: int) -> torch.Tensor:
    if periods <= 0:
        return torch.zeros_like(x)
    if periods >= x.shape[1]:
        return torch.zeros_like(x)
    delayed = torch.cat([torch.zeros((x.shape[0], periods), dtype=x.dtype, device=x.device), x[:, :-periods]], dim=1)
    out = torch.log(torch.clamp(x, min=1e-6) / torch.clamp(delayed, min=1e-6))
    out[:, :periods] = 0.0
    return out


def _rolling_std(x: torch.Tensor, window: int) -> torch.Tensor:
    values = []
    for idx in range(x.shape[1]):
        start = max(0, idx - window + 1)
        values.append(x[:, start : idx + 1].std(dim=1, unbiased=False))
    return torch.stack(values, dim=1)


def _rolling_z(x: torch.Tensor, window: int) -> torch.Tensor:
    values = []
    for idx in range(x.shape[1]):
        start = max(0, idx - window + 1)
        current = x[:, start : idx + 1]
        mean = current.mean(dim=1)
        std = current.std(dim=1, unbiased=False)
        values.append((x[:, idx] - mean) / torch.clamp(std, min=1e-6))
    return torch.stack(values, dim=1)


def _infer_shape(raw: dict[str, torch.Tensor]) -> tuple[int, int]:
    for value in raw.values():
        if isinstance(value, torch.Tensor) and value.ndim == 2:
            return int(value.shape[0]), int(value.shape[1])
    raise ValueError("raw data cache does not contain a 2D tensor")


def _infer_device(raw: dict[str, torch.Tensor]) -> torch.device:
    for value in raw.values():
        if isinstance(value, torch.Tensor):
            return value.device
    return torch.device("cpu")


def _coerce_manifest(manifest: FeatureSetManifest | dict[str, Any]) -> FeatureSetManifest:
    return manifest if isinstance(manifest, FeatureSetManifest) else manifest_from_payload(manifest)


def _needs_extended_attach(raw: dict[str, torch.Tensor], manifest: FeatureSetManifest) -> bool:
    for item in manifest.feature_definitions:
        if item.get("feature_set_name") == FEATURE_SET_V3 or item.get("feature_version") == FEATURE_SET_V3:
            if str(item.get("feature_name", "")).lower() not in raw:
                return True
    return False


def _definition_from_payload(payload: dict[str, Any]) -> FeatureDefinition:
    return FeatureDefinition(
        feature_name=str(payload["feature_name"]),
        feature_version=str(payload.get("feature_version", "ashare_features_v1")),
        family=str(payload.get("family", "unknown")),
        source_fields=list(payload.get("source_fields", [])),
        tensor_key=str(payload.get("tensor_key", payload["feature_name"].lower())),
        transform=str(payload.get("transform", "robust_zscore")),
        lookback=int(payload.get("lookback", 1) or 1),
        point_in_time_safe=bool(payload.get("point_in_time_safe", True)),
        availability_contract=dict(payload.get("availability_contract", {})),
        default_enabled=bool(payload.get("default_enabled", True)),
        feature_set_name=str(payload.get("feature_set_name", payload.get("feature_version", ""))),
        required_datasets=list(payload.get("required_datasets", [])),
        optional_datasets=list(payload.get("optional_datasets", [])),
        date_field=str(payload.get("date_field", "trade_date")),
        availability_field=payload.get("availability_field"),
        pit_safety=str(payload.get("pit_safety", "pit_safe")),
        used_for_alpha=bool(payload.get("used_for_alpha", True)),
        used_for_filter=bool(payload.get("used_for_filter", False)),
        used_for_risk=bool(payload.get("used_for_risk", False)),
        description=str(payload.get("description", "")),
        metadata=dict(payload.get("metadata", {})),
    )


def _values_summary(manifest: FeatureSetManifest, tensor: torch.Tensor) -> dict[str, Any]:
    summaries = []
    names = [item.get("feature_name") for item in manifest.feature_definitions]
    for idx, name in enumerate(names):
        values = torch.nan_to_num(tensor[:, idx, :], nan=0.0, posinf=0.0, neginf=0.0)
        summaries.append(
            {
                "feature_name": name,
                "mean": float(values.mean().item()),
                "std": float(values.std(unbiased=False).item()),
                "nonzero_ratio": float((values != 0).to(torch.float32).mean().item()),
            }
        )
    return {
        "feature_set_name": manifest.feature_set_name,
        "feature_set_version": manifest.feature_set_version,
        "feature_count": manifest.feature_count,
        "features": summaries,
    }


def _coverage_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Feature Coverage: {payload.get('feature_set_name')}",
        "",
        f"- feature_count: {payload.get('feature_count')}",
        f"- warnings: {len(payload.get('warnings', []))}",
        "",
        "| feature | finite_ratio | nonzero_ratio | warning |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in payload.get("feature_summaries", []):
        lines.append(
            f"| {row.get('feature_name')} | {row.get('finite_ratio', 0):.4f} | "
            f"{row.get('nonzero_ratio', 0):.4f} | {row.get('warning', '')} |"
        )
    return "\n".join(lines) + "\n"
