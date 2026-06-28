"""Helpers for local CI command execution."""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CiCommandResult:
    name: str
    command: list[str]
    returncode: int
    duration_seconds: float
    started_at: str
    finished_at: str
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["success"] = self.success
        return payload


def run_command(name: str, command: list[str], cwd: str | Path = ".") -> CiCommandResult:
    started_at = _utc_now()
    started = time.perf_counter()
    result = subprocess.run(command, cwd=Path(cwd), text=True, capture_output=True, check=False)
    return CiCommandResult(
        name=name,
        command=list(command),
        returncode=int(result.returncode),
        duration_seconds=float(time.perf_counter() - started),
        started_at=started_at,
        finished_at=_utc_now(),
        stdout_tail=result.stdout[-4000:],
        stderr_tail=result.stderr[-4000:],
    )


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
