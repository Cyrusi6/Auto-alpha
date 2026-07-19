from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .request_normalization import (
    normalize_tushare_request,
    stable_json_hash,
    tushare_code_semantic_hash,
    tushare_request_fingerprint,
)


CANONICAL_TUSHARE_ORIGIN = "https://api.tushare.pro"
TUSHARE_PROVIDER_API_VERSION = "tushare_pro_http.v1"
TRANSPORT_IDENTITY_VERSION = "task055f_transport_identity_v1"


@dataclass(frozen=True)
class TushareRequestIdentity:
    request_fingerprint: str
    transport_identity: str
    evidence_use_identity: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def build_tushare_request_identity(
    *,
    api_name: str,
    params: Mapping[str, Any],
    fields: Sequence[str] | str,
    stage: str,
    parent_plan_hash: str,
    frontier_root: str,
) -> TushareRequestIdentity:
    field_list = _field_list(fields)
    transport = tushare_transport_identity(api_name, params, field_list)
    return TushareRequestIdentity(
        request_fingerprint=tushare_request_fingerprint(
            api_name,
            params=dict(params),
            fields=field_list,
        ),
        transport_identity=transport,
        evidence_use_identity=stable_json_hash(
            {
                "task": "task055f",
                "stage": stage,
                "parent_plan_hash": parent_plan_hash,
                "frontier_root": frontier_root,
                "transport_hash": transport,
            }
        ),
    )


def validate_tushare_request_identity(
    *,
    identity: TushareRequestIdentity,
    api_name: str,
    params: Mapping[str, Any],
    fields: Sequence[str] | str,
) -> None:
    field_list = _field_list(fields)
    if identity.request_fingerprint != tushare_request_fingerprint(
        api_name,
        params=dict(params),
        fields=field_list,
    ):
        raise ValueError("tushare_request_fingerprint_mismatch")
    if identity.transport_identity != tushare_transport_identity(api_name, params, field_list):
        raise ValueError("tushare_transport_identity_mismatch")


def tushare_transport_identity(
    api_name: str,
    params: Mapping[str, Any],
    fields: Sequence[str] | str,
) -> str:
    return stable_json_hash(
        {
            "origin": CANONICAL_TUSHARE_ORIGIN,
            "provider_api_version": TUSHARE_PROVIDER_API_VERSION,
            "request_normalization_version": TRANSPORT_IDENTITY_VERSION,
            "code_semantic_hash": tushare_code_semantic_hash(),
            "request": normalize_tushare_request(
                api_name,
                params=dict(params),
                fields=_field_list(fields),
            ),
        }
    )


def _field_list(fields: Sequence[str] | str) -> list[str]:
    if isinstance(fields, str):
        return [value.strip() for value in fields.split(",") if value.strip()]
    return [str(value).strip() for value in fields if str(value).strip()]
