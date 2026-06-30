"""Evidence helpers for operator handoff."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import HandoffEvidenceRecord
from .store import LocalOperatorHandoffStore


def add_evidence_record(
    store_dir: str | Path,
    handoff_id: str,
    evidence_type: str,
    path: str | Path,
    description: str = "",
    recorded_by: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> HandoffEvidenceRecord:
    target = Path(path)
    sha256: str | None = None
    size_bytes: int | None = None
    if target.exists() and target.is_file():
        data = target.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)
    record = HandoffEvidenceRecord(
        evidence_id=f"evidence_{handoff_id}_{_safe_name(evidence_type)}_{_safe_time()}",
        handoff_id=handoff_id,
        evidence_type=evidence_type,
        path=str(target),
        description=description,
        created_at=_utc_now(),
        sha256=sha256,
        size_bytes=size_bytes,
        recorded_by=recorded_by,
        metadata={**(metadata or {}), "exists": target.exists()},
    )
    LocalOperatorHandoffStore(store_dir).add_evidence(record)
    return record


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_") or "item"
