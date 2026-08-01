"""Metadata-only observation-boundary scan, seal publisher, and validator."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .contracts import (
    CONTAMINATED_END_DATE,
    CONTAMINATED_START_DATE,
    EFFECTIVE_TIMEZONE,
    FORBIDDEN_MARKET_RECORD_NAMES,
    FORBIDDEN_SUFFIXES,
    HOLDOUT_READY_STATUS,
    JSON_SUFFIXES,
    OBSERVATION_BOUNDARY_SCHEMA,
    OBSERVATION_BOUNDARY_SEAL_SCHEMA,
    OBSERVATION_FILE_MARKERS,
    SEALED_STATUS,
    STATE_FILE_MARKERS,
    VALIDATOR_VERSION,
    WAITING_STATUS,
    ObservationScanConfig,
    PartitionLineage,
)

_DATE_RE = re.compile(r"^(?P<year>\d{4})-?(?P<month>\d{2})-?(?P<day>\d{2})$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_MARKERS = (
    "acquired",
    "created",
    "effective",
    "first_seen",
    "generated",
    "published",
    "timestamp",
    "updated",
)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    matched = _DATE_RE.fullmatch(value.strip())
    if matched is None:
        return None
    try:
        parsed = date(int(matched["year"]), int(matched["month"]), int(matched["day"]))
    except ValueError:
        return None
    return parsed.strftime("%Y%m%d")


def _is_json_path(path: Path) -> bool:
    return path.suffix.lower() in JSON_SUFFIXES


def _is_forbidden(path: Path) -> bool:
    return path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.lower() in FORBIDDEN_MARKET_RECORD_NAMES


def _contains_marker(path: Path, markers: tuple[str, ...]) -> bool:
    searchable = "/".join(part.lower() for part in path.parts)
    return any(marker in searchable for marker in markers)


def _observation_file_allowed(path: Path) -> bool:
    return path.is_file() and _is_json_path(path) and not _is_forbidden(path) and _contains_marker(path, OBSERVATION_FILE_MARKERS)


def _state_file_allowed(path: Path) -> bool:
    return path.is_file() and _is_json_path(path) and not _is_forbidden(path) and _contains_marker(path, STATE_FILE_MARKERS)


def _discover_files(
    roots: Iterable[Path],
    explicit_files: Iterable[Path],
    *,
    predicate: Any,
    label: str,
) -> tuple[Path, ...]:
    discovered: set[Path] = set()
    for explicit in explicit_files:
        path = explicit.resolve()
        if not path.is_file() or not _is_json_path(path) or _is_forbidden(path):
            raise RuntimeError(f"task055a_{label}_file_not_allowed:{path}")
        discovered.add(path)
    for root_value in roots:
        root = root_value.resolve()
        if not root.exists():
            raise RuntimeError(f"task055a_scan_root_missing:{root}")
        candidates = (root,) if root.is_file() else root.rglob("*")
        for candidate in candidates:
            if predicate(candidate):
                discovered.add(candidate.resolve())
    return tuple(sorted(discovered, key=lambda path: str(path)))


def _load_json_documents(path: Path) -> list[Any]:
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        documents = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    documents.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise RuntimeError(f"task055a_invalid_json_ledger:{path}:{line_number}") from error
        return documents
    try:
        return [json.loads(path.read_text(encoding="utf-8"))]
    except json.JSONDecodeError as error:
        raise RuntimeError(f"task055a_invalid_json:{path}") from error


def _endpoint_kind(key: str, ancestors: tuple[str, ...]) -> str | None:
    normalized = key.lower()
    context = "_".join((*ancestors, normalized)).lower()
    if any(marker in normalized for marker in _TIMESTAMP_MARKERS):
        return None
    if not any(marker in normalized for marker in ("date", "day", "endpoint", "end")):
        return None
    if "target" in context or "label" in context or "outcome" in context:
        return "target"
    if "signal" in context or "feature" in context:
        return "signal"
    if "source" in context or "observed" in context or normalized in {"end_date", "max_date", "date_end"}:
        return "source"
    return None


def _lineage_value(row: Mapping[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return None


def _partition_lineage(row: Mapping[str, Any], source_path: Path, ancestors: tuple[str, ...]) -> PartitionLineage | None:
    partition_id = _lineage_value(row, ("partition_id", "partition", "partition_key", "partition_path", "path", "file"))
    lineage_context = "_".join(ancestors).lower()
    has_lineage = any(
        key in row
        for key in (
            "first_seen",
            "first_seen_at",
            "acquired_at",
            "content_hash",
            "sha256",
            "revision",
            "revision_id",
        )
    )
    if not has_lineage or (partition_id is None and "partition" not in lineage_context):
        return None
    return PartitionLineage(
        partition_id=partition_id or "/".join(ancestors) or source_path.name,
        source_path=str(source_path),
        first_seen=_lineage_value(row, ("first_seen", "first_seen_at", "observed_at")),
        acquired_at=_lineage_value(row, ("acquired_at", "acquisition_time", "fetched_at", "downloaded_at")),
        content_hash=_lineage_value(row, ("content_hash", "sha256", "content_sha256", "partition_sha256")),
        revision=_lineage_value(row, ("revision", "revision_id", "version", "generation_id")),
    )


def _walk_metadata(
    value: Any,
    *,
    source_path: Path,
    ancestors: tuple[str, ...],
    endpoints: dict[str, list[dict[str, str]]],
    lineages: list[PartitionLineage],
    trading_dates: set[str],
) -> None:
    if isinstance(value, Mapping):
        lineage = _partition_lineage(value, source_path, ancestors)
        if lineage is not None:
            lineages.append(lineage)
        calendar_context = "calendar" in source_path.name.lower() or any("calendar" in item.lower() for item in ancestors)
        trade_date = _normalized_date(value.get("trade_date"))
        if calendar_context and trade_date and value.get("is_open", value.get("open", True)) in (True, 1, "1", "Y", "y"):
            trading_dates.add(trade_date)
        for raw_key, child in value.items():
            key = str(raw_key)
            kind = _endpoint_kind(key, ancestors)
            if kind is not None:
                values = child if isinstance(child, list) else [child]
                for item in values:
                    observed_date = _normalized_date(item)
                    if observed_date:
                        endpoints[kind].append(
                            {"date": observed_date, "source_path": str(source_path), "field": ".".join((*ancestors, key))}
                        )
            if key.lower() in {"trade_dates", "trading_dates", "open_dates"} and isinstance(child, list):
                trading_dates.update(filter(None, (_normalized_date(item) for item in child)))
            _walk_metadata(
                child,
                source_path=source_path,
                ancestors=(*ancestors, key),
                endpoints=endpoints,
                lineages=lineages,
                trading_dates=trading_dates,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_metadata(
                child,
                source_path=source_path,
                ancestors=(*ancestors, str(index)),
                endpoints=endpoints,
                lineages=lineages,
                trading_dates=trading_dates,
            )


def _physical_record_count(path: Path) -> int:
    documents = _load_json_documents(path)
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return sum(document not in (None, "", [], {}) for document in documents)
    value = documents[0]
    if isinstance(value, list):
        return sum(item not in (None, "", [], {}) for item in value)
    if isinstance(value, Mapping):
        for key in ("records", "items", "entries", "rows"):
            rows = value.get(key)
            if isinstance(rows, list):
                return sum(item not in (None, "", [], {}) for item in rows)
        return int(bool(value))
    return int(value not in (None, ""))


def _coerce_config(
    config: ObservationScanConfig | None,
    *,
    roots: Iterable[str | Path],
    state_roots: Iterable[str | Path],
    observation_files: Iterable[str | Path],
    state_files: Iterable[str | Path],
) -> ObservationScanConfig:
    root_values = tuple(roots)
    state_root_values = tuple(state_roots)
    observation_file_values = tuple(observation_files)
    state_file_values = tuple(state_files)
    if config is not None:
        if any((root_values, state_root_values, observation_file_values, state_file_values)):
            raise ValueError("config cannot be combined with path arguments")
        return config
    return ObservationScanConfig.from_paths(
        root_values,
        state_roots=state_root_values,
        observation_files=observation_file_values,
        state_files=state_file_values,
    )


def recompute_observation_boundary(
    config: ObservationScanConfig | None = None,
    *,
    roots: Iterable[str | Path] = (),
    state_roots: Iterable[str | Path] = (),
    observation_files: Iterable[str | Path] = (),
    state_files: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Recompute observed endpoints without opening arrays or market-record files."""

    resolved = _coerce_config(
        config,
        roots=roots,
        state_roots=state_roots,
        observation_files=observation_files,
        state_files=state_files,
    )
    metadata_paths = _discover_files(
        resolved.roots,
        resolved.explicit_observation_files,
        predicate=_observation_file_allowed,
        label="observation",
    )
    physical_paths = _discover_files(
        resolved.state_roots or resolved.roots,
        resolved.explicit_state_files,
        predicate=_state_file_allowed,
        label="state",
    )
    endpoints: dict[str, list[dict[str, str]]] = {"signal": [], "source": [], "target": []}
    lineages: list[PartitionLineage] = []
    trading_dates: set[str] = set()
    metadata_files = []
    for path in metadata_paths:
        documents = _load_json_documents(path)
        for document in documents:
            _walk_metadata(
                document,
                source_path=path,
                ancestors=(),
                endpoints=endpoints,
                lineages=lineages,
                trading_dates=trading_dates,
            )
        metadata_files.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "document_count": len(documents),
                "bytes": path.stat().st_size,
            }
        )
    state_inventory = []
    for path in physical_paths:
        state_inventory.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "nonempty_record_count": _physical_record_count(path),
            }
        )
    endpoint_maxima = {
        kind: max((row["date"] for row in observations), default=None)
        for kind, observations in endpoints.items()
    }
    max_endpoint = max((value for value in endpoint_maxima.values() if value is not None), default=None)
    unique_lineages = {
        canonical_hash(lineage.to_dict()): lineage.to_dict()
        for lineage in lineages
    }
    lineage_rows = [unique_lineages[key] for key in sorted(unique_lineages)]
    boundary = {
        "schema_version": OBSERVATION_BOUNDARY_SCHEMA,
        "scan_policy": {
            "metadata_only": True,
            "allowed_inputs": ["manifests", "json_ledgers", "raw_indexes", "queue_store_registry_state"],
            "npy_read": False,
            "market_records_read": False,
        },
        "max_observed_signal_endpoint": endpoint_maxima["signal"],
        "max_observed_source_endpoint": endpoint_maxima["source"],
        "max_observed_target_endpoint": endpoint_maxima["target"],
        "max_observed_signal_date": endpoint_maxima["signal"],
        "max_observed_source_date": endpoint_maxima["source"],
        "max_observed_target_date": endpoint_maxima["target"],
        "max_observed_endpoint": max_endpoint,
        "endpoint_evidence": {kind: sorted(rows, key=lambda row: (row["date"], row["source_path"], row["field"])) for kind, rows in endpoints.items()},
        "partition_lineage": lineage_rows,
        "metadata_files": metadata_files,
        "physical_state_inventory": state_inventory,
        "physical_nonempty_record_count": sum(row["nonempty_record_count"] for row in state_inventory),
        "provable_trading_dates": sorted(trading_dates),
        "contaminated_period": {
            "start_date": CONTAMINATED_START_DATE,
            "end_date": CONTAMINATED_END_DATE,
            "status": "contaminated",
            "clean_holdout": False,
        },
    }
    boundary["observation_hash"] = canonical_hash(boundary)
    return boundary


scan_observation_boundary = recompute_observation_boundary
build_observation_boundary = recompute_observation_boundary


def _effective_datetime(value: datetime | str | None) -> datetime:
    timezone = ZoneInfo(EFFECTIVE_TIMEZONE)
    if value is None:
        return datetime.now(timezone)
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _holdout_plan(observation: Mapping[str, Any], effective_at: datetime) -> dict[str, Any]:
    max_endpoint = observation.get("max_observed_endpoint")
    if not isinstance(max_endpoint, str):
        return {
            "status": WAITING_STATUS,
            "earliest_holdout_trade_date": None,
            "earliest_prospective_holdout_date": None,
            "strictly_after_max_endpoint": False,
            "strictly_after_seal_effective_date": True,
            "reason": "waiting_for_future_added_data_max_observed_endpoint_unavailable",
        }
    lower_bounds = [effective_at.strftime("%Y%m%d")]
    lower_bounds.append(max_endpoint)
    strict_lower_bound = max(lower_bounds)
    trading_dates = sorted(
        date_value
        for raw_value in observation.get("provable_trading_dates", [])
        if (date_value := _normalized_date(raw_value)) is not None
    )
    candidates = [date_value for date_value in trading_dates if date_value > strict_lower_bound]
    if not candidates:
        return {
            "status": WAITING_STATUS,
            "earliest_holdout_trade_date": None,
            "earliest_prospective_holdout_date": None,
            "strictly_after_max_endpoint": True,
            "strictly_after_seal_effective_date": True,
            "reason": "waiting_for_future_added_data_no_next_provable_trading_day",
        }
    return {
        "status": "ready",
        "earliest_holdout_trade_date": candidates[0],
        "earliest_prospective_holdout_date": candidates[0],
        "strictly_after_max_endpoint": candidates[0] > max_endpoint,
        "strictly_after_seal_effective_date": candidates[0] > effective_at.strftime("%Y%m%d"),
        "reason": "first_provable_trading_day_after_seal_and_max_endpoint",
    }


def publish_observation_boundary_seal(
    observation: Mapping[str, Any] | ObservationScanConfig | None = None,
    *,
    output_dir: str | Path,
    effective_at: datetime | str | None = None,
    roots: Iterable[str | Path] = (),
    state_roots: Iterable[str | Path] = (),
    observation_files: Iterable[str | Path] = (),
    state_files: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Publish one immutable, content-addressed observation-boundary seal."""

    if isinstance(observation, ObservationScanConfig):
        snapshot = recompute_observation_boundary(observation)
    elif observation is None:
        snapshot = recompute_observation_boundary(
            roots=roots,
            state_roots=state_roots,
            observation_files=observation_files,
            state_files=state_files,
        )
    else:
        if any((tuple(roots), tuple(state_roots), tuple(observation_files), tuple(state_files))):
            raise ValueError("precomputed observation cannot be combined with scan paths")
        snapshot = dict(observation)
    validate_observation_snapshot(snapshot)
    local_effective_at = _effective_datetime(effective_at)
    holdout = _holdout_plan(snapshot, local_effective_at)
    payload = {
        "schema_version": OBSERVATION_BOUNDARY_SEAL_SCHEMA,
        "status": HOLDOUT_READY_STATUS if holdout["status"] == "ready" else SEALED_STATUS,
        "effective_at": local_effective_at.isoformat(),
        "effective_timezone": EFFECTIVE_TIMEZONE,
        "observation": snapshot,
        "prospective_holdout": holdout,
        "contaminated_period": {
            "start_date": CONTAMINATED_START_DATE,
            "end_date": CONTAMINATED_END_DATE,
            "status": "contaminated",
            "clean_holdout": False,
        },
        "append_only": True,
    }
    payload["content_hash"] = canonical_hash(payload)
    seal_dir = Path(output_dir)
    seal_dir.mkdir(parents=True, exist_ok=True)
    seal_path = seal_dir / f"{payload['content_hash']}.json"
    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if seal_path.exists():
        if seal_path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"task055a_append_only_seal_collision:{seal_path}")
    else:
        seal_path.write_text(serialized, encoding="utf-8")
    return payload | {"seal_path": str(seal_path)}


def validate_observation_snapshot(observation: Mapping[str, Any]) -> dict[str, Any]:
    semantic = {key: value for key, value in observation.items() if key != "observation_hash"}
    if observation.get("schema_version") != OBSERVATION_BOUNDARY_SCHEMA:
        raise RuntimeError("task055a_observation_schema_invalid")
    if canonical_hash(semantic) != observation.get("observation_hash"):
        raise RuntimeError("task055a_observation_hash_invalid")
    policy = observation.get("scan_policy") or {}
    if policy.get("metadata_only") is not True or policy.get("npy_read") is not False or policy.get("market_records_read") is not False:
        raise RuntimeError("task055a_scan_policy_invalid")
    contamination = observation.get("contaminated_period") or {}
    if contamination != {
        "start_date": CONTAMINATED_START_DATE,
        "end_date": CONTAMINATED_END_DATE,
        "status": "contaminated",
        "clean_holdout": False,
    }:
        raise RuntimeError("task055a_contamination_contract_invalid")
    maxima = [
        observation.get("max_observed_signal_endpoint"),
        observation.get("max_observed_source_endpoint"),
        observation.get("max_observed_target_endpoint"),
    ]
    aliases = [
        observation.get("max_observed_signal_date"),
        observation.get("max_observed_source_date"),
        observation.get("max_observed_target_date"),
    ]
    if maxima != aliases:
        raise RuntimeError("task055a_endpoint_alias_invalid")
    evidence = observation.get("endpoint_evidence") or {}
    recomputed_maxima = [
        max((row["date"] for row in evidence.get(kind, [])), default=None)
        for kind in ("signal", "source", "target")
    ]
    if maxima != recomputed_maxima:
        raise RuntimeError("task055a_endpoint_evidence_invalid")
    expected_max = max((value for value in maxima if value is not None), default=None)
    if observation.get("max_observed_endpoint") != expected_max:
        raise RuntimeError("task055a_max_endpoint_invalid")
    for row in observation.get("partition_lineage", []):
        required = {"partition_id", "source_path", "first_seen", "acquired_at", "content_hash", "revision"}
        if not isinstance(row, Mapping) or set(row) != required:
            raise RuntimeError("task055a_partition_lineage_invalid")
    state_rows = observation.get("physical_state_inventory", [])
    if observation.get("physical_nonempty_record_count") != sum(int(row["nonempty_record_count"]) for row in state_rows):
        raise RuntimeError("task055a_physical_record_count_invalid")
    return dict(observation)


def _validate_source_files(observation: Mapping[str, Any]) -> None:
    for section in ("metadata_files", "physical_state_inventory"):
        for row in observation.get(section, []):
            path = Path(row["path"])
            predicate = (
                (lambda value: value.is_file() and _is_json_path(value) and not _is_forbidden(value))
                if section == "metadata_files"
                else _state_file_allowed
            )
            if not predicate(path) or sha256_file(path) != row.get("sha256") or path.stat().st_size != row.get("bytes"):
                raise RuntimeError(f"task055a_source_file_changed:{path}")
            if section == "physical_state_inventory" and _physical_record_count(path) != row.get("nonempty_record_count"):
                raise RuntimeError(f"task055a_state_record_count_changed:{path}")


def validate_observation_boundary_seal(
    path: str | Path,
    *,
    rescan: bool = True,
) -> dict[str, Any]:
    """Validate seal identity, source immutability, and holdout chronology."""

    seal_path = Path(path)
    try:
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("task055a_seal_unreadable") from error
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    content_hash = payload.get("content_hash")
    if payload.get("schema_version") != OBSERVATION_BOUNDARY_SEAL_SCHEMA or not isinstance(content_hash, str):
        raise RuntimeError("task055a_seal_schema_invalid")
    if not _HASH_RE.fullmatch(content_hash) or canonical_hash(semantic) != content_hash:
        raise RuntimeError("task055a_seal_hash_invalid")
    if seal_path.name != f"{content_hash}.json" or payload.get("append_only") is not True:
        raise RuntimeError("task055a_seal_not_content_addressed")
    if payload.get("effective_timezone") != EFFECTIVE_TIMEZONE:
        raise RuntimeError("task055a_seal_timezone_invalid")
    try:
        encoded_effective_at = datetime.fromisoformat(payload.get("effective_at"))
    except (TypeError, ValueError) as error:
        raise RuntimeError("task055a_seal_effective_time_invalid") from error
    expected_timezone = ZoneInfo(EFFECTIVE_TIMEZONE)
    if encoded_effective_at.tzinfo is None or encoded_effective_at.utcoffset() != expected_timezone.utcoffset(encoded_effective_at):
        raise RuntimeError("task055a_seal_effective_offset_invalid")
    effective_at = encoded_effective_at.astimezone(expected_timezone)
    observation = validate_observation_snapshot(payload.get("observation") or {})
    if rescan:
        _validate_source_files(observation)
    expected_holdout = _holdout_plan(observation, effective_at)
    if payload.get("prospective_holdout") != expected_holdout:
        raise RuntimeError("task055a_holdout_plan_invalid")
    expected_status = HOLDOUT_READY_STATUS if expected_holdout["status"] == "ready" else SEALED_STATUS
    if payload.get("status") != expected_status:
        raise RuntimeError("task055a_seal_status_invalid")
    if payload.get("contaminated_period") != observation["contaminated_period"]:
        raise RuntimeError("task055a_seal_contamination_invalid")
    return payload | {"seal_path": str(seal_path), "validator_version": VALIDATOR_VERSION}


validate_observation_boundary = validate_observation_boundary_seal
