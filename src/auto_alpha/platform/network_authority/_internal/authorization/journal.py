from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import ACCESS_JOURNAL_SCHEMA, MAX_DATE
from .io import canonical_hash, publish_generation, sha256_file, validate_generation


class DurableAccessError(RuntimeError):
    pass


class DurableAccessJournal:
    """Fsync pre-open and terminal evidence for every governed read."""

    def __init__(self, governed_root: str | Path, journal_root: str | Path) -> None:
        self.governed_root = Path(governed_root).resolve()
        self.root = Path(journal_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / "journal.lock"
        self.lock_path.touch(exist_ok=True)

    def read_bytes(
        self,
        relative_path: str | Path,
        *,
        principal: str,
        expected_sha256: str,
        declared_max_date: str | None,
        date_parser: str = "none",
    ) -> bytes:
        relative = _relative(relative_path)
        attempt_id = canonical_hash([principal, relative, self._next_sequence_hint()])
        if declared_max_date and declared_max_date > MAX_DATE:
            self._append({
                "event": "blocked_before_open",
                "attempt_id": attempt_id,
                "principal": principal,
                "relative_path": relative,
                "expected_sha256": expected_sha256,
                "declared_max_date": declared_max_date,
                "reason": "declared_date_exceeds_boundary",
            })
            raise DurableAccessError(f"declared_date_exceeds_boundary:{relative}")
        self._append({
            "event": "pre_open",
            "attempt_id": attempt_id,
            "principal": principal,
            "relative_path": relative,
            "expected_sha256": expected_sha256,
            "declared_max_date": declared_max_date,
            "date_parser": date_parser,
        })
        actual_sha: str | None = None
        actual_max: str | None = None
        try:
            source = self._resolve(relative)
            raw = source.read_bytes()
            actual_sha = canonical_bytes_hash(raw)
            actual_max = _max_date(raw, date_parser)
            if actual_sha != expected_sha256:
                raise DurableAccessError("sha256_mismatch")
            if actual_max and actual_max > MAX_DATE:
                self._append({
                    "event": "opened_policy_violation",
                    "attempt_id": attempt_id,
                    "principal": principal,
                    "relative_path": relative,
                    "actual_sha256": actual_sha,
                    "actual_max_date": actual_max,
                    "size_bytes": len(raw),
                    "reason": "actual_date_exceeds_boundary",
                    "opened": True,
                })
                raise DurableAccessError(f"actual_date_exceeds_boundary:{actual_max}")
        except Exception as exc:
            source_exists = "source" in locals() and source.is_file()
            if not (actual_max and actual_max > MAX_DATE):
                self._append({
                    "event": "terminal_failure",
                    "attempt_id": attempt_id,
                    "principal": principal,
                    "relative_path": relative,
                    "actual_sha256": actual_sha if actual_sha else (None if not source_exists else sha256_file(source)),
                    "actual_max_date": actual_max,
                    "reason": str(exc),
                    "opened": source_exists,
                })
            raise
        self._append({
            "event": "opened_allowed",
            "attempt_id": attempt_id,
            "principal": principal,
            "relative_path": relative,
            "actual_sha256": actual_sha,
            "actual_max_date": actual_max,
            "size_bytes": len(raw),
            "opened": True,
        })
        return raw

    def read_json(self, relative_path: str | Path, **kwargs: Any) -> dict[str, Any]:
        raw = self.read_bytes(relative_path, **kwargs)
        try:
            payload = json.loads(raw)
        except Exception as exc:
            self._append({
                "event": "parse_failure",
                "attempt_id": canonical_hash([kwargs.get("principal"), str(relative_path), "json_parse"]),
                "principal": kwargs.get("principal"),
                "relative_path": _relative(relative_path),
                "reason": str(exc),
                "opened": True,
            })
            raise
        if not isinstance(payload, dict):
            raise DurableAccessError("json_object_required")
        return payload

    def record_validator_exception(
        self,
        *,
        principal: str,
        manifest_relative_path: str,
        manifest_sha256: str,
        validator_fqn: str,
        result_content_hash: str,
    ) -> None:
        self._append({
            "event": "sealed_validator_exception",
            "attempt_id": canonical_hash([principal, manifest_relative_path, validator_fqn]),
            "principal": principal,
            "relative_path": _relative(manifest_relative_path),
            "expected_sha256": manifest_sha256,
            "validator_fqn": validator_fqn,
            "result_content_hash": result_content_hash,
            "reason": "authoritative_validator_reads_native_partition_closure",
        })

    def summary(self) -> dict[str, Any]:
        events = self._events()
        opened = [
            row
            for row in events
            if row.get("event") in {"opened_allowed", "opened_policy_violation", "terminal_failure"}
            and row.get("opened")
        ]
        dates = [str(row["actual_max_date"]) for row in opened if row.get("actual_max_date")]
        attempts = {
            str(row.get("attempt_id"))
            for row in events
            if row.get("event") in {"blocked_before_open", "pre_open"}
        }
        terminals = {
            str(row.get("attempt_id"))
            for row in events
            if row.get("event")
            in {"blocked_before_open", "opened_allowed", "opened_policy_violation", "terminal_failure"}
        }
        return {
            "event_count": len(events),
            "attempt_count": len(attempts),
            "terminal_count": len(terminals),
            "max_read_date": max(dates) if dates else None,
            "prospective_holdout_accessed": any(date > MAX_DATE for date in dates),
            "ledger_root": events[-1]["event_hash"] if events else canonical_hash([]),
        }

    def publish(self, output_root: str | Path) -> dict[str, Any]:
        events = self._events()
        rows = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in events)
        semantic = {
            "schema_version": ACCESS_JOURNAL_SCHEMA,
            "status": "published",
            **self.summary(),
            "partition": {"path": "events.jsonl", "sha256": canonical_bytes_hash(rows), "size_bytes": len(rows)},
        }
        return publish_generation(
            output_root,
            prefix="access_journal",
            manifest_name="access_journal.json",
            semantic=semantic,
            extra_files={"events.jsonl": rows},
        )

    def _resolve(self, relative: str) -> Path:
        source = (self.governed_root / relative).resolve()
        if self.governed_root not in source.parents or source.is_symlink() or not source.is_file():
            raise DurableAccessError(f"governed_source_missing_or_escape:{relative}")
        return source

    def _append(self, raw: Mapping[str, Any]) -> None:
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                events = self._events_unlocked()
                row = dict(raw) | {
                    "sequence": len(events) + 1,
                    "previous_event_hash": events[-1]["event_hash"] if events else "",
                }
                row["event_hash"] = canonical_hash(row)
                with self.events_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _events(self) -> list[dict[str, Any]]:
        with self.lock_path.open("r") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                return self._events_unlocked()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _events_unlocked(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        rows = [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]
        previous = ""
        for index, row in enumerate(rows, 1):
            unsigned = {key: value for key, value in row.items() if key != "event_hash"}
            if row.get("sequence") != index or row.get("previous_event_hash") != previous or canonical_hash(unsigned) != row.get("event_hash"):
                raise DurableAccessError("access_journal_chain_invalid")
            previous = str(row["event_hash"])
        return rows

    def _next_sequence_hint(self) -> int:
        return len(self._events()) + 1


def validate_access_journal(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=ACCESS_JOURNAL_SCHEMA, manifest_name="access_journal.json")
    partition = payload.get("partition") or {}
    rows_path = Path(payload["manifest_path"]).parent / str(partition.get("path") or "")
    if not rows_path.is_file() or sha256_file(rows_path) != partition.get("sha256"):
        raise DurableAccessError("access_journal_partition_invalid")
    return payload


def canonical_bytes_hash(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _relative(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise DurableAccessError(f"relative_path_required:{value}")
    return path.as_posix()


def _max_date(raw: bytes, parser: str) -> str | None:
    if parser == "none":
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DurableAccessError(f"date_parser_json_invalid:{parser}") from exc
    fields_by_parser = {
        "report": {"max_read_date", "max_request_date", "execution_cutoff", "valuation_cutoff", "signal_cutoff"},
        "plan": {"trade_date", "max_request_date"},
        "ledger": {"actual_max_date", "max_read_date", "trade_date"},
        "fee": {"simulation_end", "effective_end"},
    }
    fields = fields_by_parser.get(parser, set())
    dates: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in fields and isinstance(child, (str, int)):
                    normalized = str(child).replace("-", "")
                    if len(normalized) == 8 and normalized.isdigit():
                        dates.append(normalized)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return max(dates) if dates else None
