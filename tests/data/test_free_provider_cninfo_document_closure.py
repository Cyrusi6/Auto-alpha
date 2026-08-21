from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
from typing import Callable, Sequence
import urllib.parse
from unittest.mock import patch

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import (
    free_provider_backfill as capture_module,
    free_provider_cninfo_document_closure as document_closure_module,
    free_provider_cninfo_security_lifecycle as lifecycle_module,
    free_provider_http_backfill as cninfo_module,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    NormalizedArtifact,
    PauseResumeAuthorization,
    ProviderBackfillPaused,
    run_free_provider_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_cninfo_document_closure import (
    capture_missing_documents,
    finalize_document_closure,
    prepare_document_closure,
)
from auto_alpha.data.ingestion.pipeline.ashare.provider_probe import (
    ProviderProbeObservation,
    ProviderProbeRequest,
)
from auto_alpha.platform.artifacts.storage import canonical_hash, read_json
from auto_alpha.platform.governance.network.signing import EphemeralReceiptSigner


_ANNOUNCEMENT = {
    "announcementId": "58854747",
    "secCode": "600000",
    "secName": "浦发银行",
    "orgId": "gssh0600000",
    "announcementTitle": "更正公告",
    "announcementTime": 1294093800000,
    "adjunctUrl": "finalpage/2011-01-04/58854747.PDF",
    "adjunctSize": 1,
    "announcementType": "x",
    "columnId": "y",
}
_LIFECYCLE_ANNOUNCEMENTS = (
    {
        **_ANNOUNCEMENT,
        "announcementId": "1205690369",
        "announcementTime": 1545782400000,
        "adjunctUrl": "finalpage/2018-12-26/1205690369.PDF",
    },
    {
        **_ANNOUNCEMENT,
        "announcementId": "1207164397",
        "announcementTime": 1576454400000,
        "adjunctUrl": "finalpage/2019-12-16/1207164397.PDF",
    },
    {
        **_ANNOUNCEMENT,
        "announcementId": "1204831387",
        "announcementTime": 1524873600000,
        "adjunctUrl": "finalpage/2018-04-28/1204831387.PDF",
    },
    {
        **_ANNOUNCEMENT,
        "announcementId": "1204983113",
        "announcementTime": 1527033600000,
        "adjunctUrl": "finalpage/2018-05-23/1204983113.PDF",
    },
    {
        **_ANNOUNCEMENT,
        "announcementId": "1206282885",
        "announcementTime": 1558137600000,
        "adjunctUrl": "finalpage/2019-05-18/1206282885.PDF",
    },
)
_HTML_ANNOUNCEMENT = {
    **_ANNOUNCEMENT,
    "announcementId": "70000001",
    "announcementTime": 1325635200000,
    "adjunctUrl": "finalpage/2012-01-04/70000001.html",
}


def _full_aggregate_plan(plan):
    return prepare_document_closure(
        tuple(row.manifest_path for row in plan.inventory_parents),
        (),
        range(2011, 2020),
    )


def _official_observation(
    request: ProviderProbeRequest,
    body: dict[str, object] | bytes,
    *,
    exact_lifecycle_envelope: bool = False,
    response_content_type: str | None = None,
) -> ProviderProbeObservation:
    encoded_body = (
        body
        if isinstance(body, bytes)
        else json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    response_headers = (
        {
            "content-length": str(len(encoded_body)),
            "content-type": response_content_type or "application/pdf",
        }
        if isinstance(body, bytes)
        else {}
    )
    envelope_semantic: dict[str, object] = {
            "schema_version": "official_http_probe_envelope_v1",
            "url": request.url,
            "method": request.method.upper(),
            "status_code": 200,
            "response_headers": response_headers,
            "body_base64": base64.b64encode(encoded_body).decode("ascii"),
            "body_sha256": hashlib.sha256(encoded_body).hexdigest(),
            "redirect_followed": False,
    }
    if exact_lifecycle_envelope:
        envelope_semantic["elapsed_seconds"] = 0.0
    envelope = json.dumps(
        envelope_semantic,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    total = body.get("totalAnnouncement") if isinstance(body, dict) else None
    terminal_state = "empty" if total == 0 else "positive"
    return ProviderProbeObservation(
        terminal_state=terminal_state,
        raw_payload=envelope,
        row_count=0 if terminal_state == "empty" else 1,
        status_code=200,
        checks={name: True for name in request.required_checks},
        transport_exchange_count=1,
    )


def _inventory_transport(
    rows_by_leaf: dict[str, list[dict[str, object]]],
) -> Callable[[ProviderProbeRequest, float], ProviderProbeObservation]:
    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        if request.metadata.get("case") == "cninfo_org_map":
            return _official_observation(
                request,
                {"stockList": [{"code": "600000"}]},
            )
        rows = rows_by_leaf.get(str(request.metadata.get("leaf_id") or ""), [])
        return _official_observation(
            request,
            {
                "totalAnnouncement": len(rows),
                "announcements": rows or None,
                "hasMore": False,
            },
        )

    return transport


def _capture_cninfo_phase(
    output_root: Path,
    *,
    phase: str,
    leaf_profile: str,
    population: Sequence[object],
    requests: Sequence[ProviderProbeRequest],
    normalizer: object,
    signer: EphemeralReceiptSigner,
    transport: Callable[[ProviderProbeRequest, float], ProviderProbeObservation],
    input_capture_hash: str | None = None,
    max_retries: int = 0,
) -> str:
    source_binding = (
        requests[0].metadata.get("source_binding")
        if phase in {"cninfo-inventory", "cninfo-documents"}
        else None
    )
    contract = cninfo_module._contract(
        phase=phase,
        output_root=output_root,
        signer=signer,
        population_root=canonical_hash(
            {
                "population": list(population),
                "input_capture_content_hash": input_capture_hash,
            }
        ),
        request_count=len(requests),
        input_capture_hash=input_capture_hash,
        delay=0,
        timeout=3,
        max_retries=max_retries,
        max_total_bytes=256 * 1024 * 1024,
        permission_context_id="human-approved-closure-fixture",
        leaf_profile=leaf_profile,
        source_binding=source_binding,
    )
    result = run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=normalizer,  # type: ignore[arg-type]
        runtime_implementation_root=cninfo_module._implementation_root(),
    )
    assert result["status"] == "succeeded"
    return str(result["manifest_path"])


def _discovery_manifest_for_profile(
    provider_root: Path,
    leaf_profile: str,
) -> Path:
    matches = []
    for manifest in sorted(
        (provider_root / "discovery" / "generations").glob(
            "*/free_provider_backfill_manifest.json"
        )
    ):
        contract = read_json(manifest.parent / "activity_contract.json")
        if (contract.get("adapter_identity") or {}).get(
            "leaf_profile"
        ) == leaf_profile:
            matches.append(manifest)
    assert len(matches) == 1
    return matches[0]


def _copy_discovery_generation(
    source_manifest: Path,
    provider_root: Path,
) -> Path:
    target = (
        provider_root
        / "discovery"
        / "generations"
        / source_manifest.parent.name
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_manifest.parent, target)
    return target / source_manifest.name


def _valid_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"padding-padding-padding-padding\n"
        b"startxref\n9\n%%EOF\n"
    )


@pytest.fixture(scope="module")
def strong_inventory_manifests(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[str, str]:
    root = tmp_path_factory.mktemp("cninfo-document-closure")
    signer = EphemeralReceiptSigner.generate()
    manifests: list[str] = []
    profiles = (
        ("base", "st_delist_201101"),
        ("supplemental", "corrections_201101"),
    )
    with patch.object(capture_module.os, "fsync", return_value=None):
        for leaf_profile, announcement_leaf_id in profiles:
            leaves, discovery_requests = (
                cninfo_module.build_cninfo_discovery_plan(
                    leaf_profile=leaf_profile
                )
            )
            prefix = (
                "st_delist" if leaf_profile == "base" else "corrections"
            )
            rows_by_leaf: dict[str, list[dict[str, object]]] = {
                announcement_leaf_id: [_ANNOUNCEMENT]
            }
            for row in _LIFECYCLE_ANNOUNCEMENTS:
                milliseconds = int(row["announcementTime"])
                observed = __import__("datetime").datetime.fromtimestamp(
                    milliseconds / 1000,
                    tz=__import__("datetime").UTC,
                )
                leaf_id = f"{prefix}_{observed:%Y%m}"
                rows_by_leaf.setdefault(leaf_id, []).append(row)
            rows_by_leaf.setdefault(f"{prefix}_201201", []).append(
                _HTML_ANNOUNCEMENT
            )
            transport = _inventory_transport(rows_by_leaf)
            discovery_manifest = _capture_cninfo_phase(
                root / "discovery",
                phase="cninfo-discovery",
                leaf_profile=leaf_profile,
                population=leaves,
                requests=discovery_requests,
                normalizer=cninfo_module.normalize_cninfo_discovery,
                signer=signer,
                transport=transport,
            )
            population, inventory_requests, input_root = (
                cninfo_module.build_cninfo_inventory_plan(
                    [discovery_manifest],
                    leaf_profile=leaf_profile,
                )
            )
            manifests.append(
                _capture_cninfo_phase(
                    root / "inventory",
                    phase="cninfo-inventory",
                    leaf_profile=leaf_profile,
                    population=population,
                    requests=inventory_requests,
                    normalizer=cninfo_module.normalize_cninfo_inventory,
                    signer=signer,
                    transport=transport,
                    input_capture_hash=input_root,
                )
            )
    return manifests[0], manifests[1]


@pytest.fixture
def retried_base_inventory_manifest(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> str:
    fixture_root = Path(strong_inventory_manifests[0]).parents[3]
    discovery_manifest = _discovery_manifest_for_profile(
        fixture_root,
        "base",
    )
    population, requests, input_root = (
        cninfo_module.build_cninfo_inventory_plan(
            [discovery_manifest],
            leaf_profile="base",
        )
    )
    rows_by_leaf: dict[str, list[dict[str, object]]] = {
        "st_delist_201101": [_ANNOUNCEMENT]
    }
    for row in _LIFECYCLE_ANNOUNCEMENTS:
        milliseconds = int(row["announcementTime"])
        observed = __import__("datetime").datetime.fromtimestamp(
            milliseconds / 1000,
            tz=__import__("datetime").UTC,
        )
        rows_by_leaf.setdefault(f"st_delist_{observed:%Y%m}", []).append(row)
    rows_by_leaf.setdefault("st_delist_201201", []).append(
        _HTML_ANNOUNCEMENT
    )
    success_transport = _inventory_transport(rows_by_leaf)
    failed = False

    def transport(
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        nonlocal failed
        if not failed and request.metadata.get("case") == "cninfo_org_map":
            failed = True
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b'{"temporary":"server-error"}',
                row_count=None,
                status_code=500,
                error_code="http_status:500",
                checks={"http_status_success": False},
                transport_exchange_count=1,
            )
        return success_transport(request, timeout_seconds)

    signer = EphemeralReceiptSigner.generate()
    provider_root = tmp_path / "retried-base-provider"
    _copy_discovery_generation(discovery_manifest, provider_root)
    with patch.object(capture_module.os, "fsync", return_value=None):
        return _capture_cninfo_phase(
            provider_root / "inventory",
            phase="cninfo-inventory",
            leaf_profile="base",
            population=population,
            requests=requests,
            normalizer=cninfo_module.normalize_cninfo_inventory,
            signer=signer,
            transport=transport,
            input_capture_hash=input_root,
            max_retries=1,
        )


@pytest.fixture(scope="module")
def strong_document_manifest(
    tmp_path_factory: pytest.TempPathFactory,
    strong_inventory_manifests: tuple[str, str],
) -> str:
    root = tmp_path_factory.mktemp("cninfo-document-reuse")
    signer = EphemeralReceiptSigner.generate()
    rows, requests, input_root = cninfo_module.build_cninfo_document_plan(
        strong_inventory_manifests[0],
        include_years=(2011,),
    )

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(request, _valid_pdf())

    with patch.object(capture_module.os, "fsync", return_value=None):
        manifest = _capture_cninfo_phase(
            root / "documents",
            phase="cninfo-documents",
            leaf_profile="base",
            population=rows,
            requests=requests,
            normalizer=cninfo_module.normalize_cninfo_documents,
            signer=signer,
            transport=transport,
            input_capture_hash=input_root,
        )
    return manifest


@pytest.fixture
def retried_document_manifest(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> str:
    signer = EphemeralReceiptSigner.generate()
    rows, requests, input_root = cninfo_module.build_cninfo_document_plan(
        strong_inventory_manifests[0],
        include_years=(2011,),
    )
    attempts: dict[str, int] = {}

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        ordinal = attempts.get(request.request_id, 0)
        attempts[request.request_id] = ordinal + 1
        if ordinal == 0:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b'{"temporary":"server-error"}',
                row_count=None,
                status_code=500,
                error_code="http_status:500",
                checks={"http_status_success": False},
                transport_exchange_count=1,
            )
        return _official_observation(request, _valid_pdf())

    with patch.object(capture_module.os, "fsync", return_value=None):
        return _capture_cninfo_phase(
            tmp_path / "retried-documents",
            phase="cninfo-documents",
            leaf_profile="base",
            population=rows,
            requests=requests,
            normalizer=cninfo_module.normalize_cninfo_documents,
            signer=signer,
            transport=transport,
            input_capture_hash=input_root,
            max_retries=1,
        )


@pytest.fixture
def metadata_mismatched_document_manifest(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> str:
    signer = EphemeralReceiptSigner.generate()
    rows, requests, input_root = cninfo_module.build_cninfo_document_plan(
        strong_inventory_manifests[0],
        include_years=(2011,),
    )
    requests = [
        replace(
            request,
            metadata=dict(request.metadata)
            | {
                "announcement_time": int(
                    request.metadata["announcement_time"]
                )
                + 86_400_000,
                "adjunct_size_kb": 2,
            },
        )
        for request in requests
    ]

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(
            request,
            _valid_pdf().replace(
                b"startxref",
                b"padding" * 20 + b"\nstartxref",
            ),
        )

    with patch.object(capture_module.os, "fsync", return_value=None):
        return _capture_cninfo_phase(
            tmp_path / "metadata-mismatched-documents",
            phase="cninfo-documents",
            leaf_profile="base",
            population=rows,
            requests=requests,
            normalizer=cninfo_module.normalize_cninfo_documents,
            signer=signer,
            transport=transport,
            input_capture_hash=input_root,
        )


@pytest.fixture
def malformed_page_inventory_manifest(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> str:
    fixture_root = Path(strong_inventory_manifests[0]).parents[3]
    discovery_manifest = _discovery_manifest_for_profile(
        fixture_root,
        "base",
    )
    population, inventory_requests, input_root = (
        cninfo_module.build_cninfo_inventory_plan(
            [discovery_manifest],
            leaf_profile="base",
        )
    )
    selected = next(
        request
        for request in inventory_requests
        if request.metadata.get("leaf_id") == "st_delist_201101"
    )
    inventory_requests.append(
        replace(
            selected,
            request_id=f"{selected.request_id}_duplicate_page_1",
        )
    )
    signer = EphemeralReceiptSigner.generate()

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        if request.metadata.get("case") == "cninfo_org_map":
            return _official_observation(
                request,
                {"stockList": [{"code": "600000"}]},
            )
        if request.metadata.get("leaf_id") == "st_delist_201101":
            rows = [
                {
                    **_ANNOUNCEMENT,
                    "announcementId": str(8_000_000 + ordinal),
                }
                for ordinal in range(30)
            ]
            return _official_observation(
                request,
                {
                    "totalAnnouncement": 31,
                    "announcements": rows,
                    "hasMore": True,
                },
            )
        return _official_observation(
            request,
            {
                "totalAnnouncement": 0,
                "announcements": None,
                "hasMore": False,
            },
        )

    def permissive_normalizer(
        run_root: Path,
        requests: Sequence[ProviderProbeRequest],
        _terminal: dict[str, dict[str, object]],
    ) -> tuple[NormalizedArtifact, ...]:
        output = run_root / "normalized"
        output.mkdir(exist_ok=True)
        inventory_path = output / "announcement_inventory.jsonl"
        inventory_path.write_bytes(b"")
        source_ancestry = requests[0].metadata["source_ancestry"]
        source_binding = requests[0].metadata["source_binding"]
        manifest = {
            "schema_version": "cninfo_announcement_inventory_normalization_v2",
            "source_ancestry": source_ancestry,
            "source_binding": source_binding,
            "announcement_count": 0,
        }
        manifest["content_hash"] = canonical_hash(manifest)
        (output / "normalized_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True)
        )
        return (
            NormalizedArtifact(
                "cninfo_announcement_inventory",
                "normalized/announcement_inventory.jsonl",
                0,
            ),
            NormalizedArtifact(
                "normalized_manifest",
                "normalized/normalized_manifest.json",
                1,
            ),
        )

    with patch.object(capture_module.os, "fsync", return_value=None):
        provider_root = tmp_path / "malformed-page-provider"
        _copy_discovery_generation(discovery_manifest, provider_root)
        return _capture_cninfo_phase(
            provider_root / "inventory",
            phase="cninfo-inventory",
            leaf_profile="base",
            population=population,
            requests=inventory_requests,
            normalizer=permissive_normalizer,
            signer=signer,
            transport=transport,
            input_capture_hash=input_root,
        )


@pytest.fixture
def adversarial_inventory_manifest_factory(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> Callable[[str], str]:
    fixture_root = Path(strong_inventory_manifests[0]).parents[3]
    discovery_manifest = _discovery_manifest_for_profile(
        fixture_root,
        "base",
    )
    population, complete_requests, input_root = (
        cninfo_module.build_cninfo_inventory_plan(
            [discovery_manifest],
            leaf_profile="base",
        )
    )

    def permissive_normalizer(
        run_root: Path,
        requests: Sequence[ProviderProbeRequest],
        _terminal: dict[str, dict[str, object]],
    ) -> tuple[NormalizedArtifact, ...]:
        output = run_root / "normalized"
        output.mkdir(exist_ok=True)
        inventory_path = output / "announcement_inventory.jsonl"
        coverage_path = output / "page_coverage.jsonl"
        conflicts_path = output / "conflicts.jsonl"
        inventory_path.write_bytes(b"")
        coverage_rows = [
            {
                "leaf_id": leaf["leaf_id"],
                "reported_total": 0,
                "expected_page_count": 1,
                "captured_pages": [1],
                "unique_announcement_count": 0,
                "full_page_chain_valid": True,
            }
            for leaf in sorted(
                cninfo_module._cninfo_month_leaves("base"),
                key=lambda row: row["leaf_id"],
            )
        ]
        coverage_path.write_bytes(
            b"".join(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
                for row in coverage_rows
            )
        )
        conflicts_path.write_bytes(b"")
        source_ancestry = requests[0].metadata["source_ancestry"]
        source_binding = requests[0].metadata["source_binding"]
        manifest = {
            "schema_version": (
                "cninfo_announcement_inventory_normalization_v2"
            ),
            "require_full_page_chains": True,
            "leaf_count": len(coverage_rows),
            "org_map_count": 1,
            "org_map_request_count": 1,
            "announcement_count": 0,
            "conflict_count": 0,
            "all_page_chains_valid": True,
            "announcement_inventory_sha256": hashlib.sha256(
                inventory_path.read_bytes()
            ).hexdigest(),
            "page_coverage_sha256": hashlib.sha256(
                coverage_path.read_bytes()
            ).hexdigest(),
            "conflicts_sha256": hashlib.sha256(
                conflicts_path.read_bytes()
            ).hexdigest(),
            "pit_field_parsing_complete": False,
            "source_ancestry": source_ancestry,
            "source_binding": source_binding,
        }
        manifest["content_hash"] = canonical_hash(manifest)
        manifest_path = output / "normalized_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        return (
            NormalizedArtifact(
                "cninfo_announcement_inventory",
                "normalized/announcement_inventory.jsonl",
                0,
            ),
            NormalizedArtifact(
                "cninfo_page_coverage",
                "normalized/page_coverage.jsonl",
                len(coverage_rows),
            ),
            NormalizedArtifact(
                "conflicts",
                "normalized/conflicts.jsonl",
                0,
            ),
            NormalizedArtifact(
                "normalized_manifest",
                "normalized/normalized_manifest.json",
                1,
            ),
        )

    def capture(mode: str) -> str:
        requests = list(complete_requests)
        leaf_index = next(
            index
            for index, request in enumerate(requests)
            if request.metadata.get("leaf_id") == "st_delist_201101"
        )
        if mode == "signed_subset":
            requests = [requests[0], requests[leaf_index]]
        elif mode == "wrong_category_body":
            target = requests[leaf_index]
            parameters = urllib.parse.parse_qsl(
                target.body.decode(),
                keep_blank_values=True,
            )
            requests[leaf_index] = replace(
                target,
                body=urllib.parse.urlencode(
                    [
                        (key, "category_test_wrong")
                        if key == "category"
                        else (key, value)
                        for key, value in parameters
                    ]
                ).encode(),
            )
        elif mode in {
            "duplicate_official_key",
            "extra_official_key",
            "duplicate_provider_body_key",
        }:
            pass
        else:
            raise AssertionError(f"unknown adversarial mode: {mode}")

        def adversarial_transport(
            request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            if request.metadata.get("case") == "cninfo_org_map":
                return _official_observation(
                    request,
                    {"stockList": [{"code": "600000"}]},
                )
            body = (
                b'{"announcements":null,"hasMore":false,'
                b'"totalAnnouncement":0}'
            )
            leaf_id = request.metadata.get("leaf_id")
            if (
                mode == "duplicate_provider_body_key"
                and leaf_id == "st_delist_201103"
            ):
                body = (
                    b'{"announcements":null,"hasMore":false,'
                    b'"totalAnnouncement":0,"totalAnnouncement":0}'
                )
            envelope = json.dumps(
                {
                    "schema_version": "official_http_probe_envelope_v1",
                    "url": request.url,
                    "method": request.method.upper(),
                    "status_code": 200,
                    "response_headers": {},
                    "body_base64": base64.b64encode(body).decode("ascii"),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "redirect_followed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            if (
                mode == "duplicate_official_key"
                and leaf_id == "st_delist_201101"
            ):
                encoded_url = json.dumps(
                    request.url,
                    separators=(",", ":"),
                ).encode()
                member = b'"url":' + encoded_url
                envelope = envelope.replace(
                    member,
                    member + b"," + member,
                    1,
                )
            if (
                mode == "extra_official_key"
                and leaf_id == "st_delist_201102"
            ):
                envelope = envelope[:-1] + b',"unexpected":true}'
            return ProviderProbeObservation(
                terminal_state="empty",
                raw_payload=envelope,
                row_count=0,
                status_code=200,
                checks={name: True for name in request.required_checks},
                transport_exchange_count=1,
            )

        transport = (
            adversarial_transport
            if mode
            in {
                "duplicate_official_key",
                "extra_official_key",
                "duplicate_provider_body_key",
            }
            else _inventory_transport({})
        )
        signer = EphemeralReceiptSigner.generate()
        provider_root = tmp_path / f"{mode}-provider"
        _copy_discovery_generation(discovery_manifest, provider_root)
        with patch.object(capture_module.os, "fsync", return_value=None):
            return _capture_cninfo_phase(
                provider_root / "inventory",
                phase="cninfo-inventory",
                leaf_profile="base",
                population=population,
                requests=requests,
                normalizer=permissive_normalizer,
                signer=signer,
                transport=transport,
                input_capture_hash=input_root,
            )

    return capture


@pytest.fixture
def isolated_inventory_with_discovery_parent(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> tuple[Path, Path, Path]:
    source_inventory = Path(strong_inventory_manifests[0])
    source_provider_root = source_inventory.parents[3]
    provider_root = tmp_path / "isolated-cninfo-provider"
    shutil.copytree(source_provider_root, provider_root)
    for path in (provider_root, *provider_root.rglob("*")):
        path.chmod(0o700 if path.is_dir() else 0o600)
    inventory_manifest = provider_root / source_inventory.relative_to(
        source_provider_root
    )
    normalized = read_json(
        inventory_manifest.parent / "normalized/normalized_manifest.json"
    )
    direct_sources = (normalized.get("source_ancestry") or {}).get(
        "direct_sources"
    )
    assert isinstance(direct_sources, list) and len(direct_sources) == 1
    generation_id = str(direct_sources[0]["source_generation_id"])
    discovery_generation = (
        provider_root / "discovery" / "generations" / generation_id
    )
    replacements = [
        candidate
        for candidate in sorted(
            (provider_root / "discovery" / "generations").iterdir()
        )
        if candidate != discovery_generation
    ]
    assert discovery_generation.is_dir() and len(replacements) == 1
    return inventory_manifest, discovery_generation, replacements[0]


@pytest.fixture
def forged_inventory_with_weak_discovery_parent(
    tmp_path: Path,
) -> str:
    provider_root = tmp_path / "weak-parent-provider"
    signer = EphemeralReceiptSigner.generate()
    leaves, discovery_requests = cninfo_module.build_cninfo_discovery_plan(
        leaf_profile="base"
    )
    transport = _inventory_transport({})
    with (
        patch.object(capture_module.os, "fsync", return_value=None),
        patch.object(
            capture_module,
            "SCHEMA_VERSION",
            capture_module.LEGACY_SCHEMA_VERSION,
        ),
    ):
        weak_discovery_manifest = _capture_cninfo_phase(
            provider_root / "discovery",
            phase="cninfo-discovery",
            leaf_profile="base",
            population=leaves,
            requests=discovery_requests,
            normalizer=cninfo_module.normalize_cninfo_discovery,
            signer=signer,
            transport=transport,
        )
    population, inventory_requests, _weak_input_root = (
        cninfo_module.build_cninfo_inventory_plan(
            [weak_discovery_manifest],
            leaf_profile="base",
        )
    )
    forged_ancestry = json.loads(
        json.dumps(inventory_requests[0].metadata["source_ancestry"])
    )
    direct = forged_ancestry["direct_sources"][0]
    assert direct["source_capture_schema"] == (
        capture_module.LEGACY_SCHEMA_VERSION
    )
    assert direct["source_publication_signature_verified"] is False
    direct["source_publication_signature_verified"] = True
    direct["source_normalized_artifacts_trusted"] = True
    direct["weak_source_ancestry"] = False
    forged_ancestry["weak_source_ancestry"] = False
    forged_ancestry["ancestry_root"] = canonical_hash(
        {
            key: value
            for key, value in forged_ancestry.items()
            if key != "ancestry_root"
        }
    )
    derivation = {
        "discovery_capture_content_hashes": [
            direct["source_content_hash"]
        ]
    }
    forged_input_root = canonical_hash(
        {
            "leaf_profile": "base",
            **derivation,
            "source_ancestry": forged_ancestry,
        }
    )
    forged_binding = cninfo_module._cninfo_source_binding(
        phase="cninfo-inventory",
        input_capture_content_hash=forged_input_root,
        source_ancestry=forged_ancestry,
        derivation=derivation,
    )
    forged_requests = [
        replace(
            request,
            metadata=dict(request.metadata)
            | {
                "source_ancestry": forged_ancestry,
                "source_binding": forged_binding,
            },
        )
        for request in inventory_requests
    ]
    with patch.object(capture_module.os, "fsync", return_value=None):
        return _capture_cninfo_phase(
            provider_root / "inventory",
            phase="cninfo-inventory",
            leaf_profile="base",
            population=population,
            requests=forged_requests,
            normalizer=cninfo_module.normalize_cninfo_inventory,
            signer=signer,
            transport=transport,
            input_capture_hash=forged_input_root,
        )


@pytest.fixture(scope="module")
def lifecycle_document_capture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[str, str]:
    root = tmp_path_factory.mktemp("cninfo-lifecycle-document-reuse")
    signer = EphemeralReceiptSigner.generate()
    approved_hash = capture_module._public_key_hash(signer.public_key_pem)

    def official_call(
        _self: object,
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(
            request,
            _valid_pdf(),
            exact_lifecycle_envelope=True,
        )

    transport = (
        lifecycle_module.CNINFOSecurityIdentityLifecycleDocumentTransport(
            minimum_delay_seconds=2.0
        )
    )
    with (
        patch.object(
            lifecycle_module,
            "APPROVED_CAPTURE_KEY_SHA256",
            approved_hash,
        ),
        patch.object(
            lifecycle_module.OfficialHttpProbeTransport,
            "__call__",
            new=official_call,
        ),
        patch.object(capture_module.time, "sleep", return_value=None),
        patch.object(capture_module.os, "fsync", return_value=None),
    ):
        result = (
            lifecycle_module.capture_cninfo_security_identity_lifecycle_documents(
                output_root=root / "documents",
                signer=signer,
                transport=transport,
            )
        )
    assert result["status"] == "succeeded"
    return str(result["manifest_path"]), approved_hash


@pytest.fixture
def synthetic_legacy_2011_document(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> tuple[str, dict[str, str]]:
    signer = EphemeralReceiptSigner.generate()
    _rows, strong_requests, input_root = (
        cninfo_module.build_cninfo_document_plan(
            strong_inventory_manifests[0],
            include_years=(2011,),
        )
    )
    source_binding = strong_requests[0].metadata["source_binding"]
    requests = [
        replace(
            request,
            metadata={
                key: value
                for key, value in request.metadata.items()
                if key not in {"source_ancestry", "source_binding"}
            },
        )
        for request in strong_requests
    ]
    contract = cninfo_module._contract(
        phase="cninfo-documents",
        output_root=tmp_path / "legacy-documents",
        signer=signer,
        population_root=canonical_hash(
            [request.semantic() for request in requests]
        ),
        request_count=len(requests),
        input_capture_hash=input_root,
        delay=0,
        timeout=3,
        max_retries=0,
        max_total_bytes=256 * 1024 * 1024,
        permission_context_id="human-approved-legacy-fixture",
        leaf_profile="base",
        source_binding=source_binding,
    )
    request_plan_hash = canonical_hash(
        [request.semantic() for request in requests]
    )
    contract_id = canonical_hash(contract.semantic())
    identity = {
        "CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH": request_plan_hash,
        "CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID": canonical_hash(
            {
                "contract_id": contract_id,
                "request_plan_hash": request_plan_hash,
            }
        ),
        "CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID": contract_id,
        "CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH": input_root,
        "CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT": str(
            contract.adapter_identity["implementation_root"]
        ),
    }

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(request, _valid_pdf())

    with (
        patch.multiple(cninfo_module, **identity),
        patch.object(capture_module.os, "fsync", return_value=None),
    ):
        result = run_free_provider_backfill(
            contract,
            requests,
            transport=transport,
            signer=signer,
            normalizer=cninfo_module.normalize_cninfo_documents,
            runtime_implementation_root=str(
                contract.adapter_identity["implementation_root"]
            ),
        )
    assert result["status"] == "succeeded"
    return str(result["manifest_path"]), identity


def test_prepare_document_closure_requires_inventory_manifests(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="cninfo_document_closure_inventory_missing"):
        prepare_document_closure((), (), (2011,))


def test_document_closure_cli_plan_only_never_calls_network(
    strong_inventory_manifests: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = document_closure_module.main(
        [
            "--inventory",
            strong_inventory_manifests[0],
            "--inventory",
            strong_inventory_manifests[1],
            "--year",
            "2011",
            "--plan-only",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["network_called"] is False
    assert payload["inventory_parent_count"] == 2
    assert payload["physical_document_count"] == 1
    assert payload["missing_physical_document_count"] == 1
    assert all(value is False for value in payload["safety"].values())


def test_document_closure_cli_blocks_network_without_authority(
    strong_inventory_manifests: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = document_closure_module.main(
        [
            "--inventory",
            strong_inventory_manifests[0],
            "--inventory",
            strong_inventory_manifests[1],
            "--year",
            "2011",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["network_called"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == (
        "free_provider_backfill_network_authority_missing"
    )


def test_document_closure_cli_finalizes_complete_reused_union_offline(
    strong_inventory_manifests: tuple[str, str],
    strong_document_manifest: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = document_closure_module.main(
        [
            "--inventory",
            strong_inventory_manifests[0],
            "--inventory",
            strong_inventory_manifests[1],
            "--reusable-document",
            strong_document_manifest,
            "--year",
            "2011",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["network_called"] is False
    assert payload["status"] == "succeeded"
    assert payload["missing_physical_document_count"] == 0
    assert payload["evidence"]["complete"] is True


def test_document_closure_cli_blocks_oversized_plan_before_network(
    strong_inventory_manifests: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = document_closure_module.main(
        [
            "--inventory",
            strong_inventory_manifests[0],
            "--inventory",
            strong_inventory_manifests[1],
            "--year",
            "2011",
            "--allow-network",
            "--max-documents",
            "0",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["network_called"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == (
        "cninfo_document_closure_max_documents_exceeded"
    )


def test_prepare_document_closure_unions_demands_and_plans_only_missing_docs(
    strong_inventory_manifests: tuple[str, str],
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (),
        (2011,),
    )

    assert plan.demand_count == 2
    assert plan.physical_document_count == 1
    assert plan.reused_physical_document_count == 0
    assert plan.missing_physical_document_count == 1
    assert plan.missing_documents == (
        {
            "announcement_id": "58854747",
            "adjunct_url": "finalpage/2011-01-04/58854747.PDF",
            "announcement_time": 1294093800000,
            "adjunct_size_kb": 1,
        },
    )


def test_prepare_document_closure_reuses_one_parent_document_without_copying(
    strong_inventory_manifests: tuple[str, str],
    strong_document_manifest: str,
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (strong_document_manifest,),
        (2011,),
    )

    assert plan.demand_count == 2
    assert plan.physical_document_count == 1
    assert plan.reused_physical_document_count == 1
    assert plan.missing_physical_document_count == 0
    assert plan.missing_documents == ()
    assert plan.reused_documents[0]["disposition"] == "reused"
    assert plan.reused_documents[0]["parent_generation_id"].startswith(
        "free_provider_backfill_"
    )
    assert len(plan.reused_documents[0]["parent_request_semantic_hash"]) == 64
    assert len(plan.reused_documents[0]["parent_raw_envelope_sha256"]) == 64
    assert len(plan.reused_documents[0]["document_body_sha256"]) == 64
    assert plan.reused_documents[0]["parent_terminal_signature"]


def test_finalize_document_closure_replays_reused_parents(
    strong_inventory_manifests: tuple[str, str],
    strong_document_manifest: str,
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (strong_document_manifest,),
        (2011,),
    )

    evidence = finalize_document_closure(plan, None)

    assert evidence.complete is True
    assert evidence.demand_count == 2
    assert evidence.physical_document_count == 1
    assert evidence.reused_physical_document_count == 1
    assert evidence.downloaded_physical_document_count == 0
    assert evidence.weak_source_ancestry is False
    assert evidence.blockers == ()
    assert evidence.downstream_eligible is True
    assert evidence.sealed_plan_root == plan.plan_root
    assert len(evidence.closure_root) == 64


def test_exact_legacy_2011_reuse_remains_quarantined_and_weak(
    strong_inventory_manifests: tuple[str, str],
    synthetic_legacy_2011_document: tuple[str, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_2011_document_manifest, identity = synthetic_legacy_2011_document
    sealed_current_root = cninfo_module._implementation_root()
    for name, value in identity.items():
        monkeypatch.setattr(cninfo_module, name, value)
    # The synthetic exact-legacy constants are test evidence, not a new parser
    # version.  Keep the already-built strong inventory parents bound to the
    # implementation root under which this test process captured them.
    monkeypatch.setattr(
        cninfo_module,
        "_implementation_root",
        lambda: sealed_current_root,
    )
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (legacy_2011_document_manifest,),
        (2011,),
    )

    assert plan.reused_physical_document_count == 1
    assert plan.missing_physical_document_count == 0
    assert plan.weak_source_ancestry is True
    assert "legacy_2011_document_source_ancestry_incomplete" in plan.blockers
    assert "weak_source_acquisition_ancestry" in plan.blockers
    assert "cninfo_governed_evidence_ineligible" in plan.blockers
    assert plan.downstream_eligible is False

    evidence = finalize_document_closure(plan, None)
    assert evidence.complete is True
    assert evidence.weak_source_ancestry is True
    assert evidence.downstream_eligible is False
    assert evidence.blockers == plan.blockers


def test_lifecycle_documents_reuse_preserves_non_admission_blockers(
    strong_inventory_manifests: tuple[str, str],
    lifecycle_document_capture: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, approved_hash = lifecycle_document_capture
    monkeypatch.setattr(
        lifecycle_module,
        "APPROVED_CAPTURE_KEY_SHA256",
        approved_hash,
    )

    plan = prepare_document_closure(
        strong_inventory_manifests,
        (manifest,),
        (2018, 2019),
    )

    assert plan.demand_count == 10
    assert plan.physical_document_count == 5
    assert plan.reused_physical_document_count == 5
    assert plan.missing_physical_document_count == 0
    assert "provider_origin_not_attested" in plan.blockers
    assert "capture_runtime_isolation_not_verified" in plan.blockers
    assert (
        "lifecycle_document_announcement_time_not_exactly_bound"
        in plan.blockers
    )
    assert "lifecycle_document_adjunct_size_not_bound" in plan.blockers
    assert plan.downstream_eligible is False

    evidence = finalize_document_closure(plan, None)
    assert evidence.complete is True
    assert evidence.downloaded_physical_document_count == 0
    assert evidence.downstream_eligible is False
    assert evidence.blockers == plan.blockers


def test_reusable_document_accepts_valid_recovered_retry_lineage(
    strong_inventory_manifests: tuple[str, str],
    retried_document_manifest: str,
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (retried_document_manifest,),
        (2011,),
    )

    assert plan.reused_physical_document_count == 1
    assert plan.missing_physical_document_count == 0
    assert plan.reused_documents[0]["parent_request_id"] == (
        "cninfo_document_58854747"
    )


def test_inventory_accepts_valid_recovered_retry_lineage(
    strong_inventory_manifests: tuple[str, str],
    retried_base_inventory_manifest: str,
) -> None:
    plan = prepare_document_closure(
        (retried_base_inventory_manifest, strong_inventory_manifests[1]),
        (),
        (2011,),
    )

    assert plan.demand_count == 2
    assert plan.physical_document_count == 1
    assert plan.missing_physical_document_count == 1


def test_capture_and_finalize_exact_missing_document_closure(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (),
        (2011,),
    )
    signer = EphemeralReceiptSigner.generate()

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(request, _valid_pdf())

    with patch.object(capture_module.os, "fsync", return_value=None):
        captured = capture_missing_documents(
            plan,
            aggregate_plan=_full_aggregate_plan(plan),
            output_root=tmp_path / "missing-documents",
            signer=signer,
            transport=transport,
            permission_context_id=(
                document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
            ),
            minimum_delay_seconds=2,
            timeout_seconds=30,
            max_retries=2,
        )

    evidence = finalize_document_closure(plan, captured["manifest_path"])

    capture_root = Path(str(captured["manifest_path"])).parent
    contract = read_json(capture_root / "activity_contract.json")
    request_plan = read_json(capture_root / "request_plan.json")
    normalized = read_json(capture_root / "normalized/normalized_manifest.json")

    assert evidence.complete is True
    assert evidence.reused_physical_document_count == 0
    assert evidence.downloaded_physical_document_count == 1
    assert evidence.physical_document_count == 1
    assert evidence.downstream_eligible is True
    assert contract["budget"]["max_total_response_bytes"] == 132 * 1024**2
    assert contract["adapter_identity"]["storage_policy_id"] == (
        "cninfo_document_closure_year_sharded_aggregate_bound_512gib_v3"
    )
    assert contract["adapter_identity"][
        "aggregate_total_response_budget_ceiling"
    ] == str(512 * 1024**3)
    assert contract["adapter_identity"][
        "aggregate_sealed_plan_root"
    ] == _full_aggregate_plan(plan).plan_root
    assert request_plan["requests"][0]["metadata"]["demand_identities"] == sorted(
        request_plan["requests"][0]["metadata"]["demand_identities"]
    )
    assert len(request_plan["requests"][0]["metadata"]["demand_identities"]) == 2
    assert set(normalized) == {
        "schema_version",
        "sealed_plan_root",
        "missing_documents_root",
        "evidence_parents_root",
        "request_plan_hash",
        "document_count",
        "document_index_sha256",
        "exact_request_coverage_complete",
        "request_coverage_root",
        "raw_document_body_root",
        "raw_capture_contains_exact_document_bytes",
        "documents_extracted",
        "safety",
        "content_hash",
    }
    assert normalized["exact_request_coverage_complete"] is True
    assert normalized["raw_capture_contains_exact_document_bytes"] is True
    assert normalized["documents_extracted"] is False
    assert all(value is False for value in normalized["safety"].values())


def test_standard_reuse_rejects_inventory_metadata_mismatch(
    strong_inventory_manifests: tuple[str, str],
    metadata_mismatched_document_manifest: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="cninfo_document_closure_reuse_metadata_mismatch",
    ):
        prepare_document_closure(
            strong_inventory_manifests,
            (metadata_mismatched_document_manifest,),
            (2011,),
        )


def test_missing_capture_rejects_pdf_with_invalid_structure(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (),
        (2011,),
    )
    signer = EphemeralReceiptSigner.generate()
    malformed_pdf = (
        b"%PDF-1.4\n"
        + b"padding" * 20
        + b"\nstartxref\n999999999\n%%EOF\n"
    )

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(request, malformed_pdf)

    with pytest.raises(
        ValueError,
        match="cninfo_document_closure_missing_document_invalid",
    ):
        capture_missing_documents(
            plan,
            aggregate_plan=_full_aggregate_plan(plan),
            output_root=tmp_path / "malformed-pdf",
            signer=signer,
            transport=transport,
            permission_context_id=(
                document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
            ),
            minimum_delay_seconds=2,
            timeout_seconds=30,
            max_retries=2,
        )


def test_missing_capture_rejects_structurally_valid_html_waf_page(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (),
        (2012,),
    )
    signer = EphemeralReceiptSigner.generate()
    waf_page = (
        b"<!doctype html><html><body><pre>"
        b"verify you are human 2012-01-04 "
        + b"padding" * 12
        + b"</pre></body></html>"
    )

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(
            request,
            waf_page,
            response_content_type="text/html",
        )

    with pytest.raises(
        ValueError,
        match="cninfo_document_closure_missing_document_invalid",
    ):
        capture_missing_documents(
            plan,
            aggregate_plan=_full_aggregate_plan(plan),
            output_root=tmp_path / "html-waf",
            signer=signer,
            transport=transport,
            permission_context_id=(
                document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
            ),
            minimum_delay_seconds=2,
            timeout_seconds=30,
            max_retries=2,
        )


def test_missing_capture_identity_changes_with_engine_and_storage_policy(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (),
        (2011,),
    )
    signer = EphemeralReceiptSigner.generate()

    def transport(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return _official_observation(request, _valid_pdf())

    def capture(output_name: str) -> dict[str, object]:
        with patch.object(capture_module.os, "fsync", return_value=None):
            return capture_missing_documents(
                plan,
                aggregate_plan=_full_aggregate_plan(plan),
                output_root=tmp_path / output_name,
                signer=signer,
                transport=transport,
                permission_context_id=(
                    document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
                ),
                minimum_delay_seconds=2,
                timeout_seconds=30,
                max_retries=2,
            )

    original = capture("original")
    original_contract = read_json(
        Path(str(original["manifest_path"])).parent / "activity_contract.json"
    )
    original_engine_module = Path(str(capture_module.__file__))
    fake_engine_module = tmp_path / "free_provider_backfill_changed.py"
    fake_engine_module.write_bytes(
        original_engine_module.read_bytes()
        + b"\n# changed helper fixture\n"
    )
    monkeypatch.setattr(capture_module, "__file__", str(fake_engine_module))
    changed_engine = capture("changed-engine")
    changed_engine_contract = read_json(
        Path(str(changed_engine["manifest_path"])).parent
        / "activity_contract.json"
    )
    monkeypatch.setattr(capture_module, "__file__", str(original_engine_module))
    original_closure_module = Path(str(document_closure_module.__file__))
    fake_closure_module = tmp_path / "document_closure_changed.py"
    fake_closure_module.write_bytes(
        original_closure_module.read_bytes()
        + b"\n# changed closure helper fixture\n"
    )
    monkeypatch.setattr(
        document_closure_module,
        "__file__",
        str(fake_closure_module),
    )
    changed_closure = capture("changed-closure")
    changed_closure_contract = read_json(
        Path(str(changed_closure["manifest_path"])).parent
        / "activity_contract.json"
    )
    monkeypatch.setattr(
        document_closure_module,
        "_MISSING_STORAGE_POLICY_ID",
        "cninfo_document_closure_year_sharded_aggregate_test_change",
    )
    changed_policy = capture("changed-policy")
    changed_policy_contract = read_json(
        Path(str(changed_policy["manifest_path"])).parent
        / "activity_contract.json"
    )

    original_root = original_contract["adapter_identity"]["implementation_root"]
    engine_root = changed_engine_contract["adapter_identity"][
        "implementation_root"
    ]
    closure_root = changed_closure_contract["adapter_identity"][
        "implementation_root"
    ]
    policy_root = changed_policy_contract["adapter_identity"][
        "implementation_root"
    ]
    assert original_root != engine_root
    assert original_root != closure_root
    assert closure_root != policy_root


def test_missing_capture_pause_never_silently_resumes(
    tmp_path: Path,
    strong_inventory_manifests: tuple[str, str],
) -> None:
    plan = prepare_document_closure(
        strong_inventory_manifests,
        (),
        (2011,),
    )
    signer = EphemeralReceiptSigner.generate()
    output = tmp_path / "paused-missing-documents"

    def terminal_error(
        _request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        return ProviderProbeObservation(
            terminal_state="error",
            raw_payload=b'{"blocked":"schema"}',
            row_count=None,
            status_code=422,
            error_code="schema_mismatch",
            checks={"schema_valid": False},
            transport_exchange_count=1,
        )

    with pytest.raises(ProviderBackfillPaused):
        capture_missing_documents(
            plan,
            aggregate_plan=_full_aggregate_plan(plan),
            output_root=output,
            signer=signer,
            transport=terminal_error,
            permission_context_id=(
                document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
            ),
            minimum_delay_seconds=2,
            timeout_seconds=30,
            max_retries=2,
        )

    transport_called = False

    def success(
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        nonlocal transport_called
        transport_called = True
        return _official_observation(request, _valid_pdf())

    with pytest.raises(ProviderBackfillPaused):
        capture_missing_documents(
            plan,
            aggregate_plan=_full_aggregate_plan(plan),
            output_root=output,
            signer=signer,
            transport=success,
            permission_context_id=(
                document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
            ),
            minimum_delay_seconds=2,
            timeout_seconds=30,
            max_retries=2,
        )
    assert transport_called is False

    activity_roots = [
        path
        for path in tmp_path.glob(".paused-missing-documents.activities/*")
        if path.is_dir() and path.name != ".locks"
    ]
    pause = read_json(sorted(activity_roots[0].glob("pauses/pause_*.json"))[-1])
    authorization = PauseResumeAuthorization(
        authorization_id="human-resume-fixture",
        pause_content_hash=pause["content_hash"],
    )
    with pytest.raises(
        ValueError,
        match="free_provider_backfill_trusted_resume_authority_not_implemented",
    ):
        capture_missing_documents(
            plan,
            aggregate_plan=_full_aggregate_plan(plan),
            output_root=output,
            signer=signer,
            transport=success,
            permission_context_id=(
                document_closure_module.CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
            ),
            minimum_delay_seconds=2,
            timeout_seconds=30,
            max_retries=2,
            resume_authorization=authorization,
        )


def test_inventory_replay_rejects_missing_and_duplicate_page_ordinals(
    strong_inventory_manifests: tuple[str, str],
    malformed_page_inventory_manifest: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="cninfo_inventory_page_identity_invalid:st_delist_201101",
    ):
        prepare_document_closure(
            (malformed_page_inventory_manifest, strong_inventory_manifests[1]),
            (),
            (2011,),
        )


@pytest.mark.parametrize(
    "adversarial_mode",
    ("signed_subset", "wrong_category_body"),
)
def test_inventory_replay_rejects_incomplete_or_wrong_signed_request_semantics(
    adversarial_mode: str,
    strong_inventory_manifests: tuple[str, str],
    adversarial_inventory_manifest_factory: Callable[[str], str],
) -> None:
    adversarial_manifest = adversarial_inventory_manifest_factory(
        adversarial_mode
    )

    with pytest.raises(
        ValueError,
        match=(
            "cninfo_inventory_(request_closure|request_semantics)_invalid"
        ),
    ):
        prepare_document_closure(
            (adversarial_manifest, strong_inventory_manifests[1]),
            (),
            (2011,),
        )


@pytest.mark.parametrize(
    ("adversarial_mode", "expected_reason"),
    (
        (
            "duplicate_official_key",
            "cninfo_document_closure_json_duplicate_key",
        ),
        (
            "extra_official_key",
            "cninfo_document_closure_http_envelope_invalid",
        ),
        (
            "duplicate_provider_body_key",
            "cninfo_document_closure_json_duplicate_key",
        ),
    ),
)
def test_inventory_replay_rejects_signed_ambiguous_json(
    adversarial_mode: str,
    expected_reason: str,
    strong_inventory_manifests: tuple[str, str],
    adversarial_inventory_manifest_factory: Callable[[str], str],
) -> None:
    adversarial_manifest = adversarial_inventory_manifest_factory(
        adversarial_mode
    )

    with pytest.raises(ValueError, match=expected_reason):
        prepare_document_closure(
            (adversarial_manifest, strong_inventory_manifests[1]),
            (),
            (2011,),
        )


@pytest.mark.parametrize(
    "parent_failure",
    ("missing", "tampered_raw", "wrong_profile_replacement"),
)
def test_inventory_replay_recursively_rejects_invalid_discovery_parent(
    parent_failure: str,
    strong_inventory_manifests: tuple[str, str],
    isolated_inventory_with_discovery_parent: tuple[Path, Path, Path],
) -> None:
    inventory_manifest, discovery_generation, replacement = (
        isolated_inventory_with_discovery_parent
    )
    if parent_failure == "missing":
        shutil.rmtree(discovery_generation)
    elif parent_failure == "tampered_raw":
        raw = next((discovery_generation / "raw_envelopes").glob("*.json"))
        raw.write_bytes(raw.read_bytes() + b"\n")
    elif parent_failure == "wrong_profile_replacement":
        shutil.rmtree(discovery_generation)
        shutil.copytree(replacement, discovery_generation)
    else:
        raise AssertionError(parent_failure)

    with pytest.raises(
        ValueError,
        match="cninfo_document_closure_discovery_parent",
    ):
        prepare_document_closure(
            (str(inventory_manifest), strong_inventory_manifests[1]),
            (),
            (2011,),
        )


def test_inventory_replay_rejects_v1_discovery_ancestry_washed_as_strong(
    strong_inventory_manifests: tuple[str, str],
    forged_inventory_with_weak_discovery_parent: str,
) -> None:
    self_reported = cninfo_module.validate_cninfo_governance(
        forged_inventory_with_weak_discovery_parent
    )
    qualification = self_reported["cninfo_governance_qualification"]
    assert qualification["governed_evidence_eligible"] is True
    assert qualification["weak_source_ancestry"] is False

    with pytest.raises(
        ValueError,
        match="cninfo_document_closure_discovery_parent_invalid",
    ):
        prepare_document_closure(
            (
                forged_inventory_with_weak_discovery_parent,
                strong_inventory_manifests[1],
            ),
            (),
            (2011,),
        )
