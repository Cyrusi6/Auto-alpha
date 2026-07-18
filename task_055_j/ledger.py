from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_055_h.io import atomic_json, canonical_hash


class Task055JLedgerError(RuntimeError):
    pass


class DurableHashJournal:
    """Append-only, fsync'd journal anchored by an external reviewed seal.

    Local storage alone cannot prevent an administrator from rolling back the
    whole directory.  Task 055-J therefore requires the reviewed final seal's
    initial journal checkpoint as an external ancestor at every execution.
    """

    def __init__(self, root: str | Path, *, name: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise Task055JLedgerError("task055j_journal_root_symlink")
        self.name = name
        self.events_path = self.root / "events.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.lock_path = self.root / "journal.lock"
        self.lock_path.touch(exist_ok=True)
        if self.lock_path.is_symlink():
            raise Task055JLedgerError("task055j_journal_lock_symlink")

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise Task055JLedgerError("task055j_journal_event_id_missing")
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                rows = self._read_unlocked()
                existing = next((row for row in rows if row["event_id"] == event_id), None)
                semantic = dict(event)
                if existing is not None:
                    prior = {
                        key: value
                        for key, value in existing.items()
                        if key not in {"sequence", "previous_event_hash", "event_hash"}
                    }
                    if prior != semantic:
                        raise Task055JLedgerError("task055j_journal_event_conflict")
                    return existing
                row = semantic | {
                    "sequence": len(rows) + 1,
                    "previous_event_hash": rows[-1]["event_hash"] if rows else "",
                }
                row["event_hash"] = canonical_hash(row)
                with self.events_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                atomic_json(
                    self.checkpoint_path,
                    {"name": self.name, "sequence": row["sequence"], "root": row["event_hash"]},
                )
                _fsync_dir(self.root)
                return row
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def rows(self) -> list[dict[str, Any]]:
        with self.lock_path.open("r") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                return self._read_unlocked()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def checkpoint(self) -> dict[str, Any]:
        rows = self.rows()
        expected = {
            "name": self.name,
            "sequence": len(rows),
            "root": rows[-1]["event_hash"] if rows else canonical_hash([]),
        }
        if self.checkpoint_path.is_file():
            actual = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if actual != expected:
                raise Task055JLedgerError(f"task055j_{self.name}_checkpoint_mismatch")
        elif rows:
            raise Task055JLedgerError(f"task055j_{self.name}_checkpoint_missing")
        return expected

    def assert_ancestor(self, checkpoint: Mapping[str, Any]) -> None:
        rows = self.rows()
        sequence = int(checkpoint.get("sequence") or 0)
        root = str(checkpoint.get("root") or "")
        if sequence <= 0 or sequence > len(rows) or rows[sequence - 1].get("event_hash") != root:
            raise Task055JLedgerError(f"task055j_{self.name}_reviewed_ancestor_mismatch")

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        rows = [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        previous = ""
        ids: set[str] = set()
        for sequence, row in enumerate(rows, start=1):
            unsigned = {key: value for key, value in row.items() if key != "event_hash"}
            event_id = str(row.get("event_id") or "")
            if (
                not event_id
                or event_id in ids
                or row.get("sequence") != sequence
                or row.get("previous_event_hash") != previous
                or canonical_hash(unsigned) != row.get("event_hash")
            ):
                raise Task055JLedgerError(f"task055j_{self.name}_journal_chain_invalid")
            ids.add(event_id)
            previous = str(row["event_hash"])
        return rows


def event_rows(rows: Sequence[Mapping[str, Any]], *, event: str, attempt_id: str | None = None) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row.get("event") == event and (attempt_id is None or row.get("attempt_id") == attempt_id)
    ]


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
