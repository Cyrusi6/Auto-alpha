from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from .request_identity import TushareRequestIdentity, validate_tushare_request_identity
from .request_normalization import normalize_tushare_request


class TushareExecutionCapabilityError(RuntimeError):
    pass


_TASK055K_VALIDATED_GRANT = object()


@dataclass
class TushareExecutionCapability:
    authority_content_hash: str
    final_execution_seal_hash: str
    api_name: str
    params: dict[str, Any]
    fields: list[str]
    identity: TushareRequestIdentity
    attempt_id: str
    broker_contract_hash: str
    grant_verified: bool = field(repr=False)
    _validation_token: object = field(repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def authorize(self, api_name: str, params: Mapping[str, Any] | None, fields: Any) -> None:
        if not self.grant_verified or self._validation_token is not _TASK055K_VALIDATED_GRANT:
            raise TushareExecutionCapabilityError("task055k_execution_capability_unverified")
        normalized = normalize_tushare_request(api_name, params=dict(params or {}), fields=fields)
        expected = normalize_tushare_request(self.api_name, params=self.params, fields=self.fields)
        if normalized != expected:
            raise TushareExecutionCapabilityError("task055k_execution_capability_request_mismatch")
        try:
            validate_tushare_request_identity(
                identity=self.identity,
                api_name=api_name,
                params=dict(params or {}),
                fields=fields,
            )
        except ValueError as exc:
            raise TushareExecutionCapabilityError(str(exc)) from exc
        with self._lock:
            if self._consumed:
                raise TushareExecutionCapabilityError("task055k_execution_capability_already_consumed")
            self._consumed = True


def _issue_task055j_execution_capability(**_kwargs: Any) -> TushareExecutionCapability:
    raise TushareExecutionCapabilityError("superseded_by_task055k_transport_broker")


def _validated_task055k_execution_capability(
    *,
    authority_content_hash: str,
    final_execution_seal_hash: str,
    api_name: str,
    params: Mapping[str, Any],
    fields: list[str],
    identity: TushareRequestIdentity,
    attempt_id: str,
    broker_contract_hash: str,
) -> TushareExecutionCapability:
    return TushareExecutionCapability(
        authority_content_hash=authority_content_hash,
        final_execution_seal_hash=final_execution_seal_hash,
        api_name=api_name,
        params=dict(params),
        fields=list(fields),
        identity=identity,
        attempt_id=attempt_id,
        broker_contract_hash=broker_contract_hash,
        grant_verified=True,
        _validation_token=_TASK055K_VALIDATED_GRANT,
    )
