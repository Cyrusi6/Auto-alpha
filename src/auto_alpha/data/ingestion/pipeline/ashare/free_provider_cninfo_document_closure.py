"""Fail-closed closure over CNINFO inventory and document generations.

The module has three public seams.  Preparation replays the signed base and
supplemental inventories and seals logical demand separately from physical
document identity.  Residual capture downloads only the sealed missing set,
and finalization replays every referenced parent before issuing evidence.

Content roots exclude the direct ``manifest_path`` locator.  They deliberately
retain upstream content-addressed acquisition-contract identities, whose
output namespace is part of the signed provider evidence rather than a local
locator alias.
"""

from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import urllib.parse

from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    read_json,
    sha256_file,
)
from auto_alpha.platform.governance.network.signing import (
    PersistentReceiptSigner,
)

from . import free_provider_http_backfill as http_module
from . import free_provider_cninfo_security_lifecycle as lifecycle_module
from . import free_provider_backfill as capture_module
from .free_provider_backfill import (
    BackfillResourceBudget,
    BackfillTransport,
    CaptureSigner,
    FreeProviderBackfillContract,
    NormalizedArtifact,
    PauseResumeAuthorization,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from .free_provider_http_backfill import validate_cninfo_governance
from .provider_probe import ProviderProbeRequest


_SCHEMA = "cninfo_document_closure_plan_v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_INVENTORY_PROFILES = frozenset({"base", "supplemental"})
_INVENTORY_ADAPTER = "cninfo_cninfo-inventory_signed_http_capture_v1"
_DOCUMENT_ADAPTER = "cninfo_cninfo-documents_signed_http_capture_v1"
_MISSING_DOCUMENT_ADAPTER = (
    "cninfo_document_closure_missing_signed_http_capture_v1"
)
_MISSING_NORMALIZATION_SCHEMA = (
    "cninfo_document_closure_missing_normalization_v1"
)
_MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES = 512 * 1024 * 1024 * 1024
_MISSING_MAX_DOCUMENTS_PER_ACTIVITY = 60_000
_MISSING_SINGLE_RESPONSE_BYTES = 132 * 1024 * 1024
_MISSING_AUTHORIZED_YEARS = tuple(range(2011, 2020))
_MISSING_AUTHORIZED_AGGREGATE_PLAN_ROOT = (
    "00483b73c0f86b9201162c27610a950b2f85a3cd3ee6c0d34a667e70c43f2a7a"
)
_MISSING_AUTHORIZED_AGGREGATE_DEMAND_COUNT = 343_262
_MISSING_AUTHORIZED_AGGREGATE_DOCUMENT_COUNT = 342_516
_MISSING_AUTHORIZED_AGGREGATE_RESPONSE_BUDGET = 509_623_150_592
_MISSING_STORAGE_POLICY_ID = (
    "cninfo_document_closure_year_sharded_aggregate_bound_512gib_v5"
)
CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT = (
    "human_authorization_20260827_cninfo_342516_bounded_binary_trailer_v5"
)
_RAW_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "request_id",
        "request_semantic_hash",
        "retry_ordinal",
        "capture_started_at",
        "capture_completed_at",
        "terminal_state",
        "row_count",
        "status_code",
        "error_code",
        "diagnostics",
        "checks",
        "transport_exchange_count",
        "raw_payload_base64",
        "raw_payload_sha256",
        "raw_payload_size_bytes",
    }
)
_OFFICIAL_HTTP_KEYS = frozenset(
    {
        "schema_version",
        "url",
        "method",
        "status_code",
        "response_headers",
        "body_base64",
        "body_sha256",
        "redirect_followed",
    }
)


@dataclass(frozen=True)
class _InventoryParent:
    manifest_path: str
    generation_id: str
    content_hash: str
    leaf_profile: str
    replay_root: str

    def semantic(self) -> dict[str, str]:
        return {
            "generation_id": self.generation_id,
            "content_hash": self.content_hash,
            "leaf_profile": self.leaf_profile,
            "replay_root": self.replay_root,
        }


@dataclass(frozen=True)
class _Demand:
    identity: str
    inventory_content_hash: str
    leaf_profile: str
    announcement_id: str
    adjunct_url: str

    def semantic(self) -> dict[str, str]:
        return {
            "identity": self.identity,
            "inventory_content_hash": self.inventory_content_hash,
            "leaf_profile": self.leaf_profile,
            "announcement_id": self.announcement_id,
            "adjunct_url": self.adjunct_url,
        }


@dataclass(frozen=True)
class _PhysicalDocument:
    announcement_id: str
    adjunct_url: str
    announcement_time: int
    adjunct_size_kb: int
    demand_identities: tuple[str, ...]

    def semantic(self) -> dict[str, Any]:
        return {
            "announcement_id": self.announcement_id,
            "adjunct_url": self.adjunct_url,
            "announcement_time": self.announcement_time,
            "adjunct_size_kb": self.adjunct_size_kb,
            "demand_identities": list(self.demand_identities),
        }

    def missing_semantic(self) -> dict[str, Any]:
        return {
            "announcement_id": self.announcement_id,
            "adjunct_url": self.adjunct_url,
            "announcement_time": self.announcement_time,
            "adjunct_size_kb": self.adjunct_size_kb,
        }


@dataclass(frozen=True)
class _DocumentParent:
    manifest_path: str
    generation_id: str
    content_hash: str
    parent_kind: str
    replay_root: str
    weak_source_ancestry: bool
    blockers: tuple[str, ...]

    def semantic(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "content_hash": self.content_hash,
            "parent_kind": self.parent_kind,
            "replay_root": self.replay_root,
            "weak_source_ancestry": self.weak_source_ancestry,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class _ReusableDocument:
    disposition: str
    announcement_id: str
    adjunct_url: str
    parent_generation_id: str
    parent_content_hash: str
    parent_request_id: str
    parent_request_semantic_hash: str
    parent_raw_envelope_sha256: str
    parent_raw_payload_sha256: str
    document_body_sha256: str
    document_size_bytes: int
    parent_terminal_signature: str
    parent_publication_signature: str
    weak_source_ancestry: bool
    blockers: tuple[str, ...]

    def semantic(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "announcement_id": self.announcement_id,
            "adjunct_url": self.adjunct_url,
            "parent_generation_id": self.parent_generation_id,
            "parent_content_hash": self.parent_content_hash,
            "parent_request_id": self.parent_request_id,
            "parent_request_semantic_hash": (
                self.parent_request_semantic_hash
            ),
            "parent_raw_envelope_sha256": self.parent_raw_envelope_sha256,
            "parent_raw_payload_sha256": self.parent_raw_payload_sha256,
            "document_body_sha256": self.document_body_sha256,
            "document_size_bytes": self.document_size_bytes,
            "parent_terminal_signature": self.parent_terminal_signature,
            "parent_publication_signature": self.parent_publication_signature,
            "weak_source_ancestry": self.weak_source_ancestry,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class SealedDocumentClosurePlan:
    """Immutable demand plan whose root excludes filesystem locations."""

    schema_version: str
    years: tuple[int, ...]
    inventory_parents: tuple[_InventoryParent, ...]
    demands: tuple[_Demand, ...]
    physical_documents: tuple[_PhysicalDocument, ...]
    reusable_parents: tuple[_DocumentParent, ...]
    reused: tuple[_ReusableDocument, ...]
    missing: tuple[_PhysicalDocument, ...]
    weak_source_ancestry: bool
    blockers: tuple[str, ...]
    plan_root: str

    @property
    def demand_count(self) -> int:
        return len(self.demands)

    @property
    def physical_document_count(self) -> int:
        return len(self.physical_documents)

    @property
    def reused_physical_document_count(self) -> int:
        return len(self.reused)

    @property
    def missing_physical_document_count(self) -> int:
        return len(self.missing)

    @property
    def missing_documents(self) -> tuple[dict[str, Any], ...]:
        return tuple(row.missing_semantic() for row in self.missing)

    @property
    def reused_documents(self) -> tuple[dict[str, Any], ...]:
        return tuple(row.semantic() for row in self.reused)

    @property
    def downstream_eligible(self) -> bool:
        return not self.missing and not self.weak_source_ancestry and not self.blockers


@dataclass(frozen=True)
class DocumentClosureEvidence:
    """Independently replayed terminal closure evidence."""

    schema_version: str
    sealed_plan_root: str
    demand_count: int
    physical_document_count: int
    reused_physical_document_count: int
    downloaded_physical_document_count: int
    dispositions: tuple[_ReusableDocument, ...]
    downloaded_parent: _DocumentParent | None
    weak_source_ancestry: bool
    blockers: tuple[str, ...]
    complete: bool
    downstream_eligible: bool
    closure_root: str


def prepare_document_closure(
    inventory_manifests: Sequence[str | Path],
    reusable_document_manifests: Sequence[str | Path],
    years: Sequence[int],
) -> SealedDocumentClosurePlan:
    """Replay inventories, form their union, and seal missing documents."""

    if not inventory_manifests:
        raise ValueError("cninfo_document_closure_inventory_missing")
    selected_years = _selected_years(years)

    parents: list[_InventoryParent] = []
    demand_rows: list[_Demand] = []
    physical_by_id: dict[str, _PhysicalDocument] = {}
    observed_profiles: set[str] = set()
    for manifest in inventory_manifests:
        parent, rows = _replay_inventory(Path(manifest))
        if parent.leaf_profile in observed_profiles:
            raise ValueError(
                "cninfo_document_closure_inventory_profile_duplicate:"
                f"{parent.leaf_profile}"
            )
        observed_profiles.add(parent.leaf_profile)
        parents.append(parent)
        for row in rows:
            if _announcement_year(row) not in selected_years:
                continue
            announcement_id = _announcement_id(row)
            adjunct_url = _canonical_adjunct_url(row.get("adjunct_url"))
            announcement_time = _exact_nonnegative_int(
                row.get("announcement_time"),
                "cninfo_document_closure_announcement_time_invalid",
            )
            adjunct_size_kb = _exact_nonnegative_int(
                row.get("adjunct_size_kb"),
                "cninfo_document_closure_adjunct_size_invalid",
            )
            if adjunct_size_kb <= 0:
                raise ValueError(
                    "cninfo_document_closure_adjunct_size_invalid:"
                    f"{announcement_id}"
                )
            demand_semantic = {
                "inventory_content_hash": parent.content_hash,
                "leaf_profile": parent.leaf_profile,
                "announcement_id": announcement_id,
                "adjunct_url": adjunct_url,
            }
            demand_identity = canonical_hash(demand_semantic)
            demand_rows.append(
                _Demand(identity=demand_identity, **demand_semantic)
            )
            physical = _PhysicalDocument(
                announcement_id=announcement_id,
                adjunct_url=adjunct_url,
                announcement_time=announcement_time,
                adjunct_size_kb=adjunct_size_kb,
                demand_identities=(demand_identity,),
            )
            prior = physical_by_id.get(announcement_id)
            if prior is None:
                physical_by_id[announcement_id] = physical
            elif prior.adjunct_url != adjunct_url:
                raise ValueError(
                    "cninfo_document_closure_announcement_url_conflict:"
                    f"{announcement_id}"
                )
            elif (
                prior.announcement_time != announcement_time
                or prior.adjunct_size_kb != adjunct_size_kb
            ):
                raise ValueError(
                    "cninfo_document_closure_announcement_metadata_conflict:"
                    f"{announcement_id}"
                )
            else:
                physical_by_id[announcement_id] = _PhysicalDocument(
                    announcement_id=prior.announcement_id,
                    adjunct_url=prior.adjunct_url,
                    announcement_time=prior.announcement_time,
                    adjunct_size_kb=prior.adjunct_size_kb,
                    demand_identities=tuple(
                        sorted((*prior.demand_identities, demand_identity))
                    ),
                )
    if observed_profiles != _INVENTORY_PROFILES:
        raise ValueError("cninfo_document_closure_inventory_profile_union_invalid")
    demands = tuple(sorted(demand_rows, key=lambda row: row.identity))
    if len({row.identity for row in demands}) != len(demands):
        raise ValueError("cninfo_document_closure_demand_duplicate")
    physical = tuple(
        physical_by_id[key] for key in sorted(physical_by_id)
    )
    reusable_parents: list[_DocumentParent] = []
    references_by_physical: dict[
        tuple[str, str], _ReusableDocument
    ] = {}
    physical_by_key = {
        (row.announcement_id, row.adjunct_url): row for row in physical
    }
    physical_urls_by_id = {
        row.announcement_id: row.adjunct_url for row in physical
    }
    inherited_blockers: set[str] = set()
    for manifest in reusable_document_manifests:
        reusable_parent, references = _replay_document_capture(
            Path(manifest),
            demanded_physical={
                row.announcement_id: row for row in physical
            },
        )
        matched = 0
        for reference in references:
            demanded_url = physical_urls_by_id.get(reference.announcement_id)
            if demanded_url is None:
                continue
            if demanded_url != reference.adjunct_url:
                raise ValueError(
                    "cninfo_document_closure_reuse_url_mismatch:"
                    f"{reference.announcement_id}"
                )
            key = (reference.announcement_id, reference.adjunct_url)
            if key in references_by_physical:
                raise ValueError(
                    "cninfo_document_closure_reuse_disposition_duplicate:"
                    f"{reference.announcement_id}"
                )
            references_by_physical[key] = reference
            inherited_blockers.update(reference.blockers)
            matched += 1
        if matched == 0:
            raise ValueError(
                "cninfo_document_closure_reusable_parent_out_of_scope"
            )
        reusable_parents.append(reusable_parent)
    reused = tuple(
        references_by_physical[key] for key in sorted(references_by_physical)
    )
    missing = tuple(
        physical_by_key[key]
        for key in sorted(physical_by_key)
        if key not in references_by_physical
    )
    weak_source_ancestry = any(
        row.weak_source_ancestry for row in reused
    )
    blockers = tuple(sorted(inherited_blockers))
    semantic = {
        "schema_version": _SCHEMA,
        "years": list(selected_years),
        "inventory_parents": [
            row.semantic()
            for row in sorted(parents, key=lambda item: item.leaf_profile)
        ],
        "demands": [row.semantic() for row in demands],
        "physical_documents": [row.semantic() for row in physical],
        "reusable_parents": [
            row.semantic()
            for row in sorted(
                reusable_parents,
                key=lambda item: (item.content_hash, item.generation_id),
            )
        ],
        "reused_documents": [row.semantic() for row in reused],
        "missing_documents": [row.missing_semantic() for row in missing],
        "weak_source_ancestry": weak_source_ancestry,
        "blockers": list(blockers),
    }
    return SealedDocumentClosurePlan(
        schema_version=_SCHEMA,
        years=selected_years,
        inventory_parents=tuple(
            sorted(parents, key=lambda row: row.leaf_profile)
        ),
        demands=demands,
        physical_documents=physical,
        reusable_parents=tuple(
            sorted(
                reusable_parents,
                key=lambda row: (row.content_hash, row.generation_id),
            )
        ),
        reused=reused,
        missing=missing,
        weak_source_ancestry=weak_source_ancestry,
        blockers=blockers,
        plan_root=canonical_hash(semantic),
    )


def capture_missing_documents(
    sealed_plan: SealedDocumentClosurePlan,
    *,
    aggregate_plan: SealedDocumentClosurePlan,
    output_root: str | Path,
    signer: CaptureSigner,
    transport: BackfillTransport,
    permission_context_id: str = (
        CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
    ),
    minimum_delay_seconds: float = 2.0,
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
    resume_authorization: PauseResumeAuthorization | None = None,
) -> dict[str, Any]:
    """Capture exactly the residual physical documents in a sealed plan."""

    replayed = _replay_sealed_plan(sealed_plan)
    aggregate = _replay_aggregate_plan_for_shard(replayed, aggregate_plan)
    if not replayed.missing:
        raise ValueError("cninfo_document_closure_nothing_missing")
    if (
        len(replayed.years) != 1
        or len(replayed.missing) > _MISSING_MAX_DOCUMENTS_PER_ACTIVITY
    ):
        raise ValueError(
            "cninfo_document_closure_single_year_shard_required"
        )
    if (
        permission_context_id
        != CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
    ):
        raise ValueError(
            "cninfo_document_closure_permission_context_invalid"
        )
    if (
        type(minimum_delay_seconds) not in {int, float}
        or float(minimum_delay_seconds) != 2.0
        or type(timeout_seconds) not in {int, float}
        or float(timeout_seconds) != 30.0
        or type(max_retries) is not int
        or max_retries != 2
    ):
        raise ValueError("cninfo_document_closure_capture_controls_invalid")
    requests = _missing_document_requests(replayed)
    missing_root = _missing_documents_root(replayed)
    parents_root = _evidence_parents_root(replayed)
    implementation_root = _missing_capture_implementation_root()
    request_plan_hash = canonical_hash(
        [request.semantic() for request in requests]
    )
    try:
        capture_public_key_sha256 = canonical_hash(
            signer.public_key_pem.decode("ascii")
        )
    except (AttributeError, UnicodeDecodeError) as exc:
        raise ValueError(
            "cninfo_document_closure_capture_key_invalid"
        ) from exc
    response_budget = _MISSING_SINGLE_RESPONSE_BYTES
    request_attempts = len(requests) * (max_retries + 1)
    total_response_budget = _missing_total_response_budget(replayed)
    aggregate_response_budget = _missing_total_response_budget(aggregate)
    if (
        aggregate_response_budget
        > _MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES
    ):
        raise ValueError(
            "cninfo_document_closure_declared_storage_budget_exceeded"
        )
    contract = FreeProviderBackfillContract(
        activity_name=(
            "free_domestic_cninfo_document_closure_missing_"
            f"{replayed.plan_root[:24]}_v5"
        ),
        provider="cninfo",
        output_root=output_root,
        permission_context_id=permission_context_id,
        population_root=canonical_hash(
            {
                "sealed_plan_root": replayed.plan_root,
                "missing_documents_root": missing_root,
                "evidence_parents_root": parents_root,
            }
        ),
        capture_public_key_sha256=capture_public_key_sha256,
        capture_public_key_pem_b64=base64.b64encode(
            signer.public_key_pem
        ).decode("ascii"),
        scope_start=f"{min(replayed.years):04d}0101",
        scope_end=f"{max(replayed.years):04d}1231",
        request_start=f"{min(replayed.years):04d}0101",
        request_end=f"{max(replayed.years):04d}1231",
        allowed_hosts=("static.cninfo.com.cn",),
        budget=BackfillResourceBudget(
            max_requests=request_attempts,
            max_wire_exchanges=request_attempts,
            max_response_bytes=response_budget,
            max_total_response_bytes=total_response_budget,
            timeout_seconds=float(timeout_seconds),
            minimum_delay_seconds=float(minimum_delay_seconds),
            max_retries=max_retries,
        ),
        adapter_identity={
            "adapter": _MISSING_DOCUMENT_ADAPTER,
            "http": "python_urllib_no_redirect_v1",
            "implementation_root": implementation_root,
            "sealed_plan_root": replayed.plan_root,
            "missing_documents_root": missing_root,
            "evidence_parents_root": parents_root,
            "request_plan_hash": request_plan_hash,
            "storage_policy_id": _MISSING_STORAGE_POLICY_ID,
            "aggregate_sealed_plan_root": aggregate.plan_root,
            "aggregate_missing_documents_root": _missing_documents_root(
                aggregate
            ),
            "aggregate_total_response_budget": str(
                aggregate_response_budget
            ),
            "aggregate_total_response_budget_ceiling": str(
                _MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES
            ),
            "response_budget_formula": (
                "max_132mib_twice_sum_max_64kib_declared_bytes_v1"
            ),
            "max_total_response_bytes": str(
                total_response_budget
            ),
            "authorization_policy": (
                "human_authorized_cninfo_document_closure_year_shards_v5"
            ),
        },
    )
    return run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=_normalize_missing_documents,
        resume_authorization=resume_authorization,
        runtime_implementation_root=implementation_root,
    )


def _missing_document_requests(
    plan: SealedDocumentClosurePlan,
) -> list[ProviderProbeRequest]:
    missing_root = _missing_documents_root(plan)
    parents_root = _evidence_parents_root(plan)
    return [
        ProviderProbeRequest(
            request_id=(
                "cninfo_document_closure_missing_"
                f"{row.announcement_id}"
            ),
            provider="cninfo",
            endpoint="announcement_document",
            method="GET",
            url=(
                "https://static.cninfo.com.cn/"
                f"{row.adjunct_url}"
            ),
            headers={
                "Referer": "https://www.cninfo.com.cn/",
                "User-Agent": http_module.USER_AGENT,
            },
            disposition="bounded_backfill",
            evidence_semantics="official_http_response_envelope",
            expected_terminal_states=("positive",),
            required_checks=(
                "http_envelope_schema_exact",
                "request_method_bound",
                "http_status_success",
                "redirect_not_followed",
                "request_url_bound",
                "body_sha256_matches",
                "nonempty_document",
                "recognized_document_format",
                "content_length_matches",
                "content_type_compatible",
                "document_structure_valid",
                "adjunct_size_reasonable",
            ),
            metadata={
                "case": "cninfo_document_closure_missing",
                "schema_version": (
                    "cninfo_document_closure_missing_request_v1"
                ),
                "sealed_plan_root": plan.plan_root,
                "missing_documents_root": missing_root,
                "evidence_parents_root": parents_root,
                "announcement_id": row.announcement_id,
                "announcement_time": row.announcement_time,
                "adjunct_url": row.adjunct_url,
                "adjunct_size_kb": row.adjunct_size_kb,
                "demand_identities": list(row.demand_identities),
            },
        )
        for row in plan.missing
    ]


def _normalize_missing_documents(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    contract = read_json(run_root / "activity_contract.json")
    plan = read_json(run_root / "request_plan.json")
    adapter = contract.get("adapter_identity") or {}
    request_rows = [request.semantic() for request in requests]
    if (
        adapter.get("adapter") != _MISSING_DOCUMENT_ADAPTER
        or adapter.get("implementation_root")
        != _missing_capture_implementation_root()
        or adapter.get("request_plan_hash") != canonical_hash(request_rows)
        or plan.get("requests") != request_rows
        or plan.get("request_plan_hash") != canonical_hash(request_rows)
        or set(terminal) != {request.request_id for request in requests}
    ):
        raise ValueError(
            "cninfo_document_closure_missing_context_invalid"
        )
    rows = _missing_capture_rows(
        run_root,
        request_rows=request_rows,
        terminal=terminal,
    )
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    index_payload = b"".join(
        _canonical_json_bytes(row) + b"\n" for row in rows
    )
    _atomic_payload(output / "document_index.jsonl", index_payload)
    manifest_semantic = _missing_normalized_manifest_semantic(
        adapter=adapter,
        rows=rows,
        index_payload=index_payload,
    )
    normalized_manifest = manifest_semantic | {
        "content_hash": canonical_hash(manifest_semantic)
    }
    _atomic_payload(
        output / "normalized_manifest.json",
        json.dumps(
            normalized_manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n",
    )
    return (
        NormalizedArtifact(
            "cninfo_document_closure_missing_document_index",
            "normalized/document_index.jsonl",
            len(rows),
        ),
        NormalizedArtifact(
            "normalized_manifest",
            "normalized/normalized_manifest.json",
            1,
        ),
    )


def _missing_normalized_manifest_semantic(
    *,
    adapter: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    index_payload: bytes,
) -> dict[str, Any]:
    coverage = [
        {
            "announcement_id": row.get("announcement_id"),
            "adjunct_url": row.get("adjunct_url"),
            "demand_identities": row.get("demand_identities"),
            "source_request_id": row.get("source_request_id"),
            "source_request_semantic_hash": row.get(
                "source_request_semantic_hash"
            ),
            "source_raw_envelope_sha256": row.get(
                "source_raw_envelope_sha256"
            ),
            "source_raw_payload_sha256": row.get(
                "source_raw_payload_sha256"
            ),
            "source_terminal_signature": row.get(
                "source_terminal_signature"
            ),
        }
        for row in rows
    ]
    bodies = [
        {
            "announcement_id": row.get("announcement_id"),
            "adjunct_url": row.get("adjunct_url"),
            "document_sha256": row.get("document_sha256"),
            "document_size_bytes": row.get("document_size_bytes"),
        }
        for row in rows
    ]
    return {
        "schema_version": _MISSING_NORMALIZATION_SCHEMA,
        "sealed_plan_root": adapter.get("sealed_plan_root"),
        "missing_documents_root": adapter.get("missing_documents_root"),
        "evidence_parents_root": adapter.get("evidence_parents_root"),
        "request_plan_hash": adapter.get("request_plan_hash"),
        "document_count": len(rows),
        "document_index_sha256": hashlib.sha256(
            index_payload
        ).hexdigest(),
        "exact_request_coverage_complete": True,
        "request_coverage_root": canonical_hash(coverage),
        "raw_document_body_root": canonical_hash(bodies),
        "raw_capture_contains_exact_document_bytes": True,
        "documents_extracted": False,
        "safety": {
            "data_admission_eligible": False,
            "profile_activation_authorized": False,
            "alpha_search_authorized": False,
            "holdout_activation_authorized": False,
            "paper_trading_authorized": False,
            "shadow_trading_authorized": False,
            "live_trading_authorized": False,
        },
    }


def _missing_capture_rows(
    run_root: Path,
    *,
    request_rows: Sequence[Mapping[str, Any]],
    terminal: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for request in request_rows:
        request_id = str(request.get("request_id") or "")
        event = terminal[request_id]
        body, raw_payload_sha256 = _replay_official_body(
            run_root,
            request=request,
            terminal=event,
        )
        metadata = request.get("metadata") or {}
        announcement_id = _announcement_id(
            {"announcement_id": metadata.get("announcement_id")}
        )
        adjunct_url = _canonical_adjunct_url(metadata.get("adjunct_url"))
        document_format = _document_format(body, adjunct_url)
        headers = _official_response_headers(run_root, event)
        size_kb = _exact_nonnegative_int(
            metadata.get("adjunct_size_kb"),
            "cninfo_document_closure_adjunct_size_invalid",
        )
        demand_identities = metadata.get("demand_identities")
        if (
            document_format is None
            or _document_block_reason(body) is not None
            or not _content_length_matches(
                headers.get("content-length"), len(body)
            )
            or not _content_type_compatible(
                document_format, headers.get("content-type")
            )
            or not _adjunct_size_reasonable(size_kb, len(body))
            or not _document_structure_valid(
                body,
                document_format=document_format,
                announcement_id=announcement_id,
                announcement_time=metadata.get("announcement_time"),
            )
            or not isinstance(demand_identities, list)
            or not demand_identities
            or demand_identities != sorted(set(demand_identities))
            or any(
                type(value) is not str
                or _HEX_64.fullmatch(value) is None
                for value in demand_identities
            )
        ):
            raise ValueError(
                "cninfo_document_closure_missing_document_invalid:"
                f"{announcement_id}"
            )
        rows.append(
            {
                "disposition": "downloaded",
                "sealed_plan_root": metadata.get("sealed_plan_root"),
                "missing_documents_root": metadata.get(
                    "missing_documents_root"
                ),
                "evidence_parents_root": metadata.get(
                    "evidence_parents_root"
                ),
                "announcement_id": announcement_id,
                "announcement_time": metadata.get("announcement_time"),
                "adjunct_url": adjunct_url,
                "document_format": document_format,
                "document_sha256": hashlib.sha256(body).hexdigest(),
                "document_size_bytes": len(body),
                "declared_adjunct_size_kb": size_kb,
                "demand_identities": demand_identities,
                "source_request_id": request_id,
                "source_request_semantic_hash": canonical_hash(request),
                "source_raw_envelope_sha256": event.get(
                    "raw_envelope_sha256"
                ),
                "source_raw_payload_sha256": raw_payload_sha256,
                "source_terminal_signature": event.get("signature"),
                "content_length": headers.get("content-length"),
                "content_type": headers.get("content-type"),
            }
        )
    return rows


def _missing_documents_root(plan: SealedDocumentClosurePlan) -> str:
    return canonical_hash(
        [row.missing_semantic() for row in plan.missing]
    )


def _missing_total_response_budget(
    plan: SealedDocumentClosurePlan,
) -> int:
    declared = sum(
        max(64 * 1024, row.adjunct_size_kb * 1024) * 2
        for row in plan.missing
    )
    return max(_MISSING_SINGLE_RESPONSE_BYTES, declared)


def _replay_aggregate_plan_for_shard(
    shard: SealedDocumentClosurePlan,
    aggregate_plan: SealedDocumentClosurePlan,
) -> SealedDocumentClosurePlan:
    return _validate_aggregate_plan_for_shard(
        shard,
        _replay_sealed_plan(aggregate_plan),
    )


def _validate_aggregate_plan_for_shard(
    shard: SealedDocumentClosurePlan,
    aggregate: SealedDocumentClosurePlan,
) -> SealedDocumentClosurePlan:
    aggregate_physical = [
        row.semantic()
        for row in aggregate.physical_documents
        if _physical_year(row) in shard.years
    ]
    if (
        aggregate.years != _MISSING_AUTHORIZED_YEARS
        or aggregate.reusable_parents
        or aggregate.reused
        or [row.semantic() for row in aggregate.inventory_parents]
        != [row.semantic() for row in shard.inventory_parents]
        or aggregate_physical
        != [row.semantic() for row in shard.physical_documents]
    ):
        raise ValueError(
            "cninfo_document_closure_aggregate_plan_invalid"
        )
    if (
        _missing_total_response_budget(aggregate)
        > _MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES
    ):
        raise ValueError(
            "cninfo_document_closure_aggregate_storage_budget_exceeded"
        )
    return aggregate


def _evidence_parents_root(plan: SealedDocumentClosurePlan) -> str:
    return canonical_hash(
        {
            "inventory_parents": [
                row.semantic() for row in plan.inventory_parents
            ],
            "reusable_parents": [
                row.semantic() for row in plan.reusable_parents
            ],
        }
    )


def _missing_capture_implementation_root() -> str:
    return canonical_hash(
        {
            "adapter": _MISSING_DOCUMENT_ADAPTER,
            "normalization_schema": _MISSING_NORMALIZATION_SCHEMA,
            "request_builder": inspect.getsource(_missing_document_requests),
            "capture_entrypoint": inspect.getsource(
                capture_missing_documents
            ),
            "normalizer": inspect.getsource(_normalize_missing_documents),
            "normalized_manifest": inspect.getsource(
                _missing_normalized_manifest_semantic
            ),
            "row_replay": inspect.getsource(_missing_capture_rows),
            "raw_replay": inspect.getsource(_replay_official_body),
            "url_canonicalizer": inspect.getsource(_canonical_adjunct_url),
            "document_format": inspect.getsource(_document_format),
            "document_block_reason": inspect.getsource(
                _document_block_reason
            ),
            "document_structure": inspect.getsource(
                _document_structure_valid
            ),
            "content_length": inspect.getsource(_content_length_matches),
            "content_type": inspect.getsource(_content_type_compatible),
            "declared_size": inspect.getsource(_adjunct_size_reasonable),
            "response_headers": inspect.getsource(
                _official_response_headers
            ),
            "atomic_payload": inspect.getsource(_atomic_payload),
            "missing_root": inspect.getsource(_missing_documents_root),
            "parents_root": inspect.getsource(_evidence_parents_root),
            "capture_engine": inspect.getsource(run_free_provider_backfill),
            "capture_validator": inspect.getsource(
                validate_free_provider_backfill
            ),
            "official_http_transport": inspect.getsource(
                http_module.OfficialHttpProbeTransport
            ),
            "cninfo_document_transport": inspect.getsource(
                http_module.CNINFODocumentTransport
            ),
            "http_document_format": inspect.getsource(
                http_module._document_format
            ),
            "http_document_block_reason": inspect.getsource(
                http_module._document_block_reason
            ),
            "http_content_length": inspect.getsource(
                http_module._content_length_matches
            ),
            "http_content_type": inspect.getsource(
                http_module._content_type_compatible
            ),
            "http_document_structure": inspect.getsource(
                http_module._document_structure_valid
            ),
            "http_pdf_trailing_comments": inspect.getsource(
                http_module._pdf_trailing_comments_valid
            ),
            "http_declared_size": inspect.getsource(
                http_module._adjunct_size_reasonable
            ),
            "capture_engine_module_sha256": sha256_file(
                Path(str(capture_module.__file__))
            ),
            "closure_module_sha256": sha256_file(Path(__file__)),
            "storage_policy": {
                "policy_id": _MISSING_STORAGE_POLICY_ID,
                "aggregate_max_total_response_bytes": (
                    _MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES
                ),
                "max_documents_per_activity": (
                    _MISSING_MAX_DOCUMENTS_PER_ACTIVITY
                ),
                "max_single_response_bytes": (
                    _MISSING_SINGLE_RESPONSE_BYTES
                ),
                "authorized_years": _MISSING_AUTHORIZED_YEARS,
                "authorized_aggregate_plan_root": (
                    _MISSING_AUTHORIZED_AGGREGATE_PLAN_ROOT
                ),
                "authorized_aggregate_demand_count": (
                    _MISSING_AUTHORIZED_AGGREGATE_DEMAND_COUNT
                ),
                "authorized_aggregate_document_count": (
                    _MISSING_AUTHORIZED_AGGREGATE_DOCUMENT_COUNT
                ),
                "authorized_aggregate_response_budget": (
                    _MISSING_AUTHORIZED_AGGREGATE_RESPONSE_BUDGET
                ),
                "response_budget": inspect.getsource(
                    _missing_total_response_budget
                ),
                "aggregate_plan_replay": inspect.getsource(
                    _replay_aggregate_plan_for_shard
                ),
                "aggregate_plan_validation": inspect.getsource(
                    _validate_aggregate_plan_for_shard
                ),
            },
        }
    )


def _replay_missing_capture(
    plan: SealedDocumentClosurePlan,
    manifest_path: Path,
) -> tuple[_DocumentParent, list[_ReusableDocument]]:
    aggregate = _validate_aggregate_plan_for_shard(
        plan,
        prepare_document_closure(
            tuple(row.manifest_path for row in plan.inventory_parents),
            (),
            _MISSING_AUTHORIZED_YEARS,
        ),
    )
    validated = validate_free_provider_backfill(manifest_path)
    root = Path(str(validated.get("manifest_path") or "")).parent
    contract = read_json(root / "activity_contract.json")
    request_plan = read_json(root / "request_plan.json")
    adapter = contract.get("adapter_identity") or {}
    requests = _missing_document_requests(plan)
    request_rows = [request.semantic() for request in requests]
    request_plan_hash = canonical_hash(request_rows)
    missing_root = _missing_documents_root(plan)
    parents_root = _evidence_parents_root(plan)
    budget = contract.get("budget") or {}
    retries = budget.get("max_retries")
    expected_attempts = (
        len(requests) * (int(retries) + 1)
        if retries == 2
        else -1
    )
    try:
        public_key = base64.b64decode(
            contract.get("capture_public_key_pem_b64"), validate=True
        )
        public_key_hash = canonical_hash(public_key.decode("ascii"))
    except (TypeError, ValueError, UnicodeDecodeError):
        public_key_hash = ""
    if (
        validated.get("status") != "succeeded"
        or validated.get("publication_signature_verified") is not True
        or contract.get("provider") != "cninfo"
        or contract.get("activity_name")
        != (
            "free_domestic_cninfo_document_closure_missing_"
            f"{plan.plan_root[:24]}_v5"
        )
        or contract.get("permission_context_id")
        != CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
        or contract.get("population_root")
        != canonical_hash(
            {
                "sealed_plan_root": plan.plan_root,
                "missing_documents_root": missing_root,
                "evidence_parents_root": parents_root,
            }
        )
        or contract.get("capture_public_key_sha256") != public_key_hash
        or contract.get("scope")
        != {
            "date_start": f"{min(plan.years):04d}0101",
            "date_end": f"{max(plan.years):04d}1231",
            "request_start": f"{min(plan.years):04d}0101",
            "request_end": f"{max(plan.years):04d}1231",
        }
        or contract.get("allowed_hosts") != ["static.cninfo.com.cn"]
        or type(budget) is not dict
        or budget.get("max_requests") != expected_attempts
        or budget.get("max_wire_exchanges") != expected_attempts
        or budget.get("max_response_bytes")
        != _MISSING_SINGLE_RESPONSE_BYTES
        or budget.get("max_total_response_bytes")
        != _missing_total_response_budget(plan)
        or budget.get("timeout_seconds") != 30.0
        or budget.get("minimum_delay_seconds") != 2.0
        or adapter
        != {
            "adapter": _MISSING_DOCUMENT_ADAPTER,
            "http": "python_urllib_no_redirect_v1",
            "implementation_root": _missing_capture_implementation_root(),
            "sealed_plan_root": plan.plan_root,
            "missing_documents_root": missing_root,
            "evidence_parents_root": parents_root,
            "request_plan_hash": request_plan_hash,
            "storage_policy_id": _MISSING_STORAGE_POLICY_ID,
            "aggregate_sealed_plan_root": aggregate.plan_root,
            "aggregate_missing_documents_root": _missing_documents_root(
                aggregate
            ),
            "aggregate_total_response_budget": str(
                _missing_total_response_budget(aggregate)
            ),
            "aggregate_total_response_budget_ceiling": str(
                _MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES
            ),
            "response_budget_formula": (
                "max_132mib_twice_sum_max_64kib_declared_bytes_v1"
            ),
            "max_total_response_bytes": str(
                _missing_total_response_budget(plan)
            ),
            "authorization_policy": (
                "human_authorized_cninfo_document_closure_year_shards_v5"
            ),
        }
        or request_plan.get("requests") != request_rows
        or request_plan.get("request_plan_hash") != request_plan_hash
    ):
        raise ValueError(
            "cninfo_document_closure_missing_capture_identity_invalid"
        )
    terminal = _terminal_events(root / "capture_journal.jsonl")
    if set(terminal) != {request.request_id for request in requests}:
        raise ValueError(
            "cninfo_document_closure_missing_capture_terminal_invalid"
        )
    rows = _missing_capture_rows(
        root,
        request_rows=request_rows,
        terminal=terminal,
    )
    index_payload = b"".join(
        _canonical_json_bytes(row) + b"\n" for row in rows
    )
    if index_payload != _published_artifact(
        root,
        validated,
        role="cninfo_document_closure_missing_document_index",
    ):
        raise ValueError(
            "cninfo_document_closure_missing_capture_replay_mismatch"
        )
    normalized_payload = _published_artifact(
        root,
        validated,
        role="normalized_manifest",
    )
    normalized = _exact_json_object(normalized_payload)
    expected_normalized_semantic = _missing_normalized_manifest_semantic(
        adapter=adapter,
        rows=rows,
        index_payload=index_payload,
    )
    expected_normalized = expected_normalized_semantic | {
        "content_hash": canonical_hash(expected_normalized_semantic)
    }
    if normalized != expected_normalized:
        raise ValueError(
            "cninfo_document_closure_missing_normalized_manifest_invalid"
        )
    publication_signature = str(
        validated.get("capture_publication_signature") or ""
    )
    references: list[_ReusableDocument] = []
    for row in rows:
        request_id = str(row["source_request_id"])
        event = terminal[request_id]
        references.append(
            _ReusableDocument(
                disposition="downloaded",
                announcement_id=str(row["announcement_id"]),
                adjunct_url=str(row["adjunct_url"]),
                parent_generation_id=str(
                    validated.get("generation_id") or ""
                ),
                parent_content_hash=str(
                    validated.get("content_hash") or ""
                ),
                parent_request_id=request_id,
                parent_request_semantic_hash=str(
                    row["source_request_semantic_hash"]
                ),
                parent_raw_envelope_sha256=str(
                    row["source_raw_envelope_sha256"]
                ),
                parent_raw_payload_sha256=str(
                    row["source_raw_payload_sha256"]
                ),
                document_body_sha256=str(row["document_sha256"]),
                document_size_bytes=int(row["document_size_bytes"]),
                parent_terminal_signature=str(event.get("signature") or ""),
                parent_publication_signature=publication_signature,
                weak_source_ancestry=False,
                blockers=(),
            )
        )
    expected_keys = {
        (row.announcement_id, row.adjunct_url) for row in plan.missing
    }
    actual_keys = {
        (row.announcement_id, row.adjunct_url) for row in references
    }
    if (
        len(references) != len(actual_keys)
        or actual_keys != expected_keys
        or not publication_signature
    ):
        raise ValueError(
            "cninfo_document_closure_missing_capture_population_invalid"
        )
    replay_root = canonical_hash(
        {
            "capture_content_hash": validated.get("content_hash"),
            "document_index_sha256": hashlib.sha256(
                index_payload
            ).hexdigest(),
            "normalized_manifest_content_hash": normalized.get(
                "content_hash"
            ),
        }
    )
    return (
        _DocumentParent(
            manifest_path=str(
                Path(str(validated["manifest_path"])).resolve()
            ),
            generation_id=str(validated.get("generation_id") or ""),
            content_hash=str(validated.get("content_hash") or ""),
            parent_kind="cninfo_document_closure_missing_v1",
            replay_root=replay_root,
            weak_source_ancestry=False,
            blockers=(),
        ),
        references,
    )


def finalize_document_closure(
    sealed_plan: SealedDocumentClosurePlan,
    missing_document_capture: str | Path | None,
) -> DocumentClosureEvidence:
    """Replay every parent and prove exactly one disposition per document."""

    replayed = _replay_sealed_plan(sealed_plan)
    downloaded_parent: _DocumentParent | None = None
    downloaded: tuple[_ReusableDocument, ...] = ()
    if replayed.missing:
        if missing_document_capture is None:
            raise ValueError(
                "cninfo_document_closure_missing_capture_required"
            )
        downloaded_parent, downloaded_rows = _replay_missing_capture(
            replayed,
            Path(missing_document_capture),
        )
        downloaded = tuple(downloaded_rows)
    elif missing_document_capture is not None:
        raise ValueError(
            "cninfo_document_closure_unexpected_missing_capture"
        )
    dispositions = tuple(
        sorted(
            (*replayed.reused, *downloaded),
            key=lambda row: (row.announcement_id, row.adjunct_url),
        )
    )
    disposition_keys = [
        (row.announcement_id, row.adjunct_url) for row in dispositions
    ]
    physical_keys = [
        (row.announcement_id, row.adjunct_url)
        for row in replayed.physical_documents
    ]
    complete = (
        len(disposition_keys) == len(set(disposition_keys))
        and sorted(disposition_keys) == sorted(physical_keys)
    )
    if not complete:
        raise ValueError("cninfo_document_closure_disposition_closure_invalid")
    blockers = tuple(
        sorted(
            set(replayed.blockers)
            | set(downloaded_parent.blockers if downloaded_parent else ())
        )
    )
    weak_source_ancestry = bool(
        replayed.weak_source_ancestry
        or (
            downloaded_parent is not None
            and downloaded_parent.weak_source_ancestry
        )
    )
    downstream_eligible = bool(
        complete and not weak_source_ancestry and not blockers
    )
    evidence_semantic = {
        "schema_version": "cninfo_document_closure_evidence_v1",
        "sealed_plan_root": replayed.plan_root,
        "demand_count": replayed.demand_count,
        "physical_document_count": replayed.physical_document_count,
        "reused_physical_document_count": len(replayed.reused),
        "downloaded_physical_document_count": len(downloaded),
        "dispositions": [row.semantic() for row in dispositions],
        "downloaded_parent": (
            downloaded_parent.semantic() if downloaded_parent else None
        ),
        "weak_source_ancestry": weak_source_ancestry,
        "blockers": list(blockers),
        "complete": complete,
        "downstream_eligible": downstream_eligible,
    }
    return DocumentClosureEvidence(
        schema_version="cninfo_document_closure_evidence_v1",
        sealed_plan_root=replayed.plan_root,
        demand_count=replayed.demand_count,
        physical_document_count=replayed.physical_document_count,
        reused_physical_document_count=len(replayed.reused),
        downloaded_physical_document_count=len(downloaded),
        dispositions=dispositions,
        downloaded_parent=downloaded_parent,
        weak_source_ancestry=weak_source_ancestry,
        blockers=blockers,
        complete=complete,
        downstream_eligible=downstream_eligible,
        closure_root=canonical_hash(evidence_semantic),
    )


def _replay_sealed_plan(
    sealed_plan: SealedDocumentClosurePlan,
) -> SealedDocumentClosurePlan:
    if type(sealed_plan) is not SealedDocumentClosurePlan:
        raise ValueError("cninfo_document_closure_sealed_plan_type_invalid")
    try:
        sealed_semantic = _sealed_plan_semantic(sealed_plan)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            "cninfo_document_closure_sealed_plan_invalid"
        ) from exc
    if (
        sealed_plan.schema_version != _SCHEMA
        or sealed_plan.plan_root != canonical_hash(sealed_semantic)
    ):
        raise ValueError("cninfo_document_closure_sealed_plan_invalid")
    replayed = prepare_document_closure(
        tuple(row.manifest_path for row in sealed_plan.inventory_parents),
        tuple(row.manifest_path for row in sealed_plan.reusable_parents),
        sealed_plan.years,
    )
    if replayed.plan_root != sealed_plan.plan_root:
        raise ValueError("cninfo_document_closure_parent_replay_mismatch")
    return replayed


def _sealed_plan_semantic(
    plan: SealedDocumentClosurePlan,
) -> dict[str, Any]:
    return {
        "schema_version": plan.schema_version,
        "years": list(plan.years),
        "inventory_parents": [
            row.semantic() for row in plan.inventory_parents
        ],
        "demands": [row.semantic() for row in plan.demands],
        "physical_documents": [
            row.semantic() for row in plan.physical_documents
        ],
        "reusable_parents": [
            row.semantic() for row in plan.reusable_parents
        ],
        "reused_documents": [row.semantic() for row in plan.reused],
        "missing_documents": [
            row.missing_semantic() for row in plan.missing
        ],
        "weak_source_ancestry": plan.weak_source_ancestry,
        "blockers": list(plan.blockers),
    }


def _selected_years(values: Sequence[int]) -> tuple[int, ...]:
    if not values or any(
        type(value) is not int or value < 2011 or value > 2019
        for value in values
    ):
        raise ValueError("cninfo_document_closure_years_invalid")
    years = tuple(sorted(set(values)))
    if len(years) != len(values):
        raise ValueError("cninfo_document_closure_years_duplicate")
    return years


def _manifest_root(path: Path) -> Path:
    resolved = path.resolve()
    if (
        not resolved.is_file()
        or resolved.name != "free_provider_backfill_manifest.json"
        or resolved.is_symlink()
    ):
        raise ValueError("cninfo_document_closure_manifest_path_invalid")
    return resolved.parent


def _replay_inventory(
    manifest_path: Path,
) -> tuple[_InventoryParent, list[dict[str, Any]]]:
    governed = validate_cninfo_governance(manifest_path)
    qualification = governed.get("cninfo_governance_qualification") or {}
    root = Path(str(governed.get("manifest_path") or "")).parent
    contract = read_json(root / "activity_contract.json")
    adapter = contract.get("adapter_identity") or {}
    leaf_profile = str(adapter.get("leaf_profile") or "")
    if (
        governed.get("status") != "succeeded"
        or governed.get("publication_signature_verified") is not True
        or qualification.get("source_lineage_complete") is not True
        or qualification.get("weak_source_ancestry") is not False
        or qualification.get("governed_evidence_eligible") is not True
        or adapter.get("adapter") != _INVENTORY_ADAPTER
        or leaf_profile not in _INVENTORY_PROFILES
    ):
        raise ValueError("cninfo_document_closure_inventory_not_strong_v2")
    inventory_request_rows = _strict_replay_cninfo_json_raw(
        root,
        phase="inventory",
    )
    required_roles = (
        "cninfo_announcement_inventory",
        "cninfo_page_coverage",
        "conflicts",
        "normalized_manifest",
    )
    replayed_artifacts, replay_root = (
        http_module._replay_cninfo_inventory_artifacts(
            manifest_path,
            required_roles=required_roles,
        )
    )
    for role in required_roles:
        if replayed_artifacts[role] != _published_artifact(
            root,
            governed,
            role=role,
        ):
            raise ValueError(
                "cninfo_document_closure_inventory_replay_mismatch:"
                f"{role}"
            )
    normalized_manifest = _exact_json_object(
        replayed_artifacts["normalized_manifest"]
    )
    normalized_semantic = {
        key: value
        for key, value in normalized_manifest.items()
        if key != "content_hash"
    }
    if normalized_manifest.get("content_hash") != canonical_hash(
        normalized_semantic
    ):
        raise ValueError(
            "cninfo_document_closure_inventory_normalized_manifest_invalid"
        )
    http_module._validate_cninfo_inventory_normalized_closure(
        normalized_manifest,
        replayed_artifacts["cninfo_page_coverage"],
        leaf_profile=leaf_profile,
    )
    discovery_parents = _replay_discovery_parents(
        inventory_root=root,
        inventory_contract=contract,
        inventory_request_rows=inventory_request_rows,
        normalized_manifest=normalized_manifest,
        leaf_profile=leaf_profile,
    )
    replay_root = canonical_hash(
        {
            "inventory_normalized_replay_root": replay_root,
            "discovery_parents": list(discovery_parents),
        }
    )
    replayed = _jsonl_objects(
        replayed_artifacts["cninfo_announcement_inventory"]
    )
    return (
        _InventoryParent(
            manifest_path=str(Path(str(governed["manifest_path"])).resolve()),
            generation_id=str(governed.get("generation_id") or ""),
            content_hash=str(governed.get("content_hash") or ""),
            leaf_profile=leaf_profile,
            replay_root=replay_root,
        ),
        replayed,
    )


def _strict_replay_cninfo_json_raw(
    root: Path,
    *,
    phase: str,
) -> list[Mapping[str, Any]]:
    plan = _exact_json_object((root / "request_plan.json").read_bytes())
    request_rows = plan.get("requests")
    if (
        set(plan)
        != {"schema_version", "request_plan_hash", "requests"}
        or plan.get("schema_version")
        != "free_provider_backfill_request_plan_v1"
        or not isinstance(request_rows, list)
        or plan.get("request_plan_hash") != canonical_hash(request_rows)
        or any(not isinstance(row, Mapping) for row in request_rows)
    ):
        raise ValueError(
            f"cninfo_document_closure_{phase}_plan_invalid"
        )
    request_ids = [str(row.get("request_id") or "") for row in request_rows]
    if any(not request_id for request_id in request_ids) or len(
        set(request_ids)
    ) != len(request_ids):
        raise ValueError(
            f"cninfo_document_closure_{phase}_plan_invalid"
        )
    terminal = _terminal_events(root / "capture_journal.jsonl")
    if set(terminal) != set(request_ids):
        raise ValueError(
            f"cninfo_document_closure_{phase}_terminal_invalid"
        )
    for request in request_rows:
        request_id = str(request["request_id"])
        body, _raw_payload_sha256 = _replay_official_body(
            root,
            request=request,
            terminal=terminal[request_id],
        )
        _exact_json_object(body)
    return list(request_rows)


def _replay_discovery_parents(
    *,
    inventory_root: Path,
    inventory_contract: Mapping[str, Any],
    inventory_request_rows: Sequence[Mapping[str, Any]],
    normalized_manifest: Mapping[str, Any],
    leaf_profile: str,
) -> tuple[dict[str, Any], ...]:
    inventory_output = inventory_root.parent.parent
    provider_root = inventory_output.parent
    if (
        inventory_root.parent.name != "generations"
        or inventory_output.name != "inventory"
        or any(
            path.is_symlink()
            for path in (
                inventory_root,
                inventory_root.parent,
                inventory_output,
                provider_root,
            )
        )
        or inventory_root.resolve() != inventory_root.absolute()
    ):
        raise ValueError(
            "cninfo_document_closure_discovery_parent_geometry_invalid"
        )
    try:
        ancestry = http_module._validate_cninfo_source_ancestry(
            normalized_manifest.get("source_ancestry"),
            expected_stage="discovery_capture_set",
            expected_leaf_profile=leaf_profile,
        )
        direct_sources = list(ancestry.get("direct_sources") or ())
        if not direct_sources:
            raise ValueError("discovery_parent_empty")
        parent_paths: list[Path] = []
        parent_evidence: list[dict[str, Any]] = []
        for declared in sorted(
            direct_sources,
            key=lambda row: str(row.get("source_generation_id") or ""),
        ):
            generation_id = str(
                declared.get("source_generation_id") or ""
            )
            candidate = (
                provider_root
                / "discovery"
                / "generations"
                / generation_id
                / "free_provider_backfill_manifest.json"
            )
            if (
                not candidate.is_file()
                or candidate.is_symlink()
                or candidate.parent.is_symlink()
                or candidate.parent.parent.is_symlink()
                or candidate.parent.parent.parent.is_symlink()
                or candidate.resolve() != candidate.absolute()
                or not candidate.resolve().is_relative_to(
                    provider_root.resolve()
                )
            ):
                raise ValueError("discovery_parent_missing_or_unconfined")
            governed = validate_cninfo_governance(candidate)
            qualification = (
                governed.get("cninfo_governance_qualification") or {}
            )
            if (
                governed.get("status") != "succeeded"
                or governed.get("publication_signature_verified") is not True
                or qualification.get("source_lineage_complete") is not True
                or qualification.get("weak_source_ancestry") is not False
                or qualification.get("governed_evidence_eligible") is not True
            ):
                raise ValueError("discovery_parent_not_strong")
            actual_direct = http_module._cninfo_direct_source(
                governed,
                expected_phase="cninfo-discovery",
                leaf_profile=leaf_profile,
            )
            if (
                dict(declared) != actual_direct
                or not http_module._implementation_root_compatible(
                    actual_direct["source_implementation_root"]
                )
            ):
                raise ValueError("discovery_parent_identity_mismatch")
            parent_root = Path(
                str(governed.get("manifest_path") or "")
            ).parent
            _strict_replay_cninfo_json_raw(
                parent_root,
                phase="discovery",
            )
            roles = (
                "cninfo_announcement_inventory",
                "cninfo_page_coverage",
                "conflicts",
                "normalized_manifest",
            )
            replayed, normalized_replay_root = (
                capture_module.replay_normalized_artifacts(
                    candidate,
                    normalizer=http_module.normalize_cninfo_discovery,
                    required_roles=roles,
                )
            )
            for role in roles:
                if replayed[role] != _published_artifact(
                    parent_root,
                    governed,
                    role=role,
                ):
                    raise ValueError(
                        f"discovery_parent_replay_mismatch:{role}"
                    )
            discovery_manifest = _exact_json_object(
                replayed["normalized_manifest"]
            )
            discovery_semantic = {
                key: value
                for key, value in discovery_manifest.items()
                if key != "content_hash"
            }
            if (
                discovery_manifest.get("schema_version")
                != "cninfo_announcement_inventory_normalization_v1"
                or discovery_manifest.get("require_full_page_chains")
                is not False
                or "source_ancestry" in discovery_manifest
                or "source_binding" in discovery_manifest
                or discovery_manifest.get("content_hash")
                != canonical_hash(discovery_semantic)
            ):
                raise ValueError("discovery_parent_normalized_invalid")
            parent_paths.append(candidate)
            parent_evidence.append(
                {
                    "generation_id": actual_direct[
                        "source_generation_id"
                    ],
                    "content_hash": actual_direct[
                        "source_content_hash"
                    ],
                    "contract_id": actual_direct["source_contract_id"],
                    "implementation_root": actual_direct[
                        "source_implementation_root"
                    ],
                    "leaf_profile": actual_direct[
                        "source_leaf_profile"
                    ],
                    "normalized_replay_root": normalized_replay_root,
                }
            )
        population, expected_requests, expected_input_root = (
            http_module.build_cninfo_inventory_plan(
                parent_paths,
                leaf_profile=leaf_profile,
            )
        )
        expected_rows = [request.semantic() for request in expected_requests]
        expected_ancestry = expected_requests[0].metadata.get(
            "source_ancestry"
        )
        expected_binding = expected_requests[0].metadata.get(
            "source_binding"
        )
        adapter = inventory_contract.get("adapter_identity") or {}
        if (
            list(inventory_request_rows) != expected_rows
            or inventory_contract.get("population_root")
            != canonical_hash(
                {
                    "population": population,
                    "input_capture_content_hash": expected_input_root,
                }
            )
            or adapter.get("input_capture_content_hash")
            != expected_input_root
            or adapter.get("source_ancestry_root")
            != (expected_ancestry or {}).get("ancestry_root")
            or adapter.get("source_binding_root")
            != (expected_binding or {}).get("content_hash")
            or adapter.get("source_upstream_content_hashes_root")
            != (expected_binding or {}).get(
                "upstream_content_hashes_root"
            )
            or normalized_manifest.get("source_ancestry")
            != expected_ancestry
            or normalized_manifest.get("source_binding")
            != expected_binding
        ):
            raise ValueError("discovery_parent_inventory_plan_mismatch")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "cninfo_document_closure_discovery_parent_invalid"
        ) from exc
    return tuple(
        sorted(parent_evidence, key=lambda row: row["generation_id"])
    )


def _replay_document_capture(
    manifest_path: Path,
    *,
    demanded_physical: Mapping[str, _PhysicalDocument],
) -> tuple[_DocumentParent, list[_ReusableDocument]]:
    root = _manifest_root(manifest_path)
    contract = read_json(root / "activity_contract.json")
    adapter = str((contract.get("adapter_identity") or {}).get("adapter") or "")
    if adapter == lifecycle_module.ADAPTER_ID:
        return _replay_lifecycle_document_capture(
            manifest_path,
            demanded_physical=demanded_physical,
        )
    return _replay_standard_document_capture(
        manifest_path,
        demanded_physical=demanded_physical,
    )


def _replay_standard_document_capture(
    manifest_path: Path,
    *,
    demanded_physical: Mapping[str, _PhysicalDocument],
) -> tuple[_DocumentParent, list[_ReusableDocument]]:
    governed = validate_cninfo_governance(manifest_path)
    qualification = governed.get("cninfo_governance_qualification") or {}
    root = Path(str(governed.get("manifest_path") or "")).parent
    contract = read_json(root / "activity_contract.json")
    adapter = contract.get("adapter_identity") or {}
    weak = qualification.get("weak_source_ancestry") is True
    exact_legacy = _exact_legacy_2011_identity(
        governed,
        adapter=adapter,
    )
    strong_v2 = bool(
        qualification.get("source_lineage_complete") is True
        and not weak
        and qualification.get("governed_evidence_eligible") is True
    )
    if (
        governed.get("status") != "succeeded"
        or governed.get("publication_signature_verified") is not True
        or adapter.get("adapter") != _DOCUMENT_ADAPTER
        or not (strong_v2 or exact_legacy)
    ):
        raise ValueError("cninfo_document_closure_reusable_parent_invalid")
    if exact_legacy:
        expected_blockers = {
            "legacy_2011_document_source_ancestry_incomplete",
            "weak_source_acquisition_ancestry",
            "cninfo_governed_evidence_ineligible",
        }
        if (
            qualification.get("quarantined") is not True
            or weak is not True
            or not expected_blockers.issubset(
                set(qualification.get("blockers") or ())
            )
        ):
            raise ValueError(
                "cninfo_document_closure_legacy_quarantine_invalid"
            )
    plan = read_json(root / "request_plan.json")
    request_rows = plan.get("requests")
    if not isinstance(request_rows, list) or not request_rows:
        raise ValueError("cninfo_document_closure_document_plan_invalid")
    terminal = _terminal_events(root / "capture_journal.jsonl")
    if set(terminal) != {
        str(row.get("request_id") or "") for row in request_rows
    }:
        raise ValueError("cninfo_document_closure_document_terminal_invalid")
    replayed_rows: list[dict[str, Any]] = []
    references: list[_ReusableDocument] = []
    publication_signature = str(
        governed.get("capture_publication_signature") or ""
    )
    if not publication_signature:
        raise ValueError(
            "cninfo_document_closure_publication_signature_missing"
        )
    blockers = tuple(sorted(set(qualification.get("blockers") or ())))
    seen_ids: dict[str, str] = {}
    for request in request_rows:
        if not isinstance(request, Mapping):
            raise ValueError("cninfo_document_closure_document_plan_invalid")
        metadata = request.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("cninfo_document_closure_document_plan_invalid")
        announcement_id = _announcement_id(
            {"announcement_id": metadata.get("announcement_id")}
        )
        adjunct_url = _canonical_adjunct_url(metadata.get("adjunct_url"))
        prior_url = seen_ids.setdefault(announcement_id, adjunct_url)
        if prior_url != adjunct_url:
            raise ValueError(
                "cninfo_document_closure_parent_announcement_url_conflict:"
                f"{announcement_id}"
            )
        demanded = demanded_physical.get(announcement_id)
        if demanded is None:
            continue
        if demanded.adjunct_url != adjunct_url:
            raise ValueError(
                "cninfo_document_closure_reuse_url_mismatch:"
                f"{announcement_id}"
            )
        if (
            metadata.get("announcement_time")
            != demanded.announcement_time
            or metadata.get("adjunct_size_kb")
            != demanded.adjunct_size_kb
        ):
            raise ValueError(
                "cninfo_document_closure_reuse_metadata_mismatch:"
                f"{announcement_id}"
            )
        if exact_legacy and _physical_year(demanded) != 2011:
            raise ValueError(
                "cninfo_document_closure_legacy_year_scope_invalid"
            )
        request_id = str(request.get("request_id") or "")
        event = terminal[request_id]
        body, raw_payload_sha256 = _replay_official_body(
            root,
            request=request,
            terminal=event,
        )
        response_headers = _official_response_headers(root, event)
        document_format = _document_format(body, adjunct_url)
        declared_size_kb = _exact_nonnegative_int(
            metadata.get("adjunct_size_kb"),
            "cninfo_document_closure_adjunct_size_invalid",
        )
        if (
            document_format is None
            or _document_block_reason(body) is not None
            or not _content_length_matches(
                response_headers.get("content-length"), len(body)
            )
            or not _content_type_compatible(
                document_format,
                response_headers.get("content-type"),
            )
            or not _adjunct_size_reasonable(declared_size_kb, len(body))
            or not _document_structure_valid(
                body,
                document_format=document_format,
                announcement_id=announcement_id,
                announcement_time=metadata.get("announcement_time"),
            )
        ):
            raise ValueError(
                "cninfo_document_closure_document_format_invalid:"
                f"{announcement_id}"
            )
        body_sha256 = hashlib.sha256(body).hexdigest()
        replayed_rows.append(
            {
                "announcement_id": announcement_id,
                "announcement_time": metadata.get("announcement_time"),
                "adjunct_url": metadata.get("adjunct_url"),
                "document_format": document_format,
                "document_sha256": body_sha256,
                "document_size_bytes": len(body),
                "declared_adjunct_size_kb": metadata.get(
                    "adjunct_size_kb"
                ),
                "source_request_id": request_id,
                "source_payload_sha256": raw_payload_sha256,
                "content_length": response_headers.get("content-length"),
                "content_type": response_headers.get("content-type"),
            }
        )
        terminal_signature = str(event.get("signature") or "")
        if not terminal_signature:
            raise ValueError(
                "cninfo_document_closure_terminal_signature_missing"
            )
        references.append(
            _ReusableDocument(
                disposition="reused",
                announcement_id=announcement_id,
                adjunct_url=adjunct_url,
                parent_generation_id=str(
                    governed.get("generation_id") or ""
                ),
                parent_content_hash=str(governed.get("content_hash") or ""),
                parent_request_id=request_id,
                parent_request_semantic_hash=str(
                    event.get("request_semantic_hash") or ""
                ),
                parent_raw_envelope_sha256=str(
                    event.get("raw_envelope_sha256") or ""
                ),
                parent_raw_payload_sha256=raw_payload_sha256,
                document_body_sha256=body_sha256,
                document_size_bytes=len(body),
                parent_terminal_signature=terminal_signature,
                parent_publication_signature=publication_signature,
                weak_source_ancestry=weak,
                blockers=blockers,
            )
        )
    published = _published_artifact(
        root,
        governed,
        role="cninfo_document_index",
    )
    published_rows = _jsonl_objects(published)
    published_by_request = {
        str(row.get("source_request_id") or ""): row
        for row in published_rows
    }
    if len(published_by_request) != len(published_rows) or any(
        published_by_request.get(str(row.get("source_request_id") or ""))
        != row
        for row in replayed_rows
    ):
        raise ValueError("cninfo_document_closure_document_replay_mismatch")
    replayed_payload = b"".join(
        _canonical_json_bytes(row) + b"\n" for row in replayed_rows
    )
    replay_root = canonical_hash(
        {
            "document_index_sha256": hashlib.sha256(
                replayed_payload
            ).hexdigest(),
            "document_index_size_bytes": len(replayed_payload),
            "document_count": len(replayed_rows),
            "raw_document_body_root": canonical_hash(
                [
                    {
                        "announcement_id": row.announcement_id,
                        "adjunct_url": row.adjunct_url,
                        "document_body_sha256": row.document_body_sha256,
                        "document_size_bytes": row.document_size_bytes,
                    }
                    for row in sorted(
                        references,
                        key=lambda item: (
                            item.announcement_id,
                            item.adjunct_url,
                        ),
                    )
                ]
            ),
        }
    )
    parent = _DocumentParent(
        manifest_path=str(Path(str(governed["manifest_path"])).resolve()),
        generation_id=str(governed.get("generation_id") or ""),
        content_hash=str(governed.get("content_hash") or ""),
        parent_kind=(
            "cninfo_legacy_2011_documents_v1"
            if exact_legacy
            else "cninfo_documents_v2"
        ),
        replay_root=replay_root,
        weak_source_ancestry=weak,
        blockers=blockers,
    )
    return parent, sorted(
        references,
        key=lambda row: (row.announcement_id, row.adjunct_url),
    )


def _replay_lifecycle_document_capture(
    manifest_path: Path,
    *,
    demanded_physical: Mapping[str, _PhysicalDocument],
) -> tuple[_DocumentParent, list[_ReusableDocument]]:
    validated = (
        lifecycle_module.validate_cninfo_security_identity_lifecycle_capture(
            manifest_path
        )
    )
    root = Path(str(validated.get("manifest_path") or "")).parent
    blockers = tuple(
        sorted(
            set(validated.get("blockers") or ())
            | {
                "lifecycle_document_announcement_time_not_exactly_bound",
                "lifecycle_document_adjunct_size_not_bound",
            }
        )
    )
    required_blockers = {
        "provider_origin_not_attested",
        "capture_runtime_isolation_not_verified",
    }
    if (
        validated.get("status") != "succeeded"
        or validated.get("publication_signature_verified") is not True
        or validated.get("normalized_replay_verified") is not True
        or validated.get("provider_origin_attested") is not False
        or validated.get("capture_runtime_isolation_verified") is not False
        or validated.get("data_admission_eligible") is not False
        or validated.get("downstream_eligible") is not False
        or validated.get("downstream_ineligible") is not True
        or not required_blockers.issubset(blockers)
    ):
        raise ValueError(
            "cninfo_document_closure_lifecycle_parent_invalid"
        )
    plan = read_json(root / "request_plan.json")
    request_rows = plan.get("requests")
    if not isinstance(request_rows, list) or not request_rows:
        raise ValueError("cninfo_document_closure_document_plan_invalid")
    terminal = _terminal_events(root / "capture_journal.jsonl")
    if set(terminal) != {
        str(row.get("request_id") or "") for row in request_rows
    }:
        raise ValueError("cninfo_document_closure_document_terminal_invalid")
    published = _published_artifact(
        root,
        validated,
        role="cninfo_security_identity_lifecycle_document_index",
    )
    published_rows = _jsonl_objects(published)
    published_by_request = {
        str(row.get("source_request_id") or ""): row
        for row in published_rows
    }
    if len(published_by_request) != len(published_rows):
        raise ValueError(
            "cninfo_document_closure_lifecycle_index_duplicate"
        )
    publication_signature = str(
        validated.get("capture_publication_signature") or ""
    )
    if not publication_signature:
        raise ValueError(
            "cninfo_document_closure_publication_signature_missing"
        )
    references: list[_ReusableDocument] = []
    seen_ids: dict[str, str] = {}
    replay_rows: list[dict[str, Any]] = []
    for request in request_rows:
        if not isinstance(request, Mapping):
            raise ValueError("cninfo_document_closure_document_plan_invalid")
        metadata = request.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("cninfo_document_closure_document_plan_invalid")
        announcement_id = _announcement_id(
            {"announcement_id": metadata.get("announcement_id")}
        )
        adjunct_url = _canonical_adjunct_url(metadata.get("url"))
        prior_url = seen_ids.setdefault(announcement_id, adjunct_url)
        if prior_url != adjunct_url:
            raise ValueError(
                "cninfo_document_closure_parent_announcement_url_conflict:"
                f"{announcement_id}"
            )
        demanded = demanded_physical.get(announcement_id)
        if demanded is None:
            continue
        if (
            demanded.adjunct_url != adjunct_url
            or metadata.get("announcement_date")
            != _announcement_date(demanded.announcement_time)
        ):
            raise ValueError(
                "cninfo_document_closure_reuse_url_mismatch:"
                f"{announcement_id}"
            )
        request_id = str(request.get("request_id") or "")
        event = terminal[request_id]
        body, raw_payload_sha256 = _replay_official_body(
            root,
            request=request,
            terminal=event,
        )
        body_sha256 = hashlib.sha256(body).hexdigest()
        published_row = published_by_request.get(request_id)
        if (
            not isinstance(published_row, Mapping)
            or published_row.get("announcement_id") != announcement_id
            or _canonical_adjunct_url(published_row.get("url"))
            != adjunct_url
            or published_row.get("document_sha256") != body_sha256
            or published_row.get("document_size_bytes") != len(body)
            or published_row.get("source_raw_envelope_sha256")
            != event.get("raw_envelope_sha256")
            or published_row.get("source_raw_payload_sha256")
            != raw_payload_sha256
            or published_row.get("provider_origin_attested") is not False
            or published_row.get("capture_runtime_isolation_verified")
            is not False
            or published_row.get("data_admission_eligible") is not False
            or published_row.get("downstream_eligible") is not False
            or published_row.get("downstream_ineligible") is not True
        ):
            raise ValueError(
                "cninfo_document_closure_lifecycle_replay_mismatch"
            )
        terminal_signature = str(event.get("signature") or "")
        if not terminal_signature:
            raise ValueError(
                "cninfo_document_closure_terminal_signature_missing"
            )
        references.append(
            _ReusableDocument(
                disposition="reused",
                announcement_id=announcement_id,
                adjunct_url=adjunct_url,
                parent_generation_id=str(
                    validated.get("generation_id") or ""
                ),
                parent_content_hash=str(
                    validated.get("content_hash") or ""
                ),
                parent_request_id=request_id,
                parent_request_semantic_hash=str(
                    event.get("request_semantic_hash") or ""
                ),
                parent_raw_envelope_sha256=str(
                    event.get("raw_envelope_sha256") or ""
                ),
                parent_raw_payload_sha256=raw_payload_sha256,
                document_body_sha256=body_sha256,
                document_size_bytes=len(body),
                parent_terminal_signature=terminal_signature,
                parent_publication_signature=publication_signature,
                weak_source_ancestry=False,
                blockers=blockers,
            )
        )
        replay_rows.append(
            {
                "announcement_id": announcement_id,
                "adjunct_url": adjunct_url,
                "document_body_sha256": body_sha256,
                "document_size_bytes": len(body),
                "source_request_id": request_id,
                "source_raw_envelope_sha256": event.get(
                    "raw_envelope_sha256"
                ),
                "source_raw_payload_sha256": raw_payload_sha256,
            }
        )
    replay_root = canonical_hash(
        {
            "parent_normalized_replay_root": validated.get(
                "normalized_replay_root"
            ),
            "selected_document_replay": replay_rows,
        }
    )
    return (
        _DocumentParent(
            manifest_path=str(
                Path(str(validated["manifest_path"])).resolve()
            ),
            generation_id=str(validated.get("generation_id") or ""),
            content_hash=str(validated.get("content_hash") or ""),
            parent_kind="cninfo_security_identity_lifecycle_exact_v1",
            replay_root=replay_root,
            weak_source_ancestry=False,
            blockers=blockers,
        ),
        sorted(
            references,
            key=lambda row: (row.announcement_id, row.adjunct_url),
        ),
    )


def _exact_legacy_2011_identity(
    governed: Mapping[str, Any],
    *,
    adapter: Mapping[str, Any],
) -> bool:
    return bool(
        governed.get("activity_id")
        == http_module.CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID
        and governed.get("contract_id")
        == http_module.CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID
        and governed.get("request_plan_hash")
        == http_module.CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH
        and adapter.get("input_capture_content_hash")
        == http_module.CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH
        and adapter.get("implementation_root")
        == http_module.CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT
    )


def _document_format(body: bytes, adjunct_url: str) -> str | None:
    stripped = body.lstrip()
    suffix = Path(adjunct_url).suffix.lower()
    if stripped.startswith(b"%PDF-") and b"%%EOF" in stripped[-64 * 1024 :]:
        return "pdf"
    if suffix in {".html", ".htm"} and stripped.startswith(b"<"):
        return "html"
    if suffix == ".js" and stripped and not stripped.startswith(b"<"):
        return "javascript"
    return None


def _document_block_reason(body: bytes) -> str | None:
    prefix = body[: 256 * 1024].lstrip().lower()
    if not prefix.startswith((b"<", b"<!doctype")):
        return None
    tokens = (
        b"captcha",
        b"access denied",
        b"request blocked",
        b"too many requests",
        b"verify you are human",
        b"waf",
        "访问被阻断".encode(),
        "访问频繁".encode(),
        "安全验证".encode(),
    )
    return (
        "official_archive_html_block_page"
        if any(token in prefix for token in tokens)
        else None
    )


def _document_structure_valid(
    body: bytes,
    *,
    document_format: str,
    announcement_id: str,
    announcement_time: Any,
) -> bool:
    stripped = body.lstrip()
    if document_format == "pdf":
        if not stripped.startswith(b"%PDF-") or len(stripped) <= 32:
            return False
        tail = stripped[-64 * 1024 :]
        matches = tuple(
            re.finditer(
                rb"startxref[ \t\r\n]+(\d+)[ \t\r\n]+%%EOF",
                tail,
            )
        )
        match = matches[-1] if matches else None
        startxref = int(match.group(1)) if match is not None else -1
        trailing = tail[match.end() :] if match is not None else b""
        return bool(
            match is not None
            and startxref < len(stripped)
            and (
                _pdf_trailing_comments_valid(trailing)
                or _pdf_bounded_legacy_binary_trailer_valid(
                    trailing,
                    body=stripped,
                    startxref=startxref,
                )
            )
        )
    if document_format == "html":
        prefix = stripped[: 2 * 1024 * 1024].lower()
        tail = stripped[-64 * 1024 :].lower()
        announcement_date = _announcement_date(announcement_time)
        return bool(
            announcement_date
            and prefix.startswith((b"<!doctype html", b"<html"))
            and b"<body" in prefix
            and b"</html" in tail
            and (
                b'class="zbt"' in prefix
                or b'class="zw"' in prefix
                or b"<pre" in prefix
            )
            and announcement_date.encode("ascii")
            in stripped[: 4 * 1024 * 1024]
        )
    if document_format == "javascript":
        text = stripped[: 4 * 1024 * 1024].decode(
            "gb18030", errors="replace"
        )
        return bool(
            text.lstrip().startswith("var affiches=")
            and announcement_id
            and f'"webTxtID":"{announcement_id}"' in text
        )
    return False


def _pdf_trailing_comments_valid(payload: bytes) -> bool:
    if not payload:
        return True
    if (
        len(payload) > 4 * 1024
        or payload[:1] not in b" \t\r\n"
        or b"\x00" in payload
    ):
        return False
    lines = re.split(rb"\r\n|\r|\n", payload)
    if len(lines) > 32:
        return False
    for line in lines:
        if len(line) > 1024:
            return False
        content = line.lstrip(b" \t")
        if content and not content.startswith(b"%"):
            return False
    return True


def _pdf_bounded_legacy_binary_trailer_valid(
    payload: bytes,
    *,
    body: bytes,
    startxref: int,
) -> bool:
    """Accept the observed fixed-width legacy record after a valid xref."""

    return bool(
        re.fullmatch(rb"\r\n\x00[^\x00\r\n]{15}\x00\x00", payload)
        and 0 <= startxref < len(body)
        and re.match(rb"xref(?:[ \t]|\r\n|\r|\n)", body[startxref:])
    )


def _content_length_matches(value: str | None, actual: int) -> bool:
    if value is None:
        return True
    try:
        return int(value) == actual and actual >= 0
    except (TypeError, ValueError):
        return False


def _content_type_compatible(
    document_format: str,
    value: str | None,
) -> bool:
    normalized = str(value or "").split(";", 1)[0].strip().lower()
    if not normalized:
        return True
    allowed = {
        "pdf": {"application/pdf", "application/octet-stream"},
        "html": {"text/html", "application/xhtml+xml"},
        "javascript": {
            "application/javascript",
            "text/javascript",
            "application/x-javascript",
            "text/plain",
        },
    }
    return normalized in allowed.get(document_format, set())


def _adjunct_size_reasonable(declared_kb: int, actual: int) -> bool:
    if declared_kb <= 0 or actual <= 0:
        return False
    declared_bytes = declared_kb * 1024
    return max(1, declared_bytes // 16) <= actual <= max(
        64 * 1024,
        declared_bytes * 8,
    )


def _official_response_headers(
    root: Path,
    terminal: Mapping[str, Any],
) -> dict[str, str]:
    relative = Path(str(terminal.get("raw_envelope_relative_path") or ""))
    wrapper = _exact_json_object((root / relative).read_bytes())
    official = _exact_json_object(
        base64.b64decode(wrapper.get("raw_payload_base64"), validate=True)
    )
    headers = official.get("response_headers")
    if type(headers) is not dict or any(
        type(key) is not str or type(value) is not str
        for key, value in headers.items()
    ):
        raise ValueError(
            "cninfo_document_closure_response_headers_invalid"
        )
    lowered = {key.lower(): value for key, value in headers.items()}
    if len(lowered) != len(headers):
        raise ValueError(
            "cninfo_document_closure_response_headers_duplicate"
        )
    return lowered


def _atomic_payload(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replay_official_body(
    root: Path,
    *,
    request: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> tuple[bytes, str]:
    relative = Path(str(terminal.get("raw_envelope_relative_path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("cninfo_document_closure_raw_path_invalid")
    raw_path = root / relative
    if (
        not raw_path.is_file()
        or raw_path.is_symlink()
        or sha256_file(raw_path) != terminal.get("raw_envelope_sha256")
    ):
        raise ValueError("cninfo_document_closure_raw_envelope_invalid")
    wrapper = _exact_json_object(raw_path.read_bytes())
    try:
        raw_payload = base64.b64decode(
            wrapper.get("raw_payload_base64"), validate=True
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("cninfo_document_closure_raw_payload_invalid") from exc
    request_id = str(request.get("request_id") or "")
    if (
        set(wrapper) != _RAW_ENVELOPE_KEYS
        or wrapper.get("schema_version")
        != "free_provider_backfill_raw_envelope_v1"
        or wrapper.get("request_id") != request_id
        or wrapper.get("request_semantic_hash") != canonical_hash(request)
        or wrapper.get("terminal_state")
        not in {"positive", "empty"}
        or wrapper.get("status_code") != 200
        or wrapper.get("raw_payload_size_bytes") != len(raw_payload)
        or wrapper.get("raw_payload_sha256")
        != hashlib.sha256(raw_payload).hexdigest()
        or terminal.get("request_semantic_hash")
        != wrapper.get("request_semantic_hash")
        or terminal.get("raw_payload_sha256")
        != wrapper.get("raw_payload_sha256")
    ):
        raise ValueError(
            f"cninfo_document_closure_raw_binding_invalid:{request_id}"
        )
    official = _exact_json_object(raw_payload)
    allowed_keys = set(_OFFICIAL_HTTP_KEYS)
    if "elapsed_seconds" in official:
        allowed_keys.add("elapsed_seconds")
    headers = official.get("response_headers")
    try:
        body = base64.b64decode(official.get("body_base64"), validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("cninfo_document_closure_http_body_invalid") from exc
    if (
        set(official) != allowed_keys
        or official.get("schema_version")
        != "official_http_probe_envelope_v1"
        or official.get("url") != request.get("url")
        or official.get("method") != str(request.get("method") or "").upper()
        or type(official.get("status_code")) is not int
        or official.get("status_code") != 200
        or official.get("redirect_followed") is not False
        or type(headers) is not dict
        or any(type(key) is not str or type(value) is not str for key, value in headers.items())
        or official.get("body_sha256") != hashlib.sha256(body).hexdigest()
    ):
        raise ValueError(
            f"cninfo_document_closure_http_envelope_invalid:{request_id}"
        )
    return body, hashlib.sha256(raw_payload).hexdigest()


def _terminal_events(path: Path) -> dict[str, dict[str, Any]]:
    attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    previous_sequence = 0
    for line in path.read_bytes().splitlines():
        if not line:
            continue
        row = _exact_json_object(line)
        sequence = row.get("sequence")
        if (
            type(sequence) is not int
            or sequence <= previous_sequence
        ):
            raise ValueError(
                "cninfo_document_closure_journal_sequence_invalid"
            )
        previous_sequence = sequence
        if row.get("event_type") != "capture_attempt_terminal":
            continue
        request_id = str(row.get("request_id") or "")
        retry_ordinal = row.get("retry_ordinal")
        if (
            not request_id
            or type(retry_ordinal) is not int
            or retry_ordinal < 0
        ):
            raise ValueError(
                "cninfo_document_closure_retry_lineage_invalid"
            )
        attempts[request_id].append(row)
    terminal: dict[str, dict[str, Any]] = {}
    for request_id, rows in attempts.items():
        ordinals = [int(row["retry_ordinal"]) for row in rows]
        if ordinals != list(range(len(rows))):
            raise ValueError(
                "cninfo_document_closure_retry_lineage_invalid:"
                f"{request_id}"
            )
        if any(
            row.get("terminal_state") != "error"
            or row.get("expectation_met") is not False
            for row in rows[:-1]
        ):
            raise ValueError(
                "cninfo_document_closure_retry_before_success_invalid:"
                f"{request_id}"
            )
        final = rows[-1]
        if (
            final.get("terminal_state") not in {"positive", "empty"}
            or final.get("expectation_met") is not True
        ):
            raise ValueError(
                "cninfo_document_closure_final_terminal_invalid:"
                f"{request_id}"
            )
        terminal[request_id] = final
    return terminal


def _published_artifact(
    root: Path,
    capture: Mapping[str, Any],
    *,
    role: str,
) -> bytes:
    matches = [
        row
        for row in capture.get("normalized_artifacts") or ()
        if isinstance(row, Mapping) and row.get("role") == role
    ]
    if len(matches) != 1:
        raise ValueError("cninfo_document_closure_normalized_role_invalid")
    relative = Path(str(matches[0].get("relative_path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("cninfo_document_closure_normalized_path_invalid")
    path = root / relative
    payload = path.read_bytes()
    if (
        path.is_symlink()
        or hashlib.sha256(payload).hexdigest() != matches[0].get("sha256")
        or len(payload) != matches[0].get("size_bytes")
    ):
        raise ValueError("cninfo_document_closure_normalized_artifact_invalid")
    return payload


def _announcement_year(row: Mapping[str, Any]) -> int:
    observed = _announcement_date(row.get("announcement_time"))
    if observed is None or row.get("announcement_date") != observed:
        raise ValueError("cninfo_document_closure_announcement_date_invalid")
    return int(observed[:4])


def _physical_year(row: _PhysicalDocument) -> int:
    observed = _announcement_date(row.announcement_time)
    if observed is None:
        raise ValueError("cninfo_document_closure_announcement_date_invalid")
    return int(observed[:4])


def _announcement_date(value: Any) -> str | None:
    try:
        if isinstance(value, bool):
            return None
        milliseconds = int(value)
        observed = datetime.fromtimestamp(
            milliseconds / 1000,
            tz=UTC,
        ) + timedelta(hours=8)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return observed.date().isoformat()


def _announcement_id(row: Mapping[str, Any]) -> str:
    value = row.get("announcement_id")
    if type(value) is not str or not value or not value.isdecimal():
        raise ValueError("cninfo_document_closure_announcement_id_invalid")
    return value


def _canonical_adjunct_url(value: Any) -> str:
    if type(value) is not str or not value:
        raise ValueError("cninfo_document_closure_adjunct_url_invalid")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.netloc != "static.cninfo.com.cn"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("cninfo_document_closure_adjunct_url_invalid")
        path = parsed.path.lstrip("/")
    else:
        if parsed.query or parsed.fragment or value.startswith("/"):
            raise ValueError("cninfo_document_closure_adjunct_url_invalid")
        path = parsed.path
    decoded_parts: list[str] = []
    for encoded in path.split("/"):
        decoded = urllib.parse.unquote(encoded, errors="strict")
        if (
            not decoded
            or decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        ):
            raise ValueError("cninfo_document_closure_adjunct_url_invalid")
        decoded_parts.append(decoded)
    canonical = "/".join(
        urllib.parse.quote(part, safe="-_.~") for part in decoded_parts
    )
    if not canonical.startswith("finalpage/"):
        raise ValueError("cninfo_document_closure_adjunct_url_invalid")
    return canonical


def _exact_nonnegative_int(value: Any, reason: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(reason)
    return value


def _exact_json_object(payload: bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("cninfo_document_closure_json_duplicate_key")
            value[key] = item
        return value

    try:
        decoded = json.loads(payload, object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("cninfo_document_closure_json_invalid") from exc
    if type(decoded) is not dict:
        raise ValueError("cninfo_document_closure_json_object_required")
    return decoded


def _jsonl_objects(payload: bytes) -> list[dict[str, Any]]:
    return [_exact_json_object(line) for line in payload.splitlines() if line]


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Close CNINFO document demand from signed inventories."
    )
    parser.add_argument(
        "--inventory",
        action="append",
        required=True,
        help="Signed inventory manifest; provide exactly base and supplemental.",
    )
    parser.add_argument(
        "--reusable-document",
        action="append",
        default=[],
        help="Optional signed document manifest eligible for exact replay.",
    )
    parser.add_argument("--year", action="append", type=int)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--minimum-delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--max-documents",
        type=int,
        default=_MISSING_MAX_DOCUMENTS_PER_ACTIVITY,
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def _closure_summary(
    plan: SealedDocumentClosurePlan,
    *,
    network_called: bool,
) -> dict[str, Any]:
    by_year: dict[int, list[_PhysicalDocument]] = defaultdict(list)
    for row in plan.missing:
        by_year[_physical_year(row)].append(row)
    year_shards = [
        {
            "year": year,
            "missing_physical_document_count": len(rows),
            "max_total_response_bytes": max(
                _MISSING_SINGLE_RESPONSE_BYTES,
                sum(
                    max(64 * 1024, row.adjunct_size_kb * 1024) * 2
                    for row in rows
                ),
            ),
        }
        for year, rows in sorted(by_year.items())
    ]
    return {
        "schema_version": "cninfo_document_closure_plan_preview_v1",
        "sealed_plan_root": plan.plan_root,
        "years": list(plan.years),
        "inventory_parent_count": len(plan.inventory_parents),
        "demand_count": plan.demand_count,
        "physical_document_count": plan.physical_document_count,
        "reused_physical_document_count": (
            plan.reused_physical_document_count
        ),
        "missing_physical_document_count": (
            plan.missing_physical_document_count
        ),
        "weak_source_ancestry": plan.weak_source_ancestry,
        "blockers": list(plan.blockers),
        "downstream_eligible": plan.downstream_eligible,
        "selected_max_total_response_bytes": (
            _missing_total_response_budget(plan) if plan.missing else 0
        ),
        "aggregate_max_total_response_bytes": (
            _MISSING_AGGREGATE_MAX_TOTAL_RESPONSE_BYTES
        ),
        "max_documents_per_activity": (
            _MISSING_MAX_DOCUMENTS_PER_ACTIVITY
        ),
        "year_shards": year_shards,
        "network_called": network_called,
        "safety": {
            "profile_activation_authorized": False,
            "data_admission_eligible": False,
            "alpha_search_authorized": False,
            "holdout_activation_authorized": False,
            "shadow_trading_authorized": False,
            "paper_trading_authorized": False,
            "live_trading_authorized": False,
        },
    }


def _evidence_summary(evidence: DocumentClosureEvidence) -> dict[str, Any]:
    return {
        "schema_version": evidence.schema_version,
        "sealed_plan_root": evidence.sealed_plan_root,
        "closure_root": evidence.closure_root,
        "demand_count": evidence.demand_count,
        "physical_document_count": evidence.physical_document_count,
        "reused_physical_document_count": (
            evidence.reused_physical_document_count
        ),
        "downloaded_physical_document_count": (
            evidence.downloaded_physical_document_count
        ),
        "weak_source_ancestry": evidence.weak_source_ancestry,
        "blockers": list(evidence.blockers),
        "complete": evidence.complete,
        "downstream_eligible": evidence.downstream_eligible,
    }


def _render(payload: Mapping[str, Any], *, pretty: bool) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    years = tuple(args.year or range(2011, 2020))
    try:
        plan = prepare_document_closure(
            args.inventory,
            args.reusable_document,
            years,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            _render(
                {"status": "blocked", "reason": str(exc)},
                pretty=args.pretty,
            )
        )
        return 2
    preview = _closure_summary(plan, network_called=False)
    if args.plan_only:
        print(_render(preview, pretty=args.pretty))
        return 0
    if plan.missing and not args.allow_network:
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": (
                        "free_provider_backfill_network_authority_missing"
                    ),
                },
                pretty=args.pretty,
            )
        )
        return 2
    if plan.missing and len(plan.years) != 1:
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": (
                        "cninfo_document_closure_single_year_shard_required"
                    ),
                },
                pretty=args.pretty,
            )
        )
        return 2
    if plan.missing and (
        type(args.max_documents) is not int
        or args.max_documents < 0
        or args.max_documents > _MISSING_MAX_DOCUMENTS_PER_ACTIVITY
        or plan.missing_physical_document_count > args.max_documents
    ):
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": (
                        "cninfo_document_closure_max_documents_exceeded"
                    ),
                },
                pretty=args.pretty,
            )
        )
        return 2
    if plan.missing and (
        args.minimum_delay_seconds != 2.0
        or args.timeout_seconds != 30.0
        or args.max_retries != 2
    ):
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": (
                        "cninfo_document_closure_capture_controls_invalid"
                    ),
                },
                pretty=args.pretty,
            )
        )
        return 2
    try:
        capture: dict[str, Any] | None = None
        if plan.missing:
            aggregate_plan = prepare_document_closure(
                args.inventory,
                (),
                _MISSING_AUTHORIZED_YEARS,
            )
            if (
                aggregate_plan.plan_root
                != _MISSING_AUTHORIZED_AGGREGATE_PLAN_ROOT
                or aggregate_plan.demand_count
                != _MISSING_AUTHORIZED_AGGREGATE_DEMAND_COUNT
                or aggregate_plan.physical_document_count
                != _MISSING_AUTHORIZED_AGGREGATE_DOCUMENT_COUNT
                or _missing_total_response_budget(aggregate_plan)
                != _MISSING_AUTHORIZED_AGGREGATE_RESPONSE_BUDGET
            ):
                raise ValueError(
                    "cninfo_document_closure_authorized_aggregate_mismatch"
                )
            preview |= {
                "aggregate_sealed_plan_root": aggregate_plan.plan_root,
                "aggregate_total_response_budget": (
                    _missing_total_response_budget(aggregate_plan)
                ),
            }
            signer = PersistentReceiptSigner.load(
                http_module.DEFAULT_CAPTURE_KEY
            )
            capture = capture_missing_documents(
                plan,
                aggregate_plan=aggregate_plan,
                output_root=(
                    http_module.SCOPE_ROOT
                    / "cninfo"
                    / "document_closure_missing"
                ),
                signer=signer,
                transport=http_module.CNINFODocumentTransport(
                    minimum_delay_seconds=args.minimum_delay_seconds
                ),
                permission_context_id=(
                    CNINFO_DOCUMENT_CLOSURE_SHARD_PERMISSION_CONTEXT
                ),
                minimum_delay_seconds=args.minimum_delay_seconds,
                timeout_seconds=args.timeout_seconds,
                max_retries=args.max_retries,
            )
        evidence = finalize_document_closure(
            plan,
            None if capture is None else str(capture["manifest_path"]),
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            _render(
                preview | {"status": "blocked", "reason": str(exc)},
                pretty=args.pretty,
            )
        )
        return 2
    result = {
        **_closure_summary(
            plan,
            network_called=bool(
                capture and capture.get("cache_hit") is not True
            ),
        ),
        "status": "succeeded",
        "capture": capture,
        "evidence": _evidence_summary(evidence),
    }
    print(_render(result, pretty=args.pretty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
