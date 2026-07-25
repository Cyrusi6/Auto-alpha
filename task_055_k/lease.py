from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


LEASE_SCHEMA = "task055kr2_replacement_safe_lease_v1"
FENCE_SCHEMA = "task055kr2_durable_fence_v1"
HISTORY_SCHEMA = "task055kr2_lease_history_event_v1"
_LEASE_XATTR = "user.task055kr2_lease_instance"


class Task055KLeaseError(RuntimeError):
    pass


@dataclass
class ReplacementSafeLease:
    parent: Path
    lock_name: str
    scope: str
    root_binding: str
    attempt: str
    parent_fd: int
    lease_fd: int
    identity: dict[str, Any]
    expected_bytes: bytes
    instance_secret: bytes = field(repr=False)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    _closed: bool = False

    @classmethod
    def acquire(
        cls,
        *,
        parent: str | Path,
        lock_name: str,
        scope: str,
        root_binding: str,
        attempt: str,
        expected_parent_identity: Mapping[str, Any] | None = None,
        allow_legacy_empty_bootstrap: bool = False,
    ) -> "ReplacementSafeLease":
        parent_path = _canonical_parent(parent)
        _validate_component_name(lock_name)
        parent_fd = os.open(
            parent_path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            parent_stat = os.fstat(parent_fd)
            _validate_parent_stat(parent_stat)
            if expected_parent_identity and (
                parent_stat.st_dev != expected_parent_identity.get("st_dev")
                or parent_stat.st_ino != expected_parent_identity.get("st_ino")
            ):
                raise Task055KLeaseError("task055kr2_canonical_parent_identity_invalid")
            fcntl.flock(parent_fd, fcntl.LOCK_EX)
            _validate_parent_path(parent_path, parent_fd)
            state_name, history_name = _lease_state_names(lock_name)
            state = _read_optional_json_at(parent_fd, state_name)
            history = _read_history_at(parent_fd, history_name)
            _validate_state_history(state, history, scope=scope, root_binding=root_binding)
            existing = _read_optional_record_at(parent_fd, lock_name)
            if state is None:
                if history:
                    raise Task055KLeaseError("task055kr2_fence_state_missing_with_history")
                if existing is not None and not (
                    allow_legacy_empty_bootstrap and existing[0] == b""
                ):
                    raise Task055KLeaseError("task055kr2_unsealed_existing_lock")
                prior_fence = 0
                prior_event_hash = ""
            else:
                _validate_prior_lock_record(existing, state)
                prior_fence = int(state["fence"])
                prior_event_hash = str(history[-1]["event_hash"])
            fence = prior_fence + 1
            nonce = secrets.token_hex(32)
            instance_secret = secrets.token_bytes(32)
            parent_identity = _parent_identity(parent_stat)
            owner = _owner_identity()
            unsigned = {
                "schema_version": LEASE_SCHEMA,
                "scope": scope,
                "root_binding": root_binding,
                "attempt": attempt,
                "lease_nonce": nonce,
                "fence": fence,
                "owner": owner,
                "canonical_parent_identity": parent_identity,
                "acquisition_sequence": len(history) + 1,
                "immutable": True,
            }
            digest = _hash(unsigned)
            record = unsigned | {
                "sealed_content_digest": digest,
                "lease_instance_xattr_hash": hashlib.sha256(instance_secret).hexdigest(),
            }
            encoded = _encode(record)
            lease_fd = _publish_lease_record(
                parent_fd,
                lock_name,
                encoded,
                instance_secret=instance_secret,
            )
            event = _history_event(
                event="lease_acquired" if state is None or not state.get("active") else "lease_takeover",
                identity=record,
                sequence=len(history) + 1,
                previous=prior_event_hash,
            )
            _append_history_at(parent_fd, history_name, event)
            fence_state = _fence_state(
                identity=record,
                history_root=event["event_hash"],
                history_sequence=event["sequence"],
                active=True,
            )
            _write_atomic_at(parent_fd, state_name, _encode(fence_state))
            lease = cls(
                parent=parent_path,
                lock_name=lock_name,
                scope=scope,
                root_binding=root_binding,
                attempt=attempt,
                parent_fd=parent_fd,
                lease_fd=lease_fd,
                identity=record,
                expected_bytes=encoded,
                instance_secret=instance_secret,
            )
            lease.checkpoint("acquisition_complete")
            return lease
        except Exception:
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
            finally:
                os.close(parent_fd)
            raise

    def checkpoint(self, boundary: str) -> dict[str, Any]:
        if self._closed:
            raise Task055KLeaseError(f"task055kr2_lease_closed:{boundary}")
        _validate_parent_path(self.parent, self.parent_fd)
        parent_stat = os.fstat(self.parent_fd)
        _validate_parent_stat(parent_stat)
        held = os.fstat(self.lease_fd)
        _validate_lease_stat(held)
        if held.st_nlink != 1:
            raise Task055KLeaseError(f"task055kr2_lease_lost_held_link_count:{boundary}")
        if _read_fd(self.lease_fd) != self.expected_bytes:
            raise Task055KLeaseError(f"task055kr2_lease_lost_held_content:{boundary}")
        if _get_xattr(self.lease_fd, _LEASE_XATTR) != self.instance_secret:
            raise Task055KLeaseError(f"task055kr2_lease_lost_held_instance:{boundary}")
        path_fd = _open_regular_at(self.parent_fd, self.lock_name)
        try:
            path_stat = os.fstat(path_fd)
            _validate_lease_stat(path_stat)
            if path_stat.st_nlink != 1:
                raise Task055KLeaseError(
                    f"task055kr2_lease_lost_path_link_count:{boundary}"
                )
            if _read_fd(path_fd) != self.expected_bytes:
                raise Task055KLeaseError(f"task055kr2_lease_lost_path_content:{boundary}")
            if _get_xattr(path_fd, _LEASE_XATTR) != self.instance_secret:
                raise Task055KLeaseError(f"task055kr2_lease_lost_path_instance:{boundary}")
            if (held.st_dev, held.st_ino) != (path_stat.st_dev, path_stat.st_ino):
                raise Task055KLeaseError(f"task055kr2_lease_lost_descriptor_path:{boundary}")
        finally:
            os.close(path_fd)
        state_name, history_name = _lease_state_names(self.lock_name)
        state = _read_required_json_at(self.parent_fd, state_name)
        history = _read_history_at(self.parent_fd, history_name)
        _validate_state_history(
            state,
            history,
            scope=self.scope,
            root_binding=self.root_binding,
        )
        expected = {
            "active": True,
            "scope": self.scope,
            "root_binding": self.root_binding,
            "attempt": self.attempt,
            "lease_nonce": self.identity["lease_nonce"],
            "fence": self.identity["fence"],
            "lease_content_digest": self.identity["sealed_content_digest"],
            "lease_instance_xattr_hash": self.identity["lease_instance_xattr_hash"],
        }
        if any(state.get(key) != value for key, value in expected.items()):
            raise Task055KLeaseError(f"task055kr2_lease_fenced:{boundary}")
        snapshot = {
            "boundary": boundary,
            "held_fd": self.lease_fd,
            "parent_fd": self.parent_fd,
            "held_dev": held.st_dev,
            "held_ino": held.st_ino,
            "path_dev": path_stat.st_dev,
            "path_ino": path_stat.st_ino,
            "lease_nonce": self.identity["lease_nonce"],
            "fence": self.identity["fence"],
            "sealed_content_digest": self.identity["sealed_content_digest"],
            "history_root": state["history_root"],
        }
        self.checkpoints.append(snapshot)
        return snapshot

    def binding(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "root_binding": self.root_binding,
            "attempt": self.attempt,
            "lease_nonce": self.identity["lease_nonce"],
            "fence": self.identity["fence"],
            "sealed_content_digest": self.identity["sealed_content_digest"],
            "lease_instance_xattr_hash": self.identity["lease_instance_xattr_hash"],
            "canonical_parent_identity": dict(
                self.identity["canonical_parent_identity"]
            ),
            "owner": dict(self.identity["owner"]),
        }

    def release(self) -> None:
        if self._closed:
            return
        lost: Exception | None = None
        try:
            self.checkpoint("owner_safe_release")
            state_name, history_name = _lease_state_names(self.lock_name)
            history = _read_history_at(self.parent_fd, history_name)
            event = _history_event(
                event="lease_released",
                identity=self.identity,
                sequence=len(history) + 1,
                previous=str(history[-1]["event_hash"]),
            )
            _append_history_at(self.parent_fd, history_name, event)
            state = _fence_state(
                identity=self.identity,
                history_root=event["event_hash"],
                history_sequence=event["sequence"],
                active=False,
            )
            _write_atomic_at(self.parent_fd, state_name, _encode(state))
        except Exception as exc:
            lost = exc
        finally:
            self._closed = True
            try:
                os.close(self.lease_fd)
            finally:
                try:
                    fcntl.flock(self.parent_fd, fcntl.LOCK_UN)
                finally:
                    os.close(self.parent_fd)
        if lost is not None:
            raise lost

    def abandon(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self.lease_fd)
        finally:
            try:
                fcntl.flock(self.parent_fd, fcntl.LOCK_UN)
            finally:
                os.close(self.parent_fd)

    def __enter__(self) -> "ReplacementSafeLease":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is None:
            self.release()
        else:
            try:
                self.release()
            except Exception:
                self.abandon()
        return False


def validate_historical_lease_binding(
    *, parent: str | Path, lock_name: str, binding: Mapping[str, Any]
) -> None:
    parent_path = _canonical_parent(parent)
    parent_fd = os.open(
        parent_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _validate_parent_path(parent_path, parent_fd)
        _state_name, history_name = _lease_state_names(lock_name)
        history = _read_history_at(parent_fd, history_name)
        acquired = [
            row
            for row in history
            if row.get("event") in {"lease_acquired", "lease_takeover"}
            and row.get("lease_nonce") == binding.get("lease_nonce")
            and row.get("fence") == binding.get("fence")
            and row.get("sealed_content_digest")
            == binding.get("sealed_content_digest")
            and row.get("scope") == binding.get("scope")
            and row.get("root_binding") == binding.get("root_binding")
            and row.get("attempt") == binding.get("attempt")
        ]
        if len(acquired) != 1:
            raise Task055KLeaseError("task055kr2_historical_lease_binding_invalid")
    finally:
        os.close(parent_fd)


def _canonical_parent(parent: str | Path) -> Path:
    path = Path(parent)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise Task055KLeaseError("task055kr2_canonical_parent_symlink")
    return path


def _validate_component_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise Task055KLeaseError("task055kr2_lease_component_invalid")


def _validate_parent_path(path: Path, held_fd: int) -> None:
    current_fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        held = os.fstat(held_fd)
        current = os.fstat(current_fd)
        _validate_parent_stat(held)
        _validate_parent_stat(current)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise Task055KLeaseError("task055kr2_canonical_parent_replaced")
    finally:
        os.close(current_fd)


def _validate_parent_stat(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink < 2:
        raise Task055KLeaseError("task055kr2_canonical_parent_invalid")


def _validate_lease_stat(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise Task055KLeaseError("task055kr2_lease_file_invalid")


def _parent_identity(metadata: os.stat_result) -> dict[str, Any]:
    return {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "mode": stat.S_IMODE(metadata.st_mode),
        "uid": metadata.st_uid,
    }


def _owner_identity() -> dict[str, Any]:
    start_ticks = "unknown"
    try:
        start_ticks = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError, IndexError):
        pass
    boot_id = "unknown"
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        pass
    return {
        "pid": os.getpid(),
        "uid": os.getuid(),
        "process_start_ticks": start_ticks,
        "boot_id": boot_id,
    }


def _lease_state_names(lock_name: str) -> tuple[str, str]:
    return f".{lock_name}.fence", f".{lock_name}.history"


def _publish_lease_record(
    parent_fd: int, name: str, payload: bytes, *, instance_secret: bytes
) -> int:
    temporary = f".{name}.tmp.{os.getpid()}.{secrets.token_hex(12)}"
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent_fd,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
        _set_xattr(descriptor, _LEASE_XATTR, instance_secret)
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        return descriptor
    except Exception:
        os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise


def _open_regular_at(parent_fd: int, name: str) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise Task055KLeaseError("task055kr2_lease_path_missing_or_unsafe") from exc
    try:
        _validate_lease_stat(os.fstat(descriptor))
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_optional_record_at(
    parent_fd: int, name: str
) -> tuple[bytes, os.stat_result, bytes | None] | None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Task055KLeaseError("task055kr2_existing_lock_unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise Task055KLeaseError("task055kr2_existing_lock_invalid")
        try:
            instance = _get_xattr(descriptor, _LEASE_XATTR)
        except Task055KLeaseError:
            instance = None
        return _read_fd(descriptor), metadata, instance
    finally:
        os.close(descriptor)


def _validate_prior_lock_record(
    existing: tuple[bytes, os.stat_result, bytes | None] | None,
    state: Mapping[str, Any],
) -> None:
    if existing is None:
        raise Task055KLeaseError("task055kr2_prior_lease_path_missing")
    payload, metadata, instance = existing
    _validate_lease_stat(metadata)
    try:
        row = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Task055KLeaseError("task055kr2_prior_lease_record_invalid") from None
    expected = {
        "scope": state.get("scope"),
        "root_binding": state.get("root_binding"),
        "attempt": state.get("attempt"),
        "lease_nonce": state.get("lease_nonce"),
        "fence": state.get("fence"),
        "sealed_content_digest": state.get("lease_content_digest"),
        "lease_instance_xattr_hash": state.get("lease_instance_xattr_hash"),
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise Task055KLeaseError("task055kr2_prior_lease_record_replaced")
    if instance is None or hashlib.sha256(instance).hexdigest() != state.get(
        "lease_instance_xattr_hash"
    ):
        raise Task055KLeaseError("task055kr2_prior_lease_instance_replaced")
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"sealed_content_digest", "lease_instance_xattr_hash"}
    }
    if _hash(unsigned) != row.get("sealed_content_digest"):
        raise Task055KLeaseError("task055kr2_prior_lease_digest_invalid")


def _fence_state(
    *, identity: Mapping[str, Any], history_root: str, history_sequence: int, active: bool
) -> dict[str, Any]:
    semantic = {
        "schema_version": FENCE_SCHEMA,
        "active": active,
        "scope": identity["scope"],
        "root_binding": identity["root_binding"],
        "attempt": identity["attempt"],
        "lease_nonce": identity["lease_nonce"],
        "fence": identity["fence"],
        "lease_content_digest": identity["sealed_content_digest"],
        "lease_instance_xattr_hash": identity["lease_instance_xattr_hash"],
        "canonical_parent_identity": identity["canonical_parent_identity"],
        "history_sequence": history_sequence,
        "history_root": history_root,
    }
    return semantic | {"content_hash": _hash(semantic)}


def _history_event(
    *, event: str, identity: Mapping[str, Any], sequence: int, previous: str
) -> dict[str, Any]:
    semantic = {
        "schema_version": HISTORY_SCHEMA,
        "event": event,
        "sequence": sequence,
        "previous_event_hash": previous,
        "scope": identity["scope"],
        "root_binding": identity["root_binding"],
        "attempt": identity["attempt"],
        "lease_nonce": identity["lease_nonce"],
        "fence": identity["fence"],
        "sealed_content_digest": identity["sealed_content_digest"],
        "lease_instance_xattr_hash": identity["lease_instance_xattr_hash"],
        "owner": identity["owner"],
    }
    return semantic | {"event_hash": _hash(semantic)}


def _validate_state_history(
    state: Mapping[str, Any] | None,
    history: list[dict[str, Any]],
    *,
    scope: str,
    root_binding: str,
) -> None:
    if state is None:
        if history:
            raise Task055KLeaseError("task055kr2_fence_state_missing")
        return
    unsigned = {key: value for key, value in state.items() if key != "content_hash"}
    if (
        state.get("schema_version") != FENCE_SCHEMA
        or _hash(unsigned) != state.get("content_hash")
        or state.get("scope") != scope
        or state.get("root_binding") != root_binding
        or not history
        or state.get("history_sequence") != len(history)
        or state.get("history_root") != history[-1].get("event_hash")
        or state.get("fence") != max(int(row["fence"]) for row in history)
    ):
        raise Task055KLeaseError("task055kr2_fence_state_history_invalid")


def _read_history_at(parent_fd: int, name: str) -> list[dict[str, Any]]:
    payload = _read_optional_bytes_at(parent_fd, name)
    if payload is None:
        return []
    try:
        rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line]
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Task055KLeaseError("task055kr2_lease_history_invalid") from None
    previous = ""
    for sequence, row in enumerate(rows, start=1):
        unsigned = {key: value for key, value in row.items() if key != "event_hash"}
        if (
            row.get("schema_version") != HISTORY_SCHEMA
            or row.get("sequence") != sequence
            or row.get("previous_event_hash") != previous
            or row.get("event_hash") != _hash(unsigned)
        ):
            raise Task055KLeaseError("task055kr2_lease_history_chain_invalid")
        previous = str(row["event_hash"])
    return rows


def _append_history_at(parent_fd: int, name: str, event: Mapping[str, Any]) -> None:
    descriptor = os.open(
        name,
        os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise Task055KLeaseError("task055kr2_lease_history_file_invalid")
        os.write(descriptor, _encode(dict(event)))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)


def _read_optional_json_at(parent_fd: int, name: str) -> dict[str, Any] | None:
    payload = _read_optional_bytes_at(parent_fd, name)
    if payload is None:
        return None
    try:
        row = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Task055KLeaseError("task055kr2_fence_state_invalid") from None
    if not isinstance(row, dict):
        raise Task055KLeaseError("task055kr2_fence_state_invalid")
    return row


def _read_required_json_at(parent_fd: int, name: str) -> dict[str, Any]:
    row = _read_optional_json_at(parent_fd, name)
    if row is None:
        raise Task055KLeaseError("task055kr2_fence_state_missing")
    return row


def _read_optional_bytes_at(parent_fd: int, name: str) -> bytes | None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Task055KLeaseError("task055kr2_state_file_unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise Task055KLeaseError("task055kr2_state_file_invalid")
        return _read_fd(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic_at(parent_fd: int, name: str, payload: bytes) -> None:
    temporary = f".{name}.tmp.{os.getpid()}.{secrets.token_hex(12)}"
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent_fd,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)


def _read_fd(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _set_xattr(descriptor: int, name: str, value: bytes) -> None:
    try:
        os.setxattr(descriptor, name, value)
    except OSError as exc:
        raise Task055KLeaseError("task055kr2_lease_xattr_unavailable") from exc


def _get_xattr(descriptor: int, name: str) -> bytes:
    try:
        return os.getxattr(descriptor, name)
    except OSError as exc:
        raise Task055KLeaseError("task055kr2_lease_xattr_missing") from exc


def _encode(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
