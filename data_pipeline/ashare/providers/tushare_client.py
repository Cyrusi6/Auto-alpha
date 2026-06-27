"""Minimal Tushare Pro HTTP client using the Python standard library."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..config import AShareDataConfig


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
        }


class TushareHttpClient:
    def __init__(
        self,
        config: AShareDataConfig,
        urlopen: Callable[..., Any] | None = None,
    ):
        self.api_url = config.tushare_api_url
        self.token = config.tushare_token
        self.timeout_seconds = config.tushare_timeout_seconds
        self.retry_count = config.tushare_retry_count
        self._urlopen = urllib.request.urlopen if urlopen is None else urlopen

    def post(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.post_with_metadata(api_name, params=params, fields=fields).records

    def post_with_metadata(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> TushareResponseEnvelope:
        if not self.token:
            raise ValueError("TUSHARE_TOKEN is required for provider=tushare")

        request_fields = self._format_fields(fields)
        request_params = {} if params is None else dict(params)
        body = {
            "api_name": api_name,
            "token": self.token,
            "params": request_params,
            "fields": request_fields,
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.perf_counter()
        response_payload = self._send_with_retry(request)
        code = response_payload.get("code", 0)
        message = str(response_payload.get("msg") or "")
        if code != 0:
            raise _error_for_response(int(code), message or f"Tushare API returned code {code}")

        data = response_payload.get("data") or {}
        if not isinstance(data, dict):
            raise TushareSchemaError("Tushare response data must be an object")
        response_fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(response_fields, list) or not isinstance(items, list):
            raise TushareSchemaError("Tushare response data.fields/data.items must be lists")
        if not all(isinstance(field, str) for field in response_fields):
            raise TushareSchemaError("Tushare response data.fields must contain strings")
        if not all(isinstance(item, list) for item in items):
            raise TushareSchemaError("Tushare response data.items must contain row lists")

        records = [dict(zip(response_fields, item)) for item in items]
        return TushareResponseEnvelope(
            api_name=api_name,
            params_without_token=request_params,
            requested_fields=request_fields,
            response_code=int(code),
            response_message=message,
            response_fields=list(response_fields),
            records=records,
            item_count=len(items),
            duration_seconds=max(0.0, time.perf_counter() - started),
        )

    def _send_with_retry(self, request: urllib.request.Request) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max(1, self.retry_count)):
            try:
                with self._urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TushareSchemaError("Tushare response must be a JSON object")
                return payload
            except (TushareApiError, TushareSchemaError):
                raise
            except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < max(1, self.retry_count):
                    time.sleep(min(0.2 * (attempt + 1), 1.0))

        raise TushareNetworkError(f"Tushare HTTP request failed: {_safe_error(last_error)}") from last_error

    @staticmethod
    def _format_fields(fields: str | Iterable[str] | None) -> str:
        if fields is None:
            return ""
        if isinstance(fields, str):
            return fields
        return ",".join(fields)


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
