from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import (
    free_provider_backfill as capture_module,
)
from auto_alpha.data.ingestion.pipeline.ashare import (
    free_provider_cninfo_security_lifecycle as lifecycle,
)
from auto_alpha.data.ingestion.pipeline.ashare.provider_probe import (
    ProviderProbeObservation,
    ProviderProbeRequest,
)
from auto_alpha.platform.artifacts.storage import canonical_hash
from auto_alpha.platform.governance.network.signing import EphemeralReceiptSigner


EXPECTED_DOCUMENTS = (
    (
        "1205690369",
        "2018-12-26",
        "https://static.cninfo.com.cn/finalpage/2018-12-26/1205690369.PDF",
        ["000022", "001872"],
        "security_code_identity_candidate",
    ),
    (
        "1207164397",
        "2019-12-16",
        "https://static.cninfo.com.cn/finalpage/2019-12-16/1207164397.PDF",
        ["000043", "001914"],
        "security_code_identity_candidate",
    ),
    (
        "1204831387",
        "2018-04-28",
        "https://static.cninfo.com.cn/finalpage/2018-04-28/1204831387.PDF",
        ["600680"],
        "suspension_state_candidate",
    ),
    (
        "1204983113",
        "2018-05-23",
        "https://static.cninfo.com.cn/finalpage/2018-05-23/1204983113.PDF",
        ["600680"],
        "security_lifecycle_candidate",
    ),
    (
        "1206282885",
        "2019-05-18",
        "https://static.cninfo.com.cn/finalpage/2019-05-18/1206282885.PDF",
        ["600680"],
        "security_lifecycle_candidate",
    ),
)

EXPECTED_POPULATION_ROOT = (
    "58e2cdfe4cfb8f71fced3955b44a2928a570fc41004af12256996823eaacde4a"
)
EXPECTED_REQUEST_PLAN_HASH = (
    "8fdb2a82225f2e810feb36e86fbf14643e664b8e9df46d11f1984886b8c986ad"
)
_PDF_BODY = b"%PDF-1.7\n" + b"x" * 96 + b"\nstartxref\n0\n%%EOF\n"


class _HTTPResponse:
    status = 200

    def __init__(self, body: bytes = _PDF_BODY) -> None:
        self._body = body
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/pdf",
        }

    def read(self, _maximum: int) -> bytes:
        return self._body


def _alternative_document_block_reason(_body: bytes) -> str | None:
    return "test-block"


class _AlternativeNoRedirectHandler:
    pass


def _test_key_hash(signer: EphemeralReceiptSigner) -> str:
    return canonical_hash(signer.public_key_pem.decode("ascii"))


def _approve_test_signer(
    monkeypatch: pytest.MonkeyPatch,
    signer: EphemeralReceiptSigner,
) -> tuple[str, str]:
    before = lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    monkeypatch.setattr(
        lifecycle,
        "APPROVED_CAPTURE_KEY_SHA256",
        _test_key_hash(signer),
    )
    after = lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    assert after != before
    return before, after


def _actual_http_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport, list[str]]:
    called: list[str] = []
    transport = lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=lifecycle.LOCKED_MINIMUM_DELAY_SECONDS,
    )

    def open_official(request: Any, *, timeout: float) -> _HTTPResponse:
        called.append(f"{request.full_url}:{timeout}")
        return _HTTPResponse()

    monkeypatch.setattr(transport._transport._opener, "open", open_official)
    monkeypatch.setattr(capture_module.time, "sleep", lambda _seconds: None)
    return transport, called


def _capture_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], EphemeralReceiptSigner, str]:
    signer = EphemeralReceiptSigner.generate()
    _before, approved_root = _approve_test_signer(monkeypatch, signer)
    transport, called = _actual_http_transport(monkeypatch)
    published = lifecycle.capture_cninfo_security_identity_lifecycle_documents(
        output_root=tmp_path / "capture",
        signer=signer,
        transport=transport,
    )
    assert len(called) == 5
    return published, signer, approved_root


def _official_envelope(
    request: ProviderProbeRequest,
    *,
    body: bytes = _PDF_BODY,
) -> dict[str, Any]:
    return {
        "schema_version": "official_http_probe_envelope_v1",
        "url": request.url,
        "method": "GET",
        "status_code": 200,
        "response_headers": {
            "Content-Length": str(len(body)),
            "Content-Type": "application/pdf",
        },
        "body_base64": base64.b64encode(body).decode("ascii"),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "elapsed_seconds": 0.01,
        "redirect_followed": False,
    }


def _observation(payload: bytes) -> ProviderProbeObservation:
    return ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=payload,
        row_count=1,
        status_code=200,
        checks={"untrusted_upstream_check": True},
        transport_exchange_count=1,
    )


def _empty_terminal(
    requests: list[ProviderProbeRequest],
    relative_path: str = "raw_envelopes/not-read.json",
) -> dict[str, dict[str, Any]]:
    return {
        request.request_id: {
            "raw_envelope_relative_path": relative_path,
            "status_code": 200,
        }
        for request in requests
    }


def _authorized_contract_and_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], dict[str, Any]]:
    signer = EphemeralReceiptSigner.generate()
    _approve_test_signer(monkeypatch, signer)
    implementation_root = (
        lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    )
    contract = lifecycle._locked_contract(
        output_root=tmp_path / "capture",
        signer=signer,
        implementation_root=implementation_root,
    ).semantic()
    _population, requests = lifecycle._locked_plan_without_validation()
    plan = {
        "schema_version": capture_module.PLAN_SCHEMA,
        "request_plan_hash": lifecycle.REQUEST_PLAN_HASH,
        "requests": [request.semantic() for request in requests],
    }
    return contract, plan


def test_exact_profile_builds_only_the_five_locked_official_documents() -> None:
    population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )

    assert lifecycle.PROFILE_ID == "cninfo_security_identity_lifecycle_exact_v1"
    assert len(population) == len(requests) == 5
    assert [
        (
            row["announcement_id"],
            row["announcement_date"],
            row["url"],
            row["subject_security_codes"],
            row["evidence_question"],
        )
        for row in population
    ] == list(EXPECTED_DOCUMENTS)
    assert all(request.method == "GET" for request in requests)
    assert all(request.provider == "cninfo" for request in requests)
    assert all(request.url.endswith(".PDF") for request in requests)
    assert all(
        row["publication_time_proven"] is False
        and row["provider_origin_attested"] is False
        and row["capture_runtime_isolation_verified"] is False
        and row["data_admission_eligible"] is False
        and row["downstream_eligible"] is False
        and row["downstream_ineligible"] is True
        for row in population
    )
    assert all(
        request.metadata["known_at_date"]
        == request.metadata["announcement_date"]
        and request.metadata["publication_time_proven"] is False
        and request.metadata["pit_timeline_adjudicated"] is False
        and request.metadata["security_identity_adjudicated"] is False
        and request.metadata["security_lifecycle_adjudicated"] is False
        for request in requests
    )

    lifecycle.validate_cninfo_security_identity_lifecycle_plan(
        population,
        requests,
    )
    assert lifecycle.POPULATION_ROOT == EXPECTED_POPULATION_ROOT
    assert lifecycle.REQUEST_PLAN_HASH == EXPECTED_REQUEST_PLAN_HASH


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "extra",
        "announcement_id",
        "announcement_date",
        "host",
        "path",
        "extension",
        "source_metadata",
        "publication_time_claim",
    ),
)
def test_exact_profile_rejects_any_plan_drift_before_capture(mutation: str) -> None:
    population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )

    if mutation == "missing":
        requests = requests[:-1]
    elif mutation == "extra":
        requests = [*requests, requests[-1]]
    elif mutation == "announcement_id":
        requests[0] = replace(
            requests[0],
            metadata=dict(requests[0].metadata)
            | {"announcement_id": "1205690368"},
        )
    elif mutation == "announcement_date":
        requests[0] = replace(
            requests[0],
            metadata=dict(requests[0].metadata)
            | {"announcement_date": "2018-12-25"},
        )
    elif mutation == "host":
        requests[0] = replace(
            requests[0],
            url=requests[0].url.replace("static.cninfo.com.cn", "example.com"),
        )
    elif mutation == "path":
        requests[0] = replace(
            requests[0],
            url=requests[0].url.replace("/finalpage/", "/other/"),
        )
    elif mutation == "extension":
        requests[0] = replace(
            requests[0],
            url=requests[0].url.removesuffix(".PDF") + ".HTML",
        )
    elif mutation == "source_metadata":
        requests[0] = replace(
            requests[0],
            metadata=dict(requests[0].metadata)
            | {"known_at_semantics": "pit_timeline_complete"},
        )
    elif mutation == "publication_time_claim":
        requests[0] = replace(
            requests[0],
            metadata=dict(requests[0].metadata)
            | {"publication_time_proven": True},
        )

    with pytest.raises(
        ValueError,
        match="cninfo_security_identity_lifecycle_plan_exact_closure_invalid",
    ):
        lifecycle.validate_cninfo_security_identity_lifecycle_plan(
            population,
            requests,
        )


def test_mutated_opener_capture_remains_unattested_and_nonadmissible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published, _signer, approved_root = _capture_fixture(tmp_path, monkeypatch)
    validated = lifecycle.validate_cninfo_security_identity_lifecycle_capture(
        published["manifest_path"]
    )

    assert published["status"] == "succeeded"
    assert validated["publication_signature_verified"] is True
    assert validated["signed_integrity_verified"] is True
    assert (
        validated["operator_contract_and_capture_key_authorization_verified"]
        is True
    )
    assert validated["operator_capture_authorization_semantics"] == (
        "approved_operator_permission_context_and_local_capture_key_only"
    )
    assert "capture_authorization_verified" not in validated
    assert validated["approved_capture_key_verified"] is True
    assert validated["approved_capture_key_semantics"] == (
        "local_operator_capture_signature_not_provider_origin"
    )
    assert validated["provider_origin_attested"] is False
    assert validated["capture_runtime_isolation_verified"] is False
    assert validated["current_replay_compatible"] is True
    assert validated["normalized_replay_verified"] is True
    assert validated["profile_id"] == lifecycle.PROFILE_ID
    assert validated["request_count"] == 5
    assert validated["formal_data_admission_ready"] is False
    assert validated["data_admission_eligible"] is False
    assert validated["downstream_eligible"] is False
    assert validated["downstream_ineligible"] is True
    assert validated["publication_time_proven"] is False
    assert all(value is False for value in validated["safety"].values())

    generation = Path(validated["manifest_path"]).parent
    contract = json.loads(
        (generation / "activity_contract.json").read_text(encoding="utf-8")
    )
    assert contract["capture_public_key_sha256"] == lifecycle.APPROVED_CAPTURE_KEY_SHA256
    assert contract["permission_context_id"] == lifecycle.DEFAULT_PERMISSION_CONTEXT
    assert contract["activity_name"] == lifecycle.ACTIVITY_NAME
    assert contract["adapter_identity"]["http"] == lifecycle.HTTP_ADAPTER_ID
    assert contract["adapter_identity"]["authorization_policy"] == (
        lifecycle.AUTHORIZATION_POLICY_ID
    )
    assert contract["adapter_identity"]["implementation_root"] == approved_root
    assert contract["budget"] == lifecycle._locked_budget(5).to_dict()

    rows = [
        json.loads(line)
        for line in (
            generation / "normalized/document_index.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 5
    assert all(row["document_format"] == "pdf" for row in rows)
    assert all(
        row["known_at_date"] == row["announcement_date"]
        and row["known_at_semantics"]
        == "official_announcement_publication_date_only"
        and row["publication_time_proven"] is False
        and row["provider_origin_attested"] is False
        and row["capture_runtime_isolation_verified"] is False
        and row["data_admission_eligible"] is False
        and row["downstream_eligible"] is False
        and row["downstream_ineligible"] is True
        and row["pit_timeline_adjudicated"] is False
        and row["security_identity_adjudicated"] is False
        and row["security_lifecycle_adjudicated"] is False
        for row in rows
    )
    normalized = json.loads(
        (generation / "normalized/normalized_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert normalized["publication_time_proven"] is False
    assert normalized["provider_origin_attested"] is False
    assert normalized["capture_runtime_isolation_verified"] is False
    assert normalized["data_admission_eligible"] is False
    assert normalized["downstream_eligible"] is False
    assert normalized["downstream_ineligible"] is True
    assert "provider_origin_not_attested" in normalized["blockers"]
    assert "capture_runtime_isolation_not_verified" in normalized["blockers"]
    assert "official_publication_timestamp_receipt_not_bound" in normalized[
        "blockers"
    ]


@pytest.mark.parametrize(
    ("mutation", "failed_check"),
    (
        ("wrong_url", "request_url_bound"),
        ("redirect", "redirect_not_followed"),
        ("body_hash", "body_sha256_matches"),
        ("html", "pdf_magic_valid"),
        ("broken_pdf", "pdf_structure_valid"),
        ("body_truncated", "body_not_truncated"),
        ("redirect_chain", "redirect_chain_absent"),
        ("missing_elapsed", "elapsed_seconds_valid"),
        ("elapsed_bool", "elapsed_seconds_valid"),
        ("headers_list", "response_headers_shape_exact"),
        ("header_case_collision", "response_headers_shape_exact"),
        ("status_bool", "http_status_success"),
    ),
)
def test_official_transport_recomputes_exact_envelope_and_pdf_checks(
    mutation: str,
    failed_check: str,
) -> None:
    _population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )
    request = requests[0]

    def upstream(
        observed_request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        body = _PDF_BODY
        if mutation == "html":
            body = b"<html><body>not a pdf</body></html>"
        elif mutation == "broken_pdf":
            body = b"%PDF-1.7\n" + b"x" * 96
        envelope = _official_envelope(observed_request, body=body)
        if mutation == "wrong_url":
            envelope["url"] = (
                "https://static.cninfo.com.cn/finalpage/wrong.PDF"
            )
        elif mutation == "redirect":
            envelope["redirect_followed"] = True
        elif mutation == "body_hash":
            envelope["body_sha256"] = "0" * 64
        elif mutation == "body_truncated":
            envelope["body_truncated"] = True
        elif mutation == "redirect_chain":
            envelope["redirect_chain"] = [observed_request.url]
        elif mutation == "missing_elapsed":
            del envelope["elapsed_seconds"]
        elif mutation == "elapsed_bool":
            envelope["elapsed_seconds"] = True
        elif mutation == "headers_list":
            envelope["response_headers"] = []
        elif mutation == "header_case_collision":
            envelope["response_headers"]["content-type"] = "application/pdf"
        elif mutation == "status_bool":
            envelope["status_code"] = True
        return _observation(json.dumps(envelope, sort_keys=True).encode())

    transport = lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=0,
        transport=upstream,
    )
    observed = transport(request, 3)

    assert observed.terminal_state == "error"
    assert observed.checks[failed_check] is False
    if mutation in {
        "body_truncated",
        "redirect_chain",
        "missing_elapsed",
        "elapsed_bool",
        "headers_list",
        "header_case_collision",
        "status_bool",
    }:
        assert observed.checks["http_envelope_schema_exact"] is False


def test_official_transport_rejects_duplicate_inner_json_keys() -> None:
    _population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )
    request = requests[0]
    encoded = json.dumps(
        _official_envelope(request),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    encoded = encoded.replace(
        b'"method":"GET"',
        b'"method":"GET","method":"GET"',
    )

    transport = lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=0,
        transport=lambda _request, _timeout: _observation(encoded),
    )
    observed = transport(request, 3)

    assert observed.terminal_state == "error"
    assert observed.error_code == (
        "cninfo_security_lifecycle_http_envelope_invalid"
    )
    assert observed.checks == {"http_envelope_schema_exact": False}


def test_transport_rejects_request_drift_before_calling_upstream() -> None:
    _population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )
    upstream_call_count = 0

    def upstream(
        _request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        nonlocal upstream_call_count
        upstream_call_count += 1
        raise AssertionError("upstream must remain unreachable")

    transport = lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=0,
        transport=upstream,
    )
    changed = replace(
        requests[0],
        url=requests[0].url.replace("1205690369.PDF", "1205690368.PDF"),
    )

    with pytest.raises(
        ValueError,
        match="cninfo_security_identity_lifecycle_plan_exact_closure_invalid",
    ):
        transport(changed, 3)
    assert upstream_call_count == 0


@pytest.mark.parametrize(
    "kwargs",
    (
        {"permission_context_id": "unapproved"},
        {"minimum_delay_seconds": 1.0},
        {"timeout_seconds": 29.0},
        {"max_retries": 1},
    ),
)
def test_nondefault_capture_controls_block_before_transport(
    tmp_path: Path,
    kwargs: dict[str, Any],
) -> None:
    calls = 0

    def upstream(
        _request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must remain unreachable")

    transport = lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=lifecycle.LOCKED_MINIMUM_DELAY_SECONDS,
        transport=upstream,
    )
    with pytest.raises(
        ValueError,
        match="cninfo_security_lifecycle_contract_controls_invalid",
    ):
        lifecycle.capture_cninfo_security_identity_lifecycle_documents(
            output_root=tmp_path / "capture",
            signer=EphemeralReceiptSigner.generate(),
            transport=transport,
            **kwargs,
        )
    assert calls == 0


def test_injected_http_transport_cannot_publish_governed_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def upstream(
        _request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must remain unreachable")

    signer = EphemeralReceiptSigner.generate()
    _approve_test_signer(monkeypatch, signer)
    transport = lifecycle.CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=lifecycle.LOCKED_MINIMUM_DELAY_SECONDS,
        transport=upstream,
    )
    with pytest.raises(
        ValueError,
        match="cninfo_security_lifecycle_http_transport_invalid",
    ):
        lifecycle.capture_cninfo_security_identity_lifecycle_documents(
            output_root=tmp_path / "capture",
            signer=signer,
            transport=transport,
        )
    assert calls == 0


def test_unapproved_capture_key_blocks_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, called = _actual_http_transport(monkeypatch)

    with pytest.raises(
        ValueError,
        match="cninfo_security_lifecycle_capture_key_not_approved",
    ):
        lifecycle.capture_cninfo_security_identity_lifecycle_documents(
            output_root=tmp_path / "capture",
            signer=EphemeralReceiptSigner.generate(),
            transport=transport,
        )
    assert called == []


@pytest.mark.parametrize(
    "mutation",
    (
        "activity",
        "permission",
        "host",
        "scope",
        "budget_delay",
        "budget_timeout",
        "budget_retries",
        "budget_requests",
        "http",
        "authorization_policy",
        "adapter_extra",
        "contract_extra",
        "capture_key",
        "plan_extra",
        "plan_request",
    ),
)
def test_specialized_contract_validator_has_two_way_exact_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    contract, plan = _authorized_contract_and_plan(tmp_path, monkeypatch)
    contract = copy.deepcopy(contract)
    plan = copy.deepcopy(plan)
    if mutation == "activity":
        contract["activity_name"] = "lookalike"
    elif mutation == "permission":
        contract["permission_context_id"] = "lookalike"
    elif mutation == "host":
        contract["allowed_hosts"].append("example.com")
    elif mutation == "scope":
        contract["scope"]["request_start"] = "20120101"
    elif mutation == "budget_delay":
        contract["budget"]["minimum_delay_seconds"] = 1.0
    elif mutation == "budget_timeout":
        contract["budget"]["timeout_seconds"] = 31.0
    elif mutation == "budget_retries":
        contract["budget"]["max_retries"] = 3
    elif mutation == "budget_requests":
        contract["budget"]["max_requests"] += 1
    elif mutation == "http":
        contract["adapter_identity"]["http"] = "redirecting_http"
    elif mutation == "authorization_policy":
        contract["adapter_identity"]["authorization_policy"] = "lookalike"
    elif mutation == "adapter_extra":
        contract["adapter_identity"]["extra"] = "ignored-by-weak-validator"
    elif mutation == "contract_extra":
        contract["extra"] = "ignored-by-weak-validator"
    elif mutation == "capture_key":
        contract["capture_public_key_sha256"] = "0" * 64
    elif mutation == "plan_extra":
        plan["extra"] = False
    elif mutation == "plan_request":
        plan["requests"] = plan["requests"][:-1]

    with pytest.raises(
        ValueError,
        match="cninfo_security_lifecycle_capture_identity_invalid",
    ):
        lifecycle._validate_authorized_contract_closure(contract, plan)


def test_specialized_contract_validator_accepts_only_current_locked_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract, plan = _authorized_contract_and_plan(tmp_path, monkeypatch)

    assert lifecycle._validate_authorized_contract_closure(contract, plan) is True


def test_historical_signed_integrity_is_distinct_from_current_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published, _signer, _root = _capture_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        lifecycle,
        "_document_block_reason",
        _alternative_document_block_reason,
    )

    historical = lifecycle.validate_cninfo_security_identity_lifecycle_capture(
        published["manifest_path"],
        require_current_replay_compatible=False,
    )
    assert historical["signed_integrity_verified"] is True
    assert (
        historical["operator_contract_and_capture_key_authorization_verified"]
        is True
    )
    assert historical["provider_origin_attested"] is False
    assert historical["capture_runtime_isolation_verified"] is False
    assert historical["data_admission_eligible"] is False
    assert historical["current_replay_compatible"] is False
    assert historical["normalized_replay_verified"] is False
    assert historical["downstream_eligible"] is False
    assert "provider_origin_not_attested" in historical["blockers"]
    assert "capture_runtime_isolation_not_verified" in historical["blockers"]
    assert historical["blockers"][-1] == (
        "current_lifecycle_implementation_root_mismatch"
    )

    with pytest.raises(
        ValueError,
        match="cninfo_security_lifecycle_current_replay_incompatible",
    ):
        lifecycle.validate_cninfo_security_identity_lifecycle_capture(
            published["manifest_path"]
        )


def test_implementation_root_binds_behavior_no_redirect_and_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    monkeypatch.setattr(
        lifecycle,
        "_document_block_reason",
        _alternative_document_block_reason,
    )
    behavior_changed = (
        lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    )
    assert behavior_changed != baseline

    monkeypatch.undo()
    baseline = lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    monkeypatch.setattr(
        lifecycle.probe_module,
        "_NoRedirectHandler",
        _AlternativeNoRedirectHandler,
    )
    no_redirect_changed = (
        lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    )
    assert no_redirect_changed != baseline

    monkeypatch.undo()
    baseline = lifecycle.cninfo_security_identity_lifecycle_implementation_root()
    monkeypatch.setattr(
        lifecycle,
        "LOCKED_MAX_TOTAL_RESPONSE_BYTES",
        lifecycle.LOCKED_MAX_TOTAL_RESPONSE_BYTES + 1,
    )
    assert lifecycle.cninfo_security_identity_lifecycle_implementation_root() != baseline


@pytest.mark.parametrize("kind", ("symlink", "regular", "fifo", "unexpected"))
def test_normalizer_rejects_unsafe_normalized_output_before_input_read(
    tmp_path: Path,
    kind: str,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    normalized = run_root / "normalized"
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "symlink":
        normalized.symlink_to(outside, target_is_directory=True)
    elif kind == "regular":
        normalized.write_text("not a directory", encoding="utf-8")
    else:
        normalized.mkdir()
        if kind == "fifo":
            os.mkfifo(normalized / "document_index.jsonl")
        else:
            (normalized / "unexpected.txt").write_text("x", encoding="utf-8")
    _population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )

    with pytest.raises(ValueError, match="cninfo_security_lifecycle_normalized"):
        lifecycle.normalize_cninfo_security_identity_lifecycle_documents(
            run_root,
            requests,
            _empty_terminal(requests),
        )
    assert list(outside.iterdir()) == []


def test_normalizer_rejects_symlinked_ancestor_without_external_write(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    actual = outside / "run"
    actual.mkdir(parents=True)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)
    run_root = linked_parent / "run"
    _population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )

    with pytest.raises(
        ValueError,
        match="cninfo_security_lifecycle_path_symlink_forbidden",
    ):
        lifecycle.normalize_cninfo_security_identity_lifecycle_documents(
            run_root,
            requests,
            _empty_terminal(requests),
        )
    assert not (actual / "normalized").exists()


@pytest.mark.parametrize("kind", ("escape", "symlink", "fifo"))
def test_normalizer_rejects_unsafe_raw_input(
    tmp_path: Path,
    kind: str,
) -> None:
    run_root = tmp_path / "run"
    raw = run_root / "raw_envelopes"
    raw.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    relative = "../outside.json"
    if kind == "symlink":
        (raw / "unsafe.json").symlink_to(outside)
        relative = "raw_envelopes/unsafe.json"
    elif kind == "fifo":
        os.mkfifo(raw / "unsafe.json")
        relative = "raw_envelopes/unsafe.json"
    _population, requests = (
        lifecycle.build_cninfo_security_identity_lifecycle_plan()
    )

    with pytest.raises(ValueError, match="cninfo_security_lifecycle_input"):
        lifecycle.normalize_cninfo_security_identity_lifecycle_documents(
            run_root,
            requests,
            _empty_terminal(requests, relative),
        )
    normalized = run_root / "normalized"
    assert normalized.is_dir()
    assert list(normalized.iterdir()) == []
    assert outside.read_text(encoding="utf-8") == "{}"


def test_cli_exposes_only_the_fixed_plan_without_network(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert lifecycle.main(["--plan-only"]) == 0
    preview = json.loads(capsys.readouterr().out)

    assert preview == {
        "schema_version": "cninfo_security_identity_lifecycle_plan_preview_v1",
        "profile_id": lifecycle.PROFILE_ID,
        "population_count": 5,
        "population_root": EXPECTED_POPULATION_ROOT,
        "request_count": 5,
        "request_plan_hash": EXPECTED_REQUEST_PLAN_HASH,
        "network_called": False,
        "formal_data_admission_ready": False,
        "provider_origin_attested": False,
        "capture_runtime_isolation_verified": False,
        "data_admission_eligible": False,
        "downstream_eligible": False,
        "downstream_ineligible": True,
        "blockers": [
            "provider_origin_not_attested",
            "capture_runtime_isolation_not_verified",
            "official_publication_timestamp_receipt_not_bound",
            "official_document_text_derivation_not_run",
            "pit_security_identity_timeline_derivation_not_run",
            "suspension_and_lifecycle_adjudication_not_run",
        ],
        "safety": {
            "alpha_search_authorized": False,
            "data_admission_eligible": False,
            "holdout_activation_authorized": False,
            "live_trading_authorized": False,
            "paper_trading_authorized": False,
            "profile_activation_authorized": False,
            "shadow_trading_authorized": False,
        },
    }

    assert lifecycle.main([]) == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["reason"] == "free_provider_backfill_network_authority_missing"
    assert blocked["network_called"] is False

    with pytest.raises(SystemExit):
        lifecycle.main(["--url", "https://example.com/not-approved.pdf"])
