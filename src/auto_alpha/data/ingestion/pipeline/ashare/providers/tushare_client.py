"""Minimal Tushare Pro HTTP client using the Python standard library."""

from __future__ import annotations

import json
import urllib.request
import gzip
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..config import AShareDataConfig
from ..network_capability import TushareExecutionCapability
from ..rate_limit import SimpleRateLimiter
from ..request_identity import TushareRequestIdentity
from ..request_normalization import stable_json_hash, tushare_code_semantic_hash


TUSHARE_PROVIDER_API_VERSION = "tushare_pro_http.v1"


class TushareApiError(ValueError):
    """Raised when the Tushare API returns an error or unusable payload."""


class TusharePermissionError(TushareApiError):
    """Raised when the token lacks permission or quota for an API."""


class TushareRateLimitError(TushareApiError):
    """Raised when the API reports rate limiting."""


class TushareSchemaError(TushareApiError):
    """Raised when the response envelope does not match Tushare's schema."""


class TushareNetworkError(TushareApiError):
    """Raised when the HTTP request fails before a valid response is parsed."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fail-closed redirect policy retained for security validation only."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        raise TushareNetworkError("Tushare redirect forbidden")


@dataclass(frozen=True)
class TushareResponseEnvelope:
    api_name: str
    params_without_token: dict[str, Any]
    requested_fields: str
    response_code: int
    response_message: str
    response_fields: list[str]
    records: list[dict[str, Any]]
    item_count: int
    duration_seconds: float
    request_fingerprint: str = ""
    transport_identity: str = ""
    evidence_use_identity: str = ""
    code_semantic_hash: str = ""
    endpoint: str = ""
    provider_api_version: str = TUSHARE_PROVIDER_API_VERSION
    response_payload_hash: str = ""
    response_payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_name": self.api_name,
            "params_without_token": self.params_without_token,
            "requested_fields": self.requested_fields,
            "response_code": self.response_code,
            "response_message": self.response_message,
            "response_fields": self.response_fields,
            "records": self.records,
            "item_count": self.item_count,
            "duration_seconds": self.duration_seconds,
            "request_fingerprint": self.request_fingerprint,
            "transport_identity": self.transport_identity,
            "evidence_use_identity": self.evidence_use_identity,
            "code_semantic_hash": self.code_semantic_hash,
            "endpoint": self.endpoint,
            "provider_api_version": self.provider_api_version,
            "response_payload_hash": self.response_payload_hash,
            "response_payload": self.response_payload,
        }


class TushareHttpClient:
    def __init__(
        self,
        config: AShareDataConfig,
        rate_limiter: SimpleRateLimiter | None = None,
        *,
        execution_capability: TushareExecutionCapability | None = None,
    ):
        del config, rate_limiter, execution_capability
        raise TushareNetworkError(
            "task055k_execution_capability_required:"
            "superseded_by_task055k_transport_broker:task055kr_canonical_transport_gateway"
        )

    def post(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        del api_name, params, fields
        raise TushareNetworkError(
            "superseded_by_task055k_transport_broker:task055kr_canonical_transport_gateway"
        )

    def post_with_metadata(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> TushareResponseEnvelope:
        del api_name, params, fields
        raise TushareNetworkError(
            "superseded_by_task055k_transport_broker:task055kr_canonical_transport_gateway"
        )

    @staticmethod
    def _format_fields(fields: str | Iterable[str] | None) -> str:
        if fields is None:
            return ""
        if isinstance(fields, str):
            return fields
        return ",".join(fields)


def serialize_tushare_request(
    *,
    endpoint: str,
    api_name: str,
    token: str,
    params: Mapping[str, Any] | None,
    fields: str | Iterable[str] | None,
) -> urllib.request.Request:
    request_fields = TushareHttpClient._format_fields(fields)
    body = {
        "api_name": api_name,
        "token": token,
        "params": dict(params or {}),
        "fields": request_fields,
    }
    return urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"},
        method="POST",
    )


def parse_tushare_response_payload(
    response_payload: Mapping[str, Any],
    *,
    api_name: str,
    params: Mapping[str, Any],
    requested_fields: str | Iterable[str],
    identity: TushareRequestIdentity,
    duration_seconds: float,
    endpoint: str,
) -> TushareResponseEnvelope:
    request_fields = TushareHttpClient._format_fields(requested_fields)
    code = int(response_payload.get("code", 0))
    message = str(response_payload.get("msg") or "")
    if code != 0:
        raise _error_for_response(code, message or f"Tushare API returned code {code}")
    data = response_payload.get("data")
    if not isinstance(data, dict):
        raise TushareSchemaError("Tushare response data must be an object")
    response_fields = data.get("fields")
    items = data.get("items")
    if not isinstance(response_fields, list) or not isinstance(items, list):
        raise TushareSchemaError("Tushare response data.fields/data.items must be lists")
    if not all(isinstance(field, str) for field in response_fields):
        raise TushareSchemaError("Tushare response data.fields must contain strings")
    if not all(isinstance(item, list) for item in items):
        raise TushareSchemaError("Tushare response data.items must contain row lists")
    if any(len(item) != len(response_fields) for item in items):
        raise TushareSchemaError("Tushare response row width does not match data.fields")
    requested = [field.strip() for field in request_fields.split(",") if field.strip()]
    if requested and not set(requested).issubset(response_fields):
        raise TushareSchemaError("Tushare response omitted requested fields")
    records = [dict(zip(response_fields, item)) for item in items]
    return TushareResponseEnvelope(
        api_name=api_name,
        params_without_token=dict(params),
        requested_fields=request_fields,
        response_code=code,
        response_message=message,
        response_fields=list(response_fields),
        records=records,
        item_count=len(items),
        duration_seconds=max(0.0, duration_seconds),
        request_fingerprint=identity.request_fingerprint,
        transport_identity=identity.transport_identity,
        evidence_use_identity=identity.evidence_use_identity,
        code_semantic_hash=tushare_code_semantic_hash(),
        endpoint=endpoint,
        provider_api_version=TUSHARE_PROVIDER_API_VERSION,
        response_payload_hash=stable_json_hash(dict(response_payload)),
        response_payload=dict(response_payload),
    )


def _error_for_response(code: int, message: str) -> TushareApiError:
    lowered = message.lower()
    if any(pattern in lowered for pattern in ("rate", "limit", "frequency")) or any(
        pattern in message for pattern in ("频次", "每分钟", "访问次数", "限流")
    ):
        return TushareRateLimitError(message)
    if any(pattern in lowered for pattern in ("permission", "privilege", "quota", "积分", "权限", "token")) or any(
        pattern in message for pattern in ("权限", "积分", "没有访问")
    ):
        return TusharePermissionError(message)
    return TushareApiError(message)


def _safe_error(error: Exception | None) -> str:
    if error is None:
        return "unknown error"
    return str(error).replace("\n", " ")


def _redact_secret(value: str, secret: str | None) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def _decode_response_body(raw: bytes, response: Any) -> str:
    encoding = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            encoding = str(headers.get("Content-Encoding", "") or "").lower()
        except AttributeError:
            encoding = ""
    if "gzip" in encoding or raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")
