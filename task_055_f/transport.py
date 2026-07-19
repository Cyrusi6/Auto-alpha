"""Canonical Task 055-F request identities."""

from __future__ import annotations

from typing import Any, Mapping

from data_pipeline.ashare.request_identity import (
    CANONICAL_TUSHARE_ORIGIN,
    TRANSPORT_IDENTITY_VERSION,
    tushare_transport_identity,
)
from data_pipeline.ashare.request_normalization import stable_json_hash

CANONICAL_ORIGIN = CANONICAL_TUSHARE_ORIGIN


def transport_identity(api_name: str, params: Mapping[str, Any], fields: list[str] | tuple[str, ...]) -> str:
    return tushare_transport_identity(api_name, params, fields)


def evidence_use_identity(
    *,
    stage: str,
    parent_plan_hash: str,
    frontier_root: str,
    transport_hash: str,
) -> str:
    return stable_json_hash(
        {
            "task": "task055f",
            "stage": stage,
            "parent_plan_hash": parent_plan_hash,
            "frontier_root": frontier_root,
            "transport_hash": transport_hash,
        }
    )
