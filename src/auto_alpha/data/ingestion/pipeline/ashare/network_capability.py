from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .request_identity import TushareRequestIdentity


class TushareExecutionCapabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class TushareExecutionCapability:
    """Historical data-only shape retained for artifact decoding.

    A Python capability object is not a production trust anchor.  All direct
    authorization attempts fail closed; Task 055-KR validates the canonical
    authority chain again at the final HTTPS call point.
    """

    authority_content_hash: str
    final_execution_seal_hash: str
    api_name: str
    params: dict[str, Any]
    fields: list[str]
    identity: TushareRequestIdentity
    attempt_id: str
    broker_contract_hash: str
    grant_verified: bool = False
    _validation_token: object | None = None

    def authorize(self, _api_name: str, _params: Mapping[str, Any] | None, _fields: Any) -> None:
        raise TushareExecutionCapabilityError(
            "superseded_by_task055k_transport_broker:task055kr_canonical_transport_gateway"
        )


def _issue_task055j_execution_capability(**_kwargs: Any) -> TushareExecutionCapability:
    raise TushareExecutionCapabilityError(
        "superseded_by_task055k_transport_broker:task055kr_canonical_transport_gateway"
    )


def _validated_task055k_execution_capability(**_kwargs: Any) -> TushareExecutionCapability:
    raise TushareExecutionCapabilityError(
        "superseded_by_task055k_transport_broker:task055kr_canonical_transport_gateway"
    )
