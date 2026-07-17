from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_055_h.io import canonical_hash


class AuthorityLedgerError(RuntimeError):
    pass


class HashChainLedger:
    def __init__(self, root: str | Path, *, name: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise AuthorityLedgerError("authority_ledger_root_symlink")
        self.name = str(name)
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / "ledger.lock"
        self.lock_path.touch(exist_ok=True)

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise AuthorityLedgerError("authority_event_id_missing")
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                events = self._read_unlocked()
                prior = next((row for row in events if row["event_id"] == event_id), None)
                semantic = dict(event)
                if prior is not None:
                    prior_semantic = {
                        key: value
                        for key, value in prior.items()
                        if key not in {"sequence", "previous_event_hash", "event_hash"}
                    }
                    if prior_semantic != semantic:
                        raise AuthorityLedgerError("authority_event_id_payload_conflict")
                    return prior
                row = semantic | {
                    "sequence": len(events) + 1,
                    "previous_event_hash": events[-1]["event_hash"] if events else "",
                }
                row["event_hash"] = canonical_hash(row)
                with self.events_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
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

    def root_hash(self) -> str:
        rows = self.rows()
        return rows[-1]["event_hash"] if rows else canonical_hash([])

    def assert_ancestor(self, *, sequence: int, event_hash: str) -> None:
        rows = self.rows()
        if sequence <= 0 or sequence > len(rows) or rows[sequence - 1].get("event_hash") != event_hash:
            raise AuthorityLedgerError("authority_ledger_ancestor_mismatch")

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
                raise AuthorityLedgerError(f"{self.name}_ledger_chain_invalid")
            ids.add(event_id)
            previous = str(row["event_hash"])
        return rows


def count_events(rows: Sequence[Mapping[str, Any]], event: str) -> int:
    return sum(str(row.get("event")) == event for row in rows)


def terminal_for_transport(rows: Sequence[Mapping[str, Any]], transport_hash: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row.get("event") == "request_terminal" and row.get("transport_hash") == transport_hash
    ]
