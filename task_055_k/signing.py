from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass


class Task055KSigningError(RuntimeError):
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


def verify_signature(*, public_key_pem: bytes, payload: bytes, signature_b64: str) -> None:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except ValueError as exc:
        raise Task055KSigningError("task055k_receipt_signature_encoding_invalid") from exc
    key_fd = _memfd("task055k-public-key", public_key_pem)
    signature_fd = _memfd("task055k-signature", signature)
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
        raise Task055KSigningError("task055k_receipt_signature_invalid")


def _openssl(arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["openssl", *arguments],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise Task055KSigningError("task055k_openssl_operation_failed")
    return result.stdout


def _openssl_with_key(
    arguments: list[str],
    private_key_pem: bytes,
    *,
    input_bytes: bytes | None = None,
) -> bytes:
    key_fd = _memfd("task055k-private-key", private_key_pem)
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
        raise Task055KSigningError("task055k_openssl_key_operation_failed")
    return result.stdout


def _memfd(name: str, payload: bytes) -> int:
    if not hasattr(os, "memfd_create"):
        raise Task055KSigningError("task055k_memory_only_key_storage_unavailable")
    descriptor = os.memfd_create(name, flags=0)
    os.write(descriptor, payload)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor
