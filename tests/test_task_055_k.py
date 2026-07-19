from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from data_pipeline.ashare.network_capability import (
    TushareExecutionCapability,
    TushareExecutionCapabilityError,
)
from data_pipeline.ashare.request_identity import TushareRequestIdentity
from task_055_h.io import canonical_hash, publish_generation, read_json
from task_055_k.application import APPLICATION_STAGES, validate_stage_journal
from task_055_k.authority import (
    normalize_ordered_keys,
    publish_candidate_checkpoint,
    validate_candidate_checkpoint,
)
from task_055_k.broker import (
    Task055KBrokerError,
    _request_from_checkpoint,
    execute_synthetic_rehearsal_response,
    validate_transport_receipt,
)
from task_055_k.contracts import CANARY
from task_055_k.signing import EphemeralReceiptSigner, Task055KSigningError, verify_signature
from task_055_k.source_tree import git_index_source_entries


def _ordered_keys() -> list[dict]:
    payload = read_json("evidence/task_055_j/task055j_scrubbed_evidence.json")
    return normalize_ordered_keys(payload["ordered_exact_daily_keys"])


def _checkpoint(tmp_path: Path) -> dict:
    ordered = _ordered_keys()
    authority = {
        "content_hash": "a" * 64,
        "implementation_commit": "b" * 40,
        "source_root": "c" * 64,
        "ordered_exact_daily_keys": ordered,
        "ordered_key_root": canonical_hash(ordered),
        "budgets": {
            "physical_attempts": 0,
            "limits": {
                "unique_security_dates": 64,
                "logical_requests": 128,
                "physical_attempts": 160,
            },
        },
    }
    return publish_candidate_checkpoint(
        authority=authority,
        lineage={"parent": "d" * 64},
        output_root=tmp_path / "checkpoint",
    )


def _tls() -> dict:
    return {
        "status": "synthetic_passed",
        "origin": "https://api.tushare.pro",
        "hostname_verified": True,
        "certificate_verified": True,
    }


def _response(items: list[list[object]]) -> bytes:
    return json.dumps(
        {"code": 0, "msg": "", "data": {"fields": CANARY["fields"], "items": items}}
    ).encode()


def test_fixed_canary_has_distinct_request_transport_and_evidence_identities():
    assert CANARY["request_fingerprint"] == "8cec7ae0957a9d54afb1f08736db3f1c12b402554f5e1c3cc2e007658b8af869"
    assert CANARY["transport_identity"] == "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e"
    assert len({CANARY["request_fingerprint"], CANARY["transport_identity"], CANARY["evidence_use_identity"]}) == 3


def test_broker_uses_real_serializer_parser_capability_and_signed_receipt_without_network(tmp_path: Path, monkeypatch):
    checkpoint = _checkpoint(tmp_path)
    calls = []

    def no_network(*_args, **_kwargs):
        raise AssertionError("physical network must not be called")

    monkeypatch.setattr("urllib.request.urlopen", no_network)

    def memory_boundary(body: bytes) -> bytes:
        request = json.loads(body)
        calls.append(request)
        return _response([["000413.SZ", "20160726", 10.0, 11.0, 9.0, 10.5, 10.0, 100.0, 1000.0]])

    result = execute_synthetic_rehearsal_response(
        candidate_checkpoint=checkpoint["manifest_path"],
        reviewed_checkpoint_hash=checkpoint["content_hash"],
        authority_root=tmp_path / "authority",
        response_bytes_provider=memory_boundary,
        tls_attestation=_tls(),
    )
    assert len(calls) == 1
    assert calls[0]["api_name"] == "daily"
    assert result.envelope.request_fingerprint == CANARY["request_fingerprint"]
    assert result.envelope.transport_identity == CANARY["transport_identity"]
    assert result.receipt["evidence_use_identity"] == CANARY["evidence_use_identity"]
    encoded = "\n".join(path.read_text(errors="ignore") for path in (tmp_path / "authority").rglob("*") if path.is_file())
    assert "SYNTHETIC_REHEARSAL_TOKEN_NEVER_PERSISTED" not in encoded


def test_empty_response_remains_vendor_absence_and_signed(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    result = execute_synthetic_rehearsal_response(
        candidate_checkpoint=checkpoint["manifest_path"],
        reviewed_checkpoint_hash=checkpoint["content_hash"],
        authority_root=tmp_path / "authority",
        response_bytes_provider=lambda _body: _response([]),
        tls_attestation=_tls(),
    )
    assert result.receipt["item_count"] == 0
    assert result.receipt["empty_response_semantics"] == "vendor_absence_only"
    assert result.receipt["response_fields"] == CANARY["fields"]


def test_receipt_signature_tampering_is_rejected_even_with_new_generation_hash(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    result = execute_synthetic_rehearsal_response(
        candidate_checkpoint=checkpoint["manifest_path"],
        reviewed_checkpoint_hash=checkpoint["content_hash"],
        authority_root=tmp_path / "authority",
        response_bytes_provider=lambda _body: _response([]),
        tls_attestation=_tls(),
    )
    semantic = {
        key: value
        for key, value in result.receipt.items()
        if key not in {"content_hash", "generation_id", "manifest_path"}
    }
    semantic["signature"] = "AAAA"
    forged = publish_generation(
        tmp_path / "forged",
        prefix="forged",
        manifest_name="transport_receipt.json",
        semantic=semantic,
    )
    request = _request_from_checkpoint(checkpoint)
    with pytest.raises((Task055KSigningError, Task055KBrokerError)):
        validate_transport_receipt(
            forged["manifest_path"],
            request=request,
            checkpoint_content_hash=checkpoint["content_hash"],
            reservation=result.reservation,
        )


def test_arbitrary_capability_constructor_is_not_a_validated_broker_grant():
    capability = TushareExecutionCapability(
        authority_content_hash="a" * 64,
        final_execution_seal_hash="b" * 64,
        api_name="daily",
        params={"ts_code": "000413.SZ", "trade_date": "20160726"},
        fields=CANARY["fields"],
        identity=TushareRequestIdentity(
            CANARY["request_fingerprint"],
            CANARY["transport_identity"],
            CANARY["evidence_use_identity"],
        ),
        attempt_id="c" * 64,
        broker_contract_hash="d" * 64,
        grant_verified=True,
        _validation_token=object(),
    )
    with pytest.raises(TushareExecutionCapabilityError, match="unverified"):
        capability.authorize("daily", capability.params, capability.fields)


def test_candidate_checkpoint_rejects_self_consistent_second_key_tampering(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    payload = read_json(checkpoint["manifest_path"])
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    semantic["ordered_exact_daily_keys"][1]["trade_date"] = "20160101"
    semantic["ordered_key_root"] = canonical_hash(semantic["ordered_exact_daily_keys"])
    forged = publish_generation(
        tmp_path / "forged-checkpoint",
        prefix="forged",
        manifest_name="candidate_checkpoint.json",
        semantic=semantic,
    )
    with pytest.raises(Exception):
        validate_candidate_checkpoint(forged["manifest_path"])


def test_stage_journal_requires_complete_ordered_native_chain(tmp_path: Path):
    previous = "a" * 64
    stages = {}
    for index, name in enumerate(APPLICATION_STAGES, start=1):
        output = f"{index:064x}"
        stages[name] = {
            "status": "completed",
            "input_root": previous,
            "output_content_hash": output,
            "validator": f"validator.{name}",
            "terminal": "success",
        }
        previous = output
    semantic = {
        "schema_version": "task055k_application_stage_journal_v1",
        "status": "completed",
        "application_spec_hash": "a" * 64,
        "stage_root": "applications/stages/example",
        "stages": stages,
        "final_stage_root": previous,
    }
    path = tmp_path / "journal.json"
    path.write_text(json.dumps(semantic | {"content_hash": canonical_hash(semantic)}), encoding="utf-8")
    assert validate_stage_journal(path)["status"] == "completed"
    stages.pop("valuation")
    semantic["stages"] = stages
    path.write_text(json.dumps(semantic | {"content_hash": canonical_hash(semantic)}), encoding="utf-8")
    with pytest.raises(Exception):
        validate_stage_journal(path)


def test_source_entries_use_git_blob_and_portable_index_modes():
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "task_055_k/source_tree.py"],
        capture_output=True,
        check=False,
    )
    if tracked.returncode:
        pytest.skip("Task055-K runtime sources are not committed yet")
    entries = git_index_source_entries(Path(".").resolve())
    assert entries
    assert {row["git_index_mode"] for row in entries} <= {"100644", "100755"}
    assert all(len(row["git_blob_id"]) == 40 and len(row["sha256"]) == 64 for row in entries)


def test_ephemeral_signature_round_trip_and_substitution_rejection():
    signer = EphemeralReceiptSigner.generate()
    signature = signer.sign(b"receipt")
    verify_signature(public_key_pem=signer.public_key_pem, payload=b"receipt", signature_b64=signature)
    with pytest.raises(Task055KSigningError):
        verify_signature(public_key_pem=signer.public_key_pem, payload=b"replacement", signature_b64=signature)
