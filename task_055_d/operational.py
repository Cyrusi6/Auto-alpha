"""Canonical operational-state roots and schema-aware empty-queue proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from task_055_a.run import PHYSICAL_STATE_NAMES


class OperationalStateError(RuntimeError):
    pass


def inspect_canonical_operational_root(governed_root: str | Path) -> dict[str, Any]:
    root = Path(governed_root).resolve()
    operational = root / "operational_state"
    result: dict[str, Any] = {"root": str(operational), "states": {}, "status": "passed"}
    seen: set[Path] = set()
    for name in PHYSICAL_STATE_NAMES:
        path = operational / name
        if path.is_symlink():
            raise OperationalStateError(f"operational_state_symlink_forbidden:{name}")
        resolved = path.resolve()
        if resolved in seen or (root != resolved and root not in resolved.parents):
            raise OperationalStateError(f"operational_state_path_invalid:{name}")
        seen.add(resolved)
        if not path.is_dir():
            result["states"][name] = {"status": "missing", "record_count": None, "content_root": None}
            result["status"] = "blocked"
            continue
        records = []
        unknown = []
        for candidate in sorted(value for value in path.rglob("*") if value.is_file()):
            relative = str(candidate.relative_to(path))
            if candidate.suffix == ".jsonl":
                count = 0
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise OperationalStateError(f"operational_state_record_invalid:{name}:{relative}")
                    count += 1
                records.append({"path": relative, "sha256": _sha(candidate), "size": candidate.stat().st_size, "record_count": count})
            elif candidate.suffix == ".json":
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    count = len(payload)
                elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
                    count = len(payload["records"])
                elif isinstance(payload, dict) and payload.get("schema_version") and payload.get("record_count") is not None:
                    count = int(payload["record_count"])
                else:
                    unknown.append(relative)
                    continue
                records.append({"path": relative, "sha256": _sha(candidate), "size": candidate.stat().st_size, "record_count": count})
            else:
                unknown.append(relative)
        if unknown:
            raise OperationalStateError(f"operational_state_unknown_nonempty_format:{name}:{','.join(unknown)}")
        count = sum(row["record_count"] for row in records)
        result["states"][name] = {"status": "empty" if count == 0 else "nonempty", "record_count": count, "files": records, "content_root": _content_root(records)}
        if count:
            result["status"] = "blocked"
    return result


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_root(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
