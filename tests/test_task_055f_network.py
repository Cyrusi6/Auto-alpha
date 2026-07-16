from __future__ import annotations

from dataclasses import dataclass

import json
from pathlib import Path

import pytest

from task_055_f import network
from task_055_f.read_ledger import canonical_hash
from task_055_f.transport import evidence_use_identity, transport_identity


def _request(code: str, date: str) -> dict:
    fields = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"]
    params = {"ts_code": code, "trade_date": date}
    transport = transport_identity("daily", params, fields)
    return {
        "stage": "L1_daily_exact",
        "api_name": "daily",
        "params": params,
        "fields": fields,
        "ts_code": code,
        "trade_date": date,
        "max_date": "20260630",
        "transport_hash": transport,
        "evidence_use_hash": evidence_use_identity(
            stage="L1_daily_exact",
            parent_plan_hash="f" * 64,
            frontier_root="f" * 64,
            transport_hash=transport,
        ),
    }


def _plan() -> dict:
    rows = [_request("000001.SZ", "20240102"), _request("000002.SZ", "20240103")]
    frontier_root = canonical_hash([(row["ts_code"], row["trade_date"]) for row in rows])
    for row in rows:
        row["evidence_use_hash"] = evidence_use_identity(
            stage="L1_daily_exact",
            parent_plan_hash=frontier_root,
            frontier_root=frontier_root,
            transport_hash=row["transport_hash"],
        )
    payload = {
        "schema_version": "task055f_exact_frontier_network_plan_v1",
        "status": "sealed_round_one_daily_only",
        "frontier_root": frontier_root,
        "requests": rows,
    }
    payload["plan_hash"] = canonical_hash(payload)
    return payload


@dataclass
class _Envelope:
    endpoint: str
    provider_api_version: str
    records: list[dict]
    response_fields: list[str]
    response_code: int = 0
    response_message: str = ""
    item_count: int = 1


class _Client:
    retry_count = 1

    def __init__(self):
        self.calls = []

    def post_with_metadata(self, api_name, params, fields):
        self.calls.append((api_name, dict(params), list(fields)))
        close = 10.0 if params["ts_code"] == "000001.SZ" else 11.0
        record = {
            "ts_code": params["ts_code"],
            "trade_date": params["trade_date"],
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "pre_close": close - 0.1,
            "vol": 1000.0,
            "amount": 10000.0,
        }
        return _Envelope(
            endpoint="https://api.tushare.pro",
            provider_api_version="tushare_pro_http.v1",
            records=[record],
            response_fields=list(fields),
        )


def test_canary_stops_after_one_request_and_resume_is_separate(tmp_path, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(network, "validate_causal_frontier", lambda _: {"network_plan": plan})
    clients = []

    def factory(_credential, _root):
        client = _Client()
        clients.append(client)
        return client

    credential_calls = []

    def credential_loader(**_kwargs):
        credential_calls.append(1)
        return network.LoadedCredential("sentinel-secret", "environment")

    tls = lambda: {
        "status": "passed",
        "origin": "https://api.tushare.pro",
        "hostname_verified": True,
        "certificate_verified": True,
    }
    canary = network.execute_canary(
        causal_manifest=tmp_path / "causal.json",
        output_root=tmp_path / "canary",
        cache_data_root=tmp_path / "cache",
        allow_network=True,
        sealed_plan_hash=plan["plan_hash"],
        repo_root=tmp_path / "repo",
        governed_root=tmp_path / "governed",
        credential_loader=credential_loader,
        tls_checker=tls,
        client_factory=factory,
    )
    assert canary["physical_attempt_count"] == 1
    assert canary["batch_started"] is False
    assert sum(len(client.calls) for client in clients) == 1
    acceptance = network.verify_canary(
        canary["manifest_path"],
        cache_data_root=tmp_path / "cache",
        output_root=tmp_path / "acceptance",
    )
    resumed = network.execute_l1_resume(
        causal_manifest=tmp_path / "causal.json",
        canary_acceptance_manifest=acceptance["manifest_path"],
        output_root=tmp_path / "resume",
        cache_data_root=tmp_path / "cache",
        allow_network=True,
        sealed_plan_hash=plan["plan_hash"],
        repo_root=tmp_path / "repo",
        governed_root=tmp_path / "governed",
        credential_loader=credential_loader,
        tls_checker=tls,
        client_factory=factory,
    )
    assert resumed["cumulative_physical_attempt_count"] == 2
    assert len(credential_calls) == 2
    assert sum(len(client.calls) for client in clients) == 2


def test_tls_failure_happens_before_credential_load(tmp_path, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(network, "validate_causal_frontier", lambda _: {"network_plan": plan})
    calls = []
    try:
        network.execute_canary(
            causal_manifest=tmp_path / "causal.json",
            output_root=tmp_path / "canary",
            cache_data_root=tmp_path / "cache",
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            repo_root=tmp_path / "repo",
            governed_root=tmp_path / "governed",
            credential_loader=lambda **kwargs: calls.append(kwargs),
            tls_checker=lambda: {"status": "blocked"},
        )
    except network.Task055FNetworkError as exc:
        assert "tls_preflight_failed" in str(exc)
    else:
        raise AssertionError("TLS failure must block")
    assert calls == []


def test_bad_response_code_is_spent_and_fails_closed(tmp_path, monkeypatch):
    plan = _plan()
    plan["requests"] = plan["requests"][:1]
    plan["plan_hash"] = canonical_hash({key: value for key, value in plan.items() if key != "plan_hash"})
    monkeypatch.setattr(network, "validate_causal_frontier", lambda _: {"network_plan": plan})

    class BadClient(_Client):
        def post_with_metadata(self, api_name, params, fields):
            envelope = super().post_with_metadata(api_name, params, fields)
            envelope.response_code = 9
            return envelope

    with pytest.raises(network.Task055FNetworkError, match="response_code_or_item_count_invalid"):
        network.execute_canary(
            causal_manifest=tmp_path / "causal.json",
            output_root=tmp_path / "canary",
            cache_data_root=tmp_path / "cache",
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            repo_root=tmp_path / "repo",
            governed_root=tmp_path / "governed",
            credential_loader=lambda **_: network.LoadedCredential("sentinel-secret", "environment"),
            tls_checker=lambda: {
                "status": "passed",
                "origin": "https://api.tushare.pro",
                "hostname_verified": True,
                "certificate_verified": True,
            },
            client_factory=lambda *_: BadClient(),
        )
    spend = network._load_spend_ledger(tmp_path / "network_spend")
    assert network._physical_attempt_count(spend) == 1


def test_plan_hash_and_evidence_use_are_revalidated(tmp_path, monkeypatch):
    plan = _plan()
    plan["requests"][0]["evidence_use_hash"] = "0" * 64
    plan["plan_hash"] = canonical_hash({key: value for key, value in plan.items() if key != "plan_hash"})
    monkeypatch.setattr(network, "validate_causal_frontier", lambda _: {"network_plan": plan})
    with pytest.raises(network.Task055FNetworkError, match="transport_identity_or_date"):
        network.execute_canary(
            causal_manifest=tmp_path / "causal.json",
            output_root=tmp_path / "canary",
            cache_data_root=tmp_path / "cache",
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            repo_root=tmp_path / "repo",
            governed_root=tmp_path / "governed",
            credential_loader=lambda **_: network.LoadedCredential("sentinel-secret", "environment"),
            tls_checker=lambda: {
                "status": "passed",
                "origin": "https://api.tushare.pro",
                "hostname_verified": True,
                "certificate_verified": True,
            },
            client_factory=lambda *_: _Client(),
        )


def test_canary_acceptance_hash_is_revalidated_before_resume(tmp_path, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(network, "validate_causal_frontier", lambda _: {"network_plan": plan})
    canary = network.execute_canary(
        causal_manifest=tmp_path / "causal.json",
        output_root=tmp_path / "network" / "canary",
        cache_data_root=tmp_path / "network" / "cache",
        allow_network=True,
        sealed_plan_hash=plan["plan_hash"],
        repo_root=tmp_path / "repo",
        governed_root=tmp_path / "governed",
        credential_loader=lambda **_: network.LoadedCredential("sentinel-secret", "environment"),
        tls_checker=lambda: {
            "status": "passed",
            "origin": "https://api.tushare.pro",
            "hostname_verified": True,
            "certificate_verified": True,
        },
        client_factory=lambda *_: _Client(),
    )
    acceptance = network.verify_canary(
        canary["manifest_path"],
        cache_data_root=tmp_path / "network" / "cache",
        output_root=tmp_path / "network" / "canary_acceptance",
    )
    path = Path(acceptance["manifest_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["resume_authorized"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(network.Task055FNetworkError, match="content_hash_invalid"):
        network.execute_l1_resume(
            causal_manifest=tmp_path / "causal.json",
            canary_acceptance_manifest=path,
            output_root=tmp_path / "network" / "l1_resume",
            cache_data_root=tmp_path / "network" / "cache",
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            repo_root=tmp_path / "repo",
            governed_root=tmp_path / "governed",
            credential_loader=lambda **_: network.LoadedCredential("sentinel-secret", "environment"),
            tls_checker=lambda: {
                "status": "passed",
                "origin": "https://api.tushare.pro",
                "hostname_verified": True,
                "certificate_verified": True,
            },
            client_factory=lambda *_: _Client(),
        )


def test_resume_tls_certificate_failure_precedes_credential_load(tmp_path, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(network, "validate_causal_frontier", lambda _: {"network_plan": plan})
    spend = network._append_spend_event(
        tmp_path / "network" / "network_spend",
        {"event": "physical_post_started", "transport_hash": plan["requests"][0]["transport_hash"]},
    )
    acceptance_payload = {
        "schema_version": "task055f_canary_acceptance_v1",
        "status": "accepted",
        "resume_authorized": True,
        "parent_plan_hash": plan["plan_hash"],
        "spend_ledger_content_hash": spend["content_hash"],
    }
    acceptance = network._publish_json_generation(
        tmp_path / "network" / "canary_acceptance",
        "canary_acceptance",
        acceptance_payload,
        "canary_acceptance_manifest.json",
    )
    calls = []
    with pytest.raises(network.Task055FNetworkError, match="tls_preflight_failed"):
        network.execute_l1_resume(
            causal_manifest=tmp_path / "causal.json",
            canary_acceptance_manifest=acceptance["manifest_path"],
            output_root=tmp_path / "network" / "l1_resume",
            cache_data_root=tmp_path / "network" / "cache",
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            repo_root=tmp_path / "repo",
            governed_root=tmp_path / "governed",
            credential_loader=lambda **kwargs: calls.append(kwargs),
            tls_checker=lambda: {
                "status": "passed",
                "origin": "https://api.tushare.pro",
                "hostname_verified": True,
                "certificate_verified": False,
            },
        )
    assert calls == []
