"""Heartbeat helpers for compute jobs."""

from __future__ import annotations

from datetime import datetime, timezone

from .job_store import LocalComputeJobStore
from .models import ComputeHeartbeat


def write_heartbeat(store: LocalComputeJobStore, job_id: str, status: str, lease_id: str | None = None, **metadata):
    row = ComputeHeartbeat(
        job_id=job_id,
        status=status,
        heartbeat_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        lease_id=lease_id,
        metadata=metadata,
    )
    store.append_heartbeat(row.to_dict())
    return row
