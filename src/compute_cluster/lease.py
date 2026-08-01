"""Local GPU lease files for exclusive per-device shard jobs."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .gpu_probe import probe_compute_resources
from .models import ComputeDeviceType, ComputeLease


class GpuLeaseManager:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.lease_dir = self.state_dir / "leases"
        self.lease_dir.mkdir(parents=True, exist_ok=True)

    def acquire_gpu_lease(
        self,
        job_id: str,
        required_gpus: int = 1,
        preferred_devices: list[int] | None = None,
        min_free_memory_mb: float | None = None,
        heartbeat_timeout_seconds: float = 3600.0,
    ) -> ComputeLease | None:
        self.release_stale_leases(heartbeat_timeout_seconds)
        snapshot = probe_compute_resources()
        available = [
            int(device.index)
            for device in snapshot.devices
            if device.device_type == ComputeDeviceType.CUDA
            and device.index is not None
            and (min_free_memory_mb is None or device.free_memory_mb >= min_free_memory_mb)
        ]
        if preferred_devices:
            preferred = [idx for idx in preferred_devices if idx in available]
            remainder = [idx for idx in available if idx not in preferred]
            available = preferred + remainder
        selected: list[int] = []
        lock_paths: list[Path] = []
        for index in available:
            if len(selected) >= required_gpus:
                break
            path = self.lease_dir / f"gpu_{index}.lock.json"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            except OSError:
                continue
            selected.append(index)
            lock_paths.append(path)
            os.close(fd)
        if len(selected) < required_gpus:
            for path in lock_paths:
                path.unlink(missing_ok=True)
            return None
        now = _utc_now()
        lease = ComputeLease(
            lease_id=f"lease_{uuid.uuid4().hex[:16]}",
            job_id=job_id,
            device_indices=selected,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=heartbeat_timeout_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for path in lock_paths:
            path.write_text(json.dumps(lease.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8")
        self._append_event("acquired", lease.to_dict())
        return lease

    def release_lease(self, lease_id: str) -> bool:
        released = False
        for path in self.lease_dir.glob("gpu_*.lock.json"):
            payload = _read_json(path)
            if payload.get("lease_id") == lease_id:
                path.unlink(missing_ok=True)
                self._append_event("released", payload)
                released = True
        return released

    def heartbeat(self, lease_id: str) -> None:
        now = _utc_now()
        for path in self.lease_dir.glob("gpu_*.lock.json"):
            payload = _read_json(path)
            if payload.get("lease_id") == lease_id:
                payload["heartbeat_at"] = now
                path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def release_stale_leases(self, heartbeat_timeout_seconds: float = 3600.0) -> int:
        released = 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_timeout_seconds)
        for path in self.lease_dir.glob("gpu_*.lock.json"):
            payload = _read_json(path)
            heartbeat = _parse_time(str(payload.get("heartbeat_at") or payload.get("acquired_at") or ""))
            if heartbeat is None or heartbeat < cutoff:
                path.unlink(missing_ok=True)
                self._append_event("stale_released", payload)
                released += 1
        return released

    def list_leases(self) -> list[dict]:
        return [_read_json(path) for path in sorted(self.lease_dir.glob("gpu_*.lock.json"))]

    def write_leases_jsonl(self, output_dir: str | Path) -> Path:
        path = Path(output_dir) / "gpu_leases.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in self.list_leases():
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return path

    def _append_event(self, event_type: str, payload: dict) -> None:
        path = self.lease_dir / "lease_events.jsonl"
        row = {"event_type": event_type, "created_at": _utc_now(), **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
