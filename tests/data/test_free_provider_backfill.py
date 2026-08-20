from __future__ import annotations

import base64
import hashlib
import json
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import free_provider_backfill
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    BackfillResourceBudget,
    FreeProviderBackfillContract,
    PauseResumeAuthorization,
    ProviderBackfillPaused,
    RecoveringBaostockTransport,
    _public_key_hash,
    _safe_output_root,
    build_baostock_state_plan,
    normalize_baostock_state_capture,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.provider_probe import (
    ProviderProbeObservation,
    ProviderProbeRequest,
)
from auto_alpha.platform.artifacts.storage import canonical_hash
from auto_alpha.platform.governance.network.signing import EphemeralReceiptSigner

FIXTURE_IMPLEMENTATION_ROOT = "f" * 64


class FakeTransport:
    def __init__(self, observations: dict[str, ProviderProbeObservation]) -> None:
        self.observations = observations
        self.calls: list[str] = []

    def __call__(
        self, request: ProviderProbeRequest, _timeout_seconds: float
    ) -> ProviderProbeObservation:
        self.calls.append(request.request_id)
        return self.observations[request.request_id]


def test_generic_baostock_implementation_identity_binds_strict_wire_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = free_provider_backfill._baostock_implementation_root()
    monkeypatch.setattr(
        free_provider_backfill,
        "baostock_wire_protocol_root",
        lambda: "e" * 64,
    )

    assert free_provider_backfill._baostock_implementation_root() != baseline


def test_baostock_session_expiry_replaces_transport_before_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[SessionExpiryTransport] = []

    class SessionExpiryTransport:
        def __init__(self) -> None:
            self.instance_ordinal = len(instances)
            self.closed = False
            instances.append(self)

        def __call__(
            self, request: ProviderProbeRequest, _timeout_seconds: float
        ) -> ProviderProbeObservation:
            if self.instance_ordinal == 0:
                return ProviderProbeObservation(
                    terminal_state="error",
                    raw_payload=b"expired-session",
                    row_count=None,
                    error_code="baostock:10001001",
                    diagnostics={"provider_error_message": "session expired"},
                    checks={"provider_success": False},
                    transport_exchange_count=1,
                )
            return _observation(request.request_id)

        def close(self) -> None:
            self.closed = True

        def restore(
            self, _request: ProviderProbeRequest, _record: object
        ) -> None:
            return None

    monkeypatch.setattr(
        free_provider_backfill,
        "BaostockProbeTransport",
        SessionExpiryTransport,
    )
    request = _baostock_request("session-expiry")
    transport = RecoveringBaostockTransport()

    first = transport(request, 3.0)
    second = transport(request, 3.0)

    assert first.error_code == "baostock_transport:SessionExpired:10001001"
    assert first.diagnostics == {
        "provider_error_message": "session expired",
        "session_recovery": {
            "adapter": "RecoveringBaostockTransport",
            "original_error_code": "baostock:10001001",
            "transport_replaced": True,
        },
    }
    assert second.terminal_state == "positive"
    assert len(instances) == 2
    assert instances[0].closed is True


def _request(request_id: str) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        request_id=request_id,
        provider="cninfo",
        endpoint="history_state_daily",
        method="GET",
        url=(
            "https://www.cninfo.com.cn/fixture"
            f"?start=2012-01-01&end=2012-01-02&id={request_id}"
        ),
        disposition="bounded_backfill",
        evidence_semantics="raw_custom_socket_response_plus_locked_parser",
        expected_terminal_states=("positive",),
        required_checks=("provider_success",),
        metadata={"case": "history", "ts_code": "600000.SH"},
    )


def _baostock_request(request_id: str) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        request_id=request_id,
        provider="baostock",
        endpoint="history_state_daily",
        method="GET",
        url=(
            "https://public-api.baostock.com/history"
            "?code=sh.600000&start=2012-01-01&end=2012-01-02"
            "&fields=date,code"
        ),
        disposition="bounded_backfill",
        evidence_semantics="raw_sdk_socket_response_plus_locked_parser",
        expected_terminal_states=("positive",),
        required_checks=("provider_success",),
        metadata={
            "case": "history_custom",
            "ts_code": "600000.SH",
            "provider_code": "sh.600000",
            "expected_fields": ("date", "code"),
        },
    )


def _observation(request_id: str) -> ProviderProbeObservation:
    return ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=json.dumps(
            {
                "schema_version": "fixture_baostock_envelope_v1",
                "request_id": request_id,
                "wire_exchanges": [],
                "parsed": {
                    "fields": ["value"],
                    "items": [[request_id]],
                    "canonical_logical_payload_sha256": canonical_hash(
                        {"fields": ["value"], "rows": [[request_id]]}
                    ),
                },
            },
            sort_keys=True,
        ).encode(),
        row_count=1,
        status_code=0,
        checks={"provider_success": True},
        transport_exchange_count=1,
    )


def _baostock_wire_observation(
    request: ProviderProbeRequest,
    *,
    session_expired: bool,
    rows_override: list[list[str]] | None = None,
) -> ProviderProbeObservation:
    query = {
        key: values[-1]
        for key, values in parse_qs(urlsplit(request.url).query).items()
    }
    fields_value = query["fields"]
    request_body = (
        "query_history_k_data_plus\x01anonymous\x011\x012000\x01"
        f"{query['code']}\x01{fields_value}\x01{query['start']}\x01"
        f"{query['end']}\x01d\x013"
    )
    request_header = f"00.9.30\x0195\x01{len(request_body):010d}".encode()
    request_head_body = request_header + request_body.encode()
    wire_request = (
        request_head_body
        + f"\x01{zlib.crc32(request_head_body)}\n".encode()
    )
    rows = (
        []
        if session_expired
        else rows_override or [[query["end"], query["code"]]]
    )
    fields = [] if session_expired else fields_value.split(",")
    if session_expired:
        response_body = "10001001\x01session expired"
        response_header = f"00.9.00\x0104\x01{len(response_body):010d}".encode()
        response_payload = response_body.encode()
    else:
        response_body = "\x01".join(
            [
                "0",
                "success",
                "query_history_k_data_plus",
                "anonymous",
                "1",
                "2000",
                json.dumps({"record": rows}, separators=(",", ":")),
                query["code"],
                fields_value,
                query["start"],
                query["end"],
                "d",
                "3",
            ]
        )
        response_payload = zlib.compress(response_body.encode())
        response_header = (
            f"00.9.00\x0196\x01{len(response_payload):010d}".encode()
        )
    response_crc = zlib.crc32(response_header + response_payload)
    wire_response = (
        response_header
        + response_payload
        + f"\x01{response_crc}".encode()
        + (b"\n" if not session_expired else b"")
        + b"<![CDATA[]]>\n"
    )
    exchange = {
        "wire_request_base64": base64.b64encode(wire_request).decode(),
        "request_sha256": hashlib.sha256(wire_request).hexdigest(),
        "request_size_bytes": len(wire_request),
        "socket_peer": ["1.2.3.4", 10030],
        "wire_response_base64": base64.b64encode(wire_response).decode(),
        "wire_response_sha256": hashlib.sha256(wire_response).hexdigest(),
        "wire_size_bytes": len(wire_response),
        "terminal_marker_present": True,
    }
    envelope = {
        "schema_version": "baostock_wire_probe_envelope_v1",
        "package_distribution_version": "0.9.3",
        "client_protocol_version": "00.9.30",
        "request_id": request.request_id,
        "wire_exchanges": [exchange],
        "parsed": {
            "fields": fields,
            "row_count": len(rows),
            "pages": [] if session_expired else [
                {"page": 1, "row_count": len(rows), "provider_page_size": 2000}
            ],
            "first_rows": rows[:3],
            "last_rows": rows[-3:],
            "canonical_logical_payload_sha256": canonical_hash(
                {"fields": fields, "rows": rows}
            ),
        },
        "provider_error": {
            "code": "10001001" if session_expired else "0",
            "message": "session expired" if session_expired else "success",
        },
    }
    return ProviderProbeObservation(
        terminal_state="error" if session_expired else "positive",
        raw_payload=json.dumps(envelope, sort_keys=True).encode(),
        row_count=None if session_expired else len(rows),
        status_code=None if session_expired else 0,
        error_code="baostock:10001001" if session_expired else None,
        diagnostics=(
            {"provider_error_message": "session expired"}
            if session_expired
            else {}
        ),
        checks={"provider_success": not session_expired},
        transport_exchange_count=1,
    )


def _baostock_partial_timeout_observation(
    request: ProviderProbeRequest,
) -> ProviderProbeObservation:
    complete = _baostock_wire_observation(request, session_expired=False)
    envelope = json.loads(complete.raw_payload)
    exchange = envelope["wire_exchanges"][0]
    exchange.update(
        {
            "wire_response_base64": "",
            "wire_response_sha256": hashlib.sha256(b"").hexdigest(),
            "wire_size_bytes": 0,
            "terminal_marker_present": False,
        }
    )
    envelope["parsed"] = {
        "fields": [],
        "row_count": 0,
        "pages": [],
        "first_rows": [],
        "last_rows": [],
        "canonical_logical_payload_sha256": canonical_hash(
            {"fields": [], "rows": []}
        ),
    }
    envelope["provider_error"] = {
        "type": "TimeoutError",
        "message": "timed out before terminal marker",
    }
    return ProviderProbeObservation(
        terminal_state="error",
        raw_payload=json.dumps(envelope, sort_keys=True).encode(),
        row_count=None,
        error_code="baostock_transport:TimeoutError",
        diagnostics={"wire_capture_count": 1},
        checks={"transport_completed": False},
        transport_exchange_count=1,
    )


def _baostock_login_only_transport_error_observation(
    request: ProviderProbeRequest,
) -> ProviderProbeObservation:
    request_body = "login\x01anonymous\x01123456\x010"
    request_header = f"00.9.30\x0100\x01{len(request_body):010d}".encode()
    request_head_body = request_header + request_body.encode()
    wire_request = (
        request_head_body
        + f"\x01{zlib.crc32(request_head_body)}\n".encode()
    )
    response_body = "0\x01success\x01login\x01anonymous\x0120260817042420182"
    response_header = f"00.9.00\x0101\x01{len(response_body):010d}".encode()
    response_head_body = response_header + response_body.encode()
    wire_response = (
        response_head_body
        + f"\x01{zlib.crc32(response_head_body)}".encode()
        + b"<![CDATA[]]>\n"
    )
    exchange = {
        "wire_request_base64": base64.b64encode(wire_request).decode(),
        "request_sha256": hashlib.sha256(wire_request).hexdigest(),
        "request_size_bytes": len(wire_request),
        "socket_peer": ["1.2.3.4", 10030],
        "wire_response_base64": base64.b64encode(wire_response).decode(),
        "wire_response_sha256": hashlib.sha256(wire_response).hexdigest(),
        "wire_size_bytes": len(wire_response),
        "terminal_marker_present": True,
    }
    envelope = {
        "schema_version": "baostock_wire_probe_envelope_v1",
        "package_distribution_version": "0.9.3",
        "client_protocol_version": "00.9.30",
        "request_id": request.request_id,
        "wire_exchanges": [exchange],
        "parsed": {
            "fields": [],
            "row_count": 0,
            "pages": [],
            "first_rows": [],
            "last_rows": [],
            "canonical_logical_payload_sha256": canonical_hash(
                {"fields": [], "rows": []}
            ),
        },
        "provider_error": {
            "type": "OSError",
            "message": "business send failed after login",
        },
    }
    return ProviderProbeObservation(
        terminal_state="error",
        raw_payload=json.dumps(envelope, sort_keys=True).encode(),
        row_count=None,
        error_code="baostock_transport:OSError",
        diagnostics={"wire_capture_count": 1},
        checks={"transport_completed": False},
        transport_exchange_count=1,
    )


def test_baostock_login_response_user_must_bind_business_session() -> None:
    request = _baostock_request("forged-login-user")
    observation = _baostock_login_only_transport_error_observation(request)
    envelope = json.loads(observation.raw_payload)
    exchange = envelope["wire_exchanges"][0]
    response_body = "0\x01success\x01login\x01forged-user\x0120260817042420182"
    response_header = f"00.9.00\x0101\x01{len(response_body):010d}".encode()
    response_head_body = response_header + response_body.encode()
    wire_response = (
        response_head_body
        + f"\x01{zlib.crc32(response_head_body)}".encode()
        + b"<![CDATA[]]>\n"
    )
    exchange["wire_response_base64"] = base64.b64encode(wire_response).decode()
    exchange["wire_response_sha256"] = hashlib.sha256(wire_response).hexdigest()
    exchange["wire_size_bytes"] = len(wire_response)

    with pytest.raises(ValueError, match="login_session_invalid"):
        free_provider_backfill._validate_baostock_wire_envelope(
            json.dumps(envelope, sort_keys=True).encode(),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="error",
        )


def _forbidden_observation() -> ProviderProbeObservation:
    return ProviderProbeObservation(
        terminal_state="error",
        raw_payload=b"<html><title>Access Denied</title></html>",
        row_count=None,
        status_code=403,
        error_code="http_status:403",
        diagnostics={"waf_html_observed": True},
        checks={"http_success": False},
        transport_exchange_count=1,
    )


def _contract(
    output: Path,
    signer: EphemeralReceiptSigner,
    *,
    max_requests: int = 4,
    max_retries: int = 1,
    provider: str = "cninfo",
) -> FreeProviderBackfillContract:
    return FreeProviderBackfillContract(
        activity_name="fixture_backfill_v1",
        provider=provider,
        output_root=output,
        permission_context_id="human-approved-fixture",
        population_root="a" * 64,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(signer.public_key_pem).decode(),
        scope_start="20120101",
        scope_end="20120102",
        request_start="20120101",
        request_end="20120102",
        allowed_hosts=(
            ("public-api.baostock.com",)
            if provider == "baostock"
            else ("www.cninfo.com.cn",)
        ),
        budget=BackfillResourceBudget(
            max_requests=max_requests,
            max_wire_exchanges=max_requests,
            max_response_bytes=1024 * 1024,
            max_total_response_bytes=4 * 1024 * 1024,
            timeout_seconds=3.0,
            minimum_delay_seconds=0,
            max_retries=max_retries,
        ),
        adapter_identity={
            "adapter": "fixture_backfill_v1",
            "implementation_root": FIXTURE_IMPLEMENTATION_ROOT,
        },
    )


def test_backfill_budget_accepts_six_retries_and_rejects_seven(
    tmp_path: Path,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    request = _request("one")
    transport = FakeTransport({"one": _observation("one")})

    published = run_free_provider_backfill(
        _contract(
            tmp_path / "six_retries",
            signer,
            max_requests=7,
            max_retries=6,
        ),
        [request],
        transport=transport,
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )

    assert published["status"] == "succeeded"
    with pytest.raises(ValueError, match="free_provider_backfill_budget_invalid"):
        run_free_provider_backfill(
            _contract(
                tmp_path / "seven_retries",
                signer,
                max_requests=8,
                max_retries=7,
            ),
            [request],
            transport=transport,
            signer=signer,
            runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
        )


def test_signed_backfill_publishes_valid_raw_closure_and_is_idempotent(
    tmp_path: Path,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    requests = [_request("first"), _request("second")]
    transport = FakeTransport({row.request_id: _observation(row.request_id) for row in requests})
    output = tmp_path / "capture"

    published = run_free_provider_backfill(
        _contract(output, signer),
        requests,
        transport=transport,
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )
    validated = validate_free_provider_backfill(published["manifest_path"])
    cached = run_free_provider_backfill(
        _contract(output, signer),
        requests,
        transport=transport,
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )

    assert published["status"] == "succeeded"
    assert validated["generation_id"] == published["generation_id"]
    assert published["request_count"] == 2
    assert published["capture_journal_event_count"] == 4
    assert published["capture_catalog_count"] == 2
    assert transport.calls == ["first", "second"]
    assert cached["cache_hit"] is True


@pytest.mark.parametrize(
    "error_code",
    (
        "baostock:10001001",
        "baostock_transport:SessionExpired:10001001",
    ),
)
def test_capture_engine_does_not_retry_unscoped_baostock_provider_error(
    tmp_path: Path,
    error_code: str,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    request = _request("session-expiry")
    observation = ProviderProbeObservation(
        terminal_state="error",
        raw_payload=b"expired-session",
        row_count=None,
        error_code=error_code,
        diagnostics={"provider_error_message": "session expired"},
        checks={"provider_success": False},
        transport_exchange_count=1,
    )
    calls: list[str] = []

    def transport(
        current: ProviderProbeRequest, _timeout_seconds: float
    ) -> ProviderProbeObservation:
        calls.append(current.request_id)
        return observation

    with pytest.raises(ProviderBackfillPaused, match="terminal_error"):
        run_free_provider_backfill(
            _contract(tmp_path / "capture", signer),
            [request],
            transport=transport,
            signer=signer,
            runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
        )

    assert calls == [request.request_id]


def test_recovering_baostock_transport_retries_and_publishes_signed_wire_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _baostock_request("session-expiry")
    signer = EphemeralReceiptSigner.generate()
    instances: list[SignedWireSessionTransport] = []

    class SignedWireSessionTransport:
        def __init__(self) -> None:
            self.instance_ordinal = len(instances)
            self.closed = False
            instances.append(self)

        def __call__(
            self, current: ProviderProbeRequest, _timeout_seconds: float
        ) -> ProviderProbeObservation:
            return _baostock_wire_observation(
                current, session_expired=self.instance_ordinal == 0
            )

        def close(self) -> None:
            self.closed = True

        def restore(
            self, _request: ProviderProbeRequest, _record: object
        ) -> None:
            return None

    monkeypatch.setattr(
        free_provider_backfill,
        "BaostockProbeTransport",
        SignedWireSessionTransport,
    )
    published = run_free_provider_backfill(
        _contract(tmp_path / "capture", signer, provider="baostock"),
        [request],
        transport=RecoveringBaostockTransport(),
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )
    validated = validate_free_provider_backfill(published["manifest_path"])

    assert published["status"] == "succeeded"
    assert published["terminal_attempt_count"] == 2
    assert published["resource_usage"]["attempt_count"] == 2
    assert validated["publication_signature_verified"] is True
    assert len(instances) == 2
    assert instances[0].closed is True


def test_baostock_partial_timeout_then_retry_publishes_valid_wire_closure(
    tmp_path: Path,
) -> None:
    request = _baostock_request("partial-timeout")
    signer = EphemeralReceiptSigner.generate()
    observations = [
        _baostock_partial_timeout_observation(request),
        _baostock_wire_observation(request, session_expired=False),
    ]

    def transport(
        _request: ProviderProbeRequest, _timeout_seconds: float
    ) -> ProviderProbeObservation:
        return observations.pop(0)

    published = run_free_provider_backfill(
        _contract(tmp_path / "capture", signer, provider="baostock"),
        [request],
        transport=transport,
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )
    validated = validate_free_provider_backfill(published["manifest_path"])

    assert published["status"] == "succeeded"
    assert published["terminal_attempt_count"] == 2
    assert validated["publication_signature_verified"] is True
    assert observations == []


def test_baostock_login_only_error_then_retry_publishes_valid_wire_closure(
    tmp_path: Path,
) -> None:
    request = _baostock_request("login-only-error")
    signer = EphemeralReceiptSigner.generate()
    observations = [
        _baostock_login_only_transport_error_observation(request),
        _baostock_wire_observation(request, session_expired=False),
    ]

    def transport(
        _request: ProviderProbeRequest, _timeout_seconds: float
    ) -> ProviderProbeObservation:
        return observations.pop(0)

    published = run_free_provider_backfill(
        _contract(tmp_path / "capture", signer, provider="baostock"),
        [request],
        transport=transport,
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )
    validated = validate_free_provider_backfill(published["manifest_path"])

    assert published["terminal_attempt_count"] == 2
    assert validated["status"] == "succeeded"
    assert observations == []


def test_recovering_baostock_transport_stops_after_repeated_session_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _baostock_request("repeated-session-expiry")
    signer = EphemeralReceiptSigner.generate()
    calls: list[str] = []

    class AlwaysExpiredSessionTransport:
        def __call__(
            self, current: ProviderProbeRequest, _timeout_seconds: float
        ) -> ProviderProbeObservation:
            calls.append(current.request_id)
            return _baostock_wire_observation(current, session_expired=True)

        def close(self) -> None:
            return None

        def restore(
            self, _request: ProviderProbeRequest, _record: object
        ) -> None:
            return None

    monkeypatch.setattr(
        free_provider_backfill,
        "BaostockProbeTransport",
        AlwaysExpiredSessionTransport,
    )
    with pytest.raises(ProviderBackfillPaused, match="terminal_error"):
        run_free_provider_backfill(
            _contract(tmp_path / "capture", signer, provider="baostock"),
            [request],
            transport=RecoveringBaostockTransport(),
            signer=signer,
            runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
        )

    assert calls == [request.request_id, request.request_id]
    activity = next((tmp_path / ".capture.activities").glob("[0-9a-f]*"))
    terminal_rows = [
        json.loads(line)
        for line in (activity / "capture_journal.jsonl").read_text().splitlines()
        if '"event_type":"capture_attempt_terminal"' in line
    ]
    assert [row["retry_ordinal"] for row in terminal_rows] == [0, 1]
    assert {
        row["error_code"] for row in terminal_rows
    } == {"baostock_transport:SessionExpired:10001001"}
    assert all(
        row["diagnostics"]["session_recovery"]["original_error_code"]
        == "baostock:10001001"
        for row in terminal_rows
    )


def test_validator_rejects_raw_capture_tampering(tmp_path: Path) -> None:
    signer = EphemeralReceiptSigner.generate()
    request = _request("one")
    published = run_free_provider_backfill(
        _contract(tmp_path / "capture", signer),
        [request],
        transport=FakeTransport({"one": _observation("one")}),
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )
    generation = Path(published["manifest_path"]).parent
    raw = next((generation / "raw_envelopes").glob("*.json"))
    raw.chmod(0o640)
    raw.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="raw_capture|catalog|closure"):
        validate_free_provider_backfill(generation / "free_provider_backfill_manifest.json")


def test_publication_signature_blocks_rehashed_normalized_manifest_tampering(
    tmp_path: Path,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    published = run_free_provider_backfill(
        _contract(tmp_path / "capture", signer),
        [_request("one")],
        transport=FakeTransport({"one": _observation("one")}),
        signer=signer,
        runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
    )
    generation = Path(published["manifest_path"]).parent
    manifest_path = generation / "free_provider_backfill_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["normalized_artifacts"] = [
        {
            "role": "forged",
            "relative_path": "normalized/forged.json",
            "record_count": 1,
            "sha256": "0" * 64,
            "size_bytes": 2,
        }
    ]
    semantic = {
        key: value
        for key, value in manifest.items()
        if key
        not in {"capture_publication_signature", "content_hash", "generation_id"}
    }
    manifest["content_hash"] = canonical_hash(semantic)
    manifest["generation_id"] = f"free_provider_backfill_{manifest['content_hash'][:24]}"
    manifest_path.chmod(0o640)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    renamed = generation.with_name(manifest["generation_id"])
    generation.rename(renamed)

    with pytest.raises(ValueError, match="publication_signature_invalid"):
        validate_free_provider_backfill(
            renamed / "free_provider_backfill_manifest.json"
        )


def test_paused_nonretryable_attempt_cannot_resume_with_self_asserted_authorization(
    tmp_path: Path,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    request = _request("one")
    output = tmp_path / "capture"
    contract = _contract(output, signer)
    with pytest.raises(ProviderBackfillPaused, match="circuit_breaker"):
        run_free_provider_backfill(
            contract,
            [request],
            transport=FakeTransport({"one": _forbidden_observation()}),
            signer=signer,
            runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
        )
    activity = next((tmp_path / ".capture.activities").glob("[0-9a-f]*"))
    pause = json.loads(next((activity / "pauses").glob("pause_*.json")).read_text())

    resumed_transport = FakeTransport({"one": _observation("one")})
    with pytest.raises(ValueError, match="trusted_resume_authority_not_implemented"):
        run_free_provider_backfill(
            contract,
            [request],
            transport=resumed_transport,
            signer=signer,
            resume_authorization=PauseResumeAuthorization(
                authorization_id="human-approved-pause-retry-1",
                pause_content_hash=pause["content_hash"],
            ),
            runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
        )
    assert resumed_transport.calls == []

    alternate_transport = FakeTransport({"one": _observation("one")})
    alternate_contract = _contract(tmp_path / "alternate_capture", signer)
    with pytest.raises(ProviderBackfillPaused, match="breaker_already_open"):
        run_free_provider_backfill(
            alternate_contract,
            [request],
            transport=alternate_transport,
            signer=signer,
            runtime_implementation_root=FIXTURE_IMPLEMENTATION_ROOT,
        )
    assert alternate_transport.calls == []


@pytest.mark.parametrize(
    "relative",
    (
        "",
        "data/daily_bars",
        "canonical_freezes/task_056c_v1",
        "local_development_bundles/csi300_2012_2019_v1",
    ),
)
def test_output_root_cannot_target_existing_lake_assets(relative: str) -> None:
    protected = Path("/home/lijunsi/data/auto-alpha/ashare_lake") / relative

    with pytest.raises(ValueError, match="protected_lake_write_forbidden"):
        _safe_output_root(protected)


def test_runtime_implementation_drift_stops_before_write_or_transport(
    tmp_path: Path,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    output = tmp_path / "capture"
    transport = FakeTransport({"one": _observation("one")})

    with pytest.raises(ValueError, match="runtime_implementation_root_mismatch"):
        run_free_provider_backfill(
            _contract(output, signer),
            [_request("one")],
            transport=transport,
            signer=signer,
            runtime_implementation_root="e" * 64,
        )

    assert transport.calls == []
    assert not output.exists()
    assert not (tmp_path / ".capture.activities").exists()


def test_plan_freezes_only_lifecycle_intersection(tmp_path: Path) -> None:
    securities = tmp_path / "securities.jsonl"
    securities.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "ts_code": "600000.SH",
                    "exchange": "SSE",
                    "list_date": "19991110",
                    "delist_date": None,
                },
                {
                    "ts_code": "000001.SZ",
                    "exchange": "SZSE",
                    "list_date": "19910403",
                    "delist_date": "20121231",
                },
                {
                    "ts_code": "688999.SH",
                    "exchange": "SSE",
                    "list_date": "20200101",
                    "delist_date": None,
                },
                {
                    "ts_code": "920001.BJ",
                    "exchange": "BSE",
                    "list_date": "20100101",
                    "delist_date": None,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    population, requests = build_baostock_state_plan(securities)

    assert [row["ts_code"] for row in population] == ["000001.SZ", "600000.SH"]
    assert len(requests) == 3
    assert requests[0].metadata["case"] == "trade_calendar"
    assert {row.metadata.get("ts_code") for row in requests[1:]} == {
        "000001.SZ",
        "600000.SH",
    }


def test_normalizer_replays_full_archived_items_without_using_live_transport(
    tmp_path: Path,
) -> None:
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "tradestatus",
        "isST",
    ]
    rows = [
        ["2012-01-04", "sh.600000", "1", "1", "1", "1", "1", "1", "1", "1", "0"],
        ["2012-01-05", "sh.600000", "", "", "", "", "1", "0", "0", "0", "1"],
    ]
    request = ProviderProbeRequest(
        request_id="state",
        provider="baostock",
        endpoint="history_state_daily",
        method="BAOSTOCK",
        url=(
            "baostock://public-api.baostock.com/history?code=sh.600000"
            "&start=2012-01-01&end=2012-01-05&fields="
            + ",".join(fields)
        ),
        disposition="bounded_backfill",
        evidence_semantics="raw_custom_socket_response_plus_locked_parser",
        expected_terminal_states=("positive",),
        required_checks=("provider_success",),
        metadata={"case": "history", "ts_code": "600000.SH"},
    )
    observation = _baostock_wire_observation(
        request, session_expired=False, rows_override=rows
    )
    payload = observation.raw_payload
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "raw_payload_base64": base64.b64encode(payload).decode(),
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    wrapper_path = tmp_path / "raw_envelopes/state.json"
    wrapper_path.parent.mkdir()
    wrapper_path.write_text(json.dumps(wrapper), encoding="utf-8")
    terminal = {
        "state": {
            "raw_envelope_relative_path": "raw_envelopes/state.json",
            "terminal_state": "positive",
            "checks": {"provider_success": True},
        }
    }

    artifacts = normalize_baostock_state_capture(tmp_path, [request], terminal)
    st_rows = [json.loads(line) for line in (tmp_path / "normalized/st_status_daily.jsonl").read_text().splitlines()]
    suspension_rows = [json.loads(line) for line in (tmp_path / "normalized/suspensions.jsonl").read_text().splitlines()]

    assert len(st_rows) == 1 and st_rows[0]["trade_date"] == "20120105"
    assert len(suspension_rows) == 1 and suspension_rows[0]["suspend_type"] == "S"
    assert {row.role for row in artifacts} >= {"st_status_daily", "suspensions"}
