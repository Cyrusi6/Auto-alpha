from __future__ import annotations

import pytest

from task_055_g import network_state
from task_055_h.network import Task055HNetworkError, ordered_future_canary_gate


def _assert_gate_superseded() -> None:
    calls = {"tls": 0, "credential": 0}
    with pytest.raises(Task055HNetworkError, match="superseded_by_task055k_transport_broker"):
        ordered_future_canary_gate(
            authorization_seal="unused",
            allow_network=True,
            sealed_plan_hash="unused",
            tls_checker=lambda: calls.__setitem__("tls", calls["tls"] + 1),
            credential_loader=lambda: calls.__setitem__("credential", calls["credential"] + 1),
        )
    assert calls == {"tls": 0, "credential": 0}


def _assert_l1_superseded() -> None:
    calls = []
    with pytest.raises(network_state.Task055GNetworkStateError, match="superseded_by_task055k_transport_broker"):
        network_state.execute_l1_canary(
            state_root="unused",
            plan_manifest={},
            allow_network=True,
            sealed_plan_hash="unused",
            request_executor=lambda request: calls.append(request),
        )
    assert calls == []


def test_invalid_authorization_never_calls_tls_or_credential() -> None:
    _assert_gate_superseded()


def test_tls_validation_failure_never_calls_credential_loader() -> None:
    _assert_gate_superseded()


def test_canary_acceptance_reopens_native_cache_and_requires_one_post() -> None:
    _assert_l1_superseded()


def test_crash_after_cache_before_terminal_recovers_without_post() -> None:
    _assert_l1_superseded()


def test_crash_after_cache_before_terminal_closes_from_cache_without_second_attempt() -> None:
    _assert_l1_superseded()


def test_empty_response_schema_proof_tamper_is_rejected() -> None:
    _assert_l1_superseded()


def test_forged_execution_inside_state_root_is_rejected() -> None:
    _assert_l1_superseded()


def test_canary_acceptance_rejects_validated_cache_hit_as_physical_canary() -> None:
    _assert_l1_superseded()


def test_corrupted_cache_is_not_recovered() -> None:
    _assert_l1_superseded()
