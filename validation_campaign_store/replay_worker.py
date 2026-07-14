"""Subprocess entrypoint that records terminal replay telemetry."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a validation replay with terminal telemetry.")
    parser.add_argument("--entrypoint", default="validation_lab.run_validation")
    parser.add_argument("--telemetry-path", required=True)
    parser.add_argument("--candidate-pool-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    forwarded = list(options.args)
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]

    telemetry_path = Path(options.telemetry_path)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_pool_path = Path(options.candidate_pool_path)
    output_dir = Path(options.output_dir)
    started_at = _utc_now()
    started = time.perf_counter()
    cuda = _cuda_start(options.require_cuda)
    exit_code = 1
    error = None
    try:
        if options.require_cuda and not cuda.get("cuda_available"):
            raise RuntimeError("CUDA is required for Task 052-A replay")
        module = importlib.import_module(options.entrypoint)
        exit_code = int(module.main(forwarded) or 0)
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        exit_code = 1
    finally:
        cuda.update(_cuda_finish(cuda))
        payload = {
            "schema_version": "task_052a_replay_telemetry_v1",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "wall_time_seconds": float(time.perf_counter() - started),
            "exit_code": exit_code,
            "error": error,
            "candidate_ids": _candidate_ids(candidate_pool_path),
            "candidate_pool_sha256": _sha256(candidate_pool_path),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "physical_gpus": _physical_gpus() if options.require_cuda else [],
            **cuda,
            "terminal_outputs": _terminal_outputs(output_dir),
        }
        temporary = telemetry_path.with_suffix(telemetry_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, telemetry_path)
    return exit_code


def _cuda_start(enabled: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cuda_available": False,
        "cuda_memory_allocated_start_bytes": 0,
        "cuda_memory_allocated_end_bytes": 0,
        "cuda_peak_memory_allocated_bytes": 0,
        "cuda_kernel_elapsed_ms": None,
    }
    if not enabled:
        return payload
    try:
        import torch

        payload["cuda_available"] = bool(torch.cuda.is_available())
        if payload["cuda_available"]:
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            payload["cuda_memory_allocated_start_bytes"] = int(torch.cuda.memory_allocated())
            start_event = torch.cuda.Event(enable_timing=True)
            end_event = torch.cuda.Event(enable_timing=True)
            start_event.record()
            payload["_cuda_start_event"] = start_event
            payload["_cuda_end_event"] = end_event
    except Exception as exc:
        payload["cuda_telemetry_error"] = str(exc)
    return payload


def _cuda_finish(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        import torch

        if payload.get("cuda_available"):
            end_event = payload.pop("_cuda_end_event")
            start_event = payload.pop("_cuda_start_event")
            end_event.record()
            torch.cuda.synchronize()
            result["cuda_memory_allocated_end_bytes"] = int(torch.cuda.memory_allocated())
            result["cuda_peak_memory_allocated_bytes"] = int(torch.cuda.max_memory_allocated())
            result["cuda_kernel_elapsed_ms"] = float(start_event.elapsed_time(end_event))
    except Exception as exc:
        result["cuda_telemetry_error"] = str(exc)
    payload.pop("_cuda_start_event", None)
    payload.pop("_cuda_end_event", None)
    return result


def _physical_gpus() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,name", "--format=csv,noheader"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    visible = {
        int(value)
        for value in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
        if value.strip().isdigit()
    }
    records = []
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", 2)]
        if len(fields) == 3:
            physical_index = int(fields[0])
            if not visible or physical_index in visible:
                records.append({"physical_index": physical_index, "uuid": fields[1], "model": fields[2]})
    return records


def _candidate_ids(path: Path) -> list[str]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        candidate_id = row.get("validation_candidate_id") or row.get("factor_id")
        if candidate_id:
            records.append(str(candidate_id))
    return records


def _terminal_outputs(output_dir: Path) -> dict[str, dict[str, Any]]:
    outputs = {}
    for name in ("validation_candidate_pool_report.json", "validation_candidate_pool_results.jsonl"):
        path = output_dir / name
        outputs[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": _sha256(path) if path.is_file() else None,
        }
    return outputs


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
