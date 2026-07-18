from __future__ import annotations

import pytest

from task_055_f import network


def _assert_canary_superseded() -> None:
    calls = {"credential": 0, "tls": 0, "client": 0}
    with pytest.raises(network.Task055FNetworkError, match="superseded_by_task055j"):
        network.execute_canary(
            causal_manifest="unused",
            output_root="unused",
            cache_data_root="unused",
            allow_network=True,
            sealed_plan_hash="unused",
            repo_root="unused",
            governed_root="unused",
            credential_loader=lambda **_: calls.__setitem__("credential", calls["credential"] + 1),
            tls_checker=lambda: calls.__setitem__("tls", calls["tls"] + 1),
            client_factory=lambda *_: calls.__setitem__("client", calls["client"] + 1),
        )
    assert calls == {"credential": 0, "tls": 0, "client": 0}


def _assert_resume_superseded() -> None:
    calls = {"credential": 0, "tls": 0, "client": 0}
    with pytest.raises(network.Task055FNetworkError, match="superseded_by_task055j"):
        network.execute_l1_resume(
            causal_manifest="unused",
            canary_acceptance_manifest="unused",
            output_root="unused",
            cache_data_root="unused",
            allow_network=True,
            sealed_plan_hash="unused",
            repo_root="unused",
            governed_root="unused",
            credential_loader=lambda **_: calls.__setitem__("credential", calls["credential"] + 1),
            tls_checker=lambda: calls.__setitem__("tls", calls["tls"] + 1),
            client_factory=lambda *_: calls.__setitem__("client", calls["client"] + 1),
        )
    assert calls == {"credential": 0, "tls": 0, "client": 0}


def test_canary_stops_after_one_request_and_resume_is_separate():
    _assert_canary_superseded()
    _assert_resume_superseded()


def test_tls_failure_happens_before_credential_load():
    _assert_canary_superseded()


def test_bad_response_code_is_spent_and_fails_closed():
    _assert_canary_superseded()


def test_plan_hash_and_evidence_use_are_revalidated():
    _assert_canary_superseded()


def test_canary_acceptance_hash_is_revalidated_before_resume():
    _assert_resume_superseded()


def test_resume_tls_certificate_failure_precedes_credential_load():
    _assert_resume_superseded()
