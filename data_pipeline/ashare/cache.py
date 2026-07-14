"""Fail-closed local response cache for Tushare API calls."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .request_normalization import (
    normalize_tushare_request,
    stable_json_hash,
    tushare_code_semantic_hash,
    tushare_request_fingerprint,
)


CACHE_ENVELOPE_VERSION = "tushare_cache_envelope.v3"
LEGACY_CACHE_ENVELOPE_VERSION = "tushare_cache_envelope.v2"
PROVIDER_API_VERSION = "tushare_pro_http.v1"


class TushareCacheError(ValueError):
    """Raised when a cache entry cannot be trusted."""


class TushareCacheCorruptionError(TushareCacheError):
    """Raised for malformed, truncated, or tampered cache entries."""


class TushareCacheSchemaError(TushareCacheError):
    """Raised when a cache entry uses incompatible semantics."""


@dataclass(frozen=True)
class CacheReadResult:
    records: list[dict[str, Any]]
    path: Path
    hit: bool
    envelope: dict[str, Any] | None = None
    negative_attestation: dict[str, Any] | None = None


def tushare_cache_source_hash() -> str:
    """Hash the source files that define request, HTTP, and cache semantics."""

    package_dir = Path(__file__).resolve().parent
    paths = (
        Path(__file__).resolve(),
        package_dir / "request_normalization.py",
        package_dir / "dataset_registry.py",
        package_dir / "providers" / "tushare_client.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class TushareResponseCache:
    def __init__(self, data_dir: str | Path, enabled: bool = True):
        self.root_dir = Path(data_dir) / ".cache" / "tushare"
        self.enabled = enabled

    def read(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
        *,
        endpoint_schema_proof: dict[str, Any] | None = None,
        allow_legacy_source_semantics: bool = False,
    ) -> CacheReadResult | None:
        if not self.enabled:
            return None
        path = self.cache_path(api_name, params=params, fields=fields)
        if not path.exists():
            return CacheReadResult(records=[], path=path, hit=False)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TushareCacheCorruptionError(f"unreadable Tushare cache entry: {path}") from exc
        records, negative = self._validate_envelope(
            payload,
            api_name=api_name,
            params=params,
            fields=fields,
            endpoint_schema_proof=endpoint_schema_proof,
            allow_legacy_source_semantics=allow_legacy_source_semantics,
        )
        return CacheReadResult(records=records, path=path, hit=True, envelope=payload, negative_attestation=negative)

    def write(
        self,
        api_name: str,
        params: dict[str, Any] | None,
        fields: str | Iterable[str] | None,
        records: list[dict[str, Any]],
        *,
        response_code: int = 0,
        response_message: str = "",
        response_fields: Iterable[str] | None = None,
        item_count: int | None = None,
        response_fields_observed: bool = False,
        endpoint: str = "",
        provider_api_version: str = PROVIDER_API_VERSION,
    ) -> Path:
        path = self.cache_path(api_name, params=params, fields=fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized_request = normalize_tushare_request(api_name, params=params, fields=fields)
        fingerprint = tushare_request_fingerprint(api_name, params=params, fields=fields)
        normalized_response_fields = [] if response_fields is None else [str(field).strip() for field in response_fields if str(field).strip()]
        expected_count = len(records) if item_count is None else int(item_count)
        if expected_count != len(records):
            raise TushareCacheSchemaError("Tushare cache item_count does not match records")
        if not all(isinstance(record, dict) for record in records):
            raise TushareCacheSchemaError("Tushare cache records must be objects")
        if records and normalized_request["fields"] and not set(normalized_request["fields"]).issubset(normalized_response_fields):
            raise TushareCacheSchemaError("Tushare cache response omitted requested fields")
        records_hash = stable_json_hash(records)
        metadata = {
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "api_name": api_name,
            "params_hash": stable_json_hash(normalized_request["params"]),
            "records": expected_count,
        }
        negative_attestation = None
        if expected_count == 0:
            negative_attestation = {
                "assertion": "provider_returned_zero_rows",
                "request_fingerprint": fingerprint,
                "response_code": int(response_code),
                "item_count": 0,
                "response_fields_observed": bool(response_fields_observed),
            }
        payload = {
            "schema_version": CACHE_ENVELOPE_VERSION,
            "request": normalized_request,
            "request_fingerprint": fingerprint,
            "code_semantic_hash": tushare_code_semantic_hash(),
            "source_code_hash": tushare_cache_source_hash(),
            "provider": {
                "api_version": str(provider_api_version),
                "endpoint": str(endpoint),
            },
            "response": {
                "code": int(response_code),
                "message": str(response_message),
                "fields": normalized_response_fields,
                "fields_observed": bool(response_fields_observed),
                "item_count": expected_count,
                "records_sha256": records_hash,
                "complete": True,
            },
            "metadata": metadata,
            "negative_attestation": negative_attestation,
            "records": records,
        }
        _atomic_write_json(path, payload)
        return path

    def cache_path(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> Path:
        return self.root_dir / f"{tushare_request_fingerprint(api_name, params=params, fields=fields)}.json"

    def build_endpoint_schema_proof(
        self,
        api_name: str,
        fields: str | Iterable[str] | None,
        *,
        code_semantic_hash: str | None = None,
    ) -> dict[str, Any] | None:
        """Find a positive cached response proving the endpoint's response schema."""

        requested_fields = normalize_tushare_request(api_name, fields=fields)["fields"]
        candidates: list[dict[str, Any]] = []
        if not self.root_dir.exists():
            return None
        for path in sorted(self.root_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            request = payload.get("request")
            response = payload.get("response")
            records = payload.get("records")
            if not isinstance(request, dict) or not isinstance(response, dict) or not isinstance(records, list):
                continue
            if request.get("api_name") != api_name or request.get("fields") != requested_fields:
                continue
            if code_semantic_hash is not None and payload.get("code_semantic_hash") != code_semantic_hash:
                continue
            response_fields = response.get("fields")
            if (
                response.get("code") == 0
                and int(response.get("item_count", -1)) > 0
                and len(records) == int(response.get("item_count", -1))
                and isinstance(response_fields, list)
                and set(requested_fields).issubset(response_fields)
                and response.get("records_sha256") == stable_json_hash(records)
            ):
                candidates.append(
                    {
                        "api_name": api_name,
                        "requested_fields": requested_fields,
                        "response_fields": response_fields,
                        "code_semantic_hash": payload.get("code_semantic_hash"),
                        "source_cache_sha256": _sha256(path),
                        "source_request_fingerprint": payload.get("request_fingerprint"),
                    }
                )
        if not candidates:
            return None
        proof = min(candidates, key=lambda item: (str(item["source_cache_sha256"]), str(item["source_request_fingerprint"])))
        return {**proof, "proof_hash": stable_json_hash(proof)}

    def _validate_envelope(
        self,
        payload: Any,
        *,
        api_name: str,
        params: dict[str, Any] | None,
        fields: str | Iterable[str] | None,
        endpoint_schema_proof: dict[str, Any] | None,
        allow_legacy_source_semantics: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if not isinstance(payload, dict):
            raise TushareCacheCorruptionError("Tushare cache envelope must be an object")
        version = payload.get("schema_version")
        if version not in {CACHE_ENVELOPE_VERSION, LEGACY_CACHE_ENVELOPE_VERSION}:
            raise TushareCacheSchemaError("unsupported Tushare cache envelope version")
        expected_fingerprint = tushare_request_fingerprint(api_name, params=params, fields=fields)
        if payload.get("request_fingerprint") != expected_fingerprint:
            raise TushareCacheCorruptionError("Tushare cache request fingerprint mismatch")
        if payload.get("request") != normalize_tushare_request(api_name, params=params, fields=fields):
            raise TushareCacheCorruptionError("Tushare cache normalized request mismatch")
        if payload.get("code_semantic_hash") != tushare_code_semantic_hash():
            raise TushareCacheSchemaError("Tushare cache code semantic hash mismatch")
        if version == CACHE_ENVELOPE_VERSION and payload.get("source_code_hash") != tushare_cache_source_hash() and not allow_legacy_source_semantics:
            raise TushareCacheSchemaError("Tushare cache source semantic hash mismatch")
        response = payload.get("response")
        records = payload.get("records")
        if not isinstance(response, dict) or not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise TushareCacheCorruptionError("Tushare cache response schema is invalid")
        if response.get("complete") is not True or response.get("code") != 0:
            raise TushareCacheCorruptionError("Tushare cache response is not a complete successful response")
        try:
            item_count = int(response["item_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TushareCacheCorruptionError("Tushare cache item_count is invalid") from exc
        if item_count != len(records):
            raise TushareCacheCorruptionError("Tushare cache appears truncated")
        if response.get("records_sha256") != stable_json_hash(records):
            raise TushareCacheCorruptionError("Tushare cache records hash mismatch")
        response_fields = response.get("fields")
        if not isinstance(response_fields, list) or not all(isinstance(field, str) for field in response_fields):
            raise TushareCacheCorruptionError("Tushare cache response fields are invalid")
        requested_fields = normalize_tushare_request(api_name, params=params, fields=fields)["fields"]
        if records and requested_fields and not set(requested_fields).issubset(response_fields):
            raise TushareCacheSchemaError("Tushare cache response omitted requested fields")
        negative = payload.get("negative_attestation")
        if item_count == 0:
            if not isinstance(negative, dict) or negative.get("request_fingerprint") != expected_fingerprint:
                raise TushareCacheCorruptionError("empty Tushare cache entry lacks negative attestation")
            fields_observed = bool(response.get("fields_observed") or negative.get("response_fields_observed"))
            if not fields_observed and not _valid_endpoint_schema_proof(
                endpoint_schema_proof,
                api_name=api_name,
                requested_fields=requested_fields,
                code_semantic_hash=str(payload.get("code_semantic_hash") or ""),
            ):
                raise TushareCacheSchemaError("legacy empty Tushare cache requires endpoint schema proof")
        elif negative is not None:
            raise TushareCacheCorruptionError("non-empty Tushare cache entry has negative attestation")
        return list(records), negative if isinstance(negative, dict) else None


def _valid_endpoint_schema_proof(
    proof: dict[str, Any] | None,
    *,
    api_name: str,
    requested_fields: list[str],
    code_semantic_hash: str,
) -> bool:
    if not isinstance(proof, dict):
        return False
    unsigned = {key: value for key, value in proof.items() if key != "proof_hash"}
    return (
        proof.get("proof_hash") == stable_json_hash(unsigned)
        and proof.get("api_name") == api_name
        and proof.get("requested_fields") == requested_fields
        and proof.get("code_semantic_hash") == code_semantic_hash
        and isinstance(proof.get("response_fields"), list)
        and set(requested_fields).issubset(proof["response_fields"])
        and bool(proof.get("source_cache_sha256"))
        and bool(proof.get("source_request_fingerprint"))
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
