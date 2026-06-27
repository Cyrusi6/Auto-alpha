"""Minimal Tushare Pro HTTP client using the Python standard library."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from ..config import AShareDataConfig


class TushareApiError(ValueError):
    """Raised when the Tushare API returns an error or unusable payload."""


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
        if not self.token:
            raise ValueError("TUSHARE_TOKEN is required for provider=tushare")

        body = {
            "api_name": api_name,
            "token": self.token,
            "params": {} if params is None else dict(params),
            "fields": self._format_fields(fields),
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        response_payload = self._send_with_retry(request)
        code = response_payload.get("code", 0)
        if code != 0:
            message = response_payload.get("msg") or f"Tushare API returned code {code}"
            raise TushareApiError(str(message))

        data = response_payload.get("data") or {}
        response_fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(response_fields, list) or not isinstance(items, list):
            raise TushareApiError("Tushare response data.fields/data.items must be lists")

        return [dict(zip(response_fields, item)) for item in items]

    def _send_with_retry(self, request: urllib.request.Request) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max(1, self.retry_count)):
            try:
                with self._urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TushareApiError("Tushare response must be a JSON object")
                return payload
            except TushareApiError:
                raise
            except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < max(1, self.retry_count):
                    time.sleep(min(0.2 * (attempt + 1), 1.0))

        raise TushareApiError(f"Tushare HTTP request failed: {last_error}") from last_error

    @staticmethod
    def _format_fields(fields: str | Iterable[str] | None) -> str:
        if fields is None:
            return ""
        if isinstance(fields, str):
            return fields
        return ",".join(fields)
