"""Read-only broker client protocol and local implementations."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .models import BrokerConnectionProfile, BrokerConnectivityBlockedError, BrokerNetworkGuard
from .network_guard import enforce_readonly_method


class ReadOnlyBrokerClient(Protocol):
    def ping(self) -> dict[str, Any]: ...
    def get_server_time(self) -> dict[str, Any]: ...
    def get_account_snapshot(self) -> dict[str, Any]: ...
    def list_positions(self) -> list[dict[str, Any]]: ...
    def list_orders(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]: ...
    def list_fills(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]: ...
    def list_statements(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]: ...


class MockReadOnlyBrokerClient:
    def __init__(self, profile: BrokerConnectionProfile, account_id: str = "paper_account", trade_date: str = "20240104", as_of_date: str = "20240104"):
        self.profile = profile
        self.account_id = account_id or str(profile.metadata.get("account_id") or "paper_account")
        self.trade_date = trade_date
        self.as_of_date = as_of_date

    def ping(self) -> dict[str, Any]:
        return {"ok": True, "mode": self.profile.connectivity_mode, "broker_name": self.profile.broker_name}

    def get_server_time(self) -> dict[str, Any]:
        return {"server_time": _utc_now(), "timezone": "Asia/Shanghai"}

    def get_account_snapshot(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "broker_name": self.profile.broker_name,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "cash_balance": 1_000_000.0,
            "available_cash": 980_000.0,
            "withdrawable_cash": 970_000.0,
            "frozen_cash": 20_000.0,
            "metadata": {"fixture": True},
        }

    def list_positions(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        del start_date, end_date
        return [
            {
                "account_id": self.account_id,
                "broker_name": self.profile.broker_name,
                "trade_date": self.trade_date,
                "as_of_date": self.as_of_date,
                "ts_code": "000001.SZ",
                "position_shares": 1000,
                "available_shares": 800,
                "cost_basis": 9.5,
                "market_value": 10_000.0,
            },
            {
                "account_id": self.account_id,
                "broker_name": self.profile.broker_name,
                "trade_date": self.trade_date,
                "as_of_date": self.as_of_date,
                "ts_code": "600000.SH",
                "position_shares": 500,
                "available_shares": 500,
                "cost_basis": 10.0,
                "market_value": 5_500.0,
            },
        ]

    def list_orders(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        del start_date, end_date
        return [
            {
                "account_id": self.account_id,
                "broker_name": self.profile.broker_name,
                "trade_date": self.trade_date,
                "as_of_date": self.as_of_date,
                "external_order_id": "mock_order_1",
                "broker_order_id": "mock_order_1",
                "client_order_id": "client_mock_1",
                "ts_code": "000001.SZ",
                "side": "BUY",
                "price": 10.0,
                "shares": 100,
                "value": 1000.0,
                "status": "FILLED",
            }
        ]

    def list_fills(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        del start_date, end_date
        return [
            {
                "account_id": self.account_id,
                "broker_name": self.profile.broker_name,
                "trade_date": self.trade_date,
                "as_of_date": self.as_of_date,
                "external_fill_id": "mock_fill_1",
                "broker_fill_id": "mock_fill_1",
                "broker_order_id": "mock_order_1",
                "client_order_id": "client_mock_1",
                "ts_code": "000001.SZ",
                "side": "BUY",
                "price": 10.0,
                "shares": 100,
                "value": 1000.0,
                "commission": 5.0,
                "stamp_duty": 0.0,
                "transfer_fee": 0.1,
                "total_fee": 5.1,
                "status": "FILLED",
            }
        ]

    def list_statements(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        del start_date, end_date
        return [
            {
                "account_id": self.account_id,
                "broker_name": self.profile.broker_name,
                "trade_date": self.trade_date,
                "as_of_date": self.as_of_date,
                "statement_id": f"mock_stmt_{self.as_of_date}",
                "cash_balance": 1_000_000.0,
                "position_count": 2,
                "fill_count": 1,
                "metadata": {"fixture": True},
            }
        ]

    def submit_orders(self, *_args, **_kwargs):
        raise BrokerConnectivityBlockedError("read-only broker client blocks submit_orders")

    def cancel_order(self, *_args, **_kwargs):
        raise BrokerConnectivityBlockedError("read-only broker client blocks cancel_order")

    def replace_order(self, *_args, **_kwargs):
        raise BrokerConnectivityBlockedError("read-only broker client blocks replace_order")


class GenericHttpReadOnlyBrokerClient:
    def __init__(self, profile: BrokerConnectionProfile, guard: BrokerNetworkGuard, account_id: str = "", timeout_seconds: float | None = None):
        self.profile = profile
        self.guard = guard
        self.account_id = account_id
        self.timeout_seconds = float(timeout_seconds or profile.timeout_seconds)

    def ping(self) -> dict[str, Any]:
        return self._request("ping")

    def get_server_time(self) -> dict[str, Any]:
        return self._request("get_server_time")

    def get_account_snapshot(self) -> dict[str, Any]:
        return self._request("get_account_snapshot")

    def list_positions(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        return _as_list(self._request("list_positions", start_date, end_date))

    def list_orders(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        return _as_list(self._request("list_orders", start_date, end_date))

    def list_fills(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        return _as_list(self._request("list_fills", start_date, end_date))

    def list_statements(self, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        return _as_list(self._request("list_statements", start_date, end_date))

    def _request(self, method: str, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        enforce_readonly_method(self.profile, self.guard, method)
        endpoints = dict((self.profile.metadata or {}).get("endpoints") or {})
        endpoint = endpoints.get(method)
        if not endpoint:
            return {"status": "skipped", "reason": "endpoint_not_configured", "method": method}
        base = _env(self.profile.base_url_env_var)
        if not base:
            return {"status": "blocked", "reason": "base_url_missing", "method": method}
        url = base.rstrip("/") + "/" + str(endpoint).lstrip("/")
        payload = {"account_id": self.account_id, "start_date": start_date, "end_date": end_date}
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return parsed.get("records", parsed) if isinstance(parsed, dict) else parsed
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"status": "failed", "reason": type(exc).__name__, "method": method}

    def submit_orders(self, *_args, **_kwargs):
        raise BrokerConnectivityBlockedError("read-only broker client blocks submit_orders")

    def cancel_order(self, *_args, **_kwargs):
        raise BrokerConnectivityBlockedError("read-only broker client blocks cancel_order")

    def replace_order(self, *_args, **_kwargs):
        raise BrokerConnectivityBlockedError("read-only broker client blocks replace_order")


class QmtReadOnlySkeletonClient(MockReadOnlyBrokerClient):
    def ping(self) -> dict[str, Any]:
        return {"ok": False, "status": "skipped", "reason": "qmt_readonly_skeleton_fixture_only", "broker_name": self.profile.broker_name}


def build_readonly_client(profile: BrokerConnectionProfile, guard: BrokerNetworkGuard, account_id: str, trade_date: str, as_of_date: str):
    if profile.endpoint_kind == "generic_http_readonly":
        return GenericHttpReadOnlyBrokerClient(profile, guard, account_id=account_id)
    if profile.endpoint_kind == "qmt_readonly_skeleton":
        return QmtReadOnlySkeletonClient(profile, account_id=account_id, trade_date=trade_date, as_of_date=as_of_date)
    return MockReadOnlyBrokerClient(profile, account_id=account_id, trade_date=trade_date, as_of_date=as_of_date)


def load_fixture_records(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [dict(item) for item in payload["records"] if isinstance(item, dict)]
    if isinstance(payload, dict) and payload:
        return [payload]
    return []


def _env(name: str) -> str:
    if not name:
        return ""
    import os

    return os.getenv(name, "")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

