"""Self-contained, non-admissible replay bundles built from a Source Freeze.

This module deliberately rehabilitates legacy local observations only for
``development_replay``.  It never manufactures provider coverage, PIT
publication, ST, or suspension evidence and therefore cannot create a
Canonical Data Freeze.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import inspect
import json
import math
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from auto_alpha.data.lake.store.source_freeze import (
    SCHEMA_VERSION as SOURCE_FREEZE_SCHEMA,
    PhysicalResearchDataView,
    SOURCE_INVENTORY_DATASETS,
    SourceFreezeError,
    validate_physical_research_view,
    validate_source_freeze_generation,
)
from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_prepared_generation,
    read_json,
    sha256_file,
    validate_generation,
)


SCHEMA_VERSION = "local_development_bundle_v1"
MANIFEST_NAME = "local_development_bundle.json"
GENERATION_PREFIX = "local_development_bundle"
TARGET_NAME = "target_open_t1_t2"
MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS = 45
LEGACY_SOURCE_FREEZE_SCHEMA = "canonical_ashare_research_freeze_v1"
SOURCE_SEARCH_VIEW_SCHEMA = "source_ashare_research_view_v1"
LEGACY_SEARCH_VIEW_SCHEMA = "physical_ashare_research_view_v1"
EVIDENCE_FLAGS = {
    "adjustment_revision_proven": False,
    "corporate_action_lineage_proven": False,
    "provider_coverage_proven": False,
    "pit_membership_proven": False,
    "st_status_proven": False,
    "suspension_state_proven": False,
}

_SOURCE_CORE_KEYS = (
    "schema_version",
    "source_artifact_root",
    "source_catalog_hash",
    "period_policy",
    "partition_root",
    "search_partition_root",
    "period_coverage",
    "dataset_quality_root",
    "cross_source_reconciliation_hash",
    "source_semantic_hash",
    "strict_derived_bundle",
    "admission_evidence",
    "admission_evidence_root",
    "blockers",
    "warnings",
)

_LEGACY_SOURCE_CORE_KEYS = (
    "schema_version",
    "source_catalog_hash",
    "period_policy",
    "partition_root",
    "search_partition_root",
    "period_coverage",
    "dataset_quality_root",
    "cross_source_reconciliation_hash",
    "source_semantic_hash",
    "strict_derived_bundle",
    "blockers",
    "warnings",
)

_COMMON_PROVENANCE_BLOCKERS = (
    "adjustment_revision_history_unproven",
    "corporate_action_lineage_unproven",
    "pit_membership_publication_unproven",
    "st_status_unproven",
    "suspension_state_unproven",
)
_SOURCE_PROVENANCE_BLOCKERS = {
    "source_freeze_bound": tuple(
        sorted(("provider_coverage_unproven", *_COMMON_PROVENANCE_BLOCKERS))
    ),
    "legacy_unproven": tuple(
        sorted(
            (
                "legacy_provider_coverage_unproven",
                "legacy_source_artifact_root_unavailable",
                *_COMMON_PROVENANCE_BLOCKERS,
            )
        )
    ),
}

_FIELD_SOURCES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "open": ("daily_bars", ("open",), "positive"),
    "high": ("daily_bars", ("high",), "positive"),
    "low": ("daily_bars", ("low",), "positive"),
    "close": ("daily_bars", ("close",), "positive"),
    "pre_close": ("daily_bars", ("pre_close",), "positive"),
    "volume": ("daily_bars", ("volume", "vol"), "nonnegative"),
    "amount": ("daily_bars", ("amount",), "nonnegative"),
    "turnover_rate": ("daily_basic", ("turnover_rate",), "nonnegative"),
    "volume_ratio": ("daily_basic", ("volume_ratio",), "nonnegative"),
    "total_mv": ("daily_basic", ("total_mv",), "positive"),
    "up_limit": ("daily_limits", ("up_limit", "limit_up"), "positive"),
    "down_limit": ("daily_limits", ("down_limit", "limit_down"), "positive"),
    "limit_pre_close": ("daily_limits", ("pre_close",), "positive"),
    "adj_factor": ("adjustment_factors", ("adj_factor",), "positive"),
}
_CONTROL_FIELDS = ("up_limit", "down_limit", "limit_pre_close", "adj_factor")
_ALPHA_FEATURE_FIELDS = tuple(
    name for name in _FIELD_SOURCES if name not in _CONTROL_FIELDS
)
_DAILY_DATASETS = (
    "adjustment_factors",
    "daily_bars",
    "daily_basic",
    "daily_limits",
)
_POSITION_MASK_ROLES = tuple(
    f"{dataset}_{kind}_positions"
    for dataset in _DAILY_DATASETS
    for kind in ("observed", "duplicate")
) + (
    "limit_required_field_unusable_positions",
    "positive_limit_order_violation_positions",
    "cross_source_pre_close_mismatch_positions",
)

_REQUIRED_ARTIFACT_ROLES = {
    "stock_axis",
    "date_axis",
    "feature_axis",
    "feature_manifest",
    "feature_values",
    "feature_validity",
    "target_values",
    "target_availability",
    "target_contract",
    "pit_universe_membership",
    "membership_known",
    "membership_snapshots",
    "membership_weight",
    "source_identity_binding_evidence",
    "source_search_view_manifest_evidence",
    "source_to_derived_lineage",
    "development_matrix_manifest",
    "quality_report",
    "reconciliation_report",
} | {
    role
    for name in _FIELD_SOURCES
    for role in (f"raw_{name}", f"raw_{name}_validity")
} | set(_POSITION_MASK_ROLES)
_BUNDLE_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "generation_id",
        "content_hash",
        "mode",
        "scope",
        "source_schema_version",
        "source_evidence_grade",
        "source_identity_kind",
        "source_generation_id",
        "source_content_hash",
        "source_artifact_root",
        "source_declared_blockers",
        "source_declared_partition_root",
        "source_manifest_sha256",
        "source_semantic_hash",
        "search_view_content_hash",
        "search_partition_root",
        "search_view_manifest_sha256",
        "source_partition_selection_root",
        "builder_semantic_hash",
        "artifact_root",
        "artifacts",
        "evidence_flags",
        "blockers",
        "data_admission_eligible",
        "alpha_search_authorized",
        "lifecycle_publication_allowed",
        "deterministic_build",
    }
)
_SEARCH_VIEW_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "generation_id",
        "freeze_content_hash",
        "allowed_periods",
        "max_availability_date",
        "partition_root",
        "partitions",
        "source_semantic_hash",
        "strict_derived_bundle",
        "alpha_search_authorized",
        "candidate_identity_freeze_required_before_evaluation_views",
        "holdout_locator_exposed",
        "content_hash",
    }
)
_SEARCH_PARTITION_KEYS = frozenset(
    {
        "dataset",
        "format",
        "max_availability_date",
        "min_availability_date",
        "period",
        "record_count",
        "sha256",
        "size_bytes",
        "view",
        "view_relative_path",
    }
)
_RESEARCH_DATASET_NAMES = frozenset(SOURCE_INVENTORY_DATASETS)


class LocalDevelopmentBundleError(RuntimeError):
    """Raised when local replay evidence or an immutable bundle is invalid."""


class LocalDevelopmentBundleLoader:
    """Read arrays only after the complete development bundle has validated.

    The loader is intentionally not an ``AShareDataLoader`` compatibility
    adapter.  It preserves the bundle's stock × feature × date layout and its
    permanent development-only governance boundary; consumers must request
    explicit artifact roles and perform any orientation change themselves.
    """

    __slots__ = (
        "_artifacts",
        "_manifest",
        "_root",
        "feature_names",
        "stock_ids",
        "trade_dates",
    )

    def __init__(
        self,
        bundle_manifest: str | Path,
        *,
        trusted_source_freeze_manifest: str | Path | None = None,
    ) -> None:
        manifest = validate_local_development_bundle(
            bundle_manifest,
            trusted_source_freeze_manifest=trusted_source_freeze_manifest,
        )
        root = Path(str(manifest["manifest_path"])).resolve().parent
        artifacts = {
            str(row["role"]): dict(row) for row in manifest["artifacts"]
        }
        self._manifest = dict(manifest)
        self._root = root
        self._artifacts = artifacts
        self.stock_ids = tuple(_read_axis(root, artifacts["stock_axis"]))
        self.trade_dates = tuple(_read_axis(root, artifacts["date_axis"]))
        self.feature_names = tuple(_read_axis(root, artifacts["feature_axis"]))

    @property
    def manifest(self) -> dict[str, Any]:
        """Return a defensive copy of the validated immutable identity."""

        return copy.deepcopy(self._manifest)

    @property
    def root(self) -> Path:
        return self._root

    def load_array(
        self,
        role: str,
        *,
        dtype: np.dtype[Any] | type[np.generic],
    ) -> np.ndarray:
        """Map one declared NPY artifact by role with its frozen dtype/shape."""

        row = self._artifacts.get(str(role))
        if row is None or Path(str(row.get("relative_path") or "")).suffix != ".npy":
            raise LocalDevelopmentBundleError(
                f"local development array role invalid:{role}"
            )
        expected_dtype = np.dtype(dtype)
        value = _load_array(self._root, row, expected_dtype)
        return value

    def artifact_row(self, role: str) -> dict[str, Any]:
        """Return one defensive artifact-row copy for lineage binding."""

        row = self._artifacts.get(str(role))
        if row is None:
            raise LocalDevelopmentBundleError(
                f"local development artifact role invalid:{role}"
            )
        return copy.deepcopy(row)


@dataclass(frozen=True)
class ReplaySourceDescriptor:
    """Verified source identity normalized without upgrading its evidence grade."""

    schema_version: str
    evidence_grade: str
    identity_kind: str
    generation_id: str
    content_hash: str
    source_artifact_root: str
    declared_partition_root: str
    declared_blockers: tuple[str, ...]
    provenance_blockers: tuple[str, ...]


@dataclass(frozen=True)
class LocalDevelopmentScope:
    date_start: str
    date_end: str
    index_code: str

    def __post_init__(self) -> None:
        if not _valid_date(self.date_start) or not _valid_date(self.date_end):
            raise ValueError("local development scope dates must be YYYYMMDD")
        if self.date_start > self.date_end:
            raise ValueError("local development scope date_start exceeds date_end")
        if not str(self.index_code).strip():
            raise ValueError("local development scope index_code is required")

    def to_dict(self) -> dict[str, str]:
        return {
            "date_start": self.date_start,
            "date_end": self.date_end,
            "index_code": self.index_code,
        }


def _transform_contract() -> dict[str, str]:
    return {
        "membership": (
            "complete_2016_or_later_snapshot_effective_next_open_trade_day_"
            f"max_staleness_{MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS}_calendar_days"
        ),
        "target": "observed_adjusted_open_t_plus_2_over_t_plus_1_minus_one",
        "invalid_feature_storage": "zero_with_separate_false_validity",
        "invalid_target_storage": "nan_with_separate_false_availability",
    }


def _source_identity_binding(
    *,
    source: ReplaySourceDescriptor,
    search: Mapping[str, Any],
    source_manifest_sha256: str,
    search_view_manifest_sha256: str,
    source_partition_selection_root: str,
) -> dict[str, Any]:
    """Create a source receipt without copying controlled-view locators."""

    binding = {
        "schema_version": "local_development_source_identity_binding_v1",
        "mode": "development_replay",
        "source_schema_version": source.schema_version,
        "source_evidence_grade": source.evidence_grade,
        "source_identity_kind": source.identity_kind,
        "source_generation_id": source.generation_id,
        "source_content_hash": source.content_hash,
        "source_artifact_root": source.source_artifact_root,
        "source_declared_partition_root": source.declared_partition_root,
        "source_declared_blockers": list(source.declared_blockers),
        "source_manifest_sha256": source_manifest_sha256,
        "source_semantic_hash": str(search.get("source_semantic_hash") or ""),
        "search_view_content_hash": str(search.get("content_hash") or ""),
        "search_partition_root": str(search.get("partition_root") or ""),
        "search_view_manifest_sha256": search_view_manifest_sha256,
        "source_partition_selection_root": source_partition_selection_root,
        "source_manifest_raw_embedded": False,
        "search_view_manifest_raw_embedded": True,
        "holdout_locator_exposed": False,
        "evidence_flags": dict(EVIDENCE_FLAGS),
        "provenance_blockers": list(source.provenance_blockers),
    }
    if _contains_controlled_period_locator(binding):
        raise LocalDevelopmentBundleError(
            "source identity binding exposes controlled-period locator"
        )
    return binding


def _expected_source_identity_binding(bundle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "local_development_source_identity_binding_v1",
        "mode": "development_replay",
        "source_schema_version": bundle.get("source_schema_version"),
        "source_evidence_grade": bundle.get("source_evidence_grade"),
        "source_identity_kind": bundle.get("source_identity_kind"),
        "source_generation_id": bundle.get("source_generation_id"),
        "source_content_hash": bundle.get("source_content_hash"),
        "source_artifact_root": bundle.get("source_artifact_root"),
        "source_declared_partition_root": bundle.get(
            "source_declared_partition_root"
        ),
        "source_declared_blockers": bundle.get("source_declared_blockers"),
        "source_manifest_sha256": bundle.get("source_manifest_sha256"),
        "source_semantic_hash": bundle.get("source_semantic_hash"),
        "search_view_content_hash": bundle.get("search_view_content_hash"),
        "search_partition_root": bundle.get("search_partition_root"),
        "search_view_manifest_sha256": bundle.get(
            "search_view_manifest_sha256"
        ),
        "source_partition_selection_root": bundle.get(
            "source_partition_selection_root"
        ),
        "source_manifest_raw_embedded": False,
        "search_view_manifest_raw_embedded": True,
        "holdout_locator_exposed": False,
        "evidence_flags": dict(EVIDENCE_FLAGS),
        "provenance_blockers": bundle.get("blockers"),
    }


def _contains_controlled_period_locator(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_controlled_period_locator(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_controlled_period_locator(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.lower().replace("\\", "/")
    return any(
        token in normalized
        for token in (
            "sealed_holdout",
            "retrospective_test",
            "controlled",
            "validation",
            "holdout",
            "period=validation",
            "/validation/",
        )
    )


def _valid_research_partition_path(
    dataset: str,
    period: str,
    relative_text: str,
) -> bool:
    """Accept only the physical layout emitted by the research-view builder."""

    relative = Path(relative_text)
    if (
        relative.is_absolute()
        or len(relative.parts) != 4
        or relative.parts[0] != "data"
        or relative.parts[1] != dataset
        or relative.parts[2] != f"period={period}"
    ):
        return False
    filename = relative.parts[3]
    return (
        _valid_research_dataset_name(dataset)
        and filename.startswith("part-")
        and filename.endswith(".parquet")
        and filename[5:-8].isdigit()
    )


def _valid_research_dataset_name(dataset: str) -> bool:
    return dataset in _RESEARCH_DATASET_NAMES and all(
        character.islower() or character.isdigit() or character == "_"
        for character in dataset
    ) and not any(
        token in dataset
        for token in ("controlled", "validation", "retrospective", "sealed", "holdout")
    )


def _valid_strict_derived_bundle(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    allowed = {
        "artifact_root",
        "axes_present",
        "axis_hashes",
        "content_hash",
        "date_axis_max",
        "date_axis_min",
        "feature_validity_present",
        "feature_values_present",
        "feature_axis_hash",
        "manifest_sha256",
        "status",
        "strict_matrix_present",
        "target_availability_present",
        "validated_feature_shape",
        "validated_shape",
        "frozen_artifacts",
        "frozen_artifact_root",
    }
    if set(value) - allowed or _contains_controlled_period_locator(value):
        return False
    frozen = value.get("frozen_artifacts")
    if frozen is None:
        if "frozen_artifact_root" in value:
            return False
    else:
        if not isinstance(frozen, list):
            return False
        normalized: list[dict[str, Any]] = []
        for raw in frozen:
            if not isinstance(raw, Mapping) or set(raw) != {
                "role",
                "view_relative_path",
                "sha256",
                "size_bytes",
            }:
                return False
            relative = Path(str(raw.get("view_relative_path") or ""))
            try:
                if isinstance(raw["size_bytes"], bool):
                    raise ValueError("boolean derived size")
                size_bytes = int(raw["size_bytes"])
            except (TypeError, ValueError):
                return False
            if (
                not str(raw.get("role") or "")
                or relative.is_absolute()
                or ".." in relative.parts
                or len(relative.parts) < 3
                or relative.parts[0] != "derived"
                or not _sha256_hex(str(raw.get("sha256") or ""))
                or size_bytes < 0
                or _contains_controlled_period_locator(raw)
            ):
                return False
            normalized.append(
                {
                    "role": str(raw["role"]),
                    "view_relative_path": relative.as_posix(),
                    "sha256": str(raw["sha256"]),
                    "size_bytes": size_bytes,
                }
            )
        if value.get("frozen_artifact_root") != canonical_hash(normalized):
            return False
    for key in (
        "artifact_root",
        "content_hash",
        "feature_axis_hash",
        "manifest_sha256",
    ):
        if key in value and value[key] is not None and not _sha256_hex(str(value[key])):
            return False
    axis_hashes = value.get("axis_hashes")
    if axis_hashes is not None and (
        not isinstance(axis_hashes, Mapping)
        or any(not _sha256_hex(str(item)) for item in axis_hashes.values())
    ):
        return False
    for key in ("date_axis_min", "date_axis_max"):
        if key in value and value[key] is not None and not _valid_date(value[key]):
            return False
    for key in ("validated_shape", "validated_feature_shape"):
        if key in value:
            if not isinstance(value[key], list):
                return False
            try:
                if any(isinstance(item, bool) or int(item) < 0 for item in value[key]):
                    return False
            except (TypeError, ValueError):
                return False
    return True


def _source_lineage(
    *,
    source: ReplaySourceDescriptor,
    search: Mapping[str, Any],
    builder_semantic_hash: str,
    source_partitions: list[dict[str, Any]],
    source_partition_selection_root: str,
) -> dict[str, Any]:
    return {
        "schema_version": "local_development_source_lineage_v1",
        "mode": "development_replay",
        "source_schema_version": source.schema_version,
        "source_evidence_grade": source.evidence_grade,
        "source_identity_kind": source.identity_kind,
        "source_generation_id": source.generation_id,
        "source_content_hash": source.content_hash,
        "source_artifact_root": source.source_artifact_root,
        "source_declared_blockers": list(source.declared_blockers),
        "provenance_blockers": list(source.provenance_blockers),
        "search_view_content_hash": search["content_hash"],
        "search_partition_root": search["partition_root"],
        "source_partition_selection_root": source_partition_selection_root,
        "builder_semantic_hash": builder_semantic_hash,
        "source_partitions": source_partitions,
        "transform_contract": _transform_contract(),
        "evidence_flags": dict(EVIDENCE_FLAGS),
    }


def _validate_source_lineage(
    lineage: Mapping[str, Any],
    bundle: Mapping[str, Any],
    expected_partitions: list[dict[str, Any]],
) -> None:
    failure = "local development derived semantics invalid"
    expected_keys = {
        "schema_version",
        "mode",
        "source_schema_version",
        "source_evidence_grade",
        "source_identity_kind",
        "source_generation_id",
        "source_content_hash",
        "source_artifact_root",
        "source_declared_blockers",
        "provenance_blockers",
        "search_view_content_hash",
        "search_partition_root",
        "source_partition_selection_root",
        "builder_semantic_hash",
        "source_partitions",
        "transform_contract",
        "evidence_flags",
    }
    partitions = lineage.get("source_partitions")
    if not isinstance(partitions, list):
        raise LocalDevelopmentBundleError(failure)
    normalized: list[dict[str, Any]] = []
    for raw in partitions:
        if not isinstance(raw, Mapping):
            raise LocalDevelopmentBundleError(failure)
        try:
            if isinstance(raw["record_count"], bool):
                raise ValueError("boolean lineage count")
            row = {
                "dataset": str(raw["dataset"]),
                "period": str(raw["period"]),
                "view_relative_path": str(raw["view_relative_path"]),
                "sha256": str(raw["sha256"]),
                "record_count": int(raw["record_count"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalDevelopmentBundleError(failure) from exc
        relative = Path(row["view_relative_path"])
        if (
            set(raw) != set(row)
            or row["dataset"]
            not in {
                "trade_calendar",
                "index_members",
                "daily_bars",
                "daily_basic",
                "daily_limits",
                "adjustment_factors",
            }
            or row["period"] not in {"bootstrap", "research"}
            or relative.is_absolute()
            or ".." in relative.parts
            or not _valid_research_partition_path(
                row["dataset"], row["period"], row["view_relative_path"]
            )
            or not _sha256_hex(row["sha256"])
            or row["record_count"] < 0
        ):
            raise LocalDevelopmentBundleError(failure)
        normalized.append(row)
    if normalized != sorted(
        normalized,
        key=lambda row: (
            row["dataset"],
            row["period"],
            row["view_relative_path"],
        ),
    ) or len(
        {row["view_relative_path"] for row in normalized}
    ) != len(normalized):
        raise LocalDevelopmentBundleError(failure)
    partition_root = canonical_hash(normalized)
    expected_pairs = {
        "mode": "development_replay",
        "source_schema_version": bundle.get("source_schema_version"),
        "source_evidence_grade": bundle.get("source_evidence_grade"),
        "source_identity_kind": bundle.get("source_identity_kind"),
        "source_generation_id": bundle.get("source_generation_id"),
        "source_content_hash": bundle.get("source_content_hash"),
        "source_artifact_root": bundle.get("source_artifact_root"),
        "source_declared_blockers": bundle.get("source_declared_blockers"),
        "provenance_blockers": bundle.get("blockers"),
        "search_view_content_hash": bundle.get("search_view_content_hash"),
        "search_partition_root": bundle.get("search_partition_root"),
        "source_partition_selection_root": bundle.get(
            "source_partition_selection_root"
        ),
        "builder_semantic_hash": bundle.get("builder_semantic_hash"),
        "transform_contract": _transform_contract(),
        "evidence_flags": dict(EVIDENCE_FLAGS),
    }
    if (
        set(lineage) != expected_keys
        or lineage.get("schema_version")
        != "local_development_source_lineage_v1"
        or any(lineage.get(key) != value for key, value in expected_pairs.items())
        or lineage.get("source_partition_selection_root") != partition_root
        or bundle.get("source_partition_selection_root") != partition_root
        or normalized != expected_partitions
    ):
        raise LocalDevelopmentBundleError(failure)


def _validate_embedded_source_evidence(
    root: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
    bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    failure = "local development source evidence invalid"
    binding_row = artifacts["source_identity_binding_evidence"]
    search_row = artifacts["source_search_view_manifest_evidence"]
    binding = _read_json_artifact(root, binding_row)
    search_manifest = _read_json_artifact(root, search_row)
    source_schema = str(bundle.get("source_schema_version") or "")
    if source_schema == SOURCE_FREEZE_SCHEMA:
        expected_search_schema = SOURCE_SEARCH_VIEW_SCHEMA
    elif source_schema == LEGACY_SOURCE_FREEZE_SCHEMA:
        expected_search_schema = LEGACY_SEARCH_VIEW_SCHEMA
    else:
        raise LocalDevelopmentBundleError(failure)
    expected_binding = _expected_source_identity_binding(bundle)
    if (
        binding != expected_binding
        or _contains_controlled_period_locator(binding)
        or search_row.get("sha256") != bundle.get("search_view_manifest_sha256")
        or _contains_controlled_period_locator(search_manifest)
    ):
        raise LocalDevelopmentBundleError(failure)

    search_semantic = {
        key: value for key, value in search_manifest.items() if key != "content_hash"
    }
    partitions = search_manifest.get("partitions")
    if (
        set(search_manifest) != _SEARCH_VIEW_MANIFEST_KEYS
        or search_manifest.get("schema_version") != expected_search_schema
        or canonical_hash(search_semantic) != search_manifest.get("content_hash")
        or search_manifest.get("content_hash")
        != bundle.get("search_view_content_hash")
        or search_manifest.get("generation_id")
        != bundle.get("source_generation_id")
        or search_manifest.get("freeze_content_hash")
        != bundle.get("source_content_hash")
        or search_manifest.get("partition_root")
        != bundle.get("search_partition_root")
        or search_manifest.get("source_semantic_hash")
        != bundle.get("source_semantic_hash")
        or search_manifest.get("allowed_periods") != ["bootstrap", "research"]
        or search_manifest.get("max_availability_date") != "20191231"
        or search_manifest.get("alpha_search_authorized") is not False
        or search_manifest.get("holdout_locator_exposed") is not False
        or search_manifest.get(
            "candidate_identity_freeze_required_before_evaluation_views"
        ) is not True
        or not _valid_strict_derived_bundle(search_manifest.get("strict_derived_bundle"))
        or not isinstance(partitions, list)
    ):
        raise LocalDevelopmentBundleError(failure)
    partition_root_rows: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    required_datasets = {
        "trade_calendar",
        "index_members",
        "daily_bars",
        "daily_basic",
        "daily_limits",
        "adjustment_factors",
    }
    for raw in partitions:
        if not isinstance(raw, Mapping):
            raise LocalDevelopmentBundleError(failure)
        if set(raw) != _SEARCH_PARTITION_KEYS:
            raise LocalDevelopmentBundleError(failure)
        dataset = str(raw.get("dataset") or "")
        period = str(raw.get("period") or "")
        relative_text = str(raw.get("view_relative_path") or "")
        relative = Path(relative_text)
        sha256 = str(raw.get("sha256") or "")
        try:
            if isinstance(raw["record_count"], bool) or isinstance(
                raw["size_bytes"], bool
            ):
                raise ValueError("boolean partition size")
            record_count = int(raw["record_count"])
            size_bytes = int(raw["size_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalDevelopmentBundleError(failure) from exc
        if (
            not _valid_research_dataset_name(dataset)
            or period not in {"bootstrap", "research"}
            or relative.is_absolute()
            or ".." in relative.parts
            or not relative_text
            or raw.get("format") != "parquet_observable_json_envelope_v1"
            or raw.get("view") != "search"
            or not _valid_partition_date_bounds(
                period=period,
                min_date=raw.get("min_availability_date"),
                max_date=raw.get("max_availability_date"),
                record_count=record_count,
            )
            or not _sha256_hex(sha256)
            or record_count < 0
            or size_bytes < 0
            or not _valid_research_partition_path(dataset, period, relative_text)
        ):
            raise LocalDevelopmentBundleError(failure)
        partition_root_rows.append(
            {"path": relative_text, "sha256": sha256, "records": record_count}
        )
        if dataset in required_datasets:
            selected.append(
                {
                    "dataset": dataset,
                    "period": period,
                    "view_relative_path": relative_text,
                    "sha256": sha256,
                    "record_count": record_count,
                }
            )
    if canonical_hash(partition_root_rows) != search_manifest.get("partition_root"):
        raise LocalDevelopmentBundleError(failure)
    if len({row["path"] for row in partition_root_rows}) != len(partition_root_rows):
        raise LocalDevelopmentBundleError(failure)
    selected.sort(
        key=lambda row: (
            row["dataset"],
            row["period"],
            row["view_relative_path"],
        )
    )
    if (
        {row["dataset"] for row in selected} != required_datasets
        or any(
            not any(row["dataset"] == dataset for row in selected)
            for dataset in required_datasets
        )
        or canonical_hash(selected)
        != bundle.get("source_partition_selection_root")
    ):
        raise LocalDevelopmentBundleError(failure)
    return selected


def build_local_development_bundle(
    source_freeze_manifest: str | Path,
    output_root: str | Path,
    *,
    scope: LocalDevelopmentScope,
    workers: int = 1,
) -> dict[str, Any]:
    """Build one deterministic development-only matrix from search partitions."""

    if not isinstance(scope, LocalDevelopmentScope):
        raise TypeError("scope must be LocalDevelopmentScope")
    if isinstance(workers, bool) or int(workers) <= 0:
        raise ValueError("workers must be positive")

    builder_semantic_hash = _builder_semantic_hash()
    source, search = _validate_source_and_search_view(source_freeze_manifest)
    source_manifest_path = Path(source_freeze_manifest).resolve()
    source_manifest_bytes = source_manifest_path.read_bytes()
    source_manifest_sha256 = hashlib.sha256(source_manifest_bytes).hexdigest()
    search_manifest_path = Path(str(search["manifest_path"])).resolve()
    search_manifest_bytes = search_manifest_path.read_bytes()
    search_manifest_sha256 = hashlib.sha256(search_manifest_bytes).hexdigest()
    output = Path(output_root).resolve()
    _reject_source_output_overlap(
        output,
        source_manifest=source_manifest_path,
        search_manifest=search_manifest_path,
    )
    cached = _compatible_current_bundle(
        output,
        source=source,
        search=search,
        scope=scope,
        builder_semantic_hash=builder_semantic_hash,
        source_manifest_sha256=source_manifest_sha256,
        search_manifest_sha256=search_manifest_sha256,
        source_freeze_manifest=source_freeze_manifest,
    )
    if cached is not None:
        return cached | {"cache_hit": True}
    view = PhysicalResearchDataView(search["manifest_path"])
    trade_dates = _trade_date_axis(view, scope)
    snapshots, snapshot_report = _accepted_snapshots(view, scope, trade_dates)
    ts_codes = sorted({code for snapshot in snapshots.values() for code in snapshot})
    if not trade_dates:
        raise LocalDevelopmentBundleError("local development scope has no open trade dates")
    if not ts_codes:
        raise LocalDevelopmentBundleError("local development scope has no accepted index snapshots")

    membership, membership_known, membership_weight, effective_snapshots = _membership_matrices(
        snapshots,
        ts_codes,
        trade_dates,
    )
    raw_values, raw_validity, position_masks = _aligned_observations(
        view,
        ts_codes,
        trade_dates,
        workers=int(workers),
    )
    feature_names = list(_ALPHA_FEATURE_FIELDS)
    eligible = membership & membership_known
    feature_values = np.stack([raw_values[name] for name in feature_names], axis=1).astype(
        np.float32,
        copy=False,
    )
    feature_validity = np.stack(
        [raw_validity[name] & eligible for name in feature_names],
        axis=1,
    ).astype(np.bool_, copy=False)
    feature_values = np.where(feature_validity, feature_values, 0.0).astype(np.float32, copy=False)
    target_values, target_available = _observed_open_target(
        raw_values,
        raw_validity,
        membership,
        membership_known,
    )

    source_partitions = [
        {
            "dataset": str(row["dataset"]),
            "period": str(row["period"]),
            "view_relative_path": str(row["view_relative_path"]),
            "sha256": str(row["sha256"]),
            "record_count": int(row["record_count"]),
        }
        for row in search.get("partitions") or []
        if str(row.get("dataset") or "")
        in {
            "trade_calendar",
            "index_members",
            "daily_bars",
            "daily_basic",
            "daily_limits",
            "adjustment_factors",
        }
    ]
    source_partitions.sort(
        key=lambda row: (row["dataset"], row["period"], row["view_relative_path"])
    )
    source_partition_selection_root = canonical_hash(source_partitions)
    lineage = _source_lineage(
        source=source,
        search=search,
        builder_semantic_hash=builder_semantic_hash,
        source_partitions=source_partitions,
        source_partition_selection_root=source_partition_selection_root,
    )
    target_contract = _target_contract()
    quality, reconciliation = _build_quality_and_reconciliation(
        scope=scope,
        trade_dates=trade_dates,
        snapshot_report=snapshot_report,
        effective_snapshots=effective_snapshots,
        raw_values=raw_values,
        raw_validity=raw_validity,
        membership=membership,
        membership_known=membership_known,
        feature_names=feature_names,
        feature_validity=feature_validity,
        target_available=target_available,
        position_masks=position_masks,
    )
    _assert_source_identity_stable(
        source_freeze_manifest,
        expected_source=source,
        expected_search=search,
        expected_source_manifest_sha256=source_manifest_sha256,
        expected_search_manifest_sha256=search_manifest_sha256,
    )
    output.mkdir(parents=True, exist_ok=True)
    preparation_root = Path(
        tempfile.mkdtemp(prefix=".local_development_bundle.", dir=output)
    )
    staging = preparation_root / "working"
    staging.mkdir()
    try:
        matrix = staging / "development_matrix"
        matrix.mkdir(parents=True, exist_ok=True)
        source_evidence = staging / "source_evidence"
        source_evidence.mkdir(parents=True, exist_ok=True)
        _write_json(
            source_evidence / "source_identity_binding.json",
            _source_identity_binding(
                source=source,
                search=search,
                source_manifest_sha256=source_manifest_sha256,
                search_view_manifest_sha256=search_manifest_sha256,
                source_partition_selection_root=source_partition_selection_root,
            ),
        )
        _write_bytes(
            source_evidence / "research_view_manifest.json",
            search_manifest_bytes,
        )
        _write_json(matrix / "ts_codes.json", ts_codes)
        _write_json(matrix / "trade_dates.json", trade_dates)
        _write_json(matrix / "feature_names.json", feature_names)
        _write_json(matrix / "target_contract.json", target_contract)
        _write_json(
            matrix / "accepted_index_snapshots.json",
            _membership_snapshot_evidence(snapshots, scope, snapshot_report),
        )
        _write_json(matrix / "source_to_derived_lineage.json", lineage)
        _write_json(matrix / "local_quality_report.json", quality)
        _write_json(matrix / "reconciliation_report.json", reconciliation)
        feature_manifest = {
            "schema_version": "local_development_feature_manifest_v1",
            "mode": "development_replay",
            "feature_names": feature_names,
            "feature_axis_hash": canonical_hash(feature_names),
            "features": [
                {
                    "feature_name": name,
                    "source_dataset": _FIELD_SOURCES[name][0],
                    "validity_rule": f"observed_{_FIELD_SOURCES[name][2]}_and_proxy_membership_known",
                }
                for name in feature_names
            ],
        }
        _write_json(matrix / "feature_set_manifest.json", feature_manifest)

        for name in _FIELD_SOURCES:
            _write_npy(matrix / f"{name}.npy", raw_values[name].astype(np.float32, copy=False))
            _write_npy(
                matrix / f"{name}_validity.npy",
                raw_validity[name].astype(np.bool_, copy=False),
            )
        for role in _POSITION_MASK_ROLES:
            _write_npy(
                matrix / f"{role}.npy",
                position_masks[role].astype(np.bool_, copy=False),
            )
        _write_npy(matrix / "membership.npy", membership)
        _write_npy(matrix / "membership_known.npy", membership_known)
        _write_npy(matrix / "index_weight.npy", membership_weight)
        _write_npy(matrix / "feature_tensor.npy", feature_values)
        _write_npy(matrix / "feature_validity_tensor.npy", feature_validity)
        _write_npy(matrix / f"{TARGET_NAME}.npy", target_values)
        _write_npy(matrix / "target_available_mask.npy", target_available)

        matrix_partitions = {
            path.name: sha256_file(path)
            for path in sorted(matrix.iterdir())
            if path.is_file()
        }
        development_matrix = {
            "schema_version": "local_development_matrix_v1",
            "mode": "development_replay",
            "shape": [len(ts_codes), len(trade_dates)],
            "stock_axis_hash": canonical_hash(ts_codes),
            "date_axis_hash": canonical_hash(trade_dates),
            "feature_axis_hash": canonical_hash(feature_names),
            "raw_fields": list(_FIELD_SOURCES),
            "target_contract": target_contract,
            "universe_mode": "daily_retrospective_proxy",
            "historical_constituent_proof": False,
            "physical_research_projection": True,
            "evidence_flags": dict(EVIDENCE_FLAGS),
            "partition_sha256": matrix_partitions,
            "deterministic_build": True,
            "builder_semantic_hash": builder_semantic_hash,
        }
        _write_json(matrix / "development_matrix_manifest.json", development_matrix)

        roles = _artifact_roles(feature_names)
        artifacts = [
            _artifact_row(staging, role, relative_path)
            for role, relative_path in sorted(roles.items())
        ]
        if builder_semantic_hash != _builder_semantic_hash():
            raise LocalDevelopmentBundleError(
                "local development builder semantics changed during materialization"
            )
        semantic = {
            "schema_version": SCHEMA_VERSION,
            "mode": "development_replay",
            "scope": scope.to_dict(),
            "source_schema_version": source.schema_version,
            "source_evidence_grade": source.evidence_grade,
            "source_identity_kind": source.identity_kind,
            "source_generation_id": source.generation_id,
            "source_content_hash": source.content_hash,
            "source_artifact_root": source.source_artifact_root,
            "source_declared_blockers": list(source.declared_blockers),
            "source_declared_partition_root": source.declared_partition_root,
            "source_manifest_sha256": source_manifest_sha256,
            "source_semantic_hash": search["source_semantic_hash"],
            "search_view_content_hash": search["content_hash"],
            "search_partition_root": search["partition_root"],
            "search_view_manifest_sha256": search_manifest_sha256,
            "source_partition_selection_root": source_partition_selection_root,
            "builder_semantic_hash": builder_semantic_hash,
            "artifact_root": canonical_hash(artifacts),
            "artifacts": artifacts,
            "evidence_flags": dict(EVIDENCE_FLAGS),
            "blockers": list(source.provenance_blockers),
            "data_admission_eligible": False,
            "alpha_search_authorized": False,
            "lifecycle_publication_allowed": False,
            "deterministic_build": True,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"{GENERATION_PREFIX}_{content_hash[:24]}"
        manifest = semantic | {
            "content_hash": content_hash,
            "generation_id": generation_id,
        }
        _write_json(staging / MANIFEST_NAME, manifest)
        prepared = preparation_root / generation_id
        os.replace(staging, prepared)
        try:
            return publish_prepared_generation(
                output,
                prepared_directory=prepared,
                manifest_name=MANIFEST_NAME,
                validator=lambda manifest: validate_local_development_bundle(
                    manifest,
                    trusted_source_freeze_manifest=source_freeze_manifest,
                ),
                pointer_schema="local_development_bundle_pointer_v1",
                pointer_fields={
                    "mode": "development_replay",
                    "data_admission_eligible": False,
                },
            )
        except (OSError, ValueError) as exc:
            raise LocalDevelopmentBundleError(
                "local development generation publication failed"
            ) from exc
    finally:
        _remove_preparation_root(preparation_root)


def _validate_against_trusted_source(
    payload: Mapping[str, Any],
    source_freeze_manifest: str | Path,
) -> tuple[ReplaySourceDescriptor, dict[str, Any]]:
    """Bind a replay validation to a caller-supplied immutable source anchor."""

    source_path = Path(source_freeze_manifest).resolve()
    source, search = _validate_source_and_search_view(source_path)
    search_path = Path(str(search["manifest_path"])).resolve()
    if (
        sha256_file(source_path) != payload.get("source_manifest_sha256")
        or sha256_file(search_path) != payload.get("search_view_manifest_sha256")
        or payload.get("source_schema_version") != source.schema_version
        or payload.get("source_evidence_grade") != source.evidence_grade
        or payload.get("source_identity_kind") != source.identity_kind
        or payload.get("source_generation_id") != source.generation_id
        or payload.get("source_content_hash") != source.content_hash
        or payload.get("source_artifact_root") != source.source_artifact_root
        or payload.get("source_declared_partition_root")
        != source.declared_partition_root
        or payload.get("source_declared_blockers")
        != list(source.declared_blockers)
        or payload.get("blockers") != list(source.provenance_blockers)
        or payload.get("source_semantic_hash") != search.get("source_semantic_hash")
        or payload.get("search_view_content_hash") != search.get("content_hash")
        or payload.get("search_partition_root") != search.get("partition_root")
    ):
        raise LocalDevelopmentBundleError(
            "local development trusted source binding mismatch"
        )
    return source, search


def validate_local_development_bundle(
    path: str | Path,
    *,
    trusted_source_freeze_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a bundle, optionally against the original immutable source."""

    try:
        payload = validate_generation(path, schema=SCHEMA_VERSION, manifest_name=MANIFEST_NAME)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LocalDevelopmentBundleError("local development bundle identity invalid") from exc
    if (
        set(payload) - {"manifest_path"} != _BUNDLE_MANIFEST_KEYS
        or _contains_controlled_period_locator(payload)
    ):
        raise LocalDevelopmentBundleError("local development manifest boundary invalid")
    trusted_source_context: tuple[ReplaySourceDescriptor, dict[str, Any]] | None = None
    if trusted_source_freeze_manifest is not None:
        trusted_source_context = _validate_against_trusted_source(
            payload,
            trusted_source_freeze_manifest,
        )
    content_hash = str(payload.get("content_hash") or "")
    expected_generation_id = f"{GENERATION_PREFIX}_{content_hash[:24]}"
    if (
        payload.get("generation_id") != expected_generation_id
        or Path(str(payload.get("manifest_path") or "")).name != MANIFEST_NAME
    ):
        raise LocalDevelopmentBundleError("local development generation identity invalid")
    evidence_grade = str(payload.get("source_evidence_grade") or "")
    expected_blockers = _SOURCE_PROVENANCE_BLOCKERS.get(evidence_grade)
    source_content_hash = str(payload.get("source_content_hash") or "")
    source_artifact_root = str(payload.get("source_artifact_root") or "")
    declared_partition_root = str(payload.get("source_declared_partition_root") or "")
    search_partition_root = str(payload.get("search_partition_root") or "")
    source_declared_blockers = payload.get("source_declared_blockers")
    source_contract_valid = False
    if evidence_grade == "source_freeze_bound":
        source_contract_valid = (
            payload.get("source_schema_version") == SOURCE_FREEZE_SCHEMA
            and payload.get("source_identity_kind") == "source_artifact_root_v1"
            and payload.get("source_generation_id")
            == f"ashare_source_freeze_{source_content_hash[:24]}"
            and all(
                _sha256_hex(value)
                for value in (
                    source_content_hash,
                    source_artifact_root,
                    declared_partition_root,
                    search_partition_root,
                )
            )
        )
    elif evidence_grade == "legacy_unproven":
        expected_legacy_root = canonical_hash(
            {
                "schema_version": "legacy_replay_source_identity_v1",
                "legacy_source_content_hash": source_content_hash,
                "declared_partition_root": declared_partition_root,
                "verified_search_partition_root": search_partition_root,
            }
        )
        source_contract_valid = (
            payload.get("source_schema_version") == LEGACY_SOURCE_FREEZE_SCHEMA
            and payload.get("source_identity_kind") == "legacy_partition_roots_v1"
            and payload.get("source_generation_id") == f"ashare_freeze_{source_content_hash[:24]}"
            and all(
                _sha256_hex(value)
                for value in (
                    source_content_hash,
                    declared_partition_root,
                    search_partition_root,
                )
            )
            and source_artifact_root == expected_legacy_root
        )
    if (
        payload.get("mode") != "development_replay"
        or payload.get("evidence_flags") != EVIDENCE_FLAGS
        or expected_blockers is None
        or payload.get("blockers") != list(expected_blockers)
        or not isinstance(source_declared_blockers, list)
        or source_declared_blockers
        != sorted(set(str(item) for item in source_declared_blockers))
        or not _sha256_hex(
            str(payload.get("search_view_content_hash") or "")
        )
        or not _sha256_hex(
            str(payload.get("source_partition_selection_root") or "")
        )
        or not _sha256_hex(str(payload.get("source_manifest_sha256") or ""))
        or not _sha256_hex(str(payload.get("source_semantic_hash") or ""))
        or not _sha256_hex(
            str(payload.get("search_view_manifest_sha256") or "")
        )
        or not source_contract_valid
        or payload.get("data_admission_eligible") is not False
        or payload.get("alpha_search_authorized") is not False
        or payload.get("lifecycle_publication_allowed") is not False
    ):
        raise LocalDevelopmentBundleError("local development evidence boundary invalid")

    manifest_path = Path(payload["manifest_path"]).resolve()
    root = manifest_path.parent
    if root.stat().st_mode & 0o222 or manifest_path.stat().st_mode & 0o222:
        raise LocalDevelopmentBundleError("local development generation is mutable")
    if not _sha256_hex(str(payload.get("builder_semantic_hash") or "")):
        raise LocalDevelopmentBundleError("local development builder identity invalid")
    raw_artifacts = payload.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise LocalDevelopmentBundleError("local development artifacts invalid")
    artifacts: dict[str, dict[str, Any]] = {}
    normalized_rows: list[dict[str, Any]] = []
    relative_paths: set[str] = set()
    for value in raw_artifacts:
        if not isinstance(value, Mapping):
            raise LocalDevelopmentBundleError("local development artifact row invalid")
        row = dict(value)
        role = str(row.get("role") or "")
        relative = Path(str(row.get("relative_path") or ""))
        relative_text = relative.as_posix()
        if (
            not role
            or role in artifacts
            or not relative_text
            or relative_text in relative_paths
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise LocalDevelopmentBundleError("local development artifact identity invalid")
        lexical_path = root / relative
        artifact_path = lexical_path.resolve()
        if (
            not artifact_path.is_relative_to(root)
            or lexical_path.is_symlink()
            or not artifact_path.is_file()
            or bool(artifact_path.stat().st_mode & 0o222)
            or artifact_path.stat().st_size != _nonnegative_int(row.get("size_bytes"))
            or sha256_file(artifact_path) != row.get("sha256")
        ):
            raise LocalDevelopmentBundleError(f"local development artifact drift:{role}")
        artifacts[role] = row
        relative_paths.add(relative_text)
        normalized_rows.append(row)
    if set(artifacts) != _REQUIRED_ARTIFACT_ROLES:
        raise LocalDevelopmentBundleError("local development required artifacts missing")
    if normalized_rows != sorted(normalized_rows, key=lambda row: str(row["role"])):
        raise LocalDevelopmentBundleError("local development artifact order invalid")
    if payload.get("artifact_root") != canonical_hash(normalized_rows):
        raise LocalDevelopmentBundleError("local development artifact root invalid")
    _validate_artifact_closure(root, manifest_path, relative_paths)
    expected_source_partitions = _validate_embedded_source_evidence(
        root,
        artifacts,
        payload,
    )

    stocks = _read_axis(root, artifacts["stock_axis"])
    dates = _read_axis(root, artifacts["date_axis"])
    features = _read_axis(root, artifacts["feature_axis"])
    if (
        stocks != sorted(set(stocks))
        or dates != sorted(set(dates))
        or features != list(_ALPHA_FEATURE_FIELDS)
    ):
        raise LocalDevelopmentBundleError("local development axes invalid")
    scope = payload.get("scope") or {}
    if any(not _valid_date(date) for date in dates) or any(
        date < str(scope.get("date_start") or "") or date > str(scope.get("date_end") or "")
        for date in dates
    ):
        raise LocalDevelopmentBundleError("local development date scope invalid")

    _validate_derived_semantics(
        root,
        artifacts,
        bundle=payload,
        stocks=stocks,
        dates=dates,
        features=features,
        scope=scope,
        builder_semantic_hash=str(payload["builder_semantic_hash"]),
        expected_source_partitions=expected_source_partitions,
    )
    if trusted_source_context is not None:
        _validate_trusted_raw_observations(
            root,
            artifacts,
            payload,
            stocks,
            dates,
            trusted_source_context[1],
        )
    return payload


def _validate_trusted_raw_observations(
    root: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
    bundle: Mapping[str, Any],
    stocks: list[str],
    dates: list[str],
    search: Mapping[str, Any],
) -> None:
    """Replay source observations to authenticate field-to-array semantics."""

    failure = "local development trusted raw observation mismatch"
    try:
        scope_payload = bundle["scope"]
        scope = LocalDevelopmentScope(
            date_start=str(scope_payload["date_start"]),
            date_end=str(scope_payload["date_end"]),
            index_code=str(scope_payload["index_code"]),
        )
        view = PhysicalResearchDataView(search["manifest_path"])
        expected_values, expected_validity, expected_masks = _aligned_observations(
            view,
            stocks,
            dates,
            workers=1,
        )
    except (KeyError, TypeError, ValueError, SourceFreezeError, LocalDevelopmentBundleError) as exc:
        raise LocalDevelopmentBundleError(failure) from exc

    for name in _FIELD_SOURCES:
        observed_values = _load_array(root, artifacts[f"raw_{name}"], np.float32)
        observed_validity = _load_array(
            root,
            artifacts[f"raw_{name}_validity"],
            np.bool_,
        )
        if not np.array_equal(
            observed_values,
            expected_values[name].astype(np.float32, copy=False),
            equal_nan=True,
        ) or not np.array_equal(observed_validity, expected_validity[name]):
            raise LocalDevelopmentBundleError(failure)
    for role in _POSITION_MASK_ROLES:
        observed = _load_array(root, artifacts[role], np.bool_)
        if not np.array_equal(observed, expected_masks[role]):
            raise LocalDevelopmentBundleError(failure)

    trade_dates = _trade_date_axis(view, scope)
    if trade_dates != dates:
        raise LocalDevelopmentBundleError(failure)
    snapshots, _snapshot_report = _accepted_snapshots(view, scope, trade_dates)
    snapshot_union = sorted({code for members in snapshots.values() for code in members})
    if snapshot_union != stocks:
        raise LocalDevelopmentBundleError(failure)
    expected_membership, expected_known, expected_weight, _effective = (
        _membership_matrices(snapshots, stocks, dates)
    )
    for role, expected, dtype in (
        ("pit_universe_membership", expected_membership, np.bool_),
        ("membership_known", expected_known, np.bool_),
        ("membership_weight", expected_weight, np.float32),
    ):
        if not np.array_equal(
            _load_array(root, artifacts[role], dtype),
            expected,
            equal_nan=True,
        ):
            raise LocalDevelopmentBundleError(failure)

    eligible = expected_membership & expected_known
    feature_values = np.stack(
        [expected_values[name] for name in _ALPHA_FEATURE_FIELDS],
        axis=1,
    ).astype(np.float32, copy=False)
    feature_validity = np.stack(
        [expected_validity[name] & eligible for name in _ALPHA_FEATURE_FIELDS],
        axis=1,
    )
    feature_values = np.where(feature_validity, feature_values, 0.0).astype(
        np.float32,
        copy=False,
    )
    expected_target, expected_target_available = _observed_open_target(
        expected_values,
        expected_validity,
        expected_membership,
        expected_known,
    )
    if not np.array_equal(
        _load_array(root, artifacts["feature_values"], np.float32),
        feature_values,
        equal_nan=True,
    ) or not np.array_equal(
        _load_array(root, artifacts["feature_validity"], np.bool_),
        feature_validity,
    ) or not np.array_equal(
        _load_array(root, artifacts["target_values"], np.float32),
        expected_target,
        equal_nan=True,
    ) or not np.array_equal(
        _load_array(root, artifacts["target_availability"], np.bool_),
        expected_target_available,
    ):
        raise LocalDevelopmentBundleError(failure)


def _validate_source_and_search_view(
    source_freeze_manifest: str | Path,
) -> tuple[ReplaySourceDescriptor, dict[str, Any]]:
    source_path = Path(source_freeze_manifest).resolve()
    if not source_path.is_file() or source_path.is_symlink():
        raise LocalDevelopmentBundleError("source freeze manifest missing")
    try:
        source = read_json(source_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LocalDevelopmentBundleError("source freeze manifest invalid") from exc
    schema = str(source.get("schema_version") or "")
    if schema == SOURCE_FREEZE_SCHEMA:
        return _validate_new_source_adapter(source_path, source)
    if schema == LEGACY_SOURCE_FREEZE_SCHEMA:
        return _validate_legacy_source_adapter(source_path, source)
    raise LocalDevelopmentBundleError("source freeze schema unsupported")


def _compatible_current_bundle(
    output: Path,
    *,
    source: ReplaySourceDescriptor,
    search: Mapping[str, Any],
    scope: LocalDevelopmentScope,
    builder_semantic_hash: str,
    source_manifest_sha256: str,
    search_manifest_sha256: str,
    source_freeze_manifest: str | Path,
) -> dict[str, Any] | None:
    pointer_path = output / "current.json"
    if not pointer_path.is_file() or pointer_path.is_symlink():
        return None
    try:
        pointer = read_json(pointer_path)
        relative = Path(str(pointer.get("manifest") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            return None
        manifest_path = (output / relative).resolve()
        if not manifest_path.is_relative_to(output) or not manifest_path.is_file():
            return None
        validated = validate_local_development_bundle(
            manifest_path,
            trusted_source_freeze_manifest=source_freeze_manifest,
        )
    except (OSError, ValueError, json.JSONDecodeError, LocalDevelopmentBundleError):
        return None
    if (
        pointer.get("schema_version") != "local_development_bundle_pointer_v1"
        or pointer.get("generation_id") != validated.get("generation_id")
        or pointer.get("content_hash") != validated.get("content_hash")
        or pointer.get("mode") != "development_replay"
        or pointer.get("data_admission_eligible") is not False
        or validated.get("scope") != scope.to_dict()
        or validated.get("source_schema_version") != source.schema_version
        or validated.get("source_evidence_grade") != source.evidence_grade
        or validated.get("source_identity_kind") != source.identity_kind
        or validated.get("source_generation_id") != source.generation_id
        or validated.get("source_content_hash") != source.content_hash
        or validated.get("source_artifact_root") != source.source_artifact_root
        or validated.get("source_declared_partition_root")
        != source.declared_partition_root
        or validated.get("search_view_content_hash") != search.get("content_hash")
        or validated.get("search_partition_root") != search.get("partition_root")
        or validated.get("source_manifest_sha256") != source_manifest_sha256
        or validated.get("search_view_manifest_sha256") != search_manifest_sha256
        or validated.get("builder_semantic_hash") != builder_semantic_hash
    ):
        return None
    return validated


def _assert_source_identity_stable(
    source_freeze_manifest: str | Path,
    *,
    expected_source: ReplaySourceDescriptor,
    expected_search: Mapping[str, Any],
    expected_source_manifest_sha256: str,
    expected_search_manifest_sha256: str,
) -> None:
    observed_source, observed_search = _validate_source_and_search_view(
        source_freeze_manifest
    )
    if (
        observed_source != expected_source
        or observed_search.get("content_hash") != expected_search.get("content_hash")
        or observed_search.get("partition_root") != expected_search.get("partition_root")
        or observed_search.get("manifest_path") != expected_search.get("manifest_path")
        or sha256_file(Path(source_freeze_manifest).resolve())
        != expected_source_manifest_sha256
        or sha256_file(Path(str(observed_search["manifest_path"])).resolve())
        != expected_search_manifest_sha256
    ):
        raise LocalDevelopmentBundleError(
            "source freeze changed during local development materialization"
        )


def _reject_source_output_overlap(
    output_root: Path,
    *,
    source_manifest: Path,
    search_manifest: Path,
) -> None:
    protected_roots = (source_manifest.parent, search_manifest.parent)
    if any(
        output_root == protected
        or output_root in protected.parents
        or protected in output_root.parents
        for protected in protected_roots
    ):
        raise LocalDevelopmentBundleError(
            "local development output overlaps immutable source freeze"
        )


def _validate_new_source_adapter(
    source_path: Path,
    source: Mapping[str, Any],
) -> tuple[ReplaySourceDescriptor, dict[str, Any]]:
    try:
        validated_source = validate_source_freeze_generation(source_path)
    except (OSError, KeyError, ValueError, SourceFreezeError, json.JSONDecodeError) as exc:
        raise LocalDevelopmentBundleError(
            "source freeze full validation failed"
        ) from exc
    if validated_source.get("content_hash") != source.get("content_hash"):
        raise LocalDevelopmentBundleError("source freeze validation identity mismatch")
    try:
        core = {key: source[key] for key in _SOURCE_CORE_KEYS}
    except KeyError as exc:
        raise LocalDevelopmentBundleError("source freeze manifest invalid") from exc
    expected_id = f"ashare_source_freeze_{str(source.get('content_hash') or '')[:24]}"
    if (
        source.get("schema_version") != SOURCE_FREEZE_SCHEMA
        or canonical_hash(core) != source.get("content_hash")
        or source.get("generation_id") != expected_id
        or source_path.parent.name != expected_id
        or source_path.name != "source_freeze_manifest.json"
        or source.get("alpha_search_authorized") is not False
    ):
        raise LocalDevelopmentBundleError("source freeze identity invalid")
    search = dict(validated_source.get("search_view") or {})
    if (
        search.get("schema_version") != SOURCE_SEARCH_VIEW_SCHEMA
        or search.get("generation_id") != source["generation_id"]
        or search.get("freeze_content_hash") != source["content_hash"]
        or search.get("partition_root") != source.get("search_partition_root")
        or search.get("source_semantic_hash") != source.get("source_semantic_hash")
        or not search.get("manifest_path")
    ):
        raise LocalDevelopmentBundleError("source freeze search lineage invalid")
    descriptor = ReplaySourceDescriptor(
        schema_version=SOURCE_FREEZE_SCHEMA,
        evidence_grade="source_freeze_bound",
        identity_kind="source_artifact_root_v1",
        generation_id=str(source["generation_id"]),
        content_hash=str(source["content_hash"]),
        source_artifact_root=str(source["source_artifact_root"]),
        declared_partition_root=str(source["partition_root"]),
        declared_blockers=tuple(sorted(str(item) for item in source.get("blockers") or [])),
        provenance_blockers=_SOURCE_PROVENANCE_BLOCKERS["source_freeze_bound"],
    )
    return descriptor, search


def _validate_legacy_source_adapter(
    source_path: Path,
    source: Mapping[str, Any],
) -> tuple[ReplaySourceDescriptor, dict[str, Any]]:
    try:
        core = {key: source[key] for key in _LEGACY_SOURCE_CORE_KEYS}
    except KeyError as exc:
        raise LocalDevelopmentBundleError("legacy source freeze manifest invalid") from exc
    content_hash = str(source.get("content_hash") or "")
    expected_id = f"ashare_freeze_{content_hash[:24]}"
    if (
        source.get("schema_version") != LEGACY_SOURCE_FREEZE_SCHEMA
        or canonical_hash(core) != content_hash
        or source.get("generation_id") != expected_id
        or source_path.parent.name != expected_id
        or source_path.name != "canonical_freeze_manifest.json"
        or source.get("alpha_search_authorized") is not False
    ):
        raise LocalDevelopmentBundleError("legacy source freeze identity invalid")
    search = _validate_bound_search_view(
        source_path,
        source,
        expected_schema=LEGACY_SEARCH_VIEW_SCHEMA,
    )
    legacy_identity = canonical_hash(
        {
            "schema_version": "legacy_replay_source_identity_v1",
            "legacy_source_content_hash": content_hash,
            "declared_partition_root": source.get("partition_root"),
            "verified_search_partition_root": search.get("partition_root"),
        }
    )
    descriptor = ReplaySourceDescriptor(
        schema_version=LEGACY_SOURCE_FREEZE_SCHEMA,
        evidence_grade="legacy_unproven",
        identity_kind="legacy_partition_roots_v1",
        generation_id=str(source["generation_id"]),
        content_hash=content_hash,
        source_artifact_root=legacy_identity,
        declared_partition_root=str(source["partition_root"]),
        declared_blockers=tuple(sorted(str(item) for item in source.get("blockers") or [])),
        provenance_blockers=_SOURCE_PROVENANCE_BLOCKERS["legacy_unproven"],
    )
    return descriptor, search


def _validate_bound_search_view(
    source_path: Path,
    source: Mapping[str, Any],
    *,
    expected_schema: str,
) -> dict[str, Any]:
    search_path = source_path.parent / "search_view" / "research_view_manifest.json"
    if (
        not search_path.is_file()
        or search_path.is_symlink()
        or sha256_file(search_path) != source.get("search_view_manifest_sha256")
    ):
        raise LocalDevelopmentBundleError("source freeze search view reference invalid")
    try:
        declared_search = read_json(search_path)
        if declared_search.get("schema_version") != expected_schema:
            raise LocalDevelopmentBundleError("source freeze search view schema invalid")
        search = validate_physical_research_view(search_path)
    except (OSError, ValueError, SourceFreezeError, json.JSONDecodeError) as exc:
        raise LocalDevelopmentBundleError("source freeze search view invalid") from exc
    if (
        search.get("generation_id") != source["generation_id"]
        or search.get("freeze_content_hash") != source["content_hash"]
        or search.get("partition_root") != source.get("search_partition_root")
        or search.get("source_semantic_hash") != source.get("source_semantic_hash")
    ):
        raise LocalDevelopmentBundleError("source freeze search lineage invalid")
    return search


def _trade_date_axis(
    view: PhysicalResearchDataView,
    scope: LocalDevelopmentScope,
) -> list[str]:
    dates: set[str] = set()
    observed: dict[str, bool] = {}
    try:
        records = view.iter_observable_records("trade_calendar")
        for row in records:
            date = str(row.get("trade_date") or "")
            if date < scope.date_start or date > scope.date_end:
                continue
            if not _valid_date(date):
                raise LocalDevelopmentBundleError("trade calendar date invalid")
            raw_open = row.get("is_open")
            if raw_open in {True, 1, "1"}:
                is_open = True
            elif raw_open in {False, 0, "0"}:
                is_open = False
            else:
                raise LocalDevelopmentBundleError(
                    "trade calendar open flag invalid"
                )
            if date in observed:
                raise LocalDevelopmentBundleError(
                    "trade calendar duplicate date"
                )
            observed[date] = is_open
            if is_open:
                dates.add(date)
    except SourceFreezeError as exc:
        raise LocalDevelopmentBundleError("trade calendar unavailable in search view") from exc
    return sorted(dates)


def _accepted_snapshots(
    view: PhysicalResearchDataView,
    scope: LocalDevelopmentScope,
    trade_dates: list[str],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    rows_by_date: dict[str, dict[str, float]] = {}
    duplicate_dates: set[str] = set()
    try:
        records = view.iter_observable_records("index_members")
        for row in records:
            if str(row.get("index_code") or "") != scope.index_code:
                continue
            date = str(row.get("trade_date") or "")
            code = str(row.get("ts_code") or "")
            weight = _finite_float(row.get("weight"))
            if date > scope.date_end or not code or weight is None:
                continue
            snapshot = rows_by_date.setdefault(date, {})
            if code in snapshot:
                duplicate_dates.add(date)
            else:
                snapshot[code] = weight
    except SourceFreezeError as exc:
        raise LocalDevelopmentBundleError("index members unavailable in search view") from exc

    open_dates = set(trade_dates)
    accepted: dict[str, dict[str, float]] = {}
    rejected: dict[str, list[str]] = {}
    for date, snapshot in sorted(rows_by_date.items()):
        reasons: list[str] = []
        weight_sum = math.fsum(snapshot.values())
        if date < "20160101":
            reasons.append("pre_2016_membership_unproven")
        if date >= scope.date_start and date not in open_dates:
            reasons.append("snapshot_date_not_open")
        if date in duplicate_dates:
            reasons.append("duplicate_member")
        if len(snapshot) != 300:
            reasons.append("member_count_not_300")
        if any(weight <= 0 for weight in snapshot.values()):
            reasons.append("member_weight_not_positive")
        if not 99.5 <= weight_sum <= 100.5:
            reasons.append("weight_sum_out_of_range")
        if reasons:
            rejected[date] = sorted(set(reasons))
        else:
            accepted[date] = snapshot
    seed_dates = [date for date in accepted if date < scope.date_start]
    seed_date = max(seed_dates) if seed_dates else None
    retained = {
        date: snapshot
        for date, snapshot in accepted.items()
        if date >= scope.date_start or date == seed_date
    }
    return retained, {
        "observed_snapshot_count": len(rows_by_date),
        "accepted_snapshot_count": len(retained),
        "rejected_snapshot_count": len(rejected),
        "rejected_snapshots": rejected,
        "pre_scope_seed_snapshot_date": seed_date,
        "membership_evidence_grade": "retrospective_proxy_unproven_publication_time",
        "max_staleness_calendar_days": MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS,
    }


def _membership_matrices(
    snapshots: Mapping[str, Mapping[str, float]],
    ts_codes: list[str],
    trade_dates: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
    shape = (len(ts_codes), len(trade_dates))
    membership = np.zeros(shape, dtype=np.bool_)
    known = np.zeros(shape, dtype=np.bool_)
    weights = np.zeros(shape, dtype=np.float32)
    stock_index = {code: index for index, code in enumerate(ts_codes)}
    effective: list[tuple[int, str]] = []
    report: list[dict[str, Any]] = []
    for snapshot_date in sorted(snapshots):
        position = bisect.bisect_right(trade_dates, snapshot_date)
        effective_date = trade_dates[position] if position < len(trade_dates) else None
        report.append(
            {
                "snapshot_date": snapshot_date,
                "effective_trade_date": effective_date,
                "member_count": len(snapshots[snapshot_date]),
                "is_pre_scope_seed": bool(
                    trade_dates and snapshot_date < trade_dates[0]
                ),
            }
        )
        if effective_date is not None:
            effective.append((position, snapshot_date))
    pointer = -1
    for date_position, trade_date in enumerate(trade_dates):
        while pointer + 1 < len(effective) and effective[pointer + 1][0] <= date_position:
            pointer += 1
        if trade_date < "20160101" or pointer < 0:
            continue
        source_date = effective[pointer][1]
        age_days = (
            datetime.strptime(trade_date, "%Y%m%d")
            - datetime.strptime(source_date, "%Y%m%d")
        ).days
        if age_days > MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS:
            continue
        snapshot = snapshots[source_date]
        known[:, date_position] = True
        for code, weight in snapshot.items():
            stock_position = stock_index[code]
            membership[stock_position, date_position] = True
            weights[stock_position, date_position] = np.float32(weight / 100.0)
    return membership, known, weights, report


def _membership_snapshot_evidence(
    snapshots: Mapping[str, Mapping[str, float]],
    scope: LocalDevelopmentScope,
    snapshot_report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "local_development_membership_snapshots_v1",
        "index_code": scope.index_code,
        "effective_rule": "next_open_trade_day",
        "max_staleness_calendar_days": MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS,
        "publication_time_proven": False,
        "snapshot_report": dict(snapshot_report),
        "snapshots": [
            {
                "snapshot_date": snapshot_date,
                "members": [
                    {"ts_code": code, "weight": float(members[code])}
                    for code in sorted(members)
                ],
            }
            for snapshot_date, members in sorted(snapshots.items())
        ],
    }


def _aligned_observations(
    view: PhysicalResearchDataView,
    ts_codes: list[str],
    trade_dates: list[str],
    *,
    workers: int,
) -> tuple[
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
]:
    shape = (len(ts_codes), len(trade_dates))
    stock_index = {code: index for index, code in enumerate(ts_codes)}
    date_index = {date: index for index, date in enumerate(trade_dates)}
    values: dict[str, np.ndarray] = {}
    validity: dict[str, np.ndarray] = {}
    position_masks: dict[str, np.ndarray] = {}
    fields_by_dataset: dict[str, list[str]] = {}
    for name, (dataset, _aliases, _rule) in _FIELD_SOURCES.items():
        fields_by_dataset.setdefault(dataset, []).append(name)
    datasets = sorted(fields_by_dataset)
    max_workers = min(int(workers), len(datasets))
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                dataset: executor.submit(
                    _align_daily_dataset,
                    view,
                    dataset,
                    tuple(fields_by_dataset[dataset]),
                    shape,
                    stock_index,
                    date_index,
                    ts_codes,
                    trade_dates,
                )
                for dataset in datasets
            }
            aligned_by_dataset = {
                dataset: futures[dataset].result() for dataset in datasets
            }
    else:
        aligned_by_dataset = {
            dataset: _align_daily_dataset(
                view,
                dataset,
                tuple(fields_by_dataset[dataset]),
                shape,
                stock_index,
                date_index,
                ts_codes,
                trade_dates,
            )
            for dataset in datasets
        }

    for dataset in datasets:
        (
            dataset_values,
            dataset_validity,
            dataset_report,
            dataset_observed,
            dataset_duplicate,
            dataset_diagnostics,
        ) = (
            aligned_by_dataset[dataset]
        )
        values.update(dataset_values)
        validity.update(dataset_validity)
        del dataset_report
        position_masks[f"{dataset}_observed_positions"] = dataset_observed
        position_masks[f"{dataset}_duplicate_positions"] = dataset_duplicate
        position_masks.update(dataset_diagnostics)
    comparable = validity["pre_close"] & validity["limit_pre_close"]
    mismatch = comparable & ~np.isclose(
        values["pre_close"],
        values["limit_pre_close"],
        rtol=0.0,
        atol=1e-5,
    )
    position_masks["cross_source_pre_close_mismatch_positions"] = mismatch.copy()
    for name in ("up_limit", "down_limit", "limit_pre_close"):
        values[name][mismatch] = 0.0
        validity[name][mismatch] = False
    missing_roles = set(_POSITION_MASK_ROLES) - set(position_masks)
    if missing_roles:
        raise LocalDevelopmentBundleError(
            "local development diagnostic masks incomplete"
        )
    return values, validity, position_masks


def _align_daily_dataset(
    view: PhysicalResearchDataView,
    dataset: str,
    fields: tuple[str, ...],
    shape: tuple[int, int],
    stock_index: Mapping[str, int],
    date_index: Mapping[str, int],
    ts_codes: list[str],
    trade_dates: list[str],
) -> tuple[
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict[str, Any],
    np.ndarray,
    np.ndarray,
    dict[str, np.ndarray],
]:
    values = {name: np.zeros(shape, dtype=np.float32) for name in fields}
    validity = {name: np.zeros(shape, dtype=np.bool_) for name in fields}
    position_state = np.zeros(shape, dtype=np.uint8)
    duplicate_positions = np.zeros(shape, dtype=np.bool_)
    aligned = 0
    observed = np.zeros(shape, dtype=np.bool_)
    diagnostic_masks: dict[str, np.ndarray] = {}
    if dataset == "daily_limits":
        diagnostic_masks = {
            "limit_required_field_unusable_positions": np.zeros(
                shape, dtype=np.bool_
            ),
            "positive_limit_order_violation_positions": np.zeros(
                shape, dtype=np.bool_
            ),
        }
    try:
        records = _iter_scoped_daily_records(
            view,
            dataset,
            ts_codes=ts_codes,
            trade_dates=trade_dates,
        )
        for row in records:
            position = (
                stock_index.get(str(row.get("ts_code") or "")),
                date_index.get(str(row.get("trade_date") or "")),
            )
            if position[0] is None or position[1] is None:
                continue
            typed_position = (int(position[0]), int(position[1]))
            if position_state[typed_position] == 2:
                continue
            if position_state[typed_position] == 1:
                position_state[typed_position] = 2
                duplicate_positions[typed_position] = True
                for name in fields:
                    values[name][typed_position] = 0.0
                    validity[name][typed_position] = False
                for diagnostic in diagnostic_masks.values():
                    diagnostic[typed_position] = False
                continue
            position_state[typed_position] = 1
            observed[typed_position] = True
            aligned += 1
            for name in fields:
                _set_observed_field(
                    values[name],
                    validity[name],
                    typed_position,
                    row,
                    name,
                )
            if dataset == "daily_bars":
                _apply_ohlc_consistency(values, validity, typed_position)
            elif dataset == "daily_limits":
                reason = _apply_limit_consistency(
                    values,
                    validity,
                    typed_position,
                )
                if reason is not None:
                    diagnostic_masks[f"{reason}_positions"][typed_position] = True
    except (OSError, ValueError, SourceFreezeError, pa.ArrowException) as exc:
        raise LocalDevelopmentBundleError(
            f"required local development dataset unavailable:{dataset}"
        ) from exc
    return (
        values,
        validity,
        {
            "status": "observed",
            "unique_aligned_position_count": aligned,
            "duplicate_position_count": int(np.count_nonzero(duplicate_positions)),
            "valid_cell_counts": {
                name: int(np.count_nonzero(validity[name])) for name in fields
            },
        },
        observed,
        duplicate_positions,
        diagnostic_masks,
    )


def _iter_scoped_daily_records(
    view: PhysicalResearchDataView,
    dataset: str,
    *,
    ts_codes: list[str],
    trade_dates: list[str],
) -> Iterable[dict[str, Any]]:
    code_set = pa.array(ts_codes, type=pa.string())
    date_set = pa.array(trade_dates, type=pa.string())
    for path in view.dataset_partitions(dataset):
        parquet = pq.ParquetFile(path)
        required = {"ts_code", "trade_date", "observable_json"}
        if not required <= set(parquet.schema_arrow.names):
            raise SourceFreezeError(
                f"research daily envelope fields missing: {dataset}"
            )
        for batch in parquet.iter_batches(
            columns=["ts_code", "trade_date", "observable_json"],
            batch_size=65_536,
            use_threads=False,
        ):
            code_mask = pc.is_in(batch.column("ts_code"), value_set=code_set)
            date_mask = pc.is_in(batch.column("trade_date"), value_set=date_set)
            selected = batch.filter(pc.and_(code_mask, date_mask))
            for raw in selected.column("observable_json").to_pylist():
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise SourceFreezeError(
                        "research observable payload is not an object"
                    )
                yield payload


def _set_observed_field(
    values: np.ndarray,
    validity: np.ndarray,
    position: tuple[int, int],
    row: Mapping[str, Any],
    name: str,
) -> None:
    _dataset, aliases, rule = _FIELD_SOURCES[name]
    raw = next((row.get(alias) for alias in aliases if alias in row), None)
    number = _finite_float(raw)
    valid = number is not None and (
        number > 0 if rule == "positive" else number >= 0
    )
    if valid:
        values[position] = np.float32(number)
        validity[position] = True


def _apply_ohlc_consistency(
    values: Mapping[str, np.ndarray],
    validity: Mapping[str, np.ndarray],
    position: tuple[int, int],
) -> None:
    required = ("open", "high", "low", "close")
    if not all(validity[name][position] for name in required):
        return
    open_value, high, low, close = (float(values[name][position]) for name in required)
    if high < max(open_value, close) or low > min(open_value, close) or high < low:
        for name in required:
            values[name][position] = 0.0
            validity[name][position] = False


def _apply_limit_consistency(
    values: Mapping[str, np.ndarray],
    validity: Mapping[str, np.ndarray],
    position: tuple[int, int],
) -> str | None:
    fields = ("up_limit", "down_limit", "limit_pre_close")
    all_usable = all(validity[name][position] for name in fields)
    valid = all_usable
    if all_usable:
        up_limit, down_limit, pre_close = (
            float(values[name][position]) for name in fields
        )
        valid = down_limit <= pre_close <= up_limit
    if not valid:
        for name in fields:
            values[name][position] = 0.0
            validity[name][position] = False
    if valid:
        return None
    return (
        "positive_limit_order_violation"
        if all_usable
        else "limit_required_field_unusable"
    )


def _observed_open_target(
    raw_values: Mapping[str, np.ndarray],
    raw_validity: Mapping[str, np.ndarray],
    membership: np.ndarray,
    membership_known: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    target = np.full(membership.shape, np.nan, dtype=np.float32)
    available = np.zeros(membership.shape, dtype=np.bool_)
    if membership.shape[1] < 3:
        return target, available
    adjusted_open = raw_values["open"] * raw_values["adj_factor"]
    adjusted_valid = raw_validity["open"] & raw_validity["adj_factor"] & np.isfinite(
        adjusted_open
    ) & (adjusted_open > 0)
    valid = (
        membership[:, :-2]
        & membership_known[:, :-2]
        & adjusted_valid[:, 1:-1]
        & adjusted_valid[:, 2:]
        & raw_validity["up_limit"][:, 1:-1]
        & raw_validity["down_limit"][:, 1:-1]
        & raw_validity["up_limit"][:, 2:]
        & raw_validity["down_limit"][:, 2:]
        & (raw_values["open"][:, 1:-1] >= raw_values["down_limit"][:, 1:-1])
        & (raw_values["open"][:, 1:-1] < raw_values["up_limit"][:, 1:-1])
        & (raw_values["open"][:, 2:] > raw_values["down_limit"][:, 2:])
        & (raw_values["open"][:, 2:] <= raw_values["up_limit"][:, 2:])
    )
    computed = np.full(valid.shape, np.nan, dtype=np.float32)
    np.divide(adjusted_open[:, 2:], adjusted_open[:, 1:-1], out=computed, where=valid)
    computed[valid] -= np.float32(1.0)
    valid &= np.isfinite(computed)
    target[:, :-2] = computed
    available[:, :-2] = valid
    target[~available] = np.nan
    return target, available


def _target_contract() -> dict[str, Any]:
    return {
        "schema_version": "local_development_target_contract_v1",
        "name": TARGET_NAME,
        "signal_date": "t",
        "entry_price": "observed_open[t+1]*observed_adj_factor[t+1]",
        "exit_price": "observed_open[t+2]*observed_adj_factor[t+2]",
        "formula": "adjusted_open[t+2]/adjusted_open[t+1]-1",
        "execution_semantics": "retrospective_observed_proxy",
        "observed_price_band_proxy_checked": True,
        "legal_price_band_proven": False,
        "st_status_proven": False,
        "suspension_state_proven": False,
    }


def _target_attrition_report(
    raw_values: Mapping[str, np.ndarray],
    raw_validity: Mapping[str, np.ndarray],
    membership: np.ndarray,
    membership_known: np.ndarray,
) -> dict[str, Any]:
    if membership.shape[1] < 3:
        return {
            "schema_version": "local_development_target_attrition_v1",
            "mode": "ordered_incremental",
            "attrition_order": [],
            "horizon_eligible_proxy_member_signal_count": 0,
            "ordered_incremental_attrition_counts": {},
            "observed_proxy_target_available_cell_count": 0,
            "available_rate": 0.0,
        }
    adjusted_open = raw_values["open"] * raw_values["adj_factor"]
    adjusted_valid = (
        raw_validity["open"]
        & raw_validity["adj_factor"]
        & np.isfinite(adjusted_open)
        & (adjusted_open > 0)
    )
    base = membership[:, :-2] & membership_known[:, :-2]
    entry_adjusted = base & adjusted_valid[:, 1:-1]
    exit_adjusted = entry_adjusted & adjusted_valid[:, 2:]
    price_band_evidence = (
        exit_adjusted
        & raw_validity["up_limit"][:, 1:-1]
        & raw_validity["down_limit"][:, 1:-1]
        & raw_validity["up_limit"][:, 2:]
        & raw_validity["down_limit"][:, 2:]
    )
    legal_entry = (
        price_band_evidence
        & (raw_values["open"][:, 1:-1] >= raw_values["down_limit"][:, 1:-1])
        & (raw_values["open"][:, 1:-1] <= raw_values["up_limit"][:, 1:-1])
    )
    legal_exit = (
        legal_entry
        & (raw_values["open"][:, 2:] >= raw_values["down_limit"][:, 2:])
        & (raw_values["open"][:, 2:] <= raw_values["up_limit"][:, 2:])
    )
    executable_entry = legal_exit & (
        raw_values["open"][:, 1:-1] < raw_values["up_limit"][:, 1:-1]
    )
    executable_exit = executable_entry & (
        raw_values["open"][:, 2:] > raw_values["down_limit"][:, 2:]
    )
    computed = np.full(executable_exit.shape, np.nan, dtype=np.float32)
    np.divide(
        adjusted_open[:, 2:],
        adjusted_open[:, 1:-1],
        out=computed,
        where=executable_exit,
    )
    computed[executable_exit] -= np.float32(1.0)
    finite_return = executable_exit & np.isfinite(computed)

    def count(mask: np.ndarray) -> int:
        return int(np.count_nonzero(mask))

    base_count = count(base)
    available_count = count(finite_return)
    ordered_counts = {
        "missing_entry_adjusted_open": base_count - count(entry_adjusted),
        "missing_exit_adjusted_open": count(entry_adjusted) - count(exit_adjusted),
        "missing_observed_price_band_proxy_evidence": count(exit_adjusted)
        - count(price_band_evidence),
        "entry_open_outside_observed_price_band_proxy": count(price_band_evidence)
        - count(legal_entry),
        "exit_open_outside_observed_price_band_proxy": count(legal_entry)
        - count(legal_exit),
        "entry_open_at_observed_up_limit_proxy": count(legal_exit)
        - count(executable_entry),
        "exit_open_at_observed_down_limit_proxy": count(executable_entry)
        - count(executable_exit),
        "nonfinite_observed_proxy_return": count(executable_exit) - available_count,
    }
    return {
        "schema_version": "local_development_target_attrition_v1",
        "mode": "ordered_incremental",
        "attrition_order": list(ordered_counts),
        "horizon_eligible_proxy_member_signal_count": base_count,
        "ordered_incremental_attrition_counts": ordered_counts,
        "observed_proxy_target_available_cell_count": available_count,
        "available_rate": available_count / base_count if base_count else 0.0,
    }


def _build_quality_and_reconciliation(
    *,
    scope: LocalDevelopmentScope,
    trade_dates: list[str],
    snapshot_report: Mapping[str, Any],
    effective_snapshots: list[dict[str, Any]],
    raw_values: Mapping[str, np.ndarray],
    raw_validity: Mapping[str, np.ndarray],
    membership: np.ndarray,
    membership_known: np.ndarray,
    feature_names: list[str],
    feature_validity: np.ndarray,
    target_available: np.ndarray,
    position_masks: Mapping[str, np.ndarray],
) -> tuple[dict[str, Any], dict[str, Any]]:
    shape = membership.shape
    if (
        membership_known.shape != shape
        or target_available.shape != shape
        or feature_validity.shape != (shape[0], len(feature_names), shape[1])
        or len(trade_dates) != shape[1]
        or set(position_masks) != set(_POSITION_MASK_ROLES)
        or any(mask.dtype != np.bool_ or mask.shape != shape for mask in position_masks.values())
    ):
        raise LocalDevelopmentBundleError("local development quality inputs invalid")
    known_any = np.any(membership_known, axis=0)
    known_all = np.all(membership_known, axis=0)
    if not np.array_equal(known_any, known_all):
        raise LocalDevelopmentBundleError(
            "proxy membership knowledge must cover complete date columns"
        )
    if np.any(membership & ~membership_known):
        raise LocalDevelopmentBundleError(
            "proxy membership exists without complete-column knowledge"
        )

    fields_by_dataset: dict[str, list[str]] = {}
    for name, (dataset, _aliases, _rule) in _FIELD_SOURCES.items():
        fields_by_dataset.setdefault(dataset, []).append(name)
    alignment_counts = {
        dataset: {
            "scoped_observed_position_count": int(
                np.count_nonzero(position_masks[f"{dataset}_observed_positions"])
            ),
            "duplicate_position_count": int(
                np.count_nonzero(position_masks[f"{dataset}_duplicate_positions"])
            ),
            "valid_field_cell_counts": {
                name: int(np.count_nonzero(raw_validity[name]))
                for name in fields_by_dataset[dataset]
            },
        }
        for dataset in sorted(fields_by_dataset)
    }
    daily_bar_observed = position_masks["daily_bars_observed_positions"]
    daily_limit_observed = position_masks["daily_limits_observed_positions"]
    mismatch = position_masks["cross_source_pre_close_mismatch_positions"]
    comparable_after_reconciliation = (
        raw_validity["pre_close"] & raw_validity["limit_pre_close"]
    )
    limit_usable = np.logical_and.reduce(
        [
            raw_validity["up_limit"],
            raw_validity["down_limit"],
            raw_validity["limit_pre_close"],
        ]
    )
    limit_alignment_counts = {
        "scoped_limit_position_count": int(np.count_nonzero(daily_limit_observed)),
        "limit_position_without_observed_daily_bar_count": int(
            np.count_nonzero(daily_limit_observed & ~daily_bar_observed)
        ),
        "usable_limit_position_after_reconciliation_on_observed_daily_bar_count": int(
            np.count_nonzero(limit_usable & daily_bar_observed)
        ),
        "comparable_pre_close_position_count": int(
            np.count_nonzero(comparable_after_reconciliation | mismatch)
        ),
    }
    limit_anomaly_counts = {
        "limit_required_field_unusable": int(
            np.count_nonzero(
                position_masks["limit_required_field_unusable_positions"]
            )
        ),
        "positive_limit_order_violation": int(
            np.count_nonzero(
                position_masks["positive_limit_order_violation_positions"]
            )
        ),
        "cross_source_pre_close_mismatch": int(np.count_nonzero(mismatch)),
    }

    proxy_unknown = ~membership_known
    unknown_breakdown = _membership_unknown_breakdown(
        trade_dates,
        membership_known,
        effective_snapshots,
    )
    all_alpha_features_valid = np.all(feature_validity, axis=1)
    positive_proxy_membership_cell_count = int(np.count_nonzero(membership))
    alpha_feature_possible_slot_count = (
        positive_proxy_membership_cell_count * len(feature_names)
    )
    target_attrition = _target_attrition_report(
        raw_values,
        raw_validity,
        membership,
        membership_known,
    )
    available_count = int(np.count_nonzero(target_available))
    if (
        target_attrition["observed_proxy_target_available_cell_count"]
        != available_count
    ):
        raise LocalDevelopmentBundleError(
            "local development target attrition does not close"
        )
    quality = {
        "schema_version": "local_development_quality_v1",
        "scope": scope.to_dict(),
        "shape": [shape[0], shape[1]],
        "snapshot_report": dict(snapshot_report),
        "effective_snapshots": effective_snapshots,
        "alignment_counts": alignment_counts,
        "limit_alignment_counts": limit_alignment_counts,
        "limit_anomaly_counts": limit_anomaly_counts,
        "proxy_membership_known_cell_count": int(np.count_nonzero(membership_known)),
        "proxy_membership_unknown_cell_count_total": int(np.count_nonzero(proxy_unknown)),
        "proxy_membership_known_date_count": int(np.count_nonzero(known_all)),
        "proxy_membership_unknown_date_count": int(np.count_nonzero(~known_any)),
        "proxy_membership_partial_known_date_count": 0,
        "proxy_membership_unknown_breakdown": unknown_breakdown,
        "positive_proxy_membership_cell_count": positive_proxy_membership_cell_count,
        "effective_snapshot_count": sum(
            row["effective_trade_date"] is not None for row in effective_snapshots
        ),
        "alpha_feature_count": len(feature_names),
        "alpha_feature_possible_slot_count": alpha_feature_possible_slot_count,
        "alpha_feature_valid_slot_count": int(np.count_nonzero(feature_validity)),
        "alpha_feature_valid_slot_rate": (
            float(np.count_nonzero(feature_validity))
            / alpha_feature_possible_slot_count
            if alpha_feature_possible_slot_count
            else 0.0
        ),
        "all_alpha_features_valid_cell_count": int(
            np.count_nonzero(all_alpha_features_valid)
        ),
        "observed_proxy_target_available_cell_count": available_count,
        "target_and_all_alpha_features_valid_cell_count": int(
            np.count_nonzero(target_available & all_alpha_features_valid)
        ),
        "observed_proxy_target_attrition": target_attrition,
        "historical_union_axis_complete_before_2016": False,
        "evidence_flags": dict(EVIDENCE_FLAGS),
    }
    reconciliation = _reconciliation_report(
        raw_validity,
        membership,
        membership_known,
        position_masks,
    )
    return quality, reconciliation


def _membership_unknown_breakdown(
    trade_dates: list[str],
    membership_known: np.ndarray,
    effective_snapshots: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    unknown_dates = ~np.any(membership_known, axis=0)
    categories = {
        "pre_2016_on_post_2016_union_axis": [],
        "post_2015_before_first_effective_snapshot": [],
        "post_2015_stale_snapshot": [],
    }
    effective = [
        (str(row["effective_trade_date"]), str(row["snapshot_date"]))
        for row in effective_snapshots
        if row.get("effective_trade_date") is not None
    ]
    effective.sort()
    pointer = -1
    for index, date in enumerate(trade_dates):
        while pointer + 1 < len(effective) and effective[pointer + 1][0] <= date:
            pointer += 1
        if not unknown_dates[index]:
            continue
        if date < "20160101":
            categories["pre_2016_on_post_2016_union_axis"].append(index)
        elif pointer < 0:
            categories["post_2015_before_first_effective_snapshot"].append(index)
        else:
            age_days = (
                datetime.strptime(date, "%Y%m%d")
                - datetime.strptime(effective[pointer][1], "%Y%m%d")
            ).days
            if age_days <= MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS:
                raise LocalDevelopmentBundleError(
                    "proxy membership unknown reason is unexplained"
                )
            categories["post_2015_stale_snapshot"].append(index)
    return {
        reason: {
            "date_count": len(positions),
            "cell_count": int(np.count_nonzero(~membership_known[:, positions]))
            if positions
            else 0,
        }
        for reason, positions in categories.items()
    }


def _reconciliation_report(
    validity: Mapping[str, np.ndarray],
    membership: np.ndarray,
    membership_known: np.ndarray,
    position_masks: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    eligible = membership & membership_known
    observed_bar = position_masks["daily_bars_observed_positions"]
    valid_open_close_bar = validity["open"] & validity["close"]
    field_rows = []
    for dataset, fields in (
        ("adjustment_factors", ("adj_factor",)),
        ("daily_basic", ("turnover_rate", "volume_ratio", "total_mv")),
        ("daily_limits", ("up_limit", "down_limit", "limit_pre_close")),
    ):
        for field in fields:
            mask = validity[field]
            field_rows.append(
                {
                    "left": "daily_bars",
                    "right_dataset": dataset,
                    "right_field": field,
                    "shared_proxy_member_valid_open_close_bar_cell_count": int(
                        np.count_nonzero(eligible & valid_open_close_bar & mask)
                    ),
                    "missing_right_field_on_proxy_member_valid_open_close_bar_count": int(
                        np.count_nonzero(eligible & valid_open_close_bar & ~mask)
                    ),
                }
            )
    return {
        "schema_version": "local_development_reconciliation_v1",
        "mode": "development_replay",
        "positive_proxy_member_cell_count": int(np.count_nonzero(eligible)),
        "proxy_member_observed_daily_bar_position_count": int(
            np.count_nonzero(eligible & observed_bar)
        ),
        "missing_observed_daily_bar_position_on_proxy_member_count": int(
            np.count_nonzero(eligible & ~observed_bar)
        ),
        "proxy_member_valid_open_close_bar_cell_count": int(
            np.count_nonzero(eligible & valid_open_close_bar)
        ),
        "observed_daily_bar_but_invalid_open_close_on_proxy_member_count": int(
            np.count_nonzero(eligible & observed_bar & ~valid_open_close_bar)
        ),
        "field_reconciliation": field_rows,
        "coverage_claimed": False,
    }


def _validate_artifact_closure(
    root: Path,
    manifest_path: Path,
    relative_paths: set[str],
) -> None:
    expected_files = {MANIFEST_NAME, *relative_paths}
    expected_directories: set[str] = set()
    for relative in relative_paths:
        parent = Path(relative).parent
        while parent != Path("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise LocalDevelopmentBundleError(
                "local development generation contains a symlink"
            )
        if path.is_file():
            observed_files.add(relative)
            if path.stat().st_mode & 0o222:
                raise LocalDevelopmentBundleError(
                    "local development generation is mutable"
                )
        elif path.is_dir():
            observed_directories.add(relative)
            if path.stat().st_mode & 0o222:
                raise LocalDevelopmentBundleError(
                    "local development generation is mutable"
                )
        else:
            raise LocalDevelopmentBundleError(
                "local development generation contains a special file"
            )
    if (
        manifest_path.name != MANIFEST_NAME
        or observed_files != expected_files
        or observed_directories != expected_directories
    ):
        raise LocalDevelopmentBundleError(
            "local development artifact closure invalid"
        )


def _validate_derived_semantics(
    root: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    bundle: Mapping[str, Any],
    stocks: list[str],
    dates: list[str],
    features: list[str],
    scope: Mapping[str, Any],
    builder_semantic_hash: str,
    expected_source_partitions: list[dict[str, Any]],
) -> None:
    failure = "local development derived semantics invalid"
    try:
        typed_scope = LocalDevelopmentScope(
            date_start=str(scope["date_start"]),
            date_end=str(scope["date_end"]),
            index_code=str(scope["index_code"]),
        )
        snapshots, snapshot_report = _read_membership_snapshots(
            root,
            artifacts["membership_snapshots"],
            typed_scope,
            dates,
        )
    except (KeyError, TypeError, ValueError, LocalDevelopmentBundleError) as exc:
        raise LocalDevelopmentBundleError(failure) from exc
    snapshot_union = sorted(
        {code for members in snapshots.values() for code in members}
    )
    if snapshot_union != stocks:
        raise LocalDevelopmentBundleError(failure)
    expected_membership, expected_known, expected_weight, effective_snapshots = (
        _membership_matrices(snapshots, stocks, dates)
    )
    matrix_shape = (len(stocks), len(dates))
    feature_shape = (len(stocks), len(features), len(dates))
    membership = _load_array(root, artifacts["pit_universe_membership"], np.bool_)
    membership_known = _load_array(root, artifacts["membership_known"], np.bool_)
    membership_weight = _load_array(root, artifacts["membership_weight"], np.float32)
    feature_values = _load_array(root, artifacts["feature_values"], np.float32)
    feature_validity = _load_array(root, artifacts["feature_validity"], np.bool_)
    target_values = _load_array(root, artifacts["target_values"], np.float32)
    target_available = _load_array(root, artifacts["target_availability"], np.bool_)
    if (
        membership.shape != matrix_shape
        or membership_known.shape != matrix_shape
        or membership_weight.shape != matrix_shape
        or target_values.shape != matrix_shape
        or target_available.shape != matrix_shape
        or feature_values.shape != feature_shape
        or feature_validity.shape != feature_shape
        or not np.array_equal(membership, expected_membership)
        or not np.array_equal(membership_known, expected_known)
        or not np.array_equal(membership_weight, expected_weight)
    ):
        raise LocalDevelopmentBundleError(failure)

    raw_values: dict[str, np.ndarray] = {}
    raw_validity: dict[str, np.ndarray] = {}
    for name, (_dataset, _aliases, rule) in _FIELD_SOURCES.items():
        values = _load_array(root, artifacts[f"raw_{name}"], np.float32)
        validity = _load_array(root, artifacts[f"raw_{name}_validity"], np.bool_)
        if values.shape != matrix_shape or validity.shape != matrix_shape:
            raise LocalDevelopmentBundleError(failure)
        valid_values = values[validity]
        if (
            np.any(values[~validity] != 0.0)
            or np.any(~np.isfinite(valid_values))
            or (rule == "positive" and np.any(valid_values <= 0.0))
            or (rule == "nonnegative" and np.any(valid_values < 0.0))
        ):
            raise LocalDevelopmentBundleError(failure)
        raw_values[name] = values
        raw_validity[name] = validity

    position_masks = {
        role: _load_array(root, artifacts[role], np.bool_)
        for role in _POSITION_MASK_ROLES
    }
    if any(mask.shape != matrix_shape for mask in position_masks.values()):
        raise LocalDevelopmentBundleError(failure)
    for dataset in _DAILY_DATASETS:
        observed = position_masks[f"{dataset}_observed_positions"]
        duplicate = position_masks[f"{dataset}_duplicate_positions"]
        fields = [
            name for name, (source, _aliases, _rule) in _FIELD_SOURCES.items()
            if source == dataset
        ]
        if np.any(duplicate & ~observed) or any(
            np.any(raw_validity[name] & ~observed) for name in fields
        ) or any(
            np.any(raw_validity[name] & duplicate) for name in fields
        ):
            raise LocalDevelopmentBundleError(failure)
    unusable = position_masks["limit_required_field_unusable_positions"]
    order_violation = position_masks["positive_limit_order_violation_positions"]
    mismatch_positions = position_masks[
        "cross_source_pre_close_mismatch_positions"
    ]
    limit_observed = position_masks["daily_limits_observed_positions"]
    bar_observed = position_masks["daily_bars_observed_positions"]
    if (
        np.any(unusable & order_violation)
        or np.any(mismatch_positions & (unusable | order_violation))
        or np.any((unusable | order_violation) & ~limit_observed)
        or np.any(mismatch_positions & ~(limit_observed & bar_observed))
        or np.any(
            (unusable | order_violation | mismatch_positions)
            & position_masks["daily_limits_duplicate_positions"]
        )
    ):
        raise LocalDevelopmentBundleError(failure)
    limit_usable_after_reconciliation = np.logical_and.reduce(
        [
            raw_validity["up_limit"],
            raw_validity["down_limit"],
            raw_validity["limit_pre_close"],
        ]
    )
    expected_invalid_limit = (
        unusable
        | order_violation
        | mismatch_positions
        | position_masks["daily_limits_duplicate_positions"]
    )
    if not np.array_equal(
        limit_observed & ~limit_usable_after_reconciliation,
        expected_invalid_limit,
    ):
        raise LocalDevelopmentBundleError(failure)

    ohlc_valid = np.logical_and.reduce(
        [raw_validity[name] for name in ("open", "high", "low", "close")]
    )
    if np.any(
        ohlc_valid
        & (
            (raw_values["high"] < np.maximum(raw_values["open"], raw_values["close"]))
            | (raw_values["low"] > np.minimum(raw_values["open"], raw_values["close"]))
            | (raw_values["high"] < raw_values["low"])
        )
    ):
        raise LocalDevelopmentBundleError(failure)
    limit_masks = [
        raw_validity[name] for name in ("up_limit", "down_limit", "limit_pre_close")
    ]
    if not all(np.array_equal(limit_masks[0], mask) for mask in limit_masks[1:]):
        raise LocalDevelopmentBundleError(failure)
    limit_valid = limit_masks[0]
    if np.any(
        limit_valid
        & (
            (raw_values["down_limit"] > raw_values["limit_pre_close"])
            | (raw_values["limit_pre_close"] > raw_values["up_limit"])
        )
    ):
        raise LocalDevelopmentBundleError(failure)
    comparable = raw_validity["pre_close"] & raw_validity["limit_pre_close"]
    if np.any(
        comparable
        & ~np.isclose(
            raw_values["pre_close"],
            raw_values["limit_pre_close"],
            rtol=0.0,
            atol=1e-5,
        )
    ):
        raise LocalDevelopmentBundleError(failure)

    eligible = membership & membership_known
    expected_feature_validity = np.stack(
        [raw_validity[name] & eligible for name in features],
        axis=1,
    ).astype(np.bool_, copy=False)
    expected_feature_values = np.stack(
        [raw_values[name] for name in features],
        axis=1,
    ).astype(np.float32, copy=False)
    expected_feature_values = np.where(
        expected_feature_validity,
        expected_feature_values,
        0.0,
    ).astype(np.float32, copy=False)
    expected_target, expected_available = _observed_open_target(
        raw_values,
        raw_validity,
        membership,
        membership_known,
    )
    if (
        not np.array_equal(feature_validity, expected_feature_validity)
        or not np.array_equal(feature_values, expected_feature_values)
        or not np.array_equal(target_available, expected_available)
        or not np.array_equal(target_values, expected_target, equal_nan=True)
    ):
        raise LocalDevelopmentBundleError(failure)

    target_contract = _read_json_artifact(root, artifacts["target_contract"])
    feature_manifest = _read_json_artifact(root, artifacts["feature_manifest"])
    quality_report = _read_json_artifact(root, artifacts["quality_report"])
    reconciliation_report = _read_json_artifact(
        root, artifacts["reconciliation_report"]
    )
    expected_quality, expected_reconciliation = _build_quality_and_reconciliation(
        scope=typed_scope,
        trade_dates=dates,
        snapshot_report=snapshot_report,
        effective_snapshots=effective_snapshots,
        raw_values=raw_values,
        raw_validity=raw_validity,
        membership=membership,
        membership_known=membership_known,
        feature_names=features,
        feature_validity=feature_validity,
        target_available=target_available,
        position_masks=position_masks,
    )
    matrix_manifest = _read_json_artifact(
        root,
        artifacts["development_matrix_manifest"],
    )
    expected_feature_manifest = {
        "schema_version": "local_development_feature_manifest_v1",
        "mode": "development_replay",
        "feature_names": features,
        "feature_axis_hash": canonical_hash(features),
        "features": [
            {
                "feature_name": name,
                "source_dataset": _FIELD_SOURCES[name][0],
                "validity_rule": (
                    f"observed_{_FIELD_SOURCES[name][2]}_and_proxy_membership_known"
                ),
            }
            for name in features
        ],
    }
    partition_sha256 = {
        Path(str(row["relative_path"])).name: str(row["sha256"])
        for role, row in artifacts.items()
        if role != "development_matrix_manifest"
        and Path(str(row["relative_path"])).parts[0] == "development_matrix"
    }
    expected_matrix_manifest = {
        "schema_version": "local_development_matrix_v1",
        "mode": "development_replay",
        "shape": [len(stocks), len(dates)],
        "stock_axis_hash": canonical_hash(stocks),
        "date_axis_hash": canonical_hash(dates),
        "feature_axis_hash": canonical_hash(features),
        "raw_fields": list(_FIELD_SOURCES),
        "target_contract": _target_contract(),
        "universe_mode": "daily_retrospective_proxy",
        "historical_constituent_proof": False,
        "physical_research_projection": True,
        "evidence_flags": dict(EVIDENCE_FLAGS),
        "partition_sha256": partition_sha256,
        "deterministic_build": True,
        "builder_semantic_hash": builder_semantic_hash,
    }
    if (
        target_contract != _target_contract()
        or feature_manifest != expected_feature_manifest
        or matrix_manifest != expected_matrix_manifest
        or quality_report != expected_quality
        or reconciliation_report != expected_reconciliation
    ):
        raise LocalDevelopmentBundleError(failure)
    _validate_source_lineage(
        _read_json_artifact(root, artifacts["source_to_derived_lineage"]),
        bundle,
        expected_source_partitions,
    )


def _read_membership_snapshots(
    root: Path,
    row: Mapping[str, Any],
    scope: LocalDevelopmentScope,
    trade_dates: list[str],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    payload = _read_json_artifact(root, row)
    if (
        payload.get("schema_version")
        != "local_development_membership_snapshots_v1"
        or payload.get("index_code") != scope.index_code
        or payload.get("effective_rule") != "next_open_trade_day"
        or payload.get("max_staleness_calendar_days")
        != MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS
        or payload.get("publication_time_proven") is not False
        or not isinstance(payload.get("snapshot_report"), Mapping)
        or not isinstance(payload.get("snapshots"), list)
    ):
        raise LocalDevelopmentBundleError("local development snapshots invalid")
    result: dict[str, dict[str, float]] = {}
    ordered_dates: list[str] = []
    in_scope_dates = set(trade_dates)
    for raw_snapshot in payload["snapshots"]:
        if not isinstance(raw_snapshot, Mapping):
            raise LocalDevelopmentBundleError("local development snapshots invalid")
        snapshot_date = str(raw_snapshot.get("snapshot_date") or "")
        members = raw_snapshot.get("members")
        if (
            not _valid_date(snapshot_date)
            or snapshot_date < "20160101"
            or snapshot_date > scope.date_end
            or snapshot_date in result
            or not isinstance(members, list)
            or len(members) != 300
            or (snapshot_date >= scope.date_start and snapshot_date not in in_scope_dates)
        ):
            raise LocalDevelopmentBundleError("local development snapshots invalid")
        parsed: dict[str, float] = {}
        member_order: list[str] = []
        for raw_member in members:
            if not isinstance(raw_member, Mapping):
                raise LocalDevelopmentBundleError("local development snapshots invalid")
            code = str(raw_member.get("ts_code") or "")
            weight = _finite_float(raw_member.get("weight"))
            if (
                len(code) != 9
                or not code[:6].isdigit()
                or code[6:] not in {".SH", ".SZ"}
                or code in parsed
                or weight is None
                or weight <= 0
            ):
                raise LocalDevelopmentBundleError("local development snapshots invalid")
            parsed[code] = weight
            member_order.append(code)
        if member_order != sorted(member_order) or not 99.5 <= math.fsum(parsed.values()) <= 100.5:
            raise LocalDevelopmentBundleError("local development snapshots invalid")
        result[snapshot_date] = parsed
        ordered_dates.append(snapshot_date)
    pre_scope = [date for date in ordered_dates if date < scope.date_start]
    if (
        not result
        or ordered_dates != sorted(ordered_dates)
        or len(pre_scope) > 1
    ):
        raise LocalDevelopmentBundleError("local development snapshots invalid")
    snapshot_report = dict(payload["snapshot_report"])
    rejected = snapshot_report.get("rejected_snapshots")
    if (
        snapshot_report.get("membership_evidence_grade")
        != "retrospective_proxy_unproven_publication_time"
        or snapshot_report.get("max_staleness_calendar_days")
        != MEMBERSHIP_MAX_STALENESS_CALENDAR_DAYS
        or snapshot_report.get("accepted_snapshot_count") != len(result)
        or not isinstance(rejected, Mapping)
        or snapshot_report.get("rejected_snapshot_count") != len(rejected)
        or int(snapshot_report.get("observed_snapshot_count", -1))
        < len(result) + len(rejected)
        or snapshot_report.get("pre_scope_seed_snapshot_date")
        != (pre_scope[0] if pre_scope else None)
    ):
        raise LocalDevelopmentBundleError("local development snapshots invalid")
    return result, snapshot_report


def _read_json_artifact(root: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(
            (root / str(row["relative_path"])).read_text(encoding="utf-8")
        )
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise LocalDevelopmentBundleError("local development JSON artifact invalid") from exc
    if not isinstance(payload, dict):
        raise LocalDevelopmentBundleError("local development JSON artifact invalid")
    return payload


def _artifact_roles(feature_names: list[str]) -> dict[str, str]:
    roles = {
        "stock_axis": "development_matrix/ts_codes.json",
        "date_axis": "development_matrix/trade_dates.json",
        "feature_axis": "development_matrix/feature_names.json",
        "feature_manifest": "development_matrix/feature_set_manifest.json",
        "feature_values": "development_matrix/feature_tensor.npy",
        "feature_validity": "development_matrix/feature_validity_tensor.npy",
        "target_values": f"development_matrix/{TARGET_NAME}.npy",
        "target_availability": "development_matrix/target_available_mask.npy",
        "target_contract": "development_matrix/target_contract.json",
        "pit_universe_membership": "development_matrix/membership.npy",
        "membership_known": "development_matrix/membership_known.npy",
        "membership_snapshots": "development_matrix/accepted_index_snapshots.json",
        "membership_weight": "development_matrix/index_weight.npy",
        "source_identity_binding_evidence": "source_evidence/source_identity_binding.json",
        "source_search_view_manifest_evidence": "source_evidence/research_view_manifest.json",
        "source_to_derived_lineage": "development_matrix/source_to_derived_lineage.json",
        "development_matrix_manifest": "development_matrix/development_matrix_manifest.json",
        "quality_report": "development_matrix/local_quality_report.json",
        "reconciliation_report": "development_matrix/reconciliation_report.json",
    }
    for name in _FIELD_SOURCES:
        roles[f"raw_{name}"] = f"development_matrix/{name}.npy"
        roles[f"raw_{name}_validity"] = f"development_matrix/{name}_validity.npy"
    for role in _POSITION_MASK_ROLES:
        roles[role] = f"development_matrix/{role}.npy"
    return roles


def _artifact_row(root: Path, role: str, relative_path: str) -> dict[str, Any]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
        raise LocalDevelopmentBundleError(f"artifact publication containment failure:{role}")
    row: dict[str, Any] = {
        "role": role,
        "relative_path": relative_path,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    if role.endswith("axis"):
        row["axis_hash"] = canonical_hash(json.loads(path.read_text(encoding="utf-8")))
    if path.suffix == ".npy":
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        row["shape"] = list(array.shape)
        row["dtype"] = str(array.dtype)
    return row


def _read_axis(root: Path, row: Mapping[str, Any]) -> list[str]:
    path = root / str(row["relative_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise LocalDevelopmentBundleError("local development axis payload invalid")
    if row.get("axis_hash") != canonical_hash(payload):
        raise LocalDevelopmentBundleError("local development axis hash invalid")
    return payload


def _load_array(root: Path, row: Mapping[str, Any], dtype: np.dtype[Any]) -> np.ndarray:
    try:
        value = np.load(root / str(row["relative_path"]), mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise LocalDevelopmentBundleError("local development array invalid") from exc
    if (
        value.dtype != dtype
        or row.get("dtype") != str(value.dtype)
        or row.get("shape") != list(value.shape)
    ):
        raise LocalDevelopmentBundleError("local development array dtype invalid")
    return value


def _write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _write_bytes(path, payload)


def _write_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.save(handle, value, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _builder_semantic_hash() -> str:
    source_paths = {
        Path(__file__).resolve(),
        Path(inspect.getsourcefile(PhysicalResearchDataView) or "").resolve(),
        Path(inspect.getsourcefile(canonical_hash) or "").resolve(),
    }
    return canonical_hash(
        {
            "sources": [
                {
                    "path": path.name,
                    "sha256": sha256_file(path),
                }
                for path in sorted(source_paths)
            ],
            "runtime": {
                "python": (
                    f"{sys.version_info.major}.{sys.version_info.minor}."
                    f"{sys.version_info.micro}"
                ),
                "numpy": np.__version__,
                "pyarrow": pa.__version__,
                "matrix_contract": SCHEMA_VERSION,
            },
        }
    )


def _remove_preparation_root(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        try:
            path.chmod(0o700 if path.is_dir() else 0o600)
        except FileNotFoundError:
            continue
    try:
        root.chmod(0o700)
    except FileNotFoundError:
        return
    shutil.rmtree(root, ignore_errors=True)


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _valid_date(value: Any) -> bool:
    try:
        datetime.strptime(str(value), "%Y%m%d")
    except (TypeError, ValueError):
        return False
    return True


def _valid_optional_date(value: Any) -> bool:
    return value is None or _valid_date(value)


def _valid_partition_date_bounds(
    *,
    period: str,
    min_date: Any,
    max_date: Any,
    record_count: int,
) -> bool:
    if not _valid_optional_date(min_date) or not _valid_optional_date(max_date):
        return False
    if record_count > 0 and (min_date is None or max_date is None):
        return False
    if min_date is not None and max_date is not None and str(min_date) > str(max_date):
        return False
    if period == "bootstrap":
        return all(
            value is None or str(value) <= "20111231"
            for value in (min_date, max_date)
        )
    return all(
        value is None or "20120101" <= str(value) <= "20191231"
        for value in (min_date, max_date)
    )


__all__ = [
    "LocalDevelopmentBundleError",
    "LocalDevelopmentBundleLoader",
    "LocalDevelopmentScope",
    "build_local_development_bundle",
    "validate_local_development_bundle",
]
