"""Local response cache for Tushare API calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class CacheReadResult:
    records: list[dict[str, Any]]
    path: Path
    hit: bool


class TushareResponseCache:
    def __init__(self, data_dir: str | Path, enabled: bool = True):
        self.root_dir = Path(data_dir) / ".cache" / "tushare"
        self.enabled = enabled

    def read(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> CacheReadResult | None:
        if not self.enabled:
            return None
        path = self.cache_path(api_name, params=params, fields=fields)
        if not path.exists():
            return CacheReadResult(records=[], path=path, hit=False)
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") or []
        return CacheReadResult(records=list(records), path=path, hit=True)

    def write(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        fields: str | Iterable[str] | None,
        records: list[dict[str, Any]],
    ) -> Path:
        path = self.cache_path(api_name, params=params, fields=fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "api_name": api_name,
            "params_hash": _stable_hash(params or {}),
            "records": len(records),
        }
        path.write_text(
            json.dumps({"metadata": metadata, "records": records}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def cache_path(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> Path:
        payload = {
            "api_name": api_name,
            "params": params or {},
            "fields": _format_fields(fields),
        }
        return self.root_dir / f"{_stable_hash(payload)}.json"


def _format_fields(fields: str | Iterable[str] | None) -> str:
    if fields is None:
        return ""
    if isinstance(fields, str):
        return fields
    return ",".join(fields)


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
