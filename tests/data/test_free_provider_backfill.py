from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    BackfillResourceBudget,
    FreeProviderBackfillContract,
    PauseResumeAuthorization,
    ProviderBackfillPaused,
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
    output: Path, signer: EphemeralReceiptSigner, *, max_requests: int = 4
) -> FreeProviderBackfillContract:
    import base64

    return FreeProviderBackfillContract(
        activity_name="fixture_backfill_v1",
        provider="cninfo",
        output_root=output,
        permission_context_id="human-approved-fixture",
        population_root="a" * 64,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(signer.public_key_pem).decode(),
        scope_start="20120101",
        scope_end="20120102",
        request_start="20120101",
        request_end="20120102",
        allowed_hosts=("www.cninfo.com.cn",),
        budget=BackfillResourceBudget(
            max_requests=max_requests,
            max_wire_exchanges=max_requests,
            max_response_bytes=1024 * 1024,
            max_total_response_bytes=4 * 1024 * 1024,
            timeout_seconds=3.0,
            minimum_delay_seconds=0,
            max_retries=1,
        ),
        adapter_identity={
            "adapter": "fixture_backfill_v1",
            "implementation_root": FIXTURE_IMPLEMENTATION_ROOT,
        },
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
    request = _request("state")
    request = ProviderProbeRequest(
        **{
            **request.__dict__,
            "metadata": {"case": "history", "ts_code": "600000.SH"},
        }
    )
    raw = {
        "parsed": {
            "fields": [
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
            ],
            "items": [
                ["2012-01-04", "sh.600000", "1", "1", "1", "1", "1", "1", "1", "1", "0"],
                ["2012-01-05", "sh.600000", "", "", "", "", "1", "0", "0", "0", "1"],
            ],
        },
        "wire_exchanges": [],
    }
    raw["parsed"]["canonical_logical_payload_sha256"] = canonical_hash(
        {"fields": raw["parsed"]["fields"], "rows": raw["parsed"]["items"]}
    )
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "raw_payload_base64": __import__("base64").b64encode(
            json.dumps(raw).encode()
        ).decode(),
        "raw_payload_sha256": __import__("hashlib").sha256(
            json.dumps(raw).encode()
        ).hexdigest(),
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
