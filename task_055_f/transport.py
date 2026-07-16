"""Canonical Task 055-F request identities."""

from __future__ import annotations

from typing import Any, Mapping

from data_pipeline.ashare.providers.tushare_client import TUSHARE_PROVIDER_API_VERSION
from data_pipeline.ashare.request_normalization import (
    normalize_tushare_request,
    stable_json_hash,
    tushare_code_semantic_hash,
)

CANONICAL_ORIGIN = "https://api.tushare.pro"
TRANSPORT_IDENTITY_VERSION = "task055f_transport_identity_v1"


def transport_identity(api_name: str, params: Mapping[str, Any], fields: list[str] | tuple[str, ...]) -> str:
    return stable_json_hash(
        {
            "origin": CANONICAL_ORIGIN,
            "provider_api_version": TUSHARE_PROVIDER_API_VERSION,
            "request_normalization_version": TRANSPORT_IDENTITY_VERSION,
            "code_semantic_hash": tushare_code_semantic_hash(),
            "request": normalize_tushare_request(api_name, params=dict(params), fields=fields),
        }
    )


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
