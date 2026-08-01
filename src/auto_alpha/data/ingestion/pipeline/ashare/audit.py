"""API request audit logging for data synchronization."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ApiRequestAuditEntry:
    api_name: str
    dataset: str
    start_date: str | None
    end_date: str | None
    index_code: str | None
    cache_hit: bool
    records: int
    status: str
    error: str | None
    started_at: str
    finished_at: str
    duration_seconds: float
    rate_limit_wait_seconds: float = 0.0
    rate_limit_request_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ApiRequestAuditor:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, entry: ApiRequestAuditEntry) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return self.path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
