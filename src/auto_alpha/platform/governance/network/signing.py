from __future__ import annotations

import base64
import fcntl
import os
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ReceiptSigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class EphemeralReceiptSigner:
    _private_key_pem: bytes
    public_key_pem: bytes

    @classmethod
    def generate(cls) -> "EphemeralReceiptSigner":
        private = _openssl(
            ["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"]
        )
        public = _openssl_with_key(["pkey", "-pubout", "-in", "{key}"], private)
        return cls(private, public)

    def sign(self, payload: bytes) -> str:
        signature = _openssl_with_key(
            ["dgst", "-sha256", "-sign", "{key}"],
            self._private_key_pem,
            input_bytes=payload,
        )
        return base64.b64encode(signature).decode("ascii")


@dataclass(frozen=True)
class PersistentReceiptSigner:
    """RSA receipt signer backed by one owner-only private-key file."""

    _private_key_pem: bytes
    public_key_pem: bytes

    @classmethod
    def open_or_create(cls, path: str | Path) -> "PersistentReceiptSigner":
        key_path = Path(path)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            key_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ReceiptSigningError("receipt_signing_key_read_failed") from exc
        else:
            return cls.load(key_path)
        private_key_pem = _openssl(
            ["genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"]
        )
        _validate_rsa_private_key(private_key_pem)
        try:
            _publish_private_key_once(key_path, private_key_pem)
        except FileExistsError:
            pass
        return cls.load(key_path)

    @classmethod
    def load(cls, path: str | Path) -> "PersistentReceiptSigner":
        private_key_pem = _read_private_key(Path(path))
        public_key_pem = _validate_rsa_private_key(private_key_pem)
        signer = cls(private_key_pem, public_key_pem)
        challenge = b"auto-alpha-persistent-receipt-signer-self-check-v1"
        verify_signature(
            public_key_pem=public_key_pem,
            payload=challenge,
            signature_b64=signer.sign(challenge),
        )
        return signer

    def sign(self, payload: bytes) -> str:
        signature = _openssl_with_key(
            ["dgst", "-sha256", "-sign", "{key}"],
            self._private_key_pem,
            input_bytes=payload,
        )
        return base64.b64encode(signature).decode("ascii")


def verify_signature(*, public_key_pem: bytes, payload: bytes, signature_b64: str) -> None:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except ValueError as exc:
        raise ReceiptSigningError("receipt_signature_encoding_invalid") from exc
    key_fd = _memfd("receipt-public-key", public_key_pem)
    signature_fd = _memfd("receipt-signature", signature)
    try:
        result = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                f"/proc/self/fd/{key_fd}",
                "-signature",
                f"/proc/self/fd/{signature_fd}",
            ],
            input=payload,
            pass_fds=(key_fd, signature_fd),
            capture_output=True,
            check=False,
        )
    finally:
        os.close(key_fd)
        os.close(signature_fd)
    if result.returncode != 0:
        raise ReceiptSigningError("receipt_signature_invalid")


def _openssl(arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["openssl", *arguments],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReceiptSigningError("openssl_operation_failed")
    return result.stdout


def _openssl_with_key(
    arguments: list[str],
    private_key_pem: bytes,
    *,
    input_bytes: bytes | None = None,
) -> bytes:
    key_fd = _memfd("receipt-private-key", private_key_pem)
    try:
        resolved = [value.replace("{key}", f"/proc/self/fd/{key_fd}") for value in arguments]
        result = subprocess.run(
            ["openssl", *resolved],
            input=input_bytes,
            pass_fds=(key_fd,),
            capture_output=True,
            check=False,
        )
    finally:
        os.close(key_fd)
    if result.returncode != 0:
        raise ReceiptSigningError("openssl_key_operation_failed")
    return result.stdout


def _memfd(name: str, payload: bytes) -> int:
    if not hasattr(os, "memfd_create"):
        raise ReceiptSigningError("memory_only_key_storage_unavailable")
    descriptor = os.memfd_create(name, flags=0)
    os.write(descriptor, payload)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor


def _publish_private_key_once(path: Path, payload: bytes) -> None:
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    lock_name = f".{path.name}.publish.lock"
    temporary = f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(12)}"
    lock_descriptor = -1
    descriptor = -1
    try:
        lock_descriptor = os.open(
            lock_name,
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        lock_metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.geteuid()
            or lock_metadata.st_nlink != 1
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise ReceiptSigningError("receipt_signing_key_publish_lock_invalid")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(path)
        descriptor = os.open(
            temporary,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.rename(
            temporary,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.fsync(parent_fd)
        if lock_descriptor >= 0:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        os.close(parent_fd)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ReceiptSigningError("receipt_signing_key_write_failed")
        view = view[written:]


def _read_private_key(path: Path) -> bytes:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ReceiptSigningError("receipt_signing_key_read_failed") from exc
    if stat.S_ISLNK(initial.st_mode):
        raise ReceiptSigningError("receipt_signing_key_symlink_forbidden")
    if not stat.S_ISREG(initial.st_mode):
        raise ReceiptSigningError("receipt_signing_key_regular_file_required")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise ReceiptSigningError("receipt_signing_key_read_failed") from exc
    try:
        metadata = os.fstat(descriptor)
        if (initial.st_dev, initial.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ReceiptSigningError("receipt_signing_key_changed_during_open")
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise ReceiptSigningError("receipt_signing_key_owner_regular_file_required")
        if metadata.st_nlink != 1:
            raise ReceiptSigningError("receipt_signing_key_hardlink_forbidden")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ReceiptSigningError("receipt_signing_key_permissions_invalid")
        if metadata.st_size <= 0 or metadata.st_size > 64 * 1024:
            raise ReceiptSigningError("receipt_signing_key_size_invalid")
        chunks: list[bytes] = []
        remaining = metadata.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as exc:
        raise ReceiptSigningError("receipt_signing_key_changed_during_read") from exc
    if (
        len(payload) != metadata.st_size
        or _stat_identity(after) != _stat_identity(metadata)
        or (final.st_dev, final.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise ReceiptSigningError("receipt_signing_key_changed_during_read")
    return payload


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_rsa_private_key(private_key_pem: bytes) -> bytes:
    if not re.fullmatch(
        rb"-----BEGIN (RSA PRIVATE KEY|PRIVATE KEY)-----\r?\n"
        rb"[A-Za-z0-9+/=\r\n]+"
        rb"-----END \1-----\r?\n?",
        private_key_pem,
    ):
        raise ReceiptSigningError("receipt_signing_key_pem_structure_invalid")
    try:
        details = _openssl_with_key(
            ["rsa", "-in", "{key}", "-check", "-text", "-noout"],
            private_key_pem,
        )
    except ReceiptSigningError as exc:
        raise ReceiptSigningError("receipt_signing_key_not_valid_rsa_private_key") from exc
    match = re.search(rb"Private-Key:\s*\((\d+) bit", details)
    if match is None:
        raise ReceiptSigningError("receipt_signing_key_rsa_size_unavailable")
    if int(match.group(1)) < 2048:
        raise ReceiptSigningError("receipt_signing_key_rsa_size_invalid")
    try:
        return _openssl_with_key(
            ["pkey", "-pubout", "-in", "{key}"], private_key_pem
        )
    except ReceiptSigningError as exc:
        raise ReceiptSigningError("receipt_signing_public_key_derivation_failed") from exc
