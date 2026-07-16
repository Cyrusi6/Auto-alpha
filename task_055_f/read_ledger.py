"""Audited, boundary-aware reads for Task 055-F."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from artifact_schema.writer import write_artifact_sidecar

from .contracts import MAX_DATE, READ_LEDGER_SCHEMA


class ReadLedgerError(RuntimeError):
    pass


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AuditedReader:
    """Read only governed files and record the actual opened boundary."""

    def __init__(self, governed_root: str | Path, *, max_date: str = MAX_DATE) -> None:
        self.governed_root = Path(governed_root).resolve()
        self.max_date = str(max_date)
        self.rows: list[dict[str, Any]] = []
        self._sequence = 0

    def read_json(
        self,
        path: str | Path,
        *,
        component: str,
        dataset: str,
        request_key: str | None = None,
        declared_start: str | None = None,
        declared_end: str | None = None,
    ) -> Any:
        source = self._source(path)
        data = source.read_bytes()
        payload = json.loads(data)
        actual_dates = list(_extract_dates(payload))
        self._record(
            source,
            component=component,
            dataset=dataset,
            request_key=request_key,
            declared_start=declared_start,
            declared_end=declared_end,
            actual_dates=actual_dates,
            data=data,
        )
        return payload

    def read_jsonl(
        self,
        path: str | Path,
        *,
        component: str,
        dataset: str,
        request_key: str | None = None,
        declared_start: str | None = None,
        declared_end: str | None = None,
    ) -> list[dict[str, Any]]:
        source = self._source(path)
        data = source.read_bytes()
        rows = [json.loads(line) for line in data.splitlines() if line.strip()]
        actual_dates = [date for row in rows for date in _extract_dates(row)]
        self._record(
            source,
            component=component,
            dataset=dataset,
            request_key=request_key,
            declared_start=declared_start,
            declared_end=declared_end,
            actual_dates=actual_dates,
            data=data,
        )
        return rows

    def record_binary(
        self,
        path: str | Path,
        *,
        component: str,
        dataset: str,
        request_key: str | None = None,
        declared_start: str | None = None,
        declared_end: str | None = None,
    ) -> Path:
        source = self._source(path)
        self._record(
            source,
            component=component,
            dataset=dataset,
            request_key=request_key,
            declared_start=declared_start,
            declared_end=declared_end,
            actual_dates=(),
            data=None,
            data_sha256=sha256_file(source),
            size_bytes=source.stat().st_size,
        )
        return source

    @property
    def max_read_date(self) -> str | None:
        values = [str(row["actual_max_date"]) for row in self.rows if row.get("actual_max_date")]
        return max(values) if values else None

    @property
    def prospective_holdout_accessed(self) -> bool:
        return bool(self.max_read_date and self.max_read_date > self.max_date)

    def publish(self, output_root: str | Path) -> dict[str, Any]:
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".task055f.reads.", dir=root))
        try:
            ledger = staging / "read_ledger.jsonl"
            ledger.write_text(
                "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in self.rows),
                encoding="utf-8",
            )
            write_artifact_sidecar(
                ledger,
                {
                    "artifact_type": "task055f_read_ledger_rows",
                    "schema_version": "1.0",
                    "producer": "task_055_f.read_ledger.AuditedReader",
                    "created_at": "1970-01-01T00:00:00Z",
                    "extra": {"record_count": len(self.rows)},
                },
            )
            partition = {
                "path": "read_ledger.jsonl",
                "sha256": sha256_file(ledger),
                "size_bytes": ledger.stat().st_size,
            }
            semantic = {
                "schema_version": READ_LEDGER_SCHEMA,
                "status": "published",
                "max_allowed_date": self.max_date,
                "record_count": len(self.rows),
                "max_read_date": self.max_read_date,
                "prospective_holdout_accessed": self.prospective_holdout_accessed,
                "rows_root": canonical_hash(self.rows),
                "partition": partition,
            }
            content_hash = canonical_hash(semantic)
            generation_id = f"read_ledger_{content_hash[:24]}"
            manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
            (staging / "read_ledger_manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            target = root / "generations" / generation_id
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(staging)
            else:
                os.replace(staging, target)
            _atomic_json(
                root / "current.json",
                {
                    "generation_id": generation_id,
                    "content_hash": content_hash,
                    "manifest": f"generations/{generation_id}/read_ledger_manifest.json",
                },
            )
            return manifest | {"manifest_path": str(target / "read_ledger_manifest.json")}
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _source(self, path: str | Path) -> Path:
        source = Path(path).resolve()
        if source != self.governed_root and self.governed_root not in source.parents:
            raise ReadLedgerError("read_path_outside_governed_root")
        if not source.is_file():
            raise ReadLedgerError("read_source_missing")
        original = Path(path)
        if original.is_symlink():
            raise ReadLedgerError("read_source_symlink_forbidden")
        return source

    def _record(
        self,
        source: Path,
        *,
        component: str,
        dataset: str,
        request_key: str | None,
        declared_start: str | None,
        declared_end: str | None,
        actual_dates: Iterable[str],
        data: bytes | None,
        data_sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        dates = sorted({date for date in map(str, actual_dates) if len(date) == 8 and date.isdigit()})
        actual_min = dates[0] if dates else None
        actual_max = dates[-1] if dates else None
        if declared_end and str(declared_end) > self.max_date:
            raise ReadLedgerError("declared_read_range_exceeds_boundary")
        self._sequence += 1
        row = {
            "sequence": self._sequence,
            "component": str(component),
            "dataset": str(dataset),
            "relative_path": str(source.relative_to(self.governed_root)),
            "sha256": data_sha256 or hashlib.sha256(data or b"").hexdigest(),
            "size_bytes": int(size_bytes if size_bytes is not None else len(data or b"")),
            "request_key": request_key,
            "declared_start_date": declared_start,
            "declared_end_date": declared_end,
            "actual_min_date": actual_min,
            "actual_max_date": actual_max,
            "policy_decision": "allowed" if not actual_max or actual_max <= self.max_date else "blocked_future_read",
        }
        row["row_hash"] = canonical_hash(row)
        self.rows.append(row)
        if actual_max and actual_max > self.max_date:
            raise ReadLedgerError(f"actual_read_date_exceeds_boundary:{actual_max}")


def validate_read_ledger(path: str | Path, *, governed_root: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != READ_LEDGER_SCHEMA or manifest.get("status") != "published":
        raise ReadLedgerError("read_ledger_manifest_invalid")
    ledger_path = manifest_path.parent / str((manifest.get("partition") or {}).get("path"))
    if not ledger_path.is_file() or sha256_file(ledger_path) != (manifest.get("partition") or {}).get("sha256"):
        raise ReadLedgerError("read_ledger_partition_mismatch")
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != manifest.get("record_count") or canonical_hash(rows) != manifest.get("rows_root"):
        raise ReadLedgerError("read_ledger_rows_mismatch")
    if [row.get("sequence") for row in rows] != list(range(1, len(rows) + 1)):
        raise ReadLedgerError("read_ledger_sequence_invalid")
    root = Path(governed_root).resolve()
    for row in rows:
        relative = Path(str(row.get("relative_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ReadLedgerError("read_ledger_relative_path_invalid")
        source = (root / relative).resolve()
        if not source.is_file() or root not in source.parents or sha256_file(source) != row.get("sha256"):
            raise ReadLedgerError("read_ledger_source_mismatch")
        unsigned = {key: value for key, value in row.items() if key != "row_hash"}
        if canonical_hash(unsigned) != row.get("row_hash"):
            raise ReadLedgerError("read_ledger_row_hash_mismatch")
        if row.get("actual_max_date") and row["actual_max_date"] > manifest.get("max_allowed_date"):
            raise ReadLedgerError("read_ledger_future_access_detected")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise ReadLedgerError("read_ledger_content_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def _extract_dates(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"trade_date", "start_date", "end_date", "requested_start_date", "requested_end_date"}:
                text = str(item or "").replace("-", "")
                if len(text) == 8 and text.isdigit():
                    yield text
            else:
                yield from _extract_dates(item)
    elif isinstance(value, list):
        for item in value:
            yield from _extract_dates(item)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
