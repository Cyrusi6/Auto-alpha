"""Tamper-evident read ledger and production component receipts."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeVar

import numpy as np


T = TypeVar("T")
READ_LEDGER_SCHEMA = "task_054b_audited_read_ledger_v1"
RECEIPT_SCHEMA = "task_054b_component_receipt_v1"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def semantic_source_hash(entrypoint: Callable[..., Any]) -> str:
    source = inspect.getsourcefile(entrypoint)
    if not source:
        raise RuntimeError(f"component entrypoint has no source file:{entrypoint!r}")
    return sha256_file(source)


class AuditedReadBroker:
    """Read adapter which writes the ledger at the actual open/load boundary."""

    def __init__(
        self,
        ledger_path: str | Path,
        *,
        invocation_id: str,
        principal: str,
        research_end_date: str,
    ):
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.invocation_id = invocation_id
        self.principal = principal
        self.research_end_date = research_end_date
        self._sequence = len(self.rows())

    def read_json(self, path: str | Path, *, component: str, dataset: str, date_range: Sequence[str] | None = None) -> Any:
        target = Path(path)
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self._record(target, component=component, dataset=dataset, date_range=date_range)
        return payload

    def read_jsonl(self, path: str | Path, *, component: str, dataset: str, date_range: Sequence[str] | None = None) -> list[dict[str, Any]]:
        target = Path(path)
        with target.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        self._record(target, component=component, dataset=dataset, date_range=date_range)
        return rows

    def load_npy(self, path: str | Path, *, component: str, dataset: str, date_range: Sequence[str] | None = None, mmap_mode: str | None = "r") -> np.ndarray:
        target = Path(path)
        array = np.load(target, mmap_mode=mmap_mode, allow_pickle=False)
        self._record(target, component=component, dataset=dataset, date_range=date_range)
        return array

    def verify_input(self, path: str | Path, *, component: str, dataset: str, date_range: Sequence[str] | None = None) -> str:
        target = Path(path)
        if not target.is_file():
            raise FileNotFoundError(target)
        digest = sha256_file(target)
        self._record(target, component=component, dataset=dataset, date_range=date_range, known_sha=digest)
        return digest

    def rows(self) -> list[dict[str, Any]]:
        if not self.ledger_path.is_file():
            return []
        return [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _record(
        self,
        path: Path,
        *,
        component: str,
        dataset: str,
        date_range: Sequence[str] | None,
        known_sha: str | None = None,
    ) -> None:
        resolved = path.resolve(strict=True)
        start, end = _normalized_date_range(date_range)
        allowed = not (self.principal == "research" and end and end > self.research_end_date)
        self._sequence += 1
        previous_hash = self.rows()[-1]["entry_hash"] if self.rows() else "0" * 64
        row = {
            "schema_version": READ_LEDGER_SCHEMA,
            "sequence": self._sequence,
            "invocation_id": self.invocation_id,
            "principal": self.principal,
            "component": component,
            "dataset": dataset,
            "artifact_id": resolved.name,
            "path_hash": hashlib.sha256(str(resolved).encode()).hexdigest(),
            "sha256": known_sha or sha256_file(resolved),
            "date_range": [start, end],
            "policy_decision": "allow" if allowed else "deny",
            "previous_entry_hash": previous_hash,
        }
        row["entry_hash"] = _hash_json(row)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not allowed:
            raise PermissionError(f"research read exceeds cutoff:{component}:{dataset}:{end}")


class ComponentReceiptRecorder:
    """Records a receipt around a direct call to a public production entrypoint."""

    def __init__(self, receipt_path: str | Path, *, invocation_id: str):
        self.receipt_path = Path(receipt_path)
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        self.invocation_id = invocation_id

    def invoke(
        self,
        component: str,
        entrypoint: Callable[..., T],
        *args: Any,
        input_artifacts: Mapping[str, str | Path],
        output_artifacts: Mapping[str, str | Path] | Callable[[T], Mapping[str, str | Path]],
        parent_receipt_hash: str | None = None,
        **kwargs: Any,
    ) -> T:
        inputs = _artifact_hashes(input_artifacts)
        started_ns = time.time_ns()
        status = "running"
        error = None
        try:
            result = entrypoint(*args, **kwargs)
            status = "success"
            return result
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}:{exc}"
            raise
        finally:
            resolved_outputs = (
                output_artifacts(result)
                if status == "success" and callable(output_artifacts)
                else ({} if callable(output_artifacts) else output_artifacts)
            )
            outputs = _artifact_hashes(resolved_outputs, require_exists=status == "success")
            receipt = {
                "schema_version": RECEIPT_SCHEMA,
                "invocation_id": self.invocation_id,
                "component": component,
                "entrypoint": f"{entrypoint.__module__}.{entrypoint.__qualname__}",
                "source_hash": semantic_source_hash(entrypoint),
                "input_artifacts": inputs,
                "output_artifacts": outputs,
                "started_ns": started_ns,
                "finished_ns": time.time_ns(),
                "status": status,
                "error": error,
                "parent_receipt_hash": parent_receipt_hash,
            }
            receipt["receipt_hash"] = _hash_json(receipt)
            with self.receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def rows(self) -> list[dict[str, Any]]:
        if not self.receipt_path.is_file():
            return []
        return [json.loads(line) for line in self.receipt_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_read_ledger(rows: Sequence[Mapping[str, Any]], *, invocation_id: str, require_research_safe: bool = True) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("production read ledger is empty")
    previous = "0" * 64
    for expected_sequence, source in enumerate(rows, start=1):
        row = dict(source)
        entry_hash = row.pop("entry_hash", None)
        if row.get("schema_version") != READ_LEDGER_SCHEMA or row.get("invocation_id") != invocation_id:
            raise RuntimeError("read ledger identity/schema mismatch")
        if int(row.get("sequence", 0)) != expected_sequence or row.get("previous_entry_hash") != previous:
            raise RuntimeError("read ledger chain mismatch")
        if entry_hash != _hash_json(row):
            raise RuntimeError("read ledger entry hash mismatch")
        if require_research_safe and row.get("principal") == "research" and row.get("policy_decision") != "allow":
            raise RuntimeError("research read ledger contains denied access")
        for field in ("component", "dataset", "artifact_id", "path_hash", "sha256"):
            if not row.get(field):
                raise RuntimeError(f"read ledger field missing:{field}")
        previous = str(entry_hash)
    return {"entry_count": len(rows), "ledger_root": previous}


def validate_component_receipts(
    rows: Sequence[Mapping[str, Any]],
    *,
    invocation_id: str,
    required_components: Sequence[str],
) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("production component receipts are empty")
    components: set[str] = set()
    roots: list[str] = []
    for source in rows:
        row = dict(source)
        receipt_hash = row.pop("receipt_hash", None)
        if row.get("schema_version") != RECEIPT_SCHEMA or row.get("invocation_id") != invocation_id:
            raise RuntimeError("component receipt identity/schema mismatch")
        if receipt_hash != _hash_json(row):
            raise RuntimeError("component receipt hash mismatch")
        if row.get("status") != "success":
            raise RuntimeError(f"component did not succeed:{row.get('component')}")
        if not row.get("entrypoint") or not row.get("source_hash"):
            raise RuntimeError("source-hash-only or entrypoint-less component evidence rejected")
        if not row.get("input_artifacts") or not row.get("output_artifacts"):
            raise RuntimeError(f"component artifact lineage incomplete:{row.get('component')}")
        components.add(str(row.get("component")))
        roots.append(str(receipt_hash))
    missing = sorted(set(required_components) - components)
    if missing:
        raise RuntimeError(f"component receipts incomplete:{missing}")
    return {"receipt_count": len(rows), "component_count": len(components), "receipt_root": _hash_json(sorted(roots))}


def atomic_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _artifact_hashes(artifacts: Mapping[str, str | Path], *, require_exists: bool = True) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, path in sorted(artifacts.items()):
        target = Path(path)
        if require_exists and not target.is_file():
            raise RuntimeError(f"component artifact missing:{name}:{target}")
        if target.is_file():
            result[name] = {"artifact_id": target.name, "path_hash": hashlib.sha256(str(target.resolve()).encode()).hexdigest(), "sha256": sha256_file(target)}
    return result


def _normalized_date_range(date_range: Sequence[str] | None) -> tuple[str | None, str | None]:
    values = [str(value).replace("-", "") for value in (date_range or []) if value]
    return (min(values), max(values)) if values else (None, None)


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
