"""Task 055-A observation-boundary contracts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TASK_ID = "task_055_a"
OBSERVATION_BOUNDARY_SCHEMA = "task055a_observation_boundary_v1"
OBSERVATION_BOUNDARY_SEAL_SCHEMA = "task055a_observation_boundary_seal_v1"
VALIDATOR_VERSION = "task055a_observation_boundary_validator_v1"
EFFECTIVE_TIMEZONE = "Asia/Shanghai"
CONTAMINATED_START_DATE = "20240531"
CONTAMINATED_END_DATE = "20260630"
WAITING_STATUS = "waiting_for_future_data"
SEALED_STATUS = "sealed_waiting_for_future_data"
HOLDOUT_READY_STATUS = "prospective_holdout_boundary_sealed"

OBSERVATION_FILE_MARKERS = ("manifest", "ledger", "raw_index", "raw-index", "index")
STATE_FILE_MARKERS = ("queue", "store", "registry")
JSON_SUFFIXES = (".json", ".jsonl", ".ndjson")
FORBIDDEN_SUFFIXES = (".npy", ".npz", ".parquet", ".arrow", ".feather")
FORBIDDEN_MARKET_RECORD_NAMES = ("records.jsonl", "records.ndjson", "market_records.json")


@dataclass(frozen=True)
class ObservationScanConfig:
    """Filesystem roots used by the metadata-only observation scan."""

    roots: tuple[Path, ...]
    state_roots: tuple[Path, ...] = ()
    explicit_observation_files: tuple[Path, ...] = ()
    explicit_state_files: tuple[Path, ...] = ()

    @classmethod
    def from_paths(
        cls,
        roots: Iterable[str | Path],
        *,
        state_roots: Iterable[str | Path] = (),
        observation_files: Iterable[str | Path] = (),
        state_files: Iterable[str | Path] = (),
    ) -> "ObservationScanConfig":
        return cls(
            roots=tuple(Path(path) for path in roots),
            state_roots=tuple(Path(path) for path in state_roots),
            explicit_observation_files=tuple(Path(path) for path in observation_files),
            explicit_state_files=tuple(Path(path) for path in state_files),
        )


@dataclass(frozen=True)
class PartitionLineage:
    partition_id: str
    source_path: str
    first_seen: str | None
    acquired_at: str | None
    content_hash: str | None
    revision: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "partition_id": self.partition_id,
            "source_path": self.source_path,
            "first_seen": self.first_seen,
            "acquired_at": self.acquired_at,
            "content_hash": self.content_hash,
            "revision": self.revision,
        }
