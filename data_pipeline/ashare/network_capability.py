from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from .request_normalization import normalize_tushare_request, tushare_request_fingerprint


class TushareExecutionCapabilityError(RuntimeError):
    pass


_ISSUER = object()


@dataclass
class TushareExecutionCapability:
    authority_content_hash: str
    final_execution_seal_hash: str
    api_name: str
    params: dict[str, Any]
    fields: list[str]
    transport_hash: str
    attempt_id: str
    _issuer: object = field(repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def authorize(self, api_name: str, params: Mapping[str, Any] | None, fields: Any) -> None:
        if self._issuer is not _ISSUER:
            raise TushareExecutionCapabilityError("task055j_execution_capability_forged")
        normalized = normalize_tushare_request(api_name, params=dict(params or {}), fields=fields)
        expected = normalize_tushare_request(self.api_name, params=self.params, fields=self.fields)
        if normalized != expected:
            raise TushareExecutionCapabilityError("task055j_execution_capability_request_mismatch")
        if tushare_request_fingerprint(api_name, params=dict(params or {}), fields=fields) != self.transport_hash:
            raise TushareExecutionCapabilityError("task055j_execution_capability_transport_mismatch")
        with self._lock:
            if self._consumed:
                raise TushareExecutionCapabilityError("task055j_execution_capability_already_consumed")
            self._consumed = True


def _issue_task055j_execution_capability(
    *,
    authority_content_hash: str,
    final_execution_seal_hash: str,
    api_name: str,
    params: Mapping[str, Any],
    fields: list[str],
    transport_hash: str,
    attempt_id: str,
) -> TushareExecutionCapability:
    return TushareExecutionCapability(
        authority_content_hash=authority_content_hash,
        final_execution_seal_hash=final_execution_seal_hash,
        api_name=api_name,
        params=dict(params),
        fields=list(fields),
        transport_hash=transport_hash,
        attempt_id=attempt_id,
        _issuer=_ISSUER,
    )
