from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    ProviderBackfillPaused,
)
from auto_alpha.platform.artifacts.storage import canonical_hash
from auto_alpha.platform.governance.network.signing import EphemeralReceiptSigner


def _population(*, eligible: int, blocked: int) -> list[dict[str, Any]]:
    rows = [
        {
            "attachment_url": (
                f"https://oss-ch.csindex.com.cn/20120102/a{ordinal}.xls"
            ),
            "host": "oss-ch.csindex.com.cn",
            "extension": "xls",
            "path_dates": ["20120102"],
            "reference_disposition": "capture_eligible",
            "temporal_blocker": "current_retrieval_is_not_historical_proof",
            "source_announcements": [
                {
                    "announcement_id": str(ordinal),
                    "announcement_publish_date": "2012-01-02",
                    "historical_known_at_proven": False,
                }
            ],
        }
        for ordinal in range(eligible)
    ]
    rows.extend(
        {
            "attachment_url": None,
            "raw_reference": f"blocked-{ordinal}",
            "host": None,
            "extension": None,
            "path_dates": [],
            "reference_disposition": "blocked_rejected_reference",
            "rejection_reason": "fixture",
            "temporal_blocker": "current_retrieval_is_not_historical_proof",
            "source_announcements": [],
        }
        for ordinal in range(blocked)
    )
    return rows


def _prepared(
    range_capture: Any,
    population: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any], str]:
    ancestry = {
        "schema_version": "fixture_details_ancestry_v1",
        "source_stage": "details_capture",
        "ancestry_root": "c" * 64,
        "weak_source_ancestry": False,
    }
    details = {
        "schema_version": "free_provider_backfill_capture_v2",
        "generation_id": "free_provider_backfill_" + "a" * 24,
        "content_hash": "a" * 64,
        "contract_id": "b" * 64,
        "request_plan_hash": "d" * 64,
        "activity_id": "e" * 64,
        "csindex_downstream_eligible": True,
        "csindex_downstream_ancestry": ancestry,
    }
    binding = range_capture._range_source_binding(
        details,
        population=population,
        legacy_input_root="f" * 64,
        legacy_request_plan_root="1" * 64,
    )
    requests = range_capture._range_attachment_requests(population, binding)
    return details, population, requests, str(binding["content_hash"])


def _legacy_population(range_capture: Any) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for spec in range_capture.csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS:
        for announcement_id in spec["announcement_ids"]:
            details.append(
                {
                    "announcement_id": announcement_id,
                    "publish_date": spec["announcement_publish_date"],
                    "source_request_id": f"detail_{announcement_id}",
                    "source_payload_sha256": str(announcement_id).zfill(64),
                    "contains_csi300": (
                        announcement_id == spec["csi300_announcement_id"]
                    ),
                    "content_html": (
                        f'<a href="{spec["attachment_url"]}">xls</a>'
                    ),
                }
            )
    return range_capture.csindex_backfill._legacy_cons_repair_population(details)


def _prepared_legacy(
    range_capture: Any,
    population: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any], str]:
    details, _population_rows, _requests, _root = _prepared(
        range_capture, population
    )
    binding = range_capture._range_source_binding(
        details,
        population=population,
        legacy_input_root="f" * 64,
        legacy_request_plan_root="1" * 64,
        capture_profile=range_capture.LEGACY_CAPTURE_PROFILE,
    )
    requests = range_capture._range_attachment_requests(population, binding)
    return details, population, requests, str(binding["content_hash"])


class _ScriptedClient:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def exchange(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> Any:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
                "max_body_bytes": max_body_bytes,
            }
        )
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _configure_small_capture(
    monkeypatch: pytest.MonkeyPatch,
    range_capture: Any,
    tmp_path: Path,
    *,
    population: list[dict[str, Any]],
    client: _ScriptedClient,
) -> EphemeralReceiptSigner:
    signer = EphemeralReceiptSigner.generate()
    holder: dict[str, Any] = {}
    monkeypatch.setattr(range_capture, "EXPECTED_POPULATION_COUNT", len(population))
    monkeypatch.setattr(
        range_capture,
        "EXPECTED_REQUEST_COUNT",
        sum(row["reference_disposition"] == "capture_eligible" for row in population),
    )
    monkeypatch.setattr(
        range_capture,
        "_prepare_from_governed_details",
        lambda _capture: holder["prepared"],
    )
    monkeypatch.setattr(range_capture, "_load_capture_signer", lambda: signer)
    monkeypatch.setattr(range_capture, "_new_exchange_client", lambda: client)
    monkeypatch.setattr(range_capture, "SCOPE_ROOT", tmp_path / "scope")
    monkeypatch.setattr(
        range_capture,
        "APPROVED_CAPTURE_KEY_SHA256",
        canonical_hash(signer.public_key_pem.decode("ascii")),
    )
    monkeypatch.setattr(
        range_capture,
        "_validate_details_parent",
        lambda _manifest, _binding: holder["prepared"][0],
    )
    monkeypatch.setattr(
        range_capture,
        "_validate_rebuilt_parent_plan",
        lambda *args, **kwargs: None,
    )
    holder["prepared"] = _prepared(range_capture, population)
    return signer


def _configure_small_legacy_capture(
    monkeypatch: pytest.MonkeyPatch,
    range_capture: Any,
    tmp_path: Path,
    *,
    population: list[dict[str, Any]],
    client: _ScriptedClient,
) -> EphemeralReceiptSigner:
    signer = EphemeralReceiptSigner.generate()
    holder: dict[str, Any] = {}
    monkeypatch.setattr(range_capture, "EXPECTED_POPULATION_COUNT", len(population))
    monkeypatch.setattr(
        range_capture,
        "EXPECTED_LEGACY_REQUEST_COUNT",
        2,
    )
    monkeypatch.setattr(
        range_capture,
        "_prepare_legacy_from_governed_details",
        lambda _capture: holder["prepared"],
    )
    monkeypatch.setattr(range_capture, "_load_capture_signer", lambda: signer)
    monkeypatch.setattr(range_capture, "_new_exchange_client", lambda: client)
    monkeypatch.setattr(range_capture, "SCOPE_ROOT", tmp_path / "scope")
    monkeypatch.setattr(
        range_capture,
        "APPROVED_CAPTURE_KEY_SHA256",
        canonical_hash(signer.public_key_pem.decode("ascii")),
    )
    monkeypatch.setattr(
        range_capture,
        "_validate_details_parent",
        lambda _manifest, _binding: holder["prepared"][0],
    )
    monkeypatch.setattr(
        range_capture,
        "_validate_rebuilt_parent_plan",
        lambda *args, **kwargs: None,
    )
    holder["prepared"] = _prepared_legacy(range_capture, population)
    return signer


def test_plan_only_requires_the_locked_full_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    monkeypatch.setattr(
        range_capture,
        "_prepare_from_governed_details",
        lambda _capture: ({"content_hash": "a" * 64}, [], [], "b" * 64),
    )

    with pytest.raises(ValueError, match="csindex_range_attachment_population_geometry_invalid"):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json",
            allow_network=False,
            plan_only=True,
        )


def test_full_get_capture_is_signed_replayed_and_keeps_safety_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"workbook" * 16
    client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/vnd.ms-excel",
                    "ETag": '"full-v1"',
                },
                body=body,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            )
        ]
    )
    population = _population(eligible=1, blocked=1)
    _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )

    published = range_capture.run_csindex_range_attachment_capture(
        tmp_path / "details.json",
        allow_network=True,
    )
    validated = range_capture.validate_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    replayed, replay_root = range_capture.replay_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    bodies, body_replay_root = (
        range_capture.replay_csindex_range_attachment_bodies(
            published["manifest_path"]
        )
    )

    index = json.loads(replayed["csindex_range_attachment_index"].decode())
    assert validated["status"] == "succeeded"
    assert validated["strong_details_ancestry_verified"] is True
    assert validated["historical_known_at_proven"] is False
    assert validated["pit_membership_authorized"] is False
    assert all(value is False for value in validated["safety"].values())
    assert index["attachment_sha256"] == hashlib.sha256(body).hexdigest()
    assert index["retrieval_method"] == "full_get"
    assert len(replay_root) == 64
    assert len(bodies) == 1
    assert bodies[0].body == body
    assert bodies[0].attachment_sha256 == hashlib.sha256(body).hexdigest()
    assert len(body_replay_root) == 64
    assert client.calls[0]["headers"].get("Range") is None
    contract = json.loads(
        (
            Path(published["manifest_path"]).parent / "activity_contract.json"
        ).read_text()
    )
    assert contract["budget"]["max_wire_exchanges"] == 50_000
    assert contract["budget"]["max_total_response_bytes"] == 16 * 1024**3
    assert contract["adapter_identity"]["range_chunk_bytes"] == 65_536


def test_body_timeout_falls_back_to_exact_strong_etag_ranges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"r" * 70_000
    first = body[: range_capture.RANGE_CHUNK_BYTES]
    second = body[range_capture.RANGE_CHUNK_BYTES :]
    etag = '"immutable-v1"'
    client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/vnd.ms-excel",
                    "ETag": etag,
                },
                body=body[:1024],
                headers_received=True,
                completion_state="body_timeout",
                error_code="transport_exception:TimeoutError",
                elapsed_seconds=30.0,
            ),
            range_capture._HttpExchangeResult(
                status_code=206,
                response_headers={
                    "Content-Length": str(len(first)),
                    "Content-Type": "application/vnd.ms-excel",
                    "Content-Range": (
                        f"bytes 0-{len(first) - 1}/{len(body)}"
                    ),
                    "ETag": etag,
                },
                body=first,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.2,
            ),
            range_capture._HttpExchangeResult(
                status_code=206,
                response_headers={
                    "Content-Length": str(len(second)),
                    "Content-Type": "application/vnd.ms-excel",
                    "Content-Range": (
                        f"bytes {len(first)}-{len(body) - 1}/{len(body)}"
                    ),
                    "ETag": etag,
                },
                body=second,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.2,
            ),
        ]
    )
    population = _population(eligible=1, blocked=0)
    _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )

    published = range_capture.run_csindex_range_attachment_capture(
        tmp_path / "details.json",
        allow_network=True,
    )
    replayed, _root = range_capture.replay_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    index = json.loads(replayed["csindex_range_attachment_index"].decode())
    wires = [
        json.loads(line)
        for line in replayed["csindex_range_wire_exchange_index"]
        .decode()
        .splitlines()
    ]

    assert index["attachment_sha256"] == hashlib.sha256(body).hexdigest()
    assert index["retrieval_method"] == "range_if_range"
    assert index["strong_etag"] == etag
    assert len(wires) == 3
    assert client.calls[1]["headers"]["Range"] == "bytes=0-65535"
    assert "If-Range" not in client.calls[1]["headers"]
    assert client.calls[2]["headers"]["If-Range"] == etag


@pytest.mark.parametrize("drift", ("weak_etag", "changed_etag", "returned_200"))
def test_range_fallback_stops_on_missing_identity_or_object_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"x" * 32
    etag = 'W/"weak"' if drift == "weak_etag" else '"v1"'
    status = 200 if drift == "returned_200" else 206
    response_etag = '"v2"' if drift == "changed_etag" else etag
    first_etag = '"v1"' if drift == "changed_etag" else response_etag
    results = [
        range_capture._HttpExchangeResult(
            status_code=200,
            response_headers={"Content-Length": str(len(body))},
            body=b"partial",
            headers_received=True,
            completion_state="body_timeout",
            error_code="transport_exception:TimeoutError",
            elapsed_seconds=30.0,
        ),
        range_capture._HttpExchangeResult(
            status_code=status,
            response_headers={
                "Content-Length": str(len(body)),
                "Content-Type": "application/vnd.ms-excel",
                "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}",
                "ETag": first_etag,
            },
            body=body,
            headers_received=True,
            completion_state="complete",
            error_code=None,
            elapsed_seconds=0.1,
        ),
    ]
    if drift == "changed_etag":
        # Force two ranges so the second response can drift.
        large = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"y" * 70_000
        first = large[: range_capture.RANGE_CHUNK_BYTES]
        second = large[range_capture.RANGE_CHUNK_BYTES :]
        results[0] = range_capture._HttpExchangeResult(
            status_code=200,
            response_headers={"Content-Length": str(len(large))},
            body=b"partial",
            headers_received=True,
            completion_state="body_timeout",
            error_code="transport_exception:TimeoutError",
            elapsed_seconds=30.0,
        )
        results[1] = range_capture._HttpExchangeResult(
            status_code=206,
            response_headers={
                "Content-Length": str(len(first)),
                "Content-Type": "application/vnd.ms-excel",
                "Content-Range": f"bytes 0-{len(first) - 1}/{len(large)}",
                "ETag": '"v1"',
            },
            body=first,
            headers_received=True,
            completion_state="complete",
            error_code=None,
            elapsed_seconds=0.1,
        )
        results.append(
            range_capture._HttpExchangeResult(
                status_code=206,
                response_headers={
                    "Content-Length": str(len(second)),
                    "Content-Type": "application/vnd.ms-excel",
                    "Content-Range": (
                        f"bytes {len(first)}-{len(large) - 1}/{len(large)}"
                    ),
                    "ETag": response_etag,
                },
                body=second,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            )
        )
    client = _ScriptedClient(results)
    population = _population(eligible=1, blocked=0)
    _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )

    with pytest.raises(ProviderBackfillPaused):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json",
            allow_network=True,
        )


def test_legacy_cons_profile_captures_exactly_two_urls_under_separate_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    population = _legacy_population(range_capture)
    bodies = [
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + token * 16
        for token in (b"legacy-one", b"legacy-two")
    ]
    client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/vnd.ms-excel",
                    "ETag": f'"legacy-{ordinal}"',
                },
                body=body,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            )
            for ordinal, body in enumerate(bodies)
        ]
    )
    _configure_small_legacy_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )

    published = range_capture.run_csindex_range_legacy_cons_capture(
        tmp_path / "details.json",
        allow_network=True,
    )
    validated = range_capture.validate_csindex_range_legacy_cons_capture(
        published["manifest_path"]
    )
    replayed, _root = range_capture.replay_csindex_range_legacy_cons_capture(
        published["manifest_path"]
    )
    index_rows = [
        json.loads(line)
        for line in replayed["csindex_range_attachment_index"]
        .decode()
        .splitlines()
    ]
    expected_urls = {
        row["attachment_url"]
        for row in range_capture.csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS
    }

    assert validated["csindex_phase"] == range_capture.LEGACY_PHASE
    assert validated["capture_profile"] == range_capture.LEGACY_CAPTURE_PROFILE
    assert {row["attachment_url"] for row in index_rows} == expected_urls
    assert len(index_rows) == 2
    contract = json.loads(
        (
            Path(published["manifest_path"]).parent / "activity_contract.json"
        ).read_text()
    )
    assert contract["allowed_hosts"] == ["oss-ch.csindex.com.cn"]
    assert contract["adapter_identity"]["adapter"] == (
        range_capture.LEGACY_ADAPTER_IDENTITY
    )


def test_legacy_cons_profile_cannot_expand_beyond_the_reviewed_two_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    population = _legacy_population(range_capture)
    extra = dict(population[0])
    extra["attachment_url"] = (
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/"
        "sseportal/upload/files/upload/20170101cons.xls"
    )
    population.append(extra)
    details, _old_population, _requests, _root = _prepared(
        range_capture, population
    )
    binding = range_capture._range_source_binding(
        details,
        population=population,
        legacy_input_root="f" * 64,
        legacy_request_plan_root="1" * 64,
        capture_profile=range_capture.LEGACY_CAPTURE_PROFILE,
    )
    requests = range_capture._range_attachment_requests(population, binding)
    monkeypatch.setattr(range_capture, "EXPECTED_POPULATION_COUNT", len(population))
    monkeypatch.setattr(range_capture, "EXPECTED_LEGACY_REQUEST_COUNT", 3)
    monkeypatch.setattr(
        range_capture,
        "_prepare_legacy_from_governed_details",
        lambda _capture: (details, population, requests, binding["content_hash"]),
    )

    with pytest.raises(ValueError, match="exact_population_invalid|exact_scope_invalid"):
        range_capture.run_csindex_range_legacy_cons_capture(
            tmp_path / "details.json",
            allow_network=False,
            plan_only=True,
        )


def test_wire_budget_pause_retains_partial_exchange_without_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    partial = b"partial-provider-bytes"
    client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={"Content-Length": "999999"},
                body=partial,
                headers_received=True,
                completion_state="body_timeout",
                error_code="transport_exception:TimeoutError",
                elapsed_seconds=30.0,
            )
        ]
    )
    population = _population(eligible=1, blocked=0)
    monkeypatch.setattr(range_capture, "MAX_WIRE_EXCHANGES", 1)
    _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )

    with pytest.raises(ProviderBackfillPaused):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json",
            allow_network=True,
        )

    wrappers = list(
        (
            tmp_path
            / "scope/csindex/.range_attachments.activities"
        ).glob("*/raw_envelopes/*.json")
    )
    assert len(wrappers) == 1
    wrapper = json.loads(wrappers[0].read_text())
    logical = json.loads(
        __import__("base64").b64decode(wrapper["raw_payload_base64"])
    )
    retained = __import__("base64").b64decode(
        logical["exchanges"][0]["events"][2]["body_base64"]
    )
    assert logical["terminal_state"] == "error"
    assert logical["attachment_sha256"] is None
    assert logical["attachment_size_bytes"] is None
    assert logical["exchange_count"] == 1
    assert retained == partial


def test_interrupted_activity_resumes_same_signed_identity_and_skips_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"resume" * 16

    def success() -> Any:
        return range_capture._HttpExchangeResult(
            status_code=200,
            response_headers={
                "Content-Length": str(len(body)),
                "Content-Type": "application/vnd.ms-excel",
                "ETag": '"resume-v1"',
            },
            body=body,
            headers_received=True,
            completion_state="complete",
            error_code=None,
            elapsed_seconds=0.1,
        )

    first_client = _ScriptedClient([success(), KeyboardInterrupt()])
    population = _population(eligible=2, blocked=0)
    signer = _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=first_client,
    )

    with pytest.raises(KeyboardInterrupt):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json",
            allow_network=True,
        )

    second_client = _ScriptedClient([success()])
    monkeypatch.setattr(range_capture, "_new_exchange_client", lambda: second_client)
    monkeypatch.setattr(range_capture, "_load_capture_signer", lambda: signer)
    published = range_capture.run_csindex_range_attachment_capture(
        tmp_path / "details.json",
        allow_network=True,
    )
    validated = range_capture.validate_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    replayed, _root = range_capture.replay_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    rows = replayed["csindex_range_attachment_index"].decode().splitlines()

    assert validated["status"] == "succeeded"
    assert len(rows) == 2
    assert len(first_client.calls) == 2
    assert len(second_client.calls) == 1
    assert validated["resource_usage"]["wire_exchange_count"] == 3
    assert validated["terminal_attempt_count"] == 3


def test_exact_json_decoder_rejects_duplicate_keys_at_every_nesting_level() -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    with pytest.raises(ValueError, match="duplicate_key"):
        range_capture._exact_json_object(b'{"a":1,"a":2}')
    with pytest.raises(ValueError, match="duplicate_key"):
        range_capture._exact_json_object(b'{"outer":{"a":1,"a":2}}')


def test_logical_exchange_count_must_equal_wrapper_and_terminal_count(
    tmp_path: Path,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    population = _population(eligible=1, blocked=0)
    _details, _rows, requests, _root = _prepared(range_capture, population)
    request = requests[0]
    raw = json.dumps(
        {"schema_version": range_capture.LOGICAL_ENVELOPE_SCHEMA, "exchange_count": 2},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "terminal_state": "positive",
        "transport_exchange_count": 1,
        "raw_payload_base64": __import__("base64").b64encode(raw).decode(),
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_payload_size_bytes": len(raw),
    }
    terminal = {
        "terminal_state": "positive",
        "transport_exchange_count": 1,
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_payload_size_bytes": len(raw),
    }

    with pytest.raises(ValueError, match="raw_wrapper_invalid"):
        range_capture._raw_logical_payload(
            wrapper, request=request, terminal=terminal
        )


def test_wrong_capture_key_stops_before_first_client_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    client = _ScriptedClient([])
    population = _population(eligible=1, blocked=0)
    _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )
    monkeypatch.setattr(range_capture, "APPROVED_CAPTURE_KEY_SHA256", "0" * 64)

    with pytest.raises(ValueError, match="not_approved|authorized_contract"):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json",
            allow_network=True,
        )
    assert client.calls == []


def test_crash_inside_range_loop_is_counted_and_replayed_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"z" * 70_000
    first = body[: range_capture.RANGE_CHUNK_BYTES]
    etag = '"crash-range-v1"'
    first_client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={"Content-Length": str(len(body))},
                body=b"partial",
                headers_received=True,
                completion_state="body_timeout",
                error_code="transport_exception:TimeoutError",
                elapsed_seconds=30.0,
            ),
            range_capture._HttpExchangeResult(
                status_code=206,
                response_headers={
                    "Content-Length": str(len(first)),
                    "Content-Type": "application/vnd.ms-excel",
                    "Content-Range": f"bytes 0-{len(first)-1}/{len(body)}",
                    "ETag": etag,
                },
                body=first,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            ),
            KeyboardInterrupt(),
        ]
    )
    population = _population(eligible=1, blocked=0)
    signer = _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=first_client,
    )
    with pytest.raises(KeyboardInterrupt):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json", allow_network=True
        )

    final_body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"final" * 16
    second_client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={
                    "Content-Length": str(len(final_body)),
                    "Content-Type": "application/vnd.ms-excel",
                    "ETag": '"final-v1"',
                },
                body=final_body,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            )
        ]
    )
    monkeypatch.setattr(range_capture, "_new_exchange_client", lambda: second_client)
    monkeypatch.setattr(range_capture, "_load_capture_signer", lambda: signer)
    published = range_capture.run_csindex_range_attachment_capture(
        tmp_path / "details.json", allow_network=True
    )
    validated = range_capture.validate_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    replayed, _root = range_capture.replay_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    wire_rows = [
        json.loads(line)
        for line in replayed["csindex_range_wire_exchange_index"]
        .decode()
        .splitlines()
    ]

    assert validated["resource_usage"]["wire_exchange_count"] == 4
    assert len(wire_rows) == 4
    assert wire_rows[2]["completion_state"] == "ambiguous_after_interruption"
    assert len(first_client.calls) == 3
    assert len(second_client.calls) == 1


def test_torn_sidecar_tail_is_preserved_and_incomplete_exchange_stays_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    population = _population(eligible=1, blocked=0)
    first_client = _ScriptedClient([KeyboardInterrupt()])
    signer = _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=first_client,
    )
    with pytest.raises(KeyboardInterrupt):
        range_capture.run_csindex_range_attachment_capture(
            tmp_path / "details.json", allow_network=True
        )
    activity_root = next(
        path
        for path in (
            tmp_path / "scope/csindex/.range_attachments.activities"
        ).iterdir()
        if path.is_dir() and len(path.name) == 64
    )
    sidecar = activity_root / range_capture.DURABLE_EXCHANGE_JOURNAL_NAME
    complete_prefix = sidecar.read_bytes()
    torn = b'{"schema_version":"torn-event"'
    with sidecar.open("ab") as handle:
        handle.write(torn)

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"recovered" * 16
    second_client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/vnd.ms-excel",
                    "ETag": '"recovered-v1"',
                },
                body=body,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            )
        ]
    )
    monkeypatch.setattr(range_capture, "_new_exchange_client", lambda: second_client)
    monkeypatch.setattr(range_capture, "_load_capture_signer", lambda: signer)
    published = range_capture.run_csindex_range_attachment_capture(
        tmp_path / "details.json", allow_network=True
    )
    validated = range_capture.validate_csindex_range_attachment_capture(
        published["manifest_path"]
    )
    generation = Path(published["manifest_path"]).parent
    fragment_hash = hashlib.sha256(torn).hexdigest()
    fragment = (
        generation
        / "range_exchange_journal_torn_fragments"
        / f"fragment_{fragment_hash}.bin"
    )
    durable = generation / range_capture.DURABLE_EXCHANGE_JOURNAL_NAME

    assert fragment.read_bytes() == torn
    assert durable.read_bytes().startswith(complete_prefix)
    assert validated["resource_usage"]["wire_exchange_count"] == 2
    assert any(
        row["role"].startswith("csindex_range_torn_fragment_")
        for row in validated["normalized_artifacts"]
    )


def test_live_normalization_cannot_publish_without_the_durable_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    population = _population(eligible=1, blocked=0)
    _details, _rows, requests, _root = _prepared(range_capture, population)
    monkeypatch.setattr(range_capture, "EXPECTED_POPULATION_COUNT", 1)
    monkeypatch.setattr(range_capture, "EXPECTED_REQUEST_COUNT", 1)
    (tmp_path / "activity_contract.json").write_text("{}")

    with pytest.raises(ValueError, match="durable_journal_missing_live_capture"):
        range_capture._normalize_range_attachments(tmp_path, requests, {})


@pytest.mark.parametrize("mutation", ("eligible_url", "blocked_row"))
def test_validator_rebuilds_real_details_plan_instead_of_trusting_metadata_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    original = _population(eligible=1, blocked=1)
    parent, _rows, legacy_requests, _root = _prepared(
        range_capture, original
    )
    parent["manifest_path"] = str(tmp_path / "details-manifest.json")
    legacy_input_root = "f" * 64
    legacy_plan_root = canonical_hash(
        [request.semantic() for request in legacy_requests]
    )
    tampered = json.loads(json.dumps(original))
    if mutation == "eligible_url":
        tampered[0]["attachment_url"] = (
            "https://oss-ch.csindex.com.cn/20120102/substituted.xls"
        )
    else:
        tampered[1]["raw_reference"] = "forged-blocked-reference"
    binding = range_capture._range_source_binding(
        parent,
        population=tampered,
        legacy_input_root=legacy_input_root,
        legacy_request_plan_root=legacy_plan_root,
    )
    sealed_requests = range_capture._range_attachment_requests(
        tampered, binding
    )
    monkeypatch.setattr(
        range_capture.csindex_backfill,
        "build_csindex_attachment_plan",
        lambda _manifest: (original, legacy_requests, legacy_input_root),
    )

    with pytest.raises(ValueError, match="real_parent_plan_mismatch"):
        range_capture._validate_rebuilt_parent_plan(
            parent,
            population=tampered,
            requests=sealed_requests,
            binding=binding,
            capture_profile=range_capture.CAPTURE_PROFILE,
        )


@pytest.mark.parametrize(
    "mutation",
    ("output_namespace", "old_lake_mutated", "safety", "extra_key"),
)
def test_authorized_contract_requires_exact_closed_semantics_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(
        range_capture,
        "APPROVED_CAPTURE_KEY_SHA256",
        canonical_hash(signer.public_key_pem.decode("ascii")),
    )
    monkeypatch.setattr(range_capture, "SCOPE_ROOT", tmp_path / "scope")
    population = _population(eligible=1, blocked=0)
    details, _rows, _requests, input_root = _prepared(
        range_capture, population
    )
    output = tmp_path / "scope/csindex/range_attachments"
    contract = range_capture._contract(
        output_root=output,
        signer=signer,
        population_root=canonical_hash(
            {"population": population, "input_capture_content_hash": input_root}
        ),
        request_count=1,
        input_root=input_root,
        details=details,
    ).semantic()
    range_capture._validate_authorized_contract(
        contract,
        request_count=1,
        expected_profile=range_capture.CAPTURE_PROFILE,
    )
    changed = json.loads(json.dumps(contract))
    if mutation == "output_namespace":
        changed["output_namespace_id"] = "0" * 64
    elif mutation == "old_lake_mutated":
        changed["old_lake_mutated"] = True
    elif mutation == "safety":
        changed["safety"]["data_admission_eligible"] = True
    else:
        changed["unexpected"] = "forbidden"

    with pytest.raises(ValueError, match="authorized_contract_invalid"):
        range_capture._validate_authorized_contract(
            changed,
            request_count=1,
            expected_profile=range_capture.CAPTURE_PROFILE,
        )


def test_implementation_identity_binds_the_complete_range_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    baseline = range_capture._implementation_root()
    original = range_capture.sha256_file

    def changed(path: Path) -> str:
        if Path(path).resolve() == Path(range_capture.__file__).resolve():
            return "0" * 64
        return original(path)

    monkeypatch.setattr(range_capture, "sha256_file", changed)
    assert range_capture._implementation_root() != baseline


def test_public_replay_rejects_any_exchange_signature_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_range_attachment as range_capture,
    )

    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"tamper" * 16
    client = _ScriptedClient(
        [
            range_capture._HttpExchangeResult(
                status_code=200,
                response_headers={
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/vnd.ms-excel",
                    "ETag": '"tamper-v1"',
                },
                body=body,
                headers_received=True,
                completion_state="complete",
                error_code=None,
                elapsed_seconds=0.1,
            )
        ]
    )
    population = _population(eligible=1, blocked=0)
    _configure_small_capture(
        monkeypatch,
        range_capture,
        tmp_path,
        population=population,
        client=client,
    )
    published = range_capture.run_csindex_range_attachment_capture(
        tmp_path / "details.json",
        allow_network=True,
    )
    generation = Path(published["manifest_path"]).parent
    catalog = json.loads(
        (generation / "capture_catalog.jsonl").read_text().splitlines()[0]
    )
    wrapper_path = generation / catalog["relative_path"]
    wrapper = json.loads(wrapper_path.read_text())
    logical = json.loads(
        __import__("base64").b64decode(wrapper["raw_payload_base64"])
    )
    logical["exchanges"][0]["events"][0]["signature"] = "invalid"
    changed = json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()
    wrapper["raw_payload_base64"] = __import__("base64").b64encode(changed).decode()
    wrapper["raw_payload_sha256"] = hashlib.sha256(changed).hexdigest()
    wrapper["raw_payload_size_bytes"] = len(changed)
    wrapper_path.chmod(0o600)
    wrapper_path.write_text(json.dumps(wrapper, sort_keys=True))

    with pytest.raises(ValueError):
        range_capture.replay_csindex_range_attachment_capture(
            published["manifest_path"]
        )
