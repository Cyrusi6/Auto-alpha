from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from task_055_h.io import canonical_hash, read_json
from task_055_j.ledger import DurableHashJournal
from task_055_k import broker, gateway
from task_055_k.authority import normalize_ordered_keys, publish_candidate_checkpoint
from task_055_k.broker import request_from_checkpoint
from task_055_k.contracts import CANARY


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    authority_root = tmp_path / "authority"
    authority_root.mkdir()
    lock = authority_root / "single_canary.lock"
    lock.touch()
    network = DurableHashJournal(authority_root / "network_journal", name="task055kr_network")
    spend = DurableHashJournal(
        authority_root / "transport_spend_journal", name="task055kr_spend"
    )
    keys = normalize_ordered_keys(
        read_json("evidence/task_055_j/task055j_scrubbed_evidence.json")[
            "ordered_exact_daily_keys"
        ]
    )
    network.append(
        {
            "event_id": "authority-registered",
            "event": "authority_registered",
            "ordered_key_root": canonical_hash(keys),
            "logical_request_count": 17,
            "unique_security_date_count": 17,
        }
    )
    spend.append(
        {
            "event_id": "budget-initialized",
            "event": "budget_initialized",
            "physical_attempt_count": 0,
            "physical_attempt_limit": 160,
        }
    )
    authority = {
        "content_hash": "a" * 64,
        "implementation_commit": "b" * 40,
        "source_root": "c" * 64,
        "ordered_exact_daily_keys": keys,
        "ordered_key_root": canonical_hash(keys),
        "budgets": {
            "unique_security_dates": 17,
            "logical_requests": 17,
            "physical_attempts": 0,
            "credential_reads": 0,
            "limits": {
                "unique_security_dates": 64,
                "logical_requests": 128,
                "physical_attempts": 160,
                "credential_reads": 1,
            },
        },
        "root_identities": {
            "single_canary_lock": {
                "st_dev": lock.stat().st_dev,
                "st_ino": lock.stat().st_ino,
            },
            "authority_root": {
                "st_dev": authority_root.stat().st_dev,
                "st_ino": authority_root.stat().st_ino,
            },
        },
        "initial_network_journal": network.checkpoint(),
        "initial_transport_spend": spend.checkpoint(),
    }
    checkpoint = publish_candidate_checkpoint(
        authority=authority,
        lineage={"fixture": "d" * 64},
        output_root=authority_root / "candidate_checkpoint",
    )
    final = {
        "content_hash": "e" * 64,
        "manifest_path": str(tmp_path / "final_candidate_seal.json"),
        "authority_root": str(authority_root),
        "governed_root": str(tmp_path / "governed"),
        "root_identities": authority["root_identities"],
        "initial_network_journal": authority["initial_network_journal"],
        "initial_transport_spend": authority["initial_transport_spend"],
        "resolved_lineage": {"candidate_checkpoint": checkpoint},
    }
    operator = {
        "content_hash": "f" * 64,
        "manifest_path": str(tmp_path / "operator_authorization.json"),
    }
    trust = {
        "final_seal": final,
        "operator_authorization": operator,
        "checkpoint": checkpoint,
        "authority_root": str(authority_root),
        "governed_root": str(tmp_path / "governed"),
    }
    monkeypatch.setattr(gateway, "_validate_execution_trust", lambda **_kwargs: trust)
    monkeypatch.setattr(broker, "validate_final_candidate_seal", lambda *_args, **_kwargs: final)
    monkeypatch.setattr(broker, "_find_operator_authorization", lambda *_args, **_kwargs: operator)
    monkeypatch.setattr(
        gateway,
        "tls_preflight",
        lambda: {
            "status": "passed",
            "origin": "https://api.tushare.pro",
            "hostname_verified": True,
            "certificate_verified": True,
        },
    )
    counters = {"credential": 0, "post": 0}

    def load_credential(*_args, **_kwargs):
        counters["credential"] += 1
        return "SYNTHETIC_TEST_SECRET"

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "code": 0,
                    "msg": None,
                    "data": {"fields": list(CANARY["fields"]), "items": []},
                }
            ).encode("utf-8")

    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            assert request.full_url == "https://api.tushare.pro"
            counters["post"] += 1
            return Response()

    monkeypatch.setattr(gateway, "_load_credential_file", load_credential)
    monkeypatch.setattr(gateway.urllib.request, "build_opener", lambda *_args: Opener())
    kwargs = {
        "final_candidate_seal": final["manifest_path"],
        "reviewed_final_candidate_seal_hash": final["content_hash"],
        "operator_authorization": operator["manifest_path"],
        "reviewed_operator_authorization_hash": operator["content_hash"],
        "credential_file": tmp_path / "credential",
        "repository_root": tmp_path,
    }
    return trust, counters, kwargs


def test_gateway_success_and_second_call_recover_without_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    first = gateway.execute_operator_authorized_single_canary(**kwargs)
    second = gateway.execute_operator_authorized_single_canary(**kwargs)
    assert first.acceptance["content_hash"] == second.acceptance["content_hash"]
    assert counters == {"credential": 1, "post": 1}


def test_gateway_has_no_separately_callable_private_post_boundary() -> None:
    assert not hasattr(gateway, "_perform_https_post")


def test_credential_file_requires_owner_regular_nonsymlink_outside_roots(
    tmp_path: Path,
) -> None:
    credential = tmp_path / "credential.txt"
    credential.write_text("SYNTHETIC_TEST_SECRET", encoding="utf-8")
    credential.chmod(0o600)
    assert gateway._load_credential_file(credential, forbidden_roots=[]) == (
        "SYNTHETIC_TEST_SECRET"
    )
    with pytest.raises(Exception, match="inside_forbidden_root"):
        gateway._load_credential_file(credential, forbidden_roots=[tmp_path])
    credential.chmod(0o644)
    with pytest.raises(Exception, match="permissions_invalid"):
        gateway._load_credential_file(credential, forbidden_roots=[])
    credential.chmod(0o600)
    link = tmp_path / "credential-link"
    link.symlink_to(credential)
    with pytest.raises(Exception, match="absolute_regular_file_required"):
        gateway._load_credential_file(link, forbidden_roots=[])
    fifo = tmp_path / "credential-fifo"
    os.mkfifo(fifo)
    with pytest.raises(Exception, match="owner_regular_file_required"):
        gateway._load_credential_file(fifo, forbidden_roots=[])


def test_tls_failure_precedes_credential_and_attempt_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gateway,
        "tls_preflight",
        lambda: {
            "status": "blocked",
            "origin": "https://api.tushare.pro",
            "hostname_verified": False,
            "certificate_verified": False,
        },
    )
    with pytest.raises(Exception, match="tls_attestation_invalid"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 0, "post": 0}
    network = DurableHashJournal(
        Path(trust["authority_root"]) / "network_journal", name="task055kr_network"
    )
    spend = DurableHashJournal(
        Path(trust["authority_root"]) / "transport_spend_journal", name="task055kr_spend"
    )
    assert not [
        row for row in network.rows() if row.get("event") == "credential_read_intent"
    ]
    assert not [row for row in network.rows() if row.get("event") == "attempt_intent"]
    assert not [row for row in spend.rows() if row.get("event") == "physical_post_started"]


def test_forged_public_key_reservation_is_rejected_before_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    original = gateway._canonical_reservation

    def forged(*args, **arguments):
        row = dict(original(*args, **arguments))
        row["broker_public_key_sha256"] = "0" * 64
        return row

    monkeypatch.setattr(gateway, "_canonical_reservation", forged)
    with pytest.raises(Exception, match="public_key_reservation_invalid"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 0}


def test_crash_after_credential_read_never_reads_credential_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)

    def crash(_cls):
        raise RuntimeError("crash_after_credential_read")

    monkeypatch.setattr(
        gateway.EphemeralReceiptSigner, "generate", classmethod(crash)
    )
    with pytest.raises(RuntimeError, match="crash_after_credential_read"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    with pytest.raises(Exception, match="credential_read_intent_without_transport"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 0}


def test_receipt_before_cache_recovers_without_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    original = gateway.publish_validated_cache
    calls = {"count": 0}

    def crash_once(**arguments):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("crash_after_receipt_before_cache")
        return original(**arguments)

    monkeypatch.setattr(gateway, "publish_validated_cache", crash_once)
    with pytest.raises(RuntimeError, match="crash_after_receipt"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    accepted = gateway.execute_operator_authorized_single_canary(**kwargs)
    assert accepted.acceptance["status"] == "accepted"
    assert counters == {"credential": 1, "post": 1}


def test_crash_between_intent_and_spend_is_ambiguous_without_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    original = DurableHashJournal.append
    crashed = {"value": False}

    def crash_once(self, event):
        if (
            self.name == "task055kr_spend"
            and event.get("event") == "physical_post_started"
            and not crashed["value"]
        ):
            crashed["value"] = True
            raise RuntimeError("crash_between_intent_and_spend")
        return original(self, event)

    monkeypatch.setattr(DurableHashJournal, "append", crash_once)
    with pytest.raises(RuntimeError, match="crash_between_intent_and_spend"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    with pytest.raises(Exception, match="missing_canonical_receipt"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 0}


def test_spend_start_then_transport_failure_is_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)

    class FailedOpener:
        def open(self, _request, timeout):
            assert timeout == 30
            counters["post"] += 1
            raise OSError("synthetic_transport_failure")

    monkeypatch.setattr(
        gateway.urllib.request, "build_opener", lambda *_args: FailedOpener()
    )
    with pytest.raises(Exception, match="canonical Task055-KR authorization"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    with pytest.raises(Exception, match="missing_canonical_receipt"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 1}


def test_cache_before_acceptance_recovers_without_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    original = gateway.publish_canary_acceptance
    calls = {"count": 0}

    def crash_once(**arguments):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("crash_after_cache_before_acceptance")
        return original(**arguments)

    monkeypatch.setattr(gateway, "publish_canary_acceptance", crash_once)
    with pytest.raises(RuntimeError, match="crash_after_cache"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    accepted = gateway.execute_operator_authorized_single_canary(**kwargs)
    assert accepted.acceptance["status"] == "accepted"
    assert counters == {"credential": 1, "post": 1}


def test_acceptance_before_completion_recovers_without_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    original = DurableHashJournal.append
    crashed = {"value": False}

    def crash_once(self, event):
        if (
            self.name == "task055kr_spend"
            and event.get("event") == "physical_post_completed"
            and not crashed["value"]
        ):
            crashed["value"] = True
            raise RuntimeError("crash_after_acceptance_before_completion")
        return original(self, event)

    monkeypatch.setattr(DurableHashJournal, "append", crash_once)
    with pytest.raises(RuntimeError, match="crash_after_acceptance"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    accepted = gateway.execute_operator_authorized_single_canary(**kwargs)
    assert accepted.acceptance["status"] == "accepted"
    assert counters == {"credential": 1, "post": 1}


def test_completion_before_terminal_recovers_without_second_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    original = DurableHashJournal.append
    crashed = {"value": False}

    def crash_once(self, event):
        if (
            self.name == "task055kr_network"
            and event.get("event") == "request_terminal"
            and not crashed["value"]
        ):
            crashed["value"] = True
            raise RuntimeError("crash_after_completion_before_terminal")
        return original(self, event)

    monkeypatch.setattr(DurableHashJournal, "append", crash_once)
    with pytest.raises(RuntimeError, match="crash_after_completion"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    accepted = gateway.execute_operator_authorized_single_canary(**kwargs)
    assert accepted.acceptance["status"] == "accepted"
    assert counters == {"credential": 1, "post": 1}


def test_post_return_before_receipt_is_ambiguous_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gateway,
        "publish_signed_transport_receipt",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("crash_before_receipt")),
    )
    with pytest.raises(RuntimeError, match="crash_before_receipt"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    with pytest.raises(Exception, match="missing_canonical_receipt"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 1}


def test_manual_intent_and_cache_without_receipt_cannot_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    request = request_from_checkpoint(trust["checkpoint"])
    attempt_id = canonical_hash(
        [trust["final_seal"]["content_hash"], request["transport_identity"], "physical_attempt", 1]
    )
    network = DurableHashJournal(
        Path(trust["authority_root"]) / "network_journal", name="task055kr_network"
    )
    spend = DurableHashJournal(
        Path(trust["authority_root"]) / "transport_spend_journal", name="task055kr_spend"
    )
    common = {
        "attempt_id": attempt_id,
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "final_candidate_seal_content_hash": trust["final_seal"]["content_hash"],
        "operator_authorization_content_hash": trust["operator_authorization"]["content_hash"],
        "attempt_reservation_content_hash": "0" * 64,
    }
    network.append({"event_id": f"intent:{attempt_id}", "event": "attempt_intent", **common})
    spend.append({"event_id": f"spend:{attempt_id}", "event": "physical_post_started", **common})
    with pytest.raises(Exception, match="missing_canonical_reservation"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 0, "post": 0}


def test_corrupted_cache_blocks_recovery_without_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    accepted = gateway.execute_operator_authorized_single_canary(**kwargs)
    accepted.cache_path.write_text("{}", encoding="utf-8")
    with pytest.raises(Exception):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 1}


def test_corrupted_receipt_blocks_recovery_without_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    accepted = gateway.execute_operator_authorized_single_canary(**kwargs)
    receipt = Path(accepted.receipt["manifest_path"])
    receipt.write_text("{}", encoding="utf-8")
    with pytest.raises(Exception):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 1}


def test_corrupted_journal_blocks_recovery_without_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    gateway.execute_operator_authorized_single_canary(**kwargs)
    events = Path(trust["authority_root"]) / "network_journal/events.jsonl"
    rows = events.read_text(encoding="utf-8").splitlines()
    events.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(Exception):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 1, "post": 1}


def test_lock_inode_replacement_blocks_before_credential_or_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust, counters, kwargs = _fixture(tmp_path, monkeypatch)
    lock = Path(trust["authority_root"]) / "single_canary.lock"
    lock.unlink()
    lock.write_text("replacement", encoding="utf-8")
    with pytest.raises(Exception, match="lock_inode_drift"):
        gateway.execute_operator_authorized_single_canary(**kwargs)
    assert counters == {"credential": 0, "post": 0}


def test_two_process_canary_race_produces_one_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust, _counters, kwargs = _fixture(tmp_path, monkeypatch)
    marker = tmp_path / "post_attempts.jsonl"

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "code": 0,
                    "msg": None,
                    "data": {"fields": list(CANARY["fields"]), "items": []},
                }
            ).encode("utf-8")

    class SharedOpener:
        def open(self, _request, timeout):
            assert timeout == 30
            descriptor = os.open(marker, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, b"post\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return Response()

    monkeypatch.setattr(
        gateway.urllib.request, "build_opener", lambda *_args: SharedOpener()
    )
    children = []
    for _ in range(2):
        pid = os.fork()
        if pid == 0:
            try:
                gateway.execute_operator_authorized_single_canary(**kwargs)
            except Exception:
                os._exit(1)
            os._exit(0)
        children.append(pid)
    statuses = [os.waitpid(pid, 0)[1] for pid in children]
    assert all(os.waitstatus_to_exitcode(status) == 0 for status in statuses)
    assert marker.read_text(encoding="utf-8").splitlines() == ["post"]
