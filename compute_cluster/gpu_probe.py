"""CPU/GPU resource discovery using standard library and torch."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import ComputeDeviceRecord, ComputeDeviceType, ComputeResourceSnapshot


def probe_compute_resources() -> ComputeResourceSnapshot:
    warnings: list[str] = []
    torch_version = None
    cuda_available = False
    cuda_device_count = 0
    devices: list[ComputeDeviceRecord] = []
    try:
        import torch

        torch_version = str(torch.__version__)
        cuda_available = bool(torch.cuda.is_available())
        cuda_device_count = int(torch.cuda.device_count()) if cuda_available else 0
        if cuda_available:
            for index in range(cuda_device_count):
                try:
                    props = torch.cuda.get_device_properties(index)
                    free_mb = 0.0
                    total_mb = float(getattr(props, "total_memory", 0) or 0) / (1024 * 1024)
                    try:
                        free_bytes, total_bytes = torch.cuda.mem_get_info(index)
                        free_mb = float(free_bytes) / (1024 * 1024)
                        total_mb = float(total_bytes) / (1024 * 1024)
                    except Exception as exc:  # pragma: no cover - depends on CUDA runtime.
                        warnings.append(f"cuda_mem_get_info_failed:{index}:{exc}")
                    capability = f"{getattr(props, 'major', 0)}.{getattr(props, 'minor', 0)}"
                    devices.append(
                        ComputeDeviceRecord(
                            device_id=f"cuda:{index}",
                            device_type=ComputeDeviceType.CUDA,
                            name=torch.cuda.get_device_name(index),
                            index=index,
                            total_memory_mb=total_mb,
                            free_memory_mb=free_mb,
                            capability=capability,
                            torch_available=True,
                            cuda_available=True,
                            cuda_version=str(getattr(torch.version, "cuda", None)),
                            visible=True,
                            metadata={"multi_processor_count": int(getattr(props, "multi_processor_count", 0) or 0)},
                        )
                    )
                except Exception as exc:  # pragma: no cover - defensive.
                    warnings.append(f"cuda_device_probe_failed:{index}:{exc}")
    except Exception as exc:
        warnings.append(f"torch_probe_failed:{exc}")

    if not devices:
        devices.append(
            ComputeDeviceRecord(
                device_id="cpu:0",
                device_type=ComputeDeviceType.CPU,
                name=platform.processor() or "cpu",
                index=0,
                torch_available=torch_version is not None,
                cuda_available=False,
                visible=True,
            )
        )

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=uuid,driver_version", "--format=csv,noheader"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            if out.returncode == 0:
                rows = [line.strip().split(",") for line in out.stdout.splitlines() if line.strip()]
                for idx, row in enumerate(rows):
                    if idx < len(devices) and devices[idx].device_type == ComputeDeviceType.CUDA:
                        payload = devices[idx].to_dict()
                        payload["uuid"] = row[0].strip() if row else None
                        payload["driver_version"] = row[1].strip() if len(row) > 1 else None
                        devices[idx] = ComputeDeviceRecord(**payload)
            else:
                warnings.append("nvidia_smi_returned_nonzero")
        except Exception as exc:  # pragma: no cover - optional utility.
            warnings.append(f"nvidia_smi_probe_failed:{exc}")

    return ComputeResourceSnapshot(
        captured_at=_utc_now(),
        cpu_count=os.cpu_count() or 1,
        memory_total_mb=_memory_info().get("total_mb", 0.0),
        memory_available_mb=_memory_info().get("available_mb", 0.0),
        torch_version=torch_version,
        cuda_available=cuda_available,
        cuda_device_count=cuda_device_count,
        devices=devices,
        warnings=warnings,
    )


def write_resource_snapshot(snapshot: ComputeResourceSnapshot, output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(
        output / "compute_resource_snapshot.json",
        snapshot.to_dict(),
        "compute_resource_snapshot",
        "compute_cluster",
    )
    md_path = output / "compute_resource_snapshot.md"
    md_path.write_text(_render_snapshot_md(snapshot), encoding="utf-8")
    return json_path, md_path


def _memory_info() -> dict[str, float]:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return {"total_mb": 0.0, "available_mb": 0.0}
    values: dict[str, float] = {}
    for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in {"MemTotal", "MemAvailable"}:
            values[parts[0].rstrip(":")] = float(parts[1]) / 1024.0
    return {"total_mb": values.get("MemTotal", 0.0), "available_mb": values.get("MemAvailable", 0.0)}


def _render_snapshot_md(snapshot: ComputeResourceSnapshot) -> str:
    lines = [
        "# Compute Resource Snapshot",
        "",
        f"- captured_at: `{snapshot.captured_at}`",
        f"- cuda_available: `{snapshot.cuda_available}`",
        f"- cuda_device_count: {snapshot.cuda_device_count}",
        f"- cpu_count: {snapshot.cpu_count}",
        "",
        "| device | type | name | memory_mb | free_mb | visible |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for device in snapshot.devices:
        lines.append(
            f"| {device.device_id} | {device.device_type} | {device.name} | "
            f"{device.total_memory_mb:.1f} | {device.free_memory_mb:.1f} | {device.visible} |"
        )
    if snapshot.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in snapshot.warnings)
    return "\n".join(lines) + "\n"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
