"""Security boundary for governed Tushare network access."""
from __future__ import annotations

import os
import socket
import ssl
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

CANONICAL_TUSHARE_ORIGIN = "https://api.tushare.pro"


class TushareSecurityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialStatus:
    token: str | None
    credential_present: bool
    source_type: str


def load_tushare_credential(environ: dict[str, str] | None = None) -> CredentialStatus:
    env = os.environ if environ is None else environ
    inline = env.get("TUSHARE_TOKEN") or None
    file_name = env.get("TUSHARE_TOKEN_FILE") or None
    if inline and file_name:
        raise TushareSecurityError("multiple_tushare_credential_sources")
    if inline:
        token = inline.strip()
        if not token:
            return CredentialStatus(None, False, "none")
        return CredentialStatus(token, True, "environment")
    if not file_name:
        return CredentialStatus(None, False, "none")
    path = Path(file_name)
    if not path.is_absolute():
        raise TushareSecurityError("credential_file_must_be_absolute")
    if path.is_symlink():
        raise TushareSecurityError("credential_file_symlink_forbidden")
    resolved = path.resolve(strict=True)
    forbidden = [Path.cwd().resolve()]
    for name in ("ASHARE_DATA_DIR", "ASHARE_REAL_DATA_ROOT", "ASHARE_REAL_DATA_OUTPUT_DIR"):
        if env.get(name):
            forbidden.append(Path(env[name]).expanduser().resolve())
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise TushareSecurityError("credential_file_inside_repo_or_output_forbidden")
    stat = path.stat()
    if stat.st_uid != os.getuid():
        raise TushareSecurityError("credential_file_owner_invalid")
    if stat_module.S_IMODE(stat.st_mode) not in {0o400, 0o600}:
        raise TushareSecurityError("credential_file_permissions_invalid")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise TushareSecurityError("credential_file_empty")
    return CredentialStatus(token, True, "credential_file")


def scan_for_secret_leakage(
    roots: list[str | Path], *, sentinel: str | None = None
) -> dict[str, object]:
    """Scan governed outputs for an exact test sentinel without deriving it."""

    needle = sentinel.encode("utf-8") if sentinel else None
    matches: list[str] = []
    scanned = 0
    for root_value in roots:
        root = Path(root_value)
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            scanned += 1
            if needle and needle in path.read_bytes():
                matches.append(str(path))
    return {
        "status": "passed" if not matches else "failed",
        "scanned_file_count": scanned,
        "match_count": len(matches),
        "matched_paths": matches,
    }


def validate_tushare_origin(url: str, *, allow_fake_transport: bool = False) -> str:
    if allow_fake_transport:
        return url
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "api.tushare.pro" or parsed.port not in {None, 443}:
        raise TushareSecurityError("noncanonical_tushare_origin_forbidden")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise TushareSecurityError("tushare_origin_components_invalid")
    return CANONICAL_TUSHARE_ORIGIN


def tls_preflight(host: str = "api.tushare.pro", port: int = 443, timeout_seconds: float = 10.0) -> dict[str, object]:
    if host != "api.tushare.pro" or port != 443:
        raise TushareSecurityError("tls_preflight_origin_invalid")
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout_seconds) as raw:
        with context.wrap_socket(raw, server_hostname=host) as connection:
            certificate = connection.getpeercert()
            if not certificate:
                raise TushareSecurityError("tls_peer_certificate_missing")
            ssl.match_hostname(certificate, host)
            return {
                "status": "passed",
                "origin": CANONICAL_TUSHARE_ORIGIN,
                "hostname_verified": True,
                "certificate_verified": True,
                "tls_version": connection.version(),
                "credential_present": False,
                "source_type": "none",
            }
