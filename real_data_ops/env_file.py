"""Minimal env-file loading with token-safe reporting."""

from __future__ import annotations

import hashlib
from pathlib import Path


def load_env_file(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def merged_env_values(env_file_values: dict[str, str], environ: dict[str, str], cli_values: dict[str, str | None]) -> dict[str, str]:
    merged = dict(env_file_values)
    merged.update({key: value for key, value in environ.items() if value is not None})
    merged.update({key: value for key, value in cli_values.items() if value not in (None, "")})
    return {key: str(value) for key, value in merged.items() if value is not None}


def redacted_token_metadata(token: str | None) -> dict[str, object]:
    if not token:
        return {"token_present": False, "token_hash_prefix": None, "token_suffix_last4": None}
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return {
        "token_present": True,
        "token_hash_prefix": digest[:12],
        "token_suffix_last4": token[-4:],
    }
