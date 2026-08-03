"""Validate exact-date provider responses at the authority boundary."""

from __future__ import annotations

from typing import Any, Mapping


DAILY_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
)
MAX_MARKET_DATE = "20260630"
ENDPOINT_ROW_CAPS = {"daily": 6000, "suspend_d": 1000}


class ResponseValidationError(RuntimeError):
    pass


def validate_response_records(
    request: Mapping[str, Any], records: list[Mapping[str, Any]]
) -> None:
    params = request.get("params") or {}
    code = str(params.get("ts_code") or "")
    trade_date = str(params.get("trade_date") or "")
    seen: set[tuple[Any, ...]] = set()
    for row in records:
        if (
            str(row.get("ts_code")) != code
            or str(row.get("trade_date")) != trade_date
            or trade_date > MAX_MARKET_DATE
        ):
            raise ResponseValidationError("response_geometry_invalid")
        if request["api_name"] == "daily":
            key = (code, trade_date)
            values = _daily_values(row)
            if values["high"] < max(values["open"], values["low"], values["close"]):
                raise ResponseValidationError("daily_high_relation_invalid")
            if values["low"] > min(values["open"], values["high"], values["close"]):
                raise ResponseValidationError("daily_low_relation_invalid")
            if values["vol"] < 0 or values["amount"] < 0:
                raise ResponseValidationError("daily_volume_or_amount_invalid")
        else:
            suspend_type = str(row.get("suspend_type") or "")
            timing = row.get("suspend_timing")
            if suspend_type not in {"S", "R"}:
                raise ResponseValidationError("suspend_type_invalid")
            key = (code, trade_date, suspend_type, timing)
        if key in seen:
            raise ResponseValidationError("response_primary_key_duplicate")
        seen.add(key)


def _daily_values(row: Mapping[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for field in DAILY_FIELDS[2:]:
        try:
            values[field] = float(row.get(field))
        except (TypeError, ValueError) as exc:
            raise ResponseValidationError(f"daily_field_invalid:{field}") from exc
    if any(not (value == value and abs(value) != float("inf")) for value in values.values()):
        raise ResponseValidationError("daily_non_finite_value")
    if any(values[field] <= 0 for field in ("open", "high", "low", "close", "pre_close")):
        raise ResponseValidationError("daily_nonpositive_price")
    return values
