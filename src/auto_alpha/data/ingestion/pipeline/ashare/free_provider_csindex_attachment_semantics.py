"""Fail-closed semantic replay for governed CSI attachment captures.

This module turns verified attachment bytes into *candidate* CSI300 change
rows.  It does not establish historical known-at, effective-at, completeness,
membership, or weights.  Unsupported and ambiguous documents remain explicit
blocked rows instead of being guessed from filenames or announcement titles.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import io
import json
import math
import os
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

import xlrd

try:
    import resource
except ImportError:  # pragma: no cover - governed production host is POSIX.
    resource = None  # type: ignore[assignment]

from auto_alpha.platform.artifacts.storage import (
    atomic_json,
    canonical_hash,
    publish_prepared_generation,
    read_json,
    sha256_file,
    validate_generation,
)

from . import free_provider_csindex_range_attachment as range_capture


SEMANTIC_SCHEMA = "csindex_attachment_semantic_replay_v6"
SEMANTIC_INDEX_SCHEMA = "csindex_attachment_semantic_index_v6"
CANDIDATE_SCHEMA = "csindex_csi300_change_candidate_v3"
ROW_DISPOSITION_SCHEMA = "csindex_csi300_change_row_disposition_v2"
SHEET_DISPOSITION_SCHEMA = "csindex_csi300_sheet_disposition_v1"
PARSER_IDENTITY = "fail_closed_xlsx_xlrd_xls_csi300_change_parser_v6"
MAX_ZIP_ENTRIES = 2_048
MAX_ZIP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_WORKBOOK_CELLS = 2_000_000
MAX_SHARED_STRINGS = 500_000
MAX_XLS_BODY_BYTES = 16 * 1024 * 1024
MAX_XLS_SHEETS = 128
MAX_XLS_SHEET_ROWS = 65_536
MAX_XLS_SHEET_COLUMNS = 256
MAX_XLS_CELL_TEXT_BYTES = 128 * 1024
MAX_XLS_WORKBOOK_TEXT_BYTES = 64 * 1024 * 1024
MAX_XLS_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_REFERENCE_BYTES = 32 * 1024 * 1024
MAX_SEMANTIC_JSONL_BYTES = 64 * 1024 * 1024
MAX_SEMANTIC_JSONL_LINE_BYTES = 2 * 1024 * 1024
XLS_WORKER_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
XLS_WORKER_CPU_SOFT_SECONDS = 10
XLS_WORKER_CPU_HARD_SECONDS = 12
XLS_WORKER_WALL_TIMEOUT_SECONDS = 20
XLS_WORKER_OPEN_FILE_LIMIT = 32
XLS_WORKER_SCHEMA = "csindex_xls_isolated_worker_v2"
XLS_WORKER_CONTRACT_SCHEMA = "csindex_xls_worker_contract_v1"
XLS_WORKER_MODULE_NAME = (
    "auto_alpha.data.ingestion.pipeline.ashare."
    "free_provider_csindex_attachment_semantics"
)
SEMANTIC_MANIFEST_NAME = "csindex_attachment_semantic_evidence.json"
SEMANTIC_GENERATION_PREFIX = "csindex_attachment_semantics"
SEMANTIC_ARTIFACT_SET_SCHEMA = "csindex_attachment_semantic_artifact_set_v7"
SEMANTIC_POINTER_SCHEMA = "csindex_attachment_semantics_pointer_v1"
SOURCE_CAPTURE_BINDING_SCHEMA = (
    "csindex_attachment_semantic_source_capture_binding_v2"
)
SOURCE_REFERENCE_SCHEMA = "csindex_attachment_semantic_source_reference_v2"
SOURCE_REFERENCE_NAME = "source_capture_reference.json"
HISTORICAL_SIGNED_RANGE_SOURCE_IDENTITIES = {
    range_capture.CAPTURE_PROFILE: {
        "declared_capture_implementation_root": (
            "82a4c31cc5e4583ba1d2c30a9a82e8e2681d6b9bb310f36aa48f2d6285c42936"
        ),
        "capture_content_hash": (
            "11c07e34fabb5c599bb2dcd15f46296cd04a046b28b0abe4a75191b325f5e1f8"
        ),
        "contract_id": (
            "60acbb393f104def75987079eb7d374da9869c55ef9fde6af399d9185b9000a1"
        ),
        "request_plan_hash": (
            "a61a9b3126e67e6cd60674fbb0834922b16123f65f25649c13cb997d28b6970a"
        ),
        "source_binding_content_hash": (
            "c3a07464626b86087ea91e31e57dab44ab0e9d8fc9cb6d3a96ba2c3120665386"
        ),
        "legacy_attachment_input_root": (
            "6cd94c298bdb2b199e4d43fe0cce1e01b548bd384bd43cef07404e750f56c51d"
        ),
        "legacy_attachment_request_plan_root": (
            "8f69c8a9093f0dd67b0ce4ff49b5948d7a436e0ffef8aa6232ed26f83a841c3a"
        ),
        "details_content_hash": (
            "69df6bc310eab138b06c553a39fa9e0f5ae34b6de966add6f2c6ed0a191e10ef"
        ),
    },
    range_capture.LEGACY_CAPTURE_PROFILE: {
        "declared_capture_implementation_root": (
            "82a4c31cc5e4583ba1d2c30a9a82e8e2681d6b9bb310f36aa48f2d6285c42936"
        ),
        "capture_content_hash": (
            "06fd455b09738b70a465a5b6b410b84a9156b344376f9b867ed4a7113f3fb936"
        ),
        "contract_id": (
            "b662b1f79b687484f4db38c802c9e172336a0210707612df49891d9aed0ff315"
        ),
        "request_plan_hash": (
            "7673890cd30609dfa3046f389c69031af89863b4105507db66969e27b5dfcb63"
        ),
        "source_binding_content_hash": (
            "b4aeb12e2e075d3e8ba87584df8e343f3321636f00755cbf93402e6771348cda"
        ),
        "legacy_attachment_input_root": (
            "2ac045cc5f10edac751f652fc97667c27950b4782278ae7b871ce458c9f942f5"
        ),
        "legacy_attachment_request_plan_root": (
            "b7705d280e55656637ca9f0d9d34afb49a02f6112ceb18cee716d3b80905429e"
        ),
        "details_content_hash": (
            "69df6bc310eab138b06c553a39fa9e0f5ae34b6de966add6f2c6ed0a191e10ef"
        ),
    },
}
HISTORICAL_RANGE_IMPLEMENTATION_ROOTS = frozenset(
    str(identity["declared_capture_implementation_root"])
    for identity in HISTORICAL_SIGNED_RANGE_SOURCE_IDENTITIES.values()
)
SPECIALIZED_VALIDATION_MODE = "current_semantic_owned_specialized_replay"
SOURCE_VERIFIER_SCHEMA = "csindex_semantic_source_verifier_v1"
CURRENT_PLANNER_ROOT_PROOF_MODE = "current_semantics_rederived"
HISTORICAL_PLANNER_ROOT_PROOF_MODE = (
    "signed_historical_exact_source_identity_verified"
)
SEMANTIC_FILE_NAMES = {
    "csindex_attachment_semantic_index": "semantic_index.jsonl",
    "csindex_csi300_change_candidates": "csi300_change_candidates.jsonl",
    "csindex_csi300_change_row_dispositions": "row_dispositions.jsonl",
    "csindex_csi300_sheet_dispositions": "sheet_dispositions.jsonl",
}
SEMANTIC_ARTIFACT_FIELDS = frozenset(
    {"relative_path", "schema_version", "row_count", "sha256", "size_bytes"}
)
SEMANTIC_SAFETY_FLAGS = (
    "data_admission_eligible",
    "profile_activation_authorized",
    "alpha_search_authorized",
    "holdout_activation_authorized",
    "paper_trading_authorized",
    "shadow_trading_authorized",
    "live_trading_authorized",
)
SOURCE_NORMALIZED_ROLES = frozenset(
    {
        "csindex_range_attachment_index",
        "csindex_range_wire_exchange_index",
        "csindex_range_blocked_reference_index",
        "normalized_manifest",
    }
)
SOURCE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "activity_id",
        "contract_id",
        "request_plan_hash",
        "status",
        "request_count",
        "terminal_attempt_count",
        "terminal_counts",
        "resource_usage",
        "capture_journal_event_count",
        "capture_journal_sha256",
        "capture_catalog_count",
        "capture_catalog_sha256",
        "normalized_artifacts",
        "pause_artifacts",
        "mode",
        "raw_capture_replay_eligible",
        "old_lake_mutated",
        "safety",
        "content_hash",
        "generation_id",
        "capture_publication_signature",
    }
)
SEMANTIC_DISPOSITIONS = frozenset(
    {
        "blocked_unsupported_format",
        "blocked_parse_failure",
        "blocked_unsupported_csi300_sheet",
        "blocked_invalid_change_rows",
        "csi300_change_candidates_extracted",
        "blocked_ambiguous_semantics",
        "blocked_no_change_rows",
        "not_csi300_membership_evidence",
    }
)
ROW_TERMINAL_DISPOSITIONS = frozenset(
    {
        "candidate_extracted",
        "blocked_invalid_security_code",
        "blocked_attachment_has_invalid_change_shape",
        "blocked_attachment_has_unsupported_csi300_sheet",
    }
)
SHEET_TERMINAL_DISPOSITIONS = frozenset(
    {
        "blocked_unsupported_csi300_schema",
        "supported_schema_candidate_rows_terminalized",
        "supported_schema_invalid_security_code",
        "supported_schema_without_candidate_rows",
        "blocked_attachment_has_unsupported_csi300_sheet",
        "blocked_supported_schema_invalid_security_code",
        "blocked_attachment_has_invalid_change_shape",
    }
)
ACTIONS = frozenset({"add", "remove"})
SEMANTIC_BLOCKED_REASONS = frozenset(
    {
        "image_ocr_semantic_parser_not_implemented",
        "attachment_format_not_supported",
        "csi300_bearing_sheet_schema_unsupported",
        "supported_change_schema_has_invalid_security_code",
        "supported_change_schema_without_csi300_rows",
        "attachment_has_no_csi300_reference",
        "xlsx_csi300_semantic_schema_unsupported",
        "xls_csi300_semantic_schema_unsupported",
        "xls_cell_type_unsupported",
        "xls_container_limits_invalid",
        "xls_error_cell_unsupported",
        "xls_numeric_cell_invalid",
        "xls_parser_diagnostic_emitted",
        "xls_sheet_dimensions_exceeded",
        "xls_sheet_inventory_invalid",
        "xls_workbook_invalid",
        "xls_worker_diagnostic_emitted",
        "xls_worker_output_invalid",
        "xls_worker_output_limit_exceeded",
        "xls_worker_output_limits_invalid",
        "xls_worker_resource_isolation_unavailable",
        "xls_worker_resource_limit_exceeded",
        "xls_worker_wall_timeout",
        "xlsx_cell_reference_invalid",
        "xlsx_container_invalid",
        "xlsx_container_limits_invalid",
        "xlsx_duplicate_cell_reference",
        "xlsx_duplicate_row_number",
        "xlsx_error_cell_unsupported",
        "xlsx_row_number_invalid",
        "xlsx_shared_string_limit_exceeded",
        "xlsx_shared_string_reference_invalid",
        "xlsx_sheet_inventory_empty",
        "xlsx_sheet_inventory_missing",
        "xlsx_sheet_relationship_invalid",
        "xlsx_workbook_cell_limit_exceeded",
        "xlsx_workbook_parts_missing",
        "xlsx_xml_entity_declaration_rejected",
        "xlsx_xml_part_limit_exceeded",
    }
)
_OLE_COMPOUND_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_SECURITY_CODE = re.compile(r"^[0-9]{1,6}(?:\.0+)?$")
_INDEX_HEADER = frozenset({"指数代码", "INDEXCODE"})
_SECURITY_HEADER = frozenset(
    {"证券代码", "股票代码", "SECURITYCODE", "STOCKCODE", "CODE"}
)
_SECURITY_NAME_HEADER = frozenset(
    {"证券简称", "股票名称", "证券名称", "SECURITYNAME", "STOCKNAME", "NAME"}
)
_ADD_SHEETS = frozenset({"调入", "调入名单", "ADDITION", "ADDITIONS"})
_REMOVE_SHEETS = frozenset({"调出", "调出名单", "DELETION", "DELETIONS"})
_TEMPORAL_BLOCKERS = (
    "historical_known_at_not_proven",
    "effective_at_not_parsed_or_proven",
    "attachment_announcement_edge_not_semantically_proven",
    "csi300_event_chain_completeness_not_proven",
    "csi300_seed_membership_not_proven",
    "historical_weights_not_proven",
)
_XLS_ISOLATION_UNAVAILABLE_BLOCKERS = (
    "legacy_xls_worker_rlimit_isolation_unavailable",
    "legacy_xls_worker_wall_timeout_unavailable",
)


class _SemanticParseBlocked(ValueError):
    pass


@dataclass(frozen=True)
class _Sheet:
    name: str
    ordinal: int
    rows: tuple[tuple[int, Mapping[int, str]], ...]


def _specialized_source_validation(
    manifest_path: Path,
    *,
    profile: str,
    declared_implementation_root: str,
) -> tuple[dict[str, Any], str]:
    """Replay one signed range capture with current semantic-owned rules.

    The declared acquisition implementation root is an immutable field in the
    signed source contract.  A historical root can therefore identify an
    authorized capture, but it never selects code to import or execute.  This
    verifier always applies the current generic, parent-plan, range protocol,
    durable-wire and normalized-artifact validation primitives.
    """

    validated = _validate_signed_range_source_current_semantics(
        manifest_path,
        profile=profile,
        declared_implementation_root=declared_implementation_root,
    )
    expected_request_count = (
        range_capture.EXPECTED_REQUEST_COUNT
        if profile == range_capture.CAPTURE_PROFILE
        else range_capture.EXPECTED_LEGACY_REQUEST_COUNT
    )
    if (
        validated.get("status") != "succeeded"
        or validated.get("capture_profile") != profile
        or validated.get("request_count") != expected_request_count
        or validated.get("strong_details_ancestry_verified") is not True
        or validated.get("range_protocol_verified") is not True
        or validated.get("normalized_artifacts_trusted") is not True
        or validated.get("publication_signature_verified") is not True
        or validated.get("planner_root_proof_mode")
        not in {
            CURRENT_PLANNER_ROOT_PROOF_MODE,
            HISTORICAL_PLANNER_ROOT_PROOF_MODE,
        }
        or not _sha256_text(validated.get("signed_source_identity_root"))
        or validated.get("historical_known_at_proven") is not False
        or validated.get("pit_membership_authorized") is not False
    ):
        raise ValueError("csindex_semantic_specialized_source_invalid")
    return dict(validated), SPECIALIZED_VALIDATION_MODE


def _validate_signed_range_source_current_semantics(
    manifest_path: Path,
    *,
    profile: str,
    declared_implementation_root: str,
) -> dict[str, Any]:
    if profile not in {
        range_capture.CAPTURE_PROFILE,
        range_capture.LEGACY_CAPTURE_PROFILE,
    }:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    if not _authorized_declared_range_implementation_root(
        declared_implementation_root
    ):
        raise ValueError(
            "csindex_semantic_declared_capture_implementation_root_invalid"
        )

    validated = range_capture.validate_free_provider_backfill(manifest_path)
    if validated.get("status") != "succeeded":
        raise ValueError("csindex_range_attachment_capture_blocked")
    resolved_manifest = Path(str(validated.get("manifest_path") or ""))
    root = resolved_manifest.parent
    source_manifest = _read_exact_source_json(resolved_manifest)
    if (
        set(source_manifest) != SOURCE_MANIFEST_FIELDS
        or source_manifest.get("schema_version")
        != range_capture.capture_module.SCHEMA_VERSION
        or any(
            source_manifest.get(key) != validated.get(key)
            for key in (
                "generation_id",
                "content_hash",
                "contract_id",
                "request_plan_hash",
                "status",
                "request_count",
                "normalized_artifacts",
                "resource_usage",
            )
        )
    ):
        raise ValueError("csindex_semantic_source_manifest_invalid")
    _validate_source_generation_tree(root, source_manifest=source_manifest)
    contract = _read_exact_source_json(
        root / range_capture.capture_module.CONTRACT_NAME
    )
    plan = _read_exact_source_json(
        root / range_capture.capture_module.PLAN_NAME
    )
    requests = _validated_source_requests_from_plan(plan)
    if root.parent.parent.resolve() != range_capture._expected_output_root(
        profile
    ).resolve():
        raise ValueError("csindex_range_attachment_output_geometry_invalid")
    range_capture._validate_authorized_contract(
        contract,
        request_count=len(requests),
        expected_profile=profile,
        expected_implementation_root=declared_implementation_root,
    )
    population, binding = _validate_contract_plan_population_binding(
        contract,
        requests=requests,
        profile=profile,
        declared_implementation_root=declared_implementation_root,
    )
    planner_root_proof_mode, signed_source_identity_root = (
        _planner_root_proof(
            validated,
            profile=profile,
            declared_implementation_root=declared_implementation_root,
            binding=binding,
        )
    )
    parent = range_capture._validate_details_parent(resolved_manifest, binding)
    _validate_rebuilt_parent_plan_current_semantics(
        parent,
        population=population,
        requests=requests,
        binding=binding,
        capture_profile=profile,
        declared_implementation_root=declared_implementation_root,
        planner_root_proof_mode=planner_root_proof_mode,
    )

    replayed, replay_root = (
        range_capture.replay_csindex_range_attachment_capture(
            resolved_manifest
        )
    )
    artifact_rows = validated.get("normalized_artifacts")
    if not isinstance(artifact_rows, list) or any(
        type(row) is not dict for row in artifact_rows
    ):
        raise ValueError("csindex_range_attachment_normalized_role_missing")
    artifacts = {
        str(row.get("role") or ""): row for row in artifact_rows
    }
    if len(artifacts) != len(artifact_rows):
        raise ValueError("csindex_range_attachment_normalized_role_missing")
    for role, payload in replayed.items():
        artifact = artifacts.get(role)
        if artifact is None:
            raise ValueError("csindex_range_attachment_normalized_role_missing")
        published_path = root / str(artifact.get("relative_path") or "")
        try:
            published_stat = published_path.lstat()
        except OSError as exc:
            raise ValueError(
                "csindex_range_attachment_normalized_replay_bytes_mismatch"
            ) from exc
        if (
            not stat.S_ISREG(published_stat.st_mode)
            or published_path.is_symlink()
            or published_path.read_bytes() != payload
        ):
            raise ValueError(
                "csindex_range_attachment_normalized_replay_bytes_mismatch"
            )
    durable_artifact = artifacts.get(
        "csindex_range_durable_exchange_journal"
    )
    if (
        durable_artifact is None
        or durable_artifact.get("relative_path")
        != range_capture.DURABLE_EXCHANGE_JOURNAL_NAME
    ):
        raise ValueError("csindex_range_attachment_durable_artifact_missing")
    wire_rows = [
        range_capture._exact_json_object(line)
        for line in replayed[
            "csindex_range_wire_exchange_index"
        ].splitlines()
        if line.strip()
    ]
    try:
        public_key = base64.b64decode(
            str(contract.get("capture_public_key_pem_b64") or ""),
            validate=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "csindex_semantic_source_capture_public_key_invalid"
        ) from exc
    durable_event_count, durable_exchange_count = (
        range_capture._validate_durable_closure(
            root,
            requests=requests,
            public_key=public_key,
            expected_wire_rows=wire_rows,
        )
    )
    terminal_exchange_count, terminal_attempt_map = (
        range_capture._validate_all_terminal_attempt_counts(
            root,
            requests=requests,
            public_key=public_key,
            expected_wire_rows=wire_rows,
        )
    )
    range_capture._validate_durable_attempt_map(
        wire_rows,
        terminal_attempt_map=terminal_attempt_map,
    )
    try:
        normalized_manifest = json.loads(replayed["normalized_manifest"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "csindex_range_attachment_normalized_manifest_invalid"
        ) from exc
    if (
        type(normalized_manifest) is not dict
        or normalized_manifest.get("source_binding") != binding
        or normalized_manifest.get("population_count") != len(population)
        or normalized_manifest.get("attachment_count") != len(requests)
        or normalized_manifest.get("profile_complete") is not True
        or normalized_manifest.get("wire_exchange_count")
        != durable_exchange_count
        or normalized_manifest.get("durable_exchange_event_count")
        != durable_event_count
        or terminal_exchange_count != durable_exchange_count
        or (validated.get("resource_usage") or {}).get(
            "wire_exchange_count"
        )
        != durable_exchange_count
        or normalized_manifest.get("historical_known_at_proven") is not False
        or normalized_manifest.get("pit_membership_authorized") is not False
    ):
        raise ValueError("csindex_range_attachment_normalized_manifest_invalid")
    return dict(validated) | {
        "csindex_phase": (
            range_capture.LEGACY_PHASE
            if profile == range_capture.LEGACY_CAPTURE_PROFILE
            else range_capture.PHASE
        ),
        "capture_profile": profile,
        "strong_details_ancestry_verified": bool(
            parent.get("csindex_downstream_eligible") is True
        ),
        "range_protocol_verified": True,
        "normalized_replay_root": replay_root,
        "normalized_artifacts_trusted": True,
        "planner_root_proof_mode": planner_root_proof_mode,
        "signed_source_identity_root": signed_source_identity_root,
        "historical_known_at_proven": False,
        "pit_membership_authorized": False,
        "blockers": [
            range_capture.TEMPORAL_BLOCKER,
            "csi300_attachment_semantic_parser_not_run",
        ],
    }


def _validate_rebuilt_parent_plan_current_semantics(
    parent: Mapping[str, Any],
    *,
    population: Sequence[Mapping[str, Any]],
    requests: Sequence[Any],
    binding: Mapping[str, Any],
    capture_profile: str,
    declared_implementation_root: str,
    planner_root_proof_mode: str,
) -> None:
    """Rebuild parent population and requests with current plan semantics.

    The two ``legacy_attachment_*`` roots are opaque commitments made by the
    signed historical planner.  Their old hashing algorithm is not executed.
    Current code instead revalidates the governed details parent, rebuilds the
    exact population, checks every stable binding field, and regenerates every
    range request from that population plus the signed binding.
    """

    parent_manifest = parent.get("manifest_path")
    if not isinstance(parent_manifest, str) or not parent_manifest:
        raise ValueError("csindex_range_attachment_parent_manifest_missing")
    if capture_profile == range_capture.LEGACY_CAPTURE_PROFILE:
        rebuilt_population, legacy_requests, legacy_input_root = (
            range_capture.csindex_backfill.build_csindex_legacy_cons_repair_plan(
                parent_manifest
            )
        )
    elif capture_profile == range_capture.CAPTURE_PROFILE:
        rebuilt_population, legacy_requests, legacy_input_root = (
            range_capture.csindex_backfill.build_csindex_attachment_plan(
                parent_manifest
            )
        )
    else:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    legacy_plan_root = canonical_hash(
        [request.semantic() for request in legacy_requests]
    )
    current_binding = range_capture._range_source_binding(
        parent,
        population=rebuilt_population,
        legacy_input_root=legacy_input_root,
        legacy_request_plan_root=legacy_plan_root,
        capture_profile=capture_profile,
    )
    historical_only_fields = {
        "content_hash",
        "implementation_root",
        "legacy_attachment_input_root",
        "legacy_attachment_request_plan_root",
    }
    if planner_root_proof_mode == CURRENT_PLANNER_ROOT_PROOF_MODE:
        binding_verified = dict(binding) == current_binding
    elif planner_root_proof_mode == HISTORICAL_PLANNER_ROOT_PROOF_MODE:
        binding_verified = {
            key: value
            for key, value in current_binding.items()
            if key not in historical_only_fields
        } == {
            key: value
            for key, value in binding.items()
            if key not in historical_only_fields
        }
    else:
        binding_verified = False
    rebuilt_requests = range_capture._range_attachment_requests(
        rebuilt_population,
        binding,
    )
    if (
        [dict(row) for row in population] != rebuilt_population
        or not binding_verified
        or binding.get("implementation_root")
        != declared_implementation_root
        or [request.semantic() for request in requests]
        != [request.semantic() for request in rebuilt_requests]
    ):
        raise ValueError("csindex_range_attachment_real_parent_plan_mismatch")


def _planner_root_proof(
    validated: Mapping[str, Any],
    *,
    profile: str,
    declared_implementation_root: str,
    binding: Mapping[str, Any],
) -> tuple[str, str]:
    observed_identity = {
        "declared_capture_implementation_root": (
            declared_implementation_root
        ),
        "capture_content_hash": validated.get("content_hash"),
        "contract_id": validated.get("contract_id"),
        "request_plan_hash": validated.get("request_plan_hash"),
        "source_binding_content_hash": binding.get("content_hash"),
        "legacy_attachment_input_root": binding.get(
            "legacy_attachment_input_root"
        ),
        "legacy_attachment_request_plan_root": binding.get(
            "legacy_attachment_request_plan_root"
        ),
        "details_content_hash": binding.get("details_content_hash"),
    }
    if declared_implementation_root == range_capture._implementation_root():
        return (
            CURRENT_PLANNER_ROOT_PROOF_MODE,
            canonical_hash(observed_identity),
        )
    expected = HISTORICAL_SIGNED_RANGE_SOURCE_IDENTITIES.get(profile)
    if expected is None or observed_identity != expected:
        raise ValueError(
            "csindex_semantic_historical_source_identity_invalid"
        )
    return (
        HISTORICAL_PLANNER_ROOT_PROOF_MODE,
        canonical_hash(observed_identity),
    )


def _validated_source_requests_from_plan(
    plan: Mapping[str, Any],
) -> list[Any]:
    if type(plan) is not dict or set(plan) != {
        "schema_version",
        "request_plan_hash",
        "requests",
    }:
        raise ValueError("csindex_semantic_source_request_plan_invalid")
    request_rows = plan.get("requests")
    if (
        plan.get("schema_version")
        != range_capture.capture_module.PLAN_SCHEMA
        or not isinstance(request_rows, list)
        or any(type(row) is not dict for row in request_rows)
        or plan.get("request_plan_hash") != canonical_hash(request_rows)
    ):
        raise ValueError("csindex_semantic_source_request_plan_invalid")
    requests = [
        range_capture._request_from_semantic(row) for row in request_rows
    ]
    if [request.semantic() for request in requests] != request_rows:
        raise ValueError("csindex_semantic_source_request_plan_invalid")
    return requests


def _read_exact_source_json(path: Path) -> dict[str, Any]:
    try:
        payload = range_capture._exact_json_object(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError("csindex_semantic_source_json_invalid") from exc
    if type(payload) is not dict:
        raise ValueError("csindex_semantic_source_json_invalid")
    return payload


def _validate_source_generation_tree(
    root: Path,
    *,
    source_manifest: Mapping[str, Any],
) -> None:
    expected_files = {
        range_capture.capture_module.CONTRACT_NAME,
        range_capture.capture_module.PLAN_NAME,
        range_capture.capture_module.JOURNAL_NAME,
        range_capture.capture_module.CATALOG_NAME,
        range_capture.capture_module.MANIFEST_NAME,
    }
    catalog_path = root / range_capture.capture_module.CATALOG_NAME
    try:
        catalog_rows = [
            range_capture._exact_json_object(line)
            for line in catalog_path.read_bytes().splitlines()
            if line.strip()
        ]
    except (OSError, ValueError) as exc:
        raise ValueError("csindex_semantic_source_generation_tree_invalid") from exc
    artifact_rows = list(source_manifest.get("normalized_artifacts") or ())
    pause_rows = list(source_manifest.get("pause_artifacts") or ())
    if any(
        type(row) is not dict
        for row in [*catalog_rows, *artifact_rows, *pause_rows]
    ):
        raise ValueError("csindex_semantic_source_generation_tree_invalid")
    for row in [*catalog_rows, *artifact_rows, *pause_rows]:
        relative_path = row.get("relative_path")
        if (
            type(relative_path) is not str
            or not relative_path
            or relative_path.startswith("/")
            or "\\" in relative_path
            or any(
                part in {"", ".", ".."}
                for part in relative_path.split("/")
            )
            or posixpath.normpath(relative_path) != relative_path
        ):
            raise ValueError(
                "csindex_semantic_source_generation_tree_invalid"
            )
        expected_files.add(relative_path)
    expected_directories: set[str] = set()
    for relative_path in expected_files:
        parent = posixpath.dirname(relative_path)
        while parent:
            expected_directories.add(parent)
            parent = posixpath.dirname(parent)

    actual_files: set[str] = set()
    actual_directories: set[str] = set()

    def visit(directory: Path, prefix: str = "") -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ValueError(
                "csindex_semantic_source_generation_tree_invalid"
            ) from exc
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError(
                    "csindex_semantic_source_generation_tree_invalid"
                ) from exc
            if stat.S_ISREG(metadata.st_mode):
                actual_files.add(relative)
            elif stat.S_ISDIR(metadata.st_mode) and not entry.is_symlink():
                actual_directories.add(relative)
                visit(Path(entry.path), relative)
            else:
                raise ValueError(
                    "csindex_semantic_source_generation_tree_invalid"
                )

    visit(root)
    if (
        actual_files != expected_files
        or actual_directories != expected_directories
    ):
        raise ValueError("csindex_semantic_source_generation_tree_invalid")


def _validate_contract_plan_population_binding(
    contract: Mapping[str, Any],
    *,
    requests: Sequence[Any],
    profile: str,
    declared_implementation_root: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    population, binding = range_capture._request_plan_evidence(requests)
    adapter = contract.get("adapter_identity") or {}
    expected_population_root = canonical_hash(
        {
            "population": population,
            "input_capture_content_hash": binding["content_hash"],
        }
    )
    if (
        binding.get("range_capture_profile") != profile
        or adapter.get("capture_profile") != profile
        or adapter.get("implementation_root")
        != declared_implementation_root
        or binding.get("implementation_root")
        != declared_implementation_root
        or adapter.get("input_capture_content_hash")
        != binding["content_hash"]
        or adapter.get("source_details_generation_id")
        != binding.get("details_generation_id")
        or adapter.get("source_details_content_hash")
        != binding.get("details_content_hash")
        or contract.get("population_root") != expected_population_root
    ):
        raise ValueError(
            "csindex_range_attachment_contract_source_binding_invalid"
        )
    range_capture._validate_population_geometry(
        population,
        requests,
        capture_profile=profile,
    )
    return population, binding


def _authorized_declared_range_implementation_root(value: str) -> bool:
    return value == range_capture._implementation_root() or (
        value in HISTORICAL_RANGE_IMPLEMENTATION_ROOTS
    )


def _source_verifier_implementation_root() -> str:
    return canonical_hash(
        {
            "schema_version": SOURCE_VERIFIER_SCHEMA,
            "semantic_module_sha256": _module_source_sha256(),
            "range_capture_module_sha256": sha256_file(
                Path(range_capture.__file__)
            ),
            "generic_capture_module_sha256": sha256_file(
                Path(range_capture.capture_module.__file__)
            ),
            "details_capture_module_sha256": sha256_file(
                Path(range_capture.csindex_backfill.__file__)
            ),
            "historical_signed_source_identities": (
                HISTORICAL_SIGNED_RANGE_SOURCE_IDENTITIES
            ),
            "validation_mode": SPECIALIZED_VALIDATION_MODE,
        }
    )


def _iter_verified_range_attachments(
    path: str | Path,
    *,
    required_profile: str | None = None,
) -> tuple[
    Iterator[range_capture.ReplayedRangeAttachment],
    str,
    dict[str, Any],
    dict[str, Any],
]:
    """Validate a locked capture, then reconstruct one logical object at a time.

    This semantic-owned reader intentionally does not change the acquisition
    module whose complete source hash is already bound into signed CSI capture
    generations.  Only the fixed-size request/index metadata (at most 439
    entries) remains resident across yields.
    """

    generic = range_capture.validate_free_provider_backfill(path)
    manifest_path = Path(str(generic["manifest_path"]))
    root = manifest_path.parent
    contract_path = root / range_capture.capture_module.CONTRACT_NAME
    contract = _read_exact_source_json(contract_path)
    profile = str(
        (contract.get("adapter_identity") or {}).get("capture_profile") or ""
    )
    if profile not in {
        range_capture.CAPTURE_PROFILE,
        range_capture.LEGACY_CAPTURE_PROFILE,
    }:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    if required_profile is not None and profile != required_profile:
        raise ValueError("csindex_semantic_source_capture_profile_mismatch")
    declared_implementation_root = str(
        (contract.get("adapter_identity") or {}).get("implementation_root")
        or ""
    )
    specialized, specialized_validation_mode = _specialized_source_validation(
        manifest_path,
        profile=profile,
        declared_implementation_root=declared_implementation_root,
    )
    generic = specialized
    range_capture._validate_authorized_contract(
        contract,
        request_count=int(generic.get("request_count") or -1),
        expected_profile=profile,
        expected_implementation_root=declared_implementation_root,
    )
    if (
        generic.get("status") != "succeeded"
        or generic.get("publication_signature_verified") is not True
        or generic.get("normalized_artifacts_trusted") is not True
    ):
        raise ValueError("csindex_semantic_source_capture_unverified")
    normalized, _normalized_root = (
        range_capture.replay_csindex_range_attachment_capture(manifest_path)
    )
    published_by_role = {
        str(row.get("role") or ""): row
        for row in generic.get("normalized_artifacts") or ()
        if isinstance(row, Mapping)
    }
    if (
        set(normalized) != SOURCE_NORMALIZED_ROLES
        or set(normalized) - set(published_by_role)
    ):
        raise ValueError("csindex_semantic_source_normalized_role_missing")
    normalized_inventory: dict[str, dict[str, Any]] = {}
    for role, payload in normalized.items():
        artifact = published_by_role[role]
        published_path = root / str(artifact.get("relative_path") or "")
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        if (
            not published_path.is_file()
            or published_path.is_symlink()
            or payload_sha256 != artifact.get("sha256")
            or published_path.stat().st_size != len(payload)
            or published_path.read_bytes() != payload
        ):
            raise ValueError(
                "csindex_semantic_source_normalized_replay_mismatch"
            )
        normalized_inventory[role] = {
            "relative_path": str(artifact.get("relative_path") or ""),
            "sha256": payload_sha256,
            "size_bytes": len(payload),
        }
    plan_path = root / range_capture.capture_module.PLAN_NAME
    plan = _read_exact_source_json(plan_path)
    request_rows = plan.get("requests")
    if (
        not isinstance(request_rows, list)
        or not request_rows
        or len(request_rows) > range_capture.EXPECTED_REQUEST_COUNT
        or any(type(row) is not dict for row in request_rows)
    ):
        raise ValueError("csindex_semantic_source_request_plan_invalid")
    requests = tuple(
        range_capture._request_from_semantic(row) for row in request_rows
    )
    _source_population, range_source_binding = (
        range_capture._request_plan_evidence(requests)
    )
    terminal: dict[str, dict[str, Any]] = {}
    journal_path = root / range_capture.capture_module.JOURNAL_NAME
    with journal_path.open("rb") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = range_capture._exact_json_object(line)
            if row.get("event_type") == "capture_attempt_terminal":
                request_id = str(row.get("request_id") or "")
                if not request_id or request_id in terminal:
                    raise ValueError(
                        "csindex_range_attachment_terminal_count_invalid"
                    )
                terminal[request_id] = row
    if set(terminal) != {request.request_id for request in requests}:
        raise ValueError("csindex_range_attachment_terminal_count_invalid")
    public_key = range_capture._capture_public_key_from_terminal(terminal)
    index_payload = normalized.get("csindex_range_attachment_index")
    if index_payload is None:
        raise ValueError("csindex_range_attachment_index_identity_invalid")
    index_rows: dict[str, dict[str, Any]] = {}
    normalized_index_rows: list[dict[str, Any]] = []
    for line in index_payload.splitlines():
        if not line.strip():
            continue
        row = range_capture._exact_json_object(line)
        request_id = str(row.get("source_request_id") or "")
        if not request_id or request_id in index_rows:
            raise ValueError(
                "csindex_range_attachment_index_identity_invalid"
            )
        index_rows[request_id] = row
        normalized_index_rows.append(row)
    if set(index_rows) != set(terminal):
        raise ValueError("csindex_range_attachment_index_identity_invalid")
    replay_rows: list[dict[str, Any]] = []
    for request in requests:
        index_row = index_rows[request.request_id]
        announcements = index_row.get("source_announcements")
        attachment_size = index_row.get("attachment_size_bytes")
        if (
            index_row.get("attachment_url") != request.url
            or not _sha256_text(index_row.get("attachment_sha256"))
            or not _sha256_text(
                index_row.get("source_logical_payload_sha256")
            )
            or type(attachment_size) is not int
            or not 0 <= attachment_size <= range_capture.ATTACHMENT_BODY_MAX_BYTES
            or not isinstance(announcements, list)
            or any(type(row) is not dict for row in announcements)
        ):
            raise ValueError("csindex_range_attachment_body_replay_mismatch")
        replay_rows.append(
            {
                "source_request_id": request.request_id,
                "attachment_url": request.url,
                "attachment_extension": str(
                    index_row.get("attachment_extension") or ""
                ),
                "attachment_sha256": str(index_row["attachment_sha256"]),
                "source_logical_payload_sha256": str(
                    index_row["source_logical_payload_sha256"]
                ),
                "source_announcements": announcements,
                "attachment_size_bytes": attachment_size,
            }
        )
    replay_root = canonical_hash(
        {
            "schema_version": "csindex_range_attachment_body_replay_v1",
            "capture_content_hash": generic["content_hash"],
            "attachments": replay_rows,
        }
    )
    adapter = contract.get("adapter_identity") or {}
    source_capture_binding = {
        "schema_version": SOURCE_CAPTURE_BINDING_SCHEMA,
        "provider": "csindex",
        "capture_profile": profile,
        "generation_id": generic["generation_id"],
        "content_hash": generic["content_hash"],
        "manifest_name": range_capture.capture_module.MANIFEST_NAME,
        "manifest_relative_reference": (
            f"generations/{generic['generation_id']}/"
            f"{range_capture.capture_module.MANIFEST_NAME}"
        ),
        "manifest_sha256": sha256_file(manifest_path),
        "capture_status": "succeeded",
        "publication_signature_verified": True,
        "normalized_artifacts_trusted": True,
        "specialized_validator_verified": True,
        "specialized_validation_mode": specialized_validation_mode,
        "specialized_validator_identity_root": (
            _source_verifier_implementation_root()
        ),
        "planner_root_proof_mode": generic["planner_root_proof_mode"],
        "signed_source_identity_root": generic[
            "signed_source_identity_root"
        ],
        "legacy_attachment_input_root": range_source_binding[
            "legacy_attachment_input_root"
        ],
        "legacy_attachment_request_plan_root": range_source_binding[
            "legacy_attachment_request_plan_root"
        ],
        "strong_details_ancestry_verified": True,
        "contract_id": generic["contract_id"],
        "contract_sha256": sha256_file(contract_path),
        "request_plan_hash": generic["request_plan_hash"],
        "request_plan_sha256": sha256_file(plan_path),
        "request_count": len(requests),
        "normalized_replay_root": _normalized_root,
        "normalized_artifact_inventory": dict(
            sorted(normalized_inventory.items())
        ),
        "normalized_artifact_inventory_root": canonical_hash(
            normalized_inventory
        ),
        "declared_capture_implementation_root": adapter.get(
            "implementation_root"
        ),
        "input_capture_content_hash": adapter.get(
            "input_capture_content_hash"
        ),
        "source_details_generation_id": adapter.get(
            "source_details_generation_id"
        ),
        "source_details_content_hash": adapter.get(
            "source_details_content_hash"
        ),
        "capture_public_key_sha256": contract.get(
            "capture_public_key_sha256"
        ),
        "permission_context_id": contract.get("permission_context_id"),
        "source_namespace_id": contract.get("output_namespace_id"),
        "source_reference_resolution_policy": (
            "caller_supplied_source_capture_or_controlled_admission_root"
        ),
        "independent_data_admission_requires_source_reference_resolution": (
            True
        ),
    }
    _validate_source_capture_binding(source_capture_binding)
    source_reference = {
        "schema_version": SOURCE_REFERENCE_SCHEMA,
        "source_capture_binding": source_capture_binding,
        "signed_source_manifest": read_json(manifest_path),
        "source_contract": contract,
        "source_request_plan": plan,
        "normalized_attachment_index_rows": normalized_index_rows,
    }
    _validate_source_reference_payload(
        source_reference,
        expected_binding=source_capture_binding,
    )

    def one_logical_object() -> Iterator[range_capture.ReplayedRangeAttachment]:
        for request, replay_row in zip(requests, replay_rows, strict=True):
            receipt = terminal[request.request_id]
            wrapper = range_capture._read_exact_json(
                root
                / str(receipt.get("raw_envelope_relative_path") or "")
            )
            raw = range_capture._raw_logical_payload(
                wrapper,
                request=request,
                terminal=receipt,
            )
            body, _exchanges, _method, _etag = (
                range_capture._validate_and_assemble_logical(
                    raw,
                    request=request,
                    public_key=public_key,
                    expected_attempt_id=str(receipt.get("attempt_id") or ""),
                    expected_retry_ordinal=int(
                        receipt.get("retry_ordinal", -1)
                    ),
                )
            )
            body_hash = hashlib.sha256(body).hexdigest()
            raw_hash = hashlib.sha256(raw).hexdigest()
            if (
                body_hash != replay_row["attachment_sha256"]
                or len(body) != replay_row["attachment_size_bytes"]
                or raw_hash != replay_row["source_logical_payload_sha256"]
            ):
                raise ValueError(
                    "csindex_range_attachment_body_replay_mismatch"
                )
            yield range_capture.ReplayedRangeAttachment(
                source_request_id=request.request_id,
                attachment_url=request.url,
                attachment_extension=str(
                    replay_row["attachment_extension"]
                ),
                attachment_sha256=body_hash,
                source_logical_payload_sha256=raw_hash,
                source_announcements=tuple(
                    dict(row) for row in replay_row["source_announcements"]
                ),
                body=body,
            )

    return (
        one_logical_object(),
        replay_root,
        source_capture_binding,
        source_reference,
    )


def replay_csindex_range_attachment_semantics(
    path: str | Path,
) -> tuple[dict[str, bytes], str]:
    """Replay deterministic semantic artifacts from one signed range capture."""

    (
        attachments,
        body_replay_root,
        _source_capture_binding,
        _source_reference,
    ) = (
        _iter_verified_range_attachments(path)
    )
    semantic_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    row_dispositions: list[dict[str, Any]] = []
    sheet_dispositions: list[dict[str, Any]] = []
    source_capture_hashes: set[str] = set()
    source_attachment_count = 0
    xls_attachment_count = 0
    for attachment in attachments:
        source_attachment_count += 1
        source_capture_hashes.add(attachment.attachment_sha256)
        xls_attachment_count += (
            attachment.attachment_extension.lower() == "xls"
        )
        semantic, candidates, terminal_rows, terminal_sheets = _parse_attachment(
            attachment
        )
        semantic_rows.append(semantic)
        candidate_rows.extend(candidates)
        row_dispositions.extend(terminal_rows)
        sheet_dispositions.extend(terminal_sheets)

    semantic_rows.sort(key=lambda row: str(row["source_request_id"]))
    candidate_rows.sort(
        key=lambda row: (
            str(row["source_request_id"]),
            int(row["source_sheet_ordinal"]),
            int(row["source_row_number"]),
            str(row["action"]),
            str(row["security_code"]),
        )
    )
    row_dispositions.sort(
        key=lambda row: (
            str(row["source_request_id"]),
            int(row["source_sheet_ordinal"]),
            int(row["source_row_number"]),
            str(row["action"]),
        )
    )
    sheet_dispositions.sort(
        key=lambda row: (
            str(row["source_request_id"]),
            int(row["source_sheet_ordinal"]),
        )
    )
    _validate_candidate_terminal_closure(candidate_rows, row_dispositions)
    semantic_payload = _jsonl_bytes(semantic_rows)
    candidate_payload = _jsonl_bytes(candidate_rows)
    row_disposition_payload = _jsonl_bytes(row_dispositions)
    sheet_disposition_payload = _jsonl_bytes(sheet_dispositions)
    disposition_counts: dict[str, int] = {}
    blocked_reason_counts: dict[str, int] = {}
    for row in semantic_rows:
        disposition = str(row["semantic_disposition"])
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
        reason = row.get("blocked_reason")
        if reason is not None:
            key = str(reason)
            blocked_reason_counts[key] = blocked_reason_counts.get(key, 0) + 1
    sorted_source_capture_hashes = sorted(source_capture_hashes)
    worker_isolation_available = _xls_worker_isolation_available()
    row_disposition_counts: dict[str, int] = {}
    for row in row_dispositions:
        terminal = str(row["terminal_disposition"])
        row_disposition_counts[terminal] = (
            row_disposition_counts.get(terminal, 0) + 1
        )
    sheet_disposition_counts: dict[str, int] = {}
    for row in sheet_dispositions:
        terminal = str(row["terminal_disposition"])
        sheet_disposition_counts[terminal] = (
            sheet_disposition_counts.get(terminal, 0) + 1
        )
    runtime_blockers = (
        list(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
        if xls_attachment_count and not worker_isolation_available
        else []
    )
    manifest_semantic = {
        "schema_version": SEMANTIC_SCHEMA,
        "parser_identity": PARSER_IDENTITY,
        "parser_components": _parser_components(),
        "parser_implementation_root": _implementation_root(),
        "source_body_replay_root": body_replay_root,
        "source_attachment_count": source_attachment_count,
        "source_attachment_hash_set_root": canonical_hash(
            sorted_source_capture_hashes
        ),
        "semantic_index_count": len(semantic_rows),
        "semantic_index_sha256": hashlib.sha256(semantic_payload).hexdigest(),
        "candidate_count": len(candidate_rows),
        "candidate_sha256": hashlib.sha256(candidate_payload).hexdigest(),
        "row_disposition_count": len(row_dispositions),
        "row_disposition_sha256": hashlib.sha256(
            row_disposition_payload
        ).hexdigest(),
        "row_disposition_counts": dict(sorted(row_disposition_counts.items())),
        "csi300_bearing_sheet_count": len(sheet_dispositions),
        "sheet_disposition_sha256": hashlib.sha256(
            sheet_disposition_payload
        ).hexdigest(),
        "sheet_disposition_counts": dict(
            sorted(sheet_disposition_counts.items())
        ),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "blocked_reason_counts": dict(sorted(blocked_reason_counts.items())),
        "historical_known_at_proven": False,
        "effective_at_proven": False,
        "event_chain_complete": False,
        "seed_membership_proven": False,
        "historical_weights_proven": False,
        "xls_attachment_count": xls_attachment_count,
        "xls_parser_runtime_isolation_proven": worker_isolation_available,
        "xls_parser_os_timeout_enforced": worker_isolation_available,
        "xls_worker_limits": _xls_worker_limits(),
        "runtime_isolation_blockers": runtime_blockers,
        "pit_membership_authorized": False,
        "data_admission_eligible": False,
        "alpha_search_authorized": False,
        "blockers": list(_TEMPORAL_BLOCKERS) + runtime_blockers,
    }
    manifest = manifest_semantic | {
        "content_hash": canonical_hash(manifest_semantic)
    }
    manifest_payload = _json_bytes(manifest)
    artifacts = {
        "csindex_attachment_semantic_index": semantic_payload,
        "csindex_csi300_change_candidates": candidate_payload,
        "csindex_csi300_change_row_dispositions": row_disposition_payload,
        "csindex_csi300_sheet_dispositions": sheet_disposition_payload,
        "semantic_manifest": manifest_payload,
    }
    replay_root = canonical_hash(
        {
            "schema_version": "csindex_attachment_semantic_artifact_set_v5",
            "roles": {
                role: {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
                for role, payload in sorted(artifacts.items())
            },
        }
    )
    return artifacts, replay_root


def build_csindex_attachment_semantic_evidence(
    capture: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Stream one signed CSI range capture into immutable semantic evidence."""

    output = Path(output_root)
    if _has_symlink_component(output):
        raise ValueError("csindex_semantic_output_symlink_forbidden")
    output.mkdir(parents=True, exist_ok=True)
    (
        attachments,
        body_replay_root,
        source_capture_binding,
        source_reference,
    ) = (
        _iter_verified_range_attachments(
            capture,
            required_profile=range_capture.CAPTURE_PROFILE,
        )
    )
    with tempfile.TemporaryDirectory(
        prefix=".csindex-semantics-prepared-",
        dir=output,
    ) as temporary_name:
        temporary = Path(temporary_name)
        working = temporary / "working"
        working.mkdir()
        summary = _stream_semantic_artifacts(attachments, working)
        source_reference_payload = _json_bytes(source_reference)
        source_reference_path = working / SOURCE_REFERENCE_NAME
        with source_reference_path.open("xb") as handle:
            handle.write(source_reference_payload)
            handle.flush()
            os.fsync(handle.fileno())
        source_reference_artifact = {
            "relative_path": SOURCE_REFERENCE_NAME,
            "schema_version": SOURCE_REFERENCE_SCHEMA,
            "sha256": hashlib.sha256(source_reference_payload).hexdigest(),
            "size_bytes": len(source_reference_payload),
            "content_root": canonical_hash(source_reference),
        }
        semantic = _semantic_evidence_manifest(
            body_replay_root=body_replay_root,
            source_capture_binding=source_capture_binding,
            source_reference_artifact=source_reference_artifact,
            summary=summary,
        )
        content_hash = canonical_hash(semantic)
        generation_id = (
            f"{SEMANTIC_GENERATION_PREFIX}_{content_hash[:24]}"
        )
        atomic_json(
            working / SEMANTIC_MANIFEST_NAME,
            semantic
            | {
                "content_hash": content_hash,
                "generation_id": generation_id,
            },
        )
        prepared = temporary / generation_id
        os.replace(working, prepared)
        return publish_prepared_generation(
            output,
            prepared_directory=prepared,
            manifest_name=SEMANTIC_MANIFEST_NAME,
            validator=lambda manifest: (
                validate_csindex_attachment_semantic_evidence(
                    manifest,
                    source_capture=capture,
                )
            ),
            pointer_schema=SEMANTIC_POINTER_SCHEMA,
            pointer_fields={
                "pit_membership_authorized": False,
                "data_admission_eligible": False,
            },
        )


def _stream_semantic_artifacts(
    attachments: Iterator[range_capture.ReplayedRangeAttachment],
    directory: Path,
) -> dict[str, Any]:
    paths = {
        role: directory / name
        for role, name in SEMANTIC_FILE_NAMES.items()
    }
    counts = {role: 0 for role in paths}
    disposition_counts: dict[str, int] = {}
    blocked_reason_counts: dict[str, int] = {}
    row_disposition_counts: dict[str, int] = {}
    sheet_disposition_counts: dict[str, int] = {}
    source_hashes: set[str] = set()
    xls_attachment_count = 0
    with ExitStack() as stack:
        handles = {
            role: stack.enter_context(path.open("xb"))
            for role, path in paths.items()
        }
        for attachment in attachments:
            semantic, candidates, row_dispositions, sheet_dispositions = (
                _parse_attachment(attachment)
            )
            candidates.sort(
                key=lambda row: (
                    int(row["source_sheet_ordinal"]),
                    int(row["source_row_number"]),
                    str(row["action"]),
                    int(row["source_security_column"]),
                    str(row["security_code"]),
                )
            )
            row_dispositions.sort(
                key=lambda row: (
                    int(row["source_sheet_ordinal"]),
                    int(row["source_row_number"]),
                    str(row["action"]),
                    int(row["source_security_column"]),
                    str(row.get("canonical_security_code") or ""),
                )
            )
            sheet_dispositions.sort(
                key=lambda row: int(row["source_sheet_ordinal"])
            )
            _validate_candidate_terminal_closure(
                candidates,
                row_dispositions,
            )
            rows_by_role = {
                "csindex_attachment_semantic_index": [semantic],
                "csindex_csi300_change_candidates": candidates,
                "csindex_csi300_change_row_dispositions": row_dispositions,
                "csindex_csi300_sheet_dispositions": sheet_dispositions,
            }
            for role, rows in rows_by_role.items():
                for row in rows:
                    handles[role].write(_json_bytes(row))
                    counts[role] += 1
            disposition = str(semantic["semantic_disposition"])
            disposition_counts[disposition] = (
                disposition_counts.get(disposition, 0) + 1
            )
            reason = semantic.get("blocked_reason")
            if reason is not None:
                blocked_reason_counts[str(reason)] = (
                    blocked_reason_counts.get(str(reason), 0) + 1
                )
            for row in row_dispositions:
                terminal = str(row["terminal_disposition"])
                row_disposition_counts[terminal] = (
                    row_disposition_counts.get(terminal, 0) + 1
                )
            for row in sheet_dispositions:
                terminal = str(row["terminal_disposition"])
                sheet_disposition_counts[terminal] = (
                    sheet_disposition_counts.get(terminal, 0) + 1
                )
            source_hashes.add(attachment.attachment_sha256)
            xls_attachment_count += (
                attachment.attachment_extension.lower() == "xls"
            )
        for handle in handles.values():
            handle.flush()
            os.fsync(handle.fileno())
    inventory = {
        role: {
            "relative_path": path.name,
            "schema_version": _role_schema(role),
            "row_count": counts[role],
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for role, path in sorted(paths.items())
    }
    return {
        "artifact_inventory": inventory,
        "source_attachment_hashes": sorted(source_hashes),
        "xls_attachment_count": xls_attachment_count,
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "blocked_reason_counts": dict(sorted(blocked_reason_counts.items())),
        "row_disposition_counts": dict(
            sorted(row_disposition_counts.items())
        ),
        "sheet_disposition_counts": dict(
            sorted(sheet_disposition_counts.items())
        ),
    }


def _semantic_evidence_manifest(
    *,
    body_replay_root: str,
    source_capture_binding: Mapping[str, Any],
    source_reference_artifact: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    inventory = dict(summary["artifact_inventory"])
    xls_attachment_count = int(summary["xls_attachment_count"])
    worker_isolation_available = _xls_worker_isolation_available()
    runtime_blockers = (
        list(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
        if xls_attachment_count and not worker_isolation_available
        else []
    )
    safety = {name: False for name in SEMANTIC_SAFETY_FLAGS}
    return {
        "schema_version": SEMANTIC_SCHEMA,
        "artifact_set_schema_version": SEMANTIC_ARTIFACT_SET_SCHEMA,
        "parser_identity": PARSER_IDENTITY,
        "parser_components": _parser_components(),
        "parser_implementation_root": _implementation_root(),
        "source_body_replay_root": body_replay_root,
        "source_capture_binding": dict(source_capture_binding),
        "source_capture_binding_root": canonical_hash(
            source_capture_binding
        ),
        "planner_root_proof_mode": source_capture_binding[
            "planner_root_proof_mode"
        ],
        "signed_source_identity_root": source_capture_binding[
            "signed_source_identity_root"
        ],
        "source_reference_artifact": dict(source_reference_artifact),
        "source_attachment_count": inventory[
            "csindex_attachment_semantic_index"
        ]["row_count"],
        "source_attachment_hash_set_root": canonical_hash(
            summary["source_attachment_hashes"]
        ),
        "artifact_inventory": inventory,
        "artifact_set_root": canonical_hash(inventory),
        "technical_processing_status": "succeeded",
        "semantic_index_count": inventory[
            "csindex_attachment_semantic_index"
        ]["row_count"],
        "candidate_count": inventory[
            "csindex_csi300_change_candidates"
        ]["row_count"],
        "row_disposition_count": inventory[
            "csindex_csi300_change_row_dispositions"
        ]["row_count"],
        "csi300_bearing_sheet_count": inventory[
            "csindex_csi300_sheet_dispositions"
        ]["row_count"],
        "disposition_counts": summary["disposition_counts"],
        "blocked_reason_counts": summary["blocked_reason_counts"],
        "row_disposition_counts": summary["row_disposition_counts"],
        "sheet_disposition_counts": summary["sheet_disposition_counts"],
        "historical_known_at_proven": False,
        "effective_at_proven": False,
        "event_chain_complete": False,
        "seed_membership_proven": False,
        "historical_weights_proven": False,
        "xls_attachment_count": xls_attachment_count,
        "xls_parser_runtime_isolation_proven": worker_isolation_available,
        "xls_parser_os_timeout_enforced": worker_isolation_available,
        "xls_worker_limits": _xls_worker_limits(),
        "runtime_isolation_blockers": runtime_blockers,
        "bounded_processing": {
            "attachment_iteration": "one_verified_body_at_a_time",
            "resident_attachment_scope_limit": 1,
            "per_attachment_body_limit_bytes": (
                range_capture.ATTACHMENT_BODY_MAX_BYTES
            ),
            "per_logical_envelope_limit_bytes": (
                range_capture.MAX_LOGICAL_ENVELOPE_BYTES
            ),
            "contract_total_response_limit_bytes": (
                range_capture.MAX_TOTAL_RESPONSE_BYTES
            ),
        },
        "pit_membership_authorized": False,
        "data_admission_eligible": False,
        "alpha_search_authorized": False,
        "safety": safety,
        "blockers": list(_TEMPORAL_BLOCKERS) + runtime_blockers,
    }


def _validate_source_capture_binding(value: Mapping[str, Any]) -> None:
    expected_fields = {
        "schema_version",
        "provider",
        "capture_profile",
        "generation_id",
        "content_hash",
        "manifest_name",
        "manifest_relative_reference",
        "manifest_sha256",
        "capture_status",
        "publication_signature_verified",
        "normalized_artifacts_trusted",
        "specialized_validator_verified",
        "specialized_validation_mode",
        "specialized_validator_identity_root",
        "planner_root_proof_mode",
        "signed_source_identity_root",
        "legacy_attachment_input_root",
        "legacy_attachment_request_plan_root",
        "strong_details_ancestry_verified",
        "contract_id",
        "contract_sha256",
        "request_plan_hash",
        "request_plan_sha256",
        "request_count",
        "normalized_replay_root",
        "normalized_artifact_inventory",
        "normalized_artifact_inventory_root",
        "declared_capture_implementation_root",
        "input_capture_content_hash",
        "source_details_generation_id",
        "source_details_content_hash",
        "capture_public_key_sha256",
        "permission_context_id",
        "source_namespace_id",
        "source_reference_resolution_policy",
        "independent_data_admission_requires_source_reference_resolution",
    }
    inventory = value.get("normalized_artifact_inventory")
    content_hash = str(value.get("content_hash") or "")
    generation_id = str(value.get("generation_id") or "")
    details_hash = str(value.get("source_details_content_hash") or "")
    observed_source_identity = {
        "declared_capture_implementation_root": value.get(
            "declared_capture_implementation_root"
        ),
        "capture_content_hash": value.get("content_hash"),
        "contract_id": value.get("contract_id"),
        "request_plan_hash": value.get("request_plan_hash"),
        "source_binding_content_hash": value.get(
            "input_capture_content_hash"
        ),
        "legacy_attachment_input_root": value.get(
            "legacy_attachment_input_root"
        ),
        "legacy_attachment_request_plan_root": value.get(
            "legacy_attachment_request_plan_root"
        ),
        "details_content_hash": value.get("source_details_content_hash"),
    }
    expected_relative_paths = {
        "csindex_range_attachment_index": "normalized/attachment_index.jsonl",
        "csindex_range_wire_exchange_index": (
            "normalized/wire_exchange_index.jsonl"
        ),
        "csindex_range_blocked_reference_index": (
            "normalized/blocked_reference_index.jsonl"
        ),
        "normalized_manifest": "normalized/normalized_manifest.json",
    }
    if (
        set(value) != expected_fields
        or value.get("schema_version") != SOURCE_CAPTURE_BINDING_SCHEMA
        or value.get("provider") != "csindex"
        or value.get("capture_profile")
        not in {
            range_capture.CAPTURE_PROFILE,
            range_capture.LEGACY_CAPTURE_PROFILE,
        }
        or not _sha256_text(content_hash)
        or generation_id
        != f"{range_capture.capture_module.GENERATION_PREFIX}_{content_hash[:24]}"
        or value.get("manifest_name")
        != range_capture.capture_module.MANIFEST_NAME
        or value.get("manifest_relative_reference")
        != (
            f"generations/{generation_id}/"
            f"{range_capture.capture_module.MANIFEST_NAME}"
        )
        or value.get("capture_status") != "succeeded"
        or value.get("publication_signature_verified") is not True
        or value.get("normalized_artifacts_trusted") is not True
        or value.get("specialized_validator_verified") is not True
        or value.get("specialized_validation_mode")
        != SPECIALIZED_VALIDATION_MODE
        or value.get("strong_details_ancestry_verified") is not True
        or value.get("specialized_validator_identity_root")
        != _source_verifier_implementation_root()
        or value.get("planner_root_proof_mode")
        not in {
            CURRENT_PLANNER_ROOT_PROOF_MODE,
            HISTORICAL_PLANNER_ROOT_PROOF_MODE,
        }
        or value.get("signed_source_identity_root")
        != canonical_hash(observed_source_identity)
        or type(value.get("request_count")) is not int
        or not 0 < int(value["request_count"]) <= range_capture.EXPECTED_REQUEST_COUNT
        or value.get("capture_public_key_sha256")
        != range_capture.APPROVED_CAPTURE_KEY_SHA256
        or value.get("permission_context_id")
        != range_capture.DEFAULT_PERMISSION_CONTEXT
        or not _sha256_text(value.get("source_namespace_id"))
        or value.get("source_reference_resolution_policy")
        != "caller_supplied_source_capture_or_controlled_admission_root"
        or value.get(
            "independent_data_admission_requires_source_reference_resolution"
        )
        is not True
        or not isinstance(inventory, Mapping)
        or set(inventory) != SOURCE_NORMALIZED_ROLES
        or value.get("normalized_artifact_inventory_root")
        != canonical_hash(inventory)
        or not _sha256_text(details_hash)
        or value.get("source_details_generation_id")
        != (
            f"{range_capture.capture_module.GENERATION_PREFIX}_"
            f"{details_hash[:24]}"
        )
        or any(
            not _sha256_text(value.get(field))
            for field in (
                "manifest_sha256",
                "contract_id",
                "contract_sha256",
                "request_plan_hash",
                "request_plan_sha256",
                "normalized_replay_root",
                "normalized_artifact_inventory_root",
                "specialized_validator_identity_root",
                "signed_source_identity_root",
                "legacy_attachment_input_root",
                "legacy_attachment_request_plan_root",
                "declared_capture_implementation_root",
                "input_capture_content_hash",
                "source_details_content_hash",
                "capture_public_key_sha256",
            )
        )
        or not _authorized_declared_range_implementation_root(
            str(value.get("declared_capture_implementation_root") or "")
        )
        or (
            value.get("planner_root_proof_mode")
            == HISTORICAL_PLANNER_ROOT_PROOF_MODE
            and observed_source_identity
            != HISTORICAL_SIGNED_RANGE_SOURCE_IDENTITIES.get(
                value.get("capture_profile")
            )
        )
        or (
            value.get("planner_root_proof_mode")
            == CURRENT_PLANNER_ROOT_PROOF_MODE
            and value.get("declared_capture_implementation_root")
            != range_capture._implementation_root()
        )
    ):
        raise ValueError("csindex_semantic_source_capture_binding_invalid")
    for role, relative_path in expected_relative_paths.items():
        artifact = inventory.get(role)
        if (
            not isinstance(artifact, Mapping)
            or set(artifact) != {"relative_path", "sha256", "size_bytes"}
            or artifact.get("relative_path") != relative_path
            or not _sha256_text(artifact.get("sha256"))
            or type(artifact.get("size_bytes")) is not int
            or int(artifact["size_bytes"]) < 0
        ):
            raise ValueError(
                "csindex_semantic_source_capture_binding_invalid"
            )


def _validate_source_reference_payload(
    value: Mapping[str, Any],
    *,
    expected_binding: Mapping[str, Any],
) -> None:
    if set(value) != {
        "schema_version",
        "source_capture_binding",
        "signed_source_manifest",
        "source_contract",
        "source_request_plan",
        "normalized_attachment_index_rows",
    } or value.get("schema_version") != SOURCE_REFERENCE_SCHEMA:
        raise ValueError("csindex_semantic_source_reference_invalid")
    binding = value.get("source_capture_binding")
    source_manifest = value.get("signed_source_manifest")
    contract = value.get("source_contract")
    plan = value.get("source_request_plan")
    index_rows = value.get("normalized_attachment_index_rows")
    if (
        not isinstance(binding, Mapping)
        or dict(binding) != dict(expected_binding)
        or not isinstance(source_manifest, Mapping)
        or not isinstance(contract, Mapping)
        or not isinstance(plan, Mapping)
        or not isinstance(index_rows, list)
        or any(type(row) is not dict for row in index_rows)
    ):
        raise ValueError("csindex_semantic_source_reference_invalid")
    _validate_source_capture_binding(binding)
    profile = str(binding.get("capture_profile") or "")
    declared_root = str(
        binding.get("declared_capture_implementation_root") or ""
    )
    range_capture._validate_authorized_contract(
        contract,
        request_count=int(binding.get("request_count") or -1),
        expected_profile=profile,
        expected_implementation_root=declared_root,
    )
    try:
        public_key = range_capture.capture_module._public_key_bytes(contract)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "csindex_semantic_source_reference_public_key_invalid"
        ) from exc
    if (
        range_capture._public_key_hash(public_key)
        != range_capture.APPROVED_CAPTURE_KEY_SHA256
        or contract.get("capture_public_key_sha256")
        != range_capture.APPROVED_CAPTURE_KEY_SHA256
        or contract.get("capture_public_key_sha256")
        != binding.get("capture_public_key_sha256")
    ):
        raise ValueError(
            "csindex_semantic_source_reference_public_key_invalid"
        )
    requests = _validated_source_requests_from_plan(plan)
    _population, plan_binding = _validate_contract_plan_population_binding(
        contract,
        requests=requests,
        profile=profile,
        declared_implementation_root=declared_root,
    )
    planner_root_proof_mode, signed_source_identity_root = (
        _planner_root_proof(
            source_manifest,
            profile=profile,
            declared_implementation_root=declared_root,
            binding=plan_binding,
        )
    )
    if (
        planner_root_proof_mode != binding.get("planner_root_proof_mode")
        or signed_source_identity_root
        != binding.get("signed_source_identity_root")
        or plan_binding.get("content_hash")
        != binding.get("input_capture_content_hash")
        or plan_binding.get("details_generation_id")
        != binding.get("source_details_generation_id")
        or plan_binding.get("details_content_hash")
        != binding.get("source_details_content_hash")
    ):
        raise ValueError("csindex_semantic_source_reference_invalid")
    manifest_semantic = {
        key: item
        for key, item in source_manifest.items()
        if key
        not in {
            "capture_publication_signature",
            "content_hash",
            "generation_id",
        }
    }
    request_rows = plan["requests"]
    if (
        set(source_manifest) != SOURCE_MANIFEST_FIELDS
        or source_manifest.get("schema_version")
        != range_capture.capture_module.SCHEMA_VERSION
        or source_manifest.get("content_hash") != binding.get("content_hash")
        or source_manifest.get("generation_id")
        != binding.get("generation_id")
        or canonical_hash(manifest_semantic)
        != source_manifest.get("content_hash")
        or canonical_hash(contract) != binding.get("contract_id")
        or source_manifest.get("contract_id") != binding.get("contract_id")
        or canonical_hash(request_rows) != binding.get("request_plan_hash")
        or plan.get("request_plan_hash") != binding.get("request_plan_hash")
        or source_manifest.get("request_plan_hash")
        != binding.get("request_plan_hash")
        or len(request_rows) != binding.get("request_count")
        or source_manifest.get("request_count") != binding.get("request_count")
        or len(index_rows) != binding.get("request_count")
        or (contract.get("adapter_identity") or {}).get("capture_profile")
        != binding.get("capture_profile")
        or (contract.get("adapter_identity") or {}).get("implementation_root")
        != binding.get("declared_capture_implementation_root")
        or contract.get("output_namespace_id")
        != binding.get("source_namespace_id")
        or contract.get("capture_public_key_sha256")
        != binding.get("capture_public_key_sha256")
    ):
        raise ValueError("csindex_semantic_source_reference_invalid")
    try:
        range_capture.verify_signature(
            public_key_pem=public_key,
            payload=range_capture.capture_module._canonical_bytes(
                manifest_semantic
                | {
                    "content_hash": source_manifest["content_hash"],
                    "generation_id": source_manifest["generation_id"],
                }
            ),
            signature_b64=str(
                source_manifest.get("capture_publication_signature") or ""
            ),
        )
    except range_capture.ReceiptSigningError as exc:
        raise ValueError(
            "csindex_semantic_source_reference_signature_invalid"
        ) from exc
    signed_artifacts = {
        str(row.get("role") or ""): row
        for row in source_manifest.get("normalized_artifacts") or ()
        if isinstance(row, Mapping)
    }
    inventory = binding["normalized_artifact_inventory"]
    for role in SOURCE_NORMALIZED_ROLES:
        signed = signed_artifacts.get(role)
        archived = inventory[role]
        if (
            signed is None
            or signed.get("relative_path") != archived["relative_path"]
            or signed.get("sha256") != archived["sha256"]
        ):
            raise ValueError("csindex_semantic_source_reference_invalid")
    index_payload = b"".join(_json_bytes(row) for row in index_rows)
    index_inventory = inventory["csindex_range_attachment_index"]
    if (
        hashlib.sha256(index_payload).hexdigest()
        != index_inventory["sha256"]
        or len(index_payload) != index_inventory["size_bytes"]
    ):
        raise ValueError("csindex_semantic_source_reference_index_invalid")
    request_ids = [str(row.get("request_id") or "") for row in request_rows]
    index_ids = [str(row.get("source_request_id") or "") for row in index_rows]
    if (
        not all(request_ids)
        or len(request_ids) != len(set(request_ids))
        or request_ids != index_ids
    ):
        raise ValueError("csindex_semantic_source_reference_index_invalid")


def _source_row_context(
    source_reference: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    plan_rows = source_reference["source_request_plan"]["requests"]
    index_rows = source_reference["normalized_attachment_index_rows"]
    context: dict[str, dict[str, Any]] = {}
    for rank, (request, index) in enumerate(
        zip(plan_rows, index_rows, strict=True)
    ):
        request_id = str(request.get("request_id") or "")
        announcements = index.get("source_announcements")
        if (
            not request_id
            or request_id in context
            or index.get("source_request_id") != request_id
            or index.get("attachment_url") != request.get("url")
            or not _sha256_text(index.get("attachment_sha256"))
            or not _sha256_text(index.get("source_logical_payload_sha256"))
            or not isinstance(announcements, list)
            or any(type(row) is not dict for row in announcements)
        ):
            raise ValueError("csindex_semantic_source_tuple_invalid")
        announcement_ids = sorted(
            {
                str(row.get("announcement_id") or "")
                for row in announcements
                if str(row.get("announcement_id") or "")
            }
        )
        publish_dates = sorted(
            {
                str(row.get("announcement_publish_date") or "")
                for row in announcements
                if str(row.get("announcement_publish_date") or "")
            }
        )
        if any(not _date_text(value) for value in publish_dates):
            raise ValueError("csindex_semantic_source_tuple_invalid")
        context[request_id] = {
            "rank": rank,
            "attachment_url": str(index["attachment_url"]),
            "attachment_extension": str(
                index.get("attachment_extension") or ""
            ).lower(),
            "attachment_sha256": str(index["attachment_sha256"]),
            "source_logical_payload_sha256": str(
                index["source_logical_payload_sha256"]
            ),
            "source_announcement_ids": announcement_ids,
            "declared_announcement_publish_dates": publish_dates,
        }
    return context


def validate_csindex_attachment_semantic_evidence(
    path: str | Path,
    *,
    source_capture: str | Path | None = None,
) -> dict[str, Any]:
    """Validate exact bytes, schemas, toolchain and fail-closed safety."""

    payload = validate_generation(
        path,
        schema=SEMANTIC_SCHEMA,
        manifest_name=SEMANTIC_MANIFEST_NAME,
    )
    manifest_path = Path(str(payload["manifest_path"]))
    root = manifest_path.parent
    source_capture_binding = payload.get("source_capture_binding")
    expected_files = {
        SEMANTIC_MANIFEST_NAME,
        SOURCE_REFERENCE_NAME,
        *SEMANTIC_FILE_NAMES.values(),
    }
    if (
        set(payload) != _semantic_manifest_fields()
        or not _semantic_generation_tree_exact(root, expected_files)
        or root.stat().st_mode & 0o222
        or any(item.stat().st_mode & 0o222 for item in root.iterdir())
        or payload.get("artifact_set_schema_version")
        != SEMANTIC_ARTIFACT_SET_SCHEMA
        or payload.get("technical_processing_status") != "succeeded"
        or payload.get("parser_identity") != PARSER_IDENTITY
        or payload.get("parser_components") != _parser_components()
        or payload.get("parser_implementation_root") != _implementation_root()
        or not _semantic_manifest_scalar_contract_valid(payload)
        or not isinstance(source_capture_binding, Mapping)
        or payload.get("source_capture_binding_root")
        != canonical_hash(source_capture_binding)
        or payload.get("planner_root_proof_mode")
        != source_capture_binding.get("planner_root_proof_mode")
        or payload.get("signed_source_identity_root")
        != source_capture_binding.get("signed_source_identity_root")
        or not _sha256_text(payload.get("signed_source_identity_root"))
        or payload.get("historical_known_at_proven") is not False
        or payload.get("effective_at_proven") is not False
        or payload.get("event_chain_complete") is not False
        or payload.get("seed_membership_proven") is not False
        or payload.get("historical_weights_proven") is not False
        or payload.get("pit_membership_authorized") is not False
        or payload.get("data_admission_eligible") is not False
        or payload.get("alpha_search_authorized") is not False
        or not _sha256_text(payload.get("source_body_replay_root"))
    ):
        raise ValueError("csindex_semantic_evidence_manifest_invalid")
    _validate_source_capture_binding(source_capture_binding)
    source_reference_artifact = payload.get("source_reference_artifact")
    source_reference_path = root / SOURCE_REFERENCE_NAME
    try:
        source_reference_stat = source_reference_path.lstat()
    except OSError as exc:
        raise ValueError(
            "csindex_semantic_source_reference_artifact_invalid"
        ) from exc
    if (
        type(source_reference_artifact) is not dict
        or set(source_reference_artifact)
        != {
            "relative_path",
            "schema_version",
            "sha256",
            "size_bytes",
            "content_root",
        }
        or source_reference_artifact.get("relative_path")
        != SOURCE_REFERENCE_NAME
        or source_reference_artifact.get("schema_version")
        != SOURCE_REFERENCE_SCHEMA
        or not _exact_int(
            source_reference_artifact.get("size_bytes"), minimum=0
        )
        or not stat.S_ISREG(source_reference_stat.st_mode)
        or source_reference_path.is_symlink()
        or source_reference_stat.st_size > MAX_SOURCE_REFERENCE_BYTES
        or source_reference_artifact.get("sha256")
        != sha256_file(source_reference_path)
        or source_reference_artifact.get("size_bytes")
        != source_reference_stat.st_size
    ):
        raise ValueError("csindex_semantic_source_reference_artifact_invalid")
    try:
        source_reference_payload = source_reference_path.read_bytes()
        source_reference = range_capture._exact_json_object(
            source_reference_payload
        )
    except (OSError, ValueError) as exc:
        raise ValueError(
            "csindex_semantic_source_reference_artifact_invalid"
        ) from exc
    if (
        type(source_reference) is not dict
        or source_reference_payload != _json_bytes(source_reference)
        or source_reference_artifact.get("content_root")
        != canonical_hash(source_reference)
    ):
        raise ValueError("csindex_semantic_source_reference_artifact_invalid")
    _validate_source_reference_payload(
        source_reference,
        expected_binding=source_capture_binding,
    )
    source_row_context = _source_row_context(source_reference)
    inventory = payload.get("artifact_inventory")
    if (
        type(inventory) is not dict
        or set(inventory) != set(SEMANTIC_FILE_NAMES)
        or payload.get("artifact_set_root") != canonical_hash(inventory)
    ):
        raise ValueError("csindex_semantic_artifact_inventory_invalid")
    observed: dict[str, dict[str, Any]] = {}
    for role, relative_path in SEMANTIC_FILE_NAMES.items():
        declared = inventory.get(role)
        artifact_path = root / relative_path
        if (
            not _semantic_artifact_descriptor_valid(
                declared,
                role=role,
                relative_path=relative_path,
            )
            or not artifact_path.is_file()
            or artifact_path.is_symlink()
            or declared.get("sha256") != sha256_file(artifact_path)
            or declared.get("size_bytes") != artifact_path.stat().st_size
        ):
            raise ValueError("csindex_semantic_artifact_inventory_invalid")
        observed[role] = _validate_semantic_jsonl(
            artifact_path,
            role=role,
            source_context=source_row_context,
        )
        if declared.get("row_count") != observed[role]["row_count"]:
            raise ValueError("csindex_semantic_artifact_row_count_invalid")
    _validate_streamed_candidate_terminal_closure(
        root / SEMANTIC_FILE_NAMES["csindex_csi300_change_candidates"],
        root
        / SEMANTIC_FILE_NAMES[
            "csindex_csi300_change_row_dispositions"
        ],
    )
    index = observed["csindex_attachment_semantic_index"]
    rows = observed["csindex_csi300_change_row_dispositions"]
    sheets = observed["csindex_csi300_sheet_dispositions"]
    if (
        payload.get("source_attachment_count") != index["row_count"]
        or payload.get("semantic_index_count") != index["row_count"]
        or payload.get("candidate_count")
        != observed["csindex_csi300_change_candidates"]["row_count"]
        or payload.get("row_disposition_count") != rows["row_count"]
        or payload.get("csi300_bearing_sheet_count")
        != sheets["row_count"]
        or payload.get("source_attachment_hash_set_root")
        != canonical_hash(sorted(index["attachment_hashes"]))
        or payload.get("xls_attachment_count") != index["xls_count"]
        or payload.get("xls_parser_runtime_isolation_proven")
        is not _xls_worker_isolation_available()
        or payload.get("xls_parser_os_timeout_enforced")
        is not _xls_worker_isolation_available()
        or not _xls_worker_limits_valid(payload.get("xls_worker_limits"))
        or payload.get("runtime_isolation_blockers")
        != (
            list(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
            if index["xls_count"] and not _xls_worker_isolation_available()
            else []
        )
        or payload.get("blockers")
        != list(_TEMPORAL_BLOCKERS)
        + (
            list(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
            if index["xls_count"] and not _xls_worker_isolation_available()
            else []
        )
        or payload.get("disposition_counts") != index["dispositions"]
        or payload.get("blocked_reason_counts")
        != index["blocked_reasons"]
        or payload.get("row_disposition_counts") != rows["terminals"]
        or payload.get("sheet_disposition_counts") != sheets["terminals"]
        or sum(index["declared_candidate_counts"].values())
        != observed["csindex_csi300_change_candidates"]["row_count"]
        or {
            key: value
            for key, value in index["declared_candidate_counts"].items()
            if value
        }
        != observed["csindex_csi300_change_candidates"]["request_counts"]
        or any(
            not set(observed[role]["request_counts"]).issubset(
                index["request_counts"]
            )
            for role in (
                "csindex_csi300_change_candidates",
                "csindex_csi300_change_row_dispositions",
                "csindex_csi300_sheet_dispositions",
            )
        )
        or not _bounded_processing_valid(payload.get("bounded_processing"))
    ):
        raise ValueError("csindex_semantic_evidence_closure_invalid")
    if source_capture is None:
        return payload | {
            "validation_status": "blocked_source_resolution_required",
            "source_reference_resolution_required": True,
            "deep_source_replay_verified": False,
        }
    _deep_validate_semantic_source_replay(
        root,
        source_capture=source_capture,
        expected_binding=source_capture_binding,
        expected_reference=source_reference,
        expected_body_replay_root=str(payload["source_body_replay_root"]),
    )
    return payload | {
        "validation_status": "verified",
        "source_reference_resolution_required": False,
        "deep_source_replay_verified": True,
    }


def _semantic_generation_tree_exact(
    root: Path,
    expected_files: set[str],
) -> bool:
    """Accept exactly the expected root-level regular files."""

    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            return False
        observed: set[str] = set()
        for entry in root.iterdir():
            if (
                not stat.S_ISREG(entry.lstat().st_mode)
                or entry.name not in expected_files
            ):
                return False
            observed.add(entry.name)
    except OSError:
        return False
    return observed == expected_files


def _deep_validate_semantic_source_replay(
    evidence_root: Path,
    *,
    source_capture: str | Path,
    expected_binding: Mapping[str, Any],
    expected_reference: Mapping[str, Any],
    expected_body_replay_root: str,
) -> None:
    (
        attachments,
        body_replay_root,
        replayed_binding,
        replayed_reference,
    ) = _iter_verified_range_attachments(
        source_capture,
        required_profile=range_capture.CAPTURE_PROFILE,
    )
    if (
        body_replay_root != expected_body_replay_root
        or replayed_binding != dict(expected_binding)
        or replayed_reference != dict(expected_reference)
    ):
        raise ValueError("csindex_semantic_source_reference_resolution_mismatch")
    with tempfile.TemporaryDirectory(
        prefix="auto-alpha-csindex-semantic-validation-"
    ) as temporary_name:
        replay_root = Path(temporary_name)
        _stream_semantic_artifacts(attachments, replay_root)
        for role, relative_path in SEMANTIC_FILE_NAMES.items():
            del role
            expected = evidence_root / relative_path
            replayed = replay_root / relative_path
            if (
                sha256_file(expected) != sha256_file(replayed)
                or expected.stat().st_size != replayed.stat().st_size
                or not _files_equal(expected, replayed)
            ):
                raise ValueError("csindex_semantic_deep_source_replay_mismatch")


def _files_equal(left: Path, right: Path) -> bool:
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        while True:
            left_chunk = left_handle.read(1024 * 1024)
            right_chunk = right_handle.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _semantic_manifest_fields() -> set[str]:
    return {
        "schema_version",
        "artifact_set_schema_version",
        "parser_identity",
        "parser_components",
        "parser_implementation_root",
        "source_body_replay_root",
        "source_capture_binding",
        "source_capture_binding_root",
        "planner_root_proof_mode",
        "signed_source_identity_root",
        "source_reference_artifact",
        "source_attachment_count",
        "source_attachment_hash_set_root",
        "artifact_inventory",
        "artifact_set_root",
        "technical_processing_status",
        "semantic_index_count",
        "candidate_count",
        "row_disposition_count",
        "csi300_bearing_sheet_count",
        "disposition_counts",
        "blocked_reason_counts",
        "row_disposition_counts",
        "sheet_disposition_counts",
        "historical_known_at_proven",
        "effective_at_proven",
        "event_chain_complete",
        "seed_membership_proven",
        "historical_weights_proven",
        "xls_attachment_count",
        "xls_parser_runtime_isolation_proven",
        "xls_parser_os_timeout_enforced",
        "xls_worker_limits",
        "runtime_isolation_blockers",
        "bounded_processing",
        "pit_membership_authorized",
        "data_admission_eligible",
        "alpha_search_authorized",
        "safety",
        "blockers",
        "content_hash",
        "generation_id",
        "manifest_path",
    }


def _semantic_manifest_scalar_contract_valid(
    payload: Mapping[str, Any],
) -> bool:
    count_fields = (
        "source_attachment_count",
        "semantic_index_count",
        "candidate_count",
        "row_disposition_count",
        "csi300_bearing_sheet_count",
        "xls_attachment_count",
    )
    count_map_fields = (
        "disposition_counts",
        "blocked_reason_counts",
        "row_disposition_counts",
        "sheet_disposition_counts",
    )
    safety = payload.get("safety")
    return bool(
        all(_exact_int(payload.get(field), minimum=0) for field in count_fields)
        and all(
            _exact_count_mapping(payload.get(field))
            for field in count_map_fields
        )
        and type(safety) is dict
        and set(safety) == set(SEMANTIC_SAFETY_FLAGS)
        and all(safety[name] is False for name in SEMANTIC_SAFETY_FLAGS)
        and _xls_worker_limits_valid(payload.get("xls_worker_limits"))
        and _bounded_processing_valid(payload.get("bounded_processing"))
    )


def _exact_count_mapping(value: object) -> bool:
    return bool(
        type(value) is dict
        and all(type(key) is str and key for key in value)
        and all(_exact_int(count, minimum=0) for count in value.values())
    )


def _semantic_artifact_descriptor_valid(
    value: object,
    *,
    role: str,
    relative_path: str,
) -> bool:
    return bool(
        type(value) is dict
        and set(value) == SEMANTIC_ARTIFACT_FIELDS
        and value.get("relative_path") == relative_path
        and type(value.get("relative_path")) is str
        and value.get("schema_version") == _role_schema(role)
        and type(value.get("schema_version")) is str
        and _exact_int(value.get("row_count"), minimum=0)
        and _sha256_text(value.get("sha256"))
        and _exact_int(value.get("size_bytes"), minimum=0)
    )


def _xls_worker_limits_valid(value: object) -> bool:
    expected = _xls_worker_limits()
    integer_fields = {
        "address_space_bytes",
        "cpu_soft_seconds",
        "cpu_hard_seconds",
        "wall_timeout_seconds",
        "file_size_bytes",
        "open_file_limit",
        "core_dump_bytes",
        "input_body_bytes",
        "output_body_bytes",
    }
    return bool(
        type(value) is dict
        and set(value) == set(expected)
        and type(value.get("worker_schema")) is str
        and type(value.get("process_model")) is str
        and type(value.get("python_isolated_flag")) is bool
        and all(_exact_int(value.get(field), minimum=0) for field in integer_fields)
        and value == expected
    )


def _bounded_processing_valid(value: object) -> bool:
    expected = {
        "attachment_iteration": "one_verified_body_at_a_time",
        "resident_attachment_scope_limit": 1,
        "per_attachment_body_limit_bytes": (
            range_capture.ATTACHMENT_BODY_MAX_BYTES
        ),
        "per_logical_envelope_limit_bytes": (
            range_capture.MAX_LOGICAL_ENVELOPE_BYTES
        ),
        "contract_total_response_limit_bytes": (
            range_capture.MAX_TOTAL_RESPONSE_BYTES
        ),
    }
    integer_fields = set(expected) - {"attachment_iteration"}
    return bool(
        type(value) is dict
        and set(value) == set(expected)
        and type(value.get("attachment_iteration")) is str
        and all(_exact_int(value.get(field), minimum=0) for field in integer_fields)
        and value == expected
    )


def _validate_semantic_jsonl(
    path: Path,
    *,
    role: str,
    source_context: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError("csindex_semantic_jsonl_framing_invalid") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_size > MAX_SEMANTIC_JSONL_BYTES
    ):
        raise ValueError("csindex_semantic_jsonl_framing_invalid")
    row_count = 0
    attachment_hashes: set[str] = set()
    dispositions: dict[str, int] = {}
    blocked_reasons: dict[str, int] = {}
    terminals: dict[str, int] = {}
    request_counts: dict[str, int] = {}
    declared_candidate_counts: dict[str, int] = {}
    xls_count = 0
    previous_order: tuple[Any, ...] | None = None
    with path.open("rb") as handle:
        for line in handle:
            if (
                len(line) > MAX_SEMANTIC_JSONL_LINE_BYTES
                or not line.endswith(b"\n")
                or line == b"\n"
            ):
                raise ValueError("csindex_semantic_jsonl_framing_invalid")
            try:
                row = range_capture._exact_json_object(line)
            except ValueError as exc:
                raise ValueError("csindex_semantic_jsonl_invalid") from exc
            if (
                type(row) is not dict
                or line != _json_bytes(row)
                or row.get("schema_version") != _role_schema(role)
                or set(row) != _role_fields(role)
                or row.get("pit_membership_authorized") is not False
            ):
                raise ValueError("csindex_semantic_row_schema_invalid")
            request_id = str(row.get("source_request_id") or "")
            source = source_context.get(request_id)
            if source is None:
                raise ValueError("csindex_semantic_row_order_invalid")
            _validate_semantic_row_types(row, role=role)
            if (
                row.get("attachment_url") != source["attachment_url"]
                or row.get("attachment_sha256")
                != source["attachment_sha256"]
                or row.get("source_announcement_ids")
                != source["source_announcement_ids"]
                or row.get("declared_announcement_publish_dates")
                != source["declared_announcement_publish_dates"]
                or (
                    role == "csindex_attachment_semantic_index"
                    and (
                        row.get("attachment_extension")
                        != source["attachment_extension"]
                        or row.get("source_logical_payload_sha256")
                        != source["source_logical_payload_sha256"]
                    )
                )
            ):
                raise ValueError("csindex_semantic_source_tuple_invalid")
            order = _semantic_row_order_key(
                row,
                role=role,
                request_rank=int(source["rank"]),
            )
            if previous_order is not None and order <= previous_order:
                raise ValueError("csindex_semantic_row_order_invalid")
            previous_order = order
            request_counts[request_id] = request_counts.get(request_id, 0) + 1
            attachment_hash = str(row.get("attachment_sha256") or "")
            if not _sha256_text(attachment_hash):
                raise ValueError("csindex_semantic_attachment_hash_invalid")
            attachment_hashes.add(attachment_hash)
            if role == "csindex_attachment_semantic_index":
                if (
                    row.get("historical_known_at_proven") is not False
                    or row.get("effective_at_proven") is not False
                ):
                    raise ValueError("csindex_semantic_temporal_claim_invalid")
                disposition = str(row["semantic_disposition"])
                if disposition not in SEMANTIC_DISPOSITIONS:
                    raise ValueError("csindex_semantic_disposition_invalid")
                if request_counts[request_id] != 1:
                    raise ValueError(
                        "csindex_semantic_index_request_duplicate"
                    )
                candidate_count = row.get("candidate_count")
                if type(candidate_count) is not int or candidate_count < 0:
                    raise ValueError(
                        "csindex_semantic_index_candidate_count_invalid"
                    )
                declared_candidate_counts[request_id] = candidate_count
                dispositions[disposition] = dispositions.get(disposition, 0) + 1
                if row.get("blocked_reason") is not None:
                    reason = str(row["blocked_reason"])
                    if reason not in SEMANTIC_BLOCKED_REASONS:
                        raise ValueError(
                            "csindex_semantic_blocked_reason_invalid"
                        )
                    blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                xls_count += row.get("attachment_extension") == "xls"
            elif role in {
                "csindex_csi300_change_row_dispositions",
                "csindex_csi300_sheet_dispositions",
            }:
                terminal = str(row["terminal_disposition"])
                allowed_terminals = (
                    ROW_TERMINAL_DISPOSITIONS
                    if role
                    == "csindex_csi300_change_row_dispositions"
                    else SHEET_TERMINAL_DISPOSITIONS
                )
                if terminal not in allowed_terminals:
                    raise ValueError("csindex_semantic_terminal_invalid")
                terminals[terminal] = terminals.get(terminal, 0) + 1
            elif (
                row.get("historical_known_at") is not None
                or row.get("historical_known_at_proven") is not False
                or row.get("effective_at") is not None
                or row.get("effective_at_proven") is not False
            ):
                raise ValueError("csindex_semantic_temporal_claim_invalid")
            row_count += 1
    return {
        "row_count": row_count,
        "attachment_hashes": attachment_hashes,
        "dispositions": dict(sorted(dispositions.items())),
        "blocked_reasons": dict(sorted(blocked_reasons.items())),
        "terminals": dict(sorted(terminals.items())),
        "xls_count": xls_count,
        "request_counts": request_counts,
        "declared_candidate_counts": declared_candidate_counts,
    }


def _validate_semantic_row_types(
    row: Mapping[str, Any],
    *,
    role: str,
) -> None:
    common_invalid = (
        type(row.get("schema_version")) is not str
        or type(row.get("source_request_id")) is not str
        or not row["source_request_id"]
        or type(row.get("attachment_url")) is not str
        or not row["attachment_url"].startswith("https://")
        or not _sha256_text(row.get("attachment_sha256"))
        or not _string_list(row.get("source_announcement_ids"))
        or not _string_list(
            row.get("declared_announcement_publish_dates"),
            dates=True,
        )
        or row.get("pit_membership_authorized") is not False
    )
    if common_invalid:
        raise ValueError("csindex_semantic_row_type_invalid")
    if role == "csindex_attachment_semantic_index":
        sheet_count = row.get("sheet_count")
        bearing_count = row.get("csi300_bearing_sheet_count")
        blocked_reason = row.get("blocked_reason")
        extension = row.get("attachment_extension")
        xls_isolation = _xls_worker_isolation_available()
        expected_isolation = xls_isolation if extension == "xls" else None
        expected_isolation_blockers = (
            list(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
            if extension == "xls" and not xls_isolation
            else []
        )
        if (
            extension not in {"xls", "xlsx", "jpg", "jpeg", "png"}
            or not _sha256_text(row.get("source_logical_payload_sha256"))
            or row.get("historical_known_at_proven") is not False
            or row.get("effective_at_proven") is not False
            or type(row.get("legacy_xls_runtime_isolation_required")) is not bool
            or not _exact_optional_bool(
                row.get("legacy_xls_runtime_isolation_proven")
            )
            or not _exact_optional_bool(
                row.get("legacy_xls_os_timeout_enforced")
            )
            or not _string_list(row.get("runtime_isolation_blockers"))
            or row.get("legacy_xls_runtime_isolation_required")
            is not (extension == "xls")
            or row.get("legacy_xls_runtime_isolation_proven")
            is not expected_isolation
            or row.get("legacy_xls_os_timeout_enforced")
            is not expected_isolation
            or row.get("runtime_isolation_blockers")
            != expected_isolation_blockers
            or (
                blocked_reason is not None
                and type(blocked_reason) is not str
            )
            or (
                sheet_count is not None
                and not _exact_int(sheet_count, minimum=0)
            )
            or not _exact_int(bearing_count, minimum=0)
            or not _exact_int(row.get("candidate_count"), minimum=0)
            or (
                isinstance(sheet_count, int)
                and bearing_count > sheet_count
            )
            or (
                row.get("semantic_disposition")
                == "csi300_change_candidates_extracted"
                and (
                    blocked_reason is not None
                    or row["candidate_count"] <= 0
                )
            )
            or (
                row.get("semantic_disposition")
                != "csi300_change_candidates_extracted"
                and (
                    not blocked_reason
                    or row["candidate_count"] != 0
                )
            )
        ):
            raise ValueError("csindex_semantic_row_type_invalid")
        return
    blockers = row.get("blockers")
    allowed_blockers = set(_TEMPORAL_BLOCKERS) | set(
        _XLS_ISOLATION_UNAVAILABLE_BLOCKERS
    ) | {
        "candidate_shape_security_code_invalid",
        "sibling_change_shape_has_invalid_security_code",
        "sibling_csi300_sheet_schema_unsupported",
        "csi300_sheet_schema_unsupported",
    }
    if (
        not _string_list(blockers, require_unique=True)
        or not set(_TEMPORAL_BLOCKERS).issubset(blockers)
        or not set(blockers).issubset(allowed_blockers)
    ):
        raise ValueError("csindex_semantic_row_type_invalid")
    if role == "csindex_csi300_change_candidates":
        if (
            row.get("index_code") != "000300"
            or row.get("action") not in ACTIONS
            or not _security_code_text(row.get("security_code"))
            or type(row.get("security_name")) is not str
            or type(row.get("source_sheet_name")) is not str
            or not _exact_int(row.get("source_sheet_ordinal"), minimum=0)
            or not _exact_int(row.get("source_row_number"), minimum=1)
            or not _exact_int(row.get("source_security_column"), minimum=1)
            or row.get("historical_known_at") is not None
            or row.get("historical_known_at_proven") is not False
            or row.get("effective_at") is not None
            or row.get("effective_at_proven") is not False
        ):
            raise ValueError("csindex_semantic_row_type_invalid")
        return
    if role == "csindex_csi300_change_row_dispositions":
        canonical_code = row.get("canonical_security_code")
        if (
            row.get("index_code") != "000300"
            or row.get("action") not in ACTIONS
            or type(row.get("raw_security_code")) is not str
            or type(row.get("raw_security_name")) is not str
            or canonical_code is not None
            and not _security_code_text(canonical_code)
            or type(row.get("source_sheet_name")) is not str
            or not _exact_int(row.get("source_sheet_ordinal"), minimum=0)
            or not _exact_int(row.get("source_row_number"), minimum=1)
            or not _exact_int(row.get("source_security_column"), minimum=1)
            or (
                row.get("terminal_disposition") == "candidate_extracted"
                and canonical_code is None
            )
            or (
                row.get("terminal_disposition")
                == "blocked_invalid_security_code"
                and canonical_code is not None
            )
        ):
            raise ValueError("csindex_semantic_row_type_invalid")
        return
    if role == "csindex_csi300_sheet_dispositions":
        if (
            type(row.get("source_sheet_name")) is not str
            or not _exact_int(row.get("source_sheet_ordinal"), minimum=0)
            or row.get("csi300_reference_observed") is not True
            or not _exact_int(
                row.get("local_row_disposition_count"), minimum=0
            )
            or not _exact_int(
                row.get("local_valid_candidate_count"), minimum=0
            )
            or type(row.get("semantic_candidate_emitted")) is not bool
        ):
            raise ValueError("csindex_semantic_row_type_invalid")
        return
    raise ValueError("csindex_semantic_artifact_role_invalid")


def _semantic_row_order_key(
    row: Mapping[str, Any],
    *,
    role: str,
    request_rank: int,
) -> tuple[Any, ...]:
    if role == "csindex_attachment_semantic_index":
        return (request_rank,)
    if role == "csindex_csi300_sheet_dispositions":
        return (request_rank, int(row["source_sheet_ordinal"]))
    common = (
        request_rank,
        int(row["source_sheet_ordinal"]),
        int(row["source_row_number"]),
        str(row["action"]),
        int(row["source_security_column"]),
    )
    if role == "csindex_csi300_change_candidates":
        return common + (str(row["security_code"]),)
    if role == "csindex_csi300_change_row_dispositions":
        return common + (str(row.get("canonical_security_code") or ""),)
    raise ValueError("csindex_semantic_artifact_role_invalid")


def _string_list(
    value: object,
    *,
    dates: bool = False,
    require_unique: bool = True,
) -> bool:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        return False
    if require_unique and len(value) != len(set(value)):
        return False
    if dates and any(not _date_text(item) for item in value):
        return False
    return True


def _date_text(value: object) -> bool:
    text = str(value or "")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return False
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d") == text
    except ValueError:
        return False


def _exact_int(value: object, *, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _exact_optional_bool(value: object) -> bool:
    return value is None or type(value) is bool


def _security_code_text(value: object) -> bool:
    return type(value) is str and bool(re.fullmatch(r"[0-9]{6}", value))


def _iter_candidate_closure_keys(path: Path) -> Iterator[tuple[Any, ...]]:
    with path.open("rb") as handle:
        for line in handle:
            row = json.loads(line)
            yield (
                row["source_request_id"],
                row["source_sheet_ordinal"],
                row["source_row_number"],
                row["action"],
                row["source_security_column"],
                row["security_code"],
            )


def _iter_extracted_terminal_keys(path: Path) -> Iterator[tuple[Any, ...]]:
    with path.open("rb") as handle:
        for line in handle:
            row = json.loads(line)
            if row["terminal_disposition"] == "candidate_extracted":
                yield (
                    row["source_request_id"],
                    row["source_sheet_ordinal"],
                    row["source_row_number"],
                    row["action"],
                    row["source_security_column"],
                    row["canonical_security_code"],
                )


def _validate_streamed_candidate_terminal_closure(
    candidates_path: Path,
    terminal_path: Path,
) -> None:
    candidate_keys = _iter_candidate_closure_keys(candidates_path)
    terminal_keys = _iter_extracted_terminal_keys(terminal_path)
    sentinel = object()
    while True:
        candidate = next(candidate_keys, sentinel)
        terminal = next(terminal_keys, sentinel)
        if candidate is sentinel or terminal is sentinel:
            if candidate is not terminal:
                raise ValueError("csindex_candidate_terminal_closure_invalid")
            return
        if candidate != terminal:
            raise ValueError("csindex_candidate_terminal_closure_invalid")


def _parser_components() -> dict[str, Any]:
    return {
        "xlsx": "python_stdlib_zipfile_elementtree",
        "xls": "xlrd",
        "xlrd_version": xlrd.__version__,
        "xlrd_toolchain_root": _xlrd_toolchain_root(),
        "xls_worker_contract_root": canonical_hash(_xls_worker_contract()),
        "module_source_sha256": _module_source_sha256(),
        "locked_range_capture_module_sha256": sha256_file(
            Path(range_capture.__file__)
        ),
        "semantic_owned_iterator_root": canonical_hash(
            {
                "source": inspect.getsource(_iter_verified_range_attachments),
                "attachment_body_max_bytes": (
                    range_capture.ATTACHMENT_BODY_MAX_BYTES
                ),
                "logical_envelope_max_bytes": (
                    range_capture.MAX_LOGICAL_ENVELOPE_BYTES
                ),
                "contract_total_response_max_bytes": (
                    range_capture.MAX_TOTAL_RESPONSE_BYTES
                ),
            }
        ),
        "python_implementation": sys.implementation.name,
        "python_version": list(sys.version_info[:3]),
    }


def _role_schema(role: str) -> str:
    schemas = {
        "csindex_attachment_semantic_index": SEMANTIC_INDEX_SCHEMA,
        "csindex_csi300_change_candidates": CANDIDATE_SCHEMA,
        "csindex_csi300_change_row_dispositions": ROW_DISPOSITION_SCHEMA,
        "csindex_csi300_sheet_dispositions": SHEET_DISPOSITION_SCHEMA,
    }
    try:
        return schemas[role]
    except KeyError as exc:
        raise ValueError("csindex_semantic_artifact_role_invalid") from exc


def _role_fields(role: str) -> set[str]:
    common_source = {
        "schema_version",
        "source_request_id",
        "attachment_sha256",
        "attachment_url",
        "source_announcement_ids",
        "declared_announcement_publish_dates",
        "pit_membership_authorized",
    }
    if role == "csindex_attachment_semantic_index":
        return common_source | {
            "attachment_extension",
            "source_logical_payload_sha256",
            "historical_known_at_proven",
            "effective_at_proven",
            "legacy_xls_runtime_isolation_required",
            "legacy_xls_runtime_isolation_proven",
            "legacy_xls_os_timeout_enforced",
            "runtime_isolation_blockers",
            "semantic_disposition",
            "blocked_reason",
            "sheet_count",
            "csi300_bearing_sheet_count",
            "candidate_count",
        }
    if role == "csindex_csi300_change_candidates":
        return common_source | {
            "index_code",
            "action",
            "security_code",
            "security_name",
            "source_sheet_name",
            "source_sheet_ordinal",
            "source_row_number",
            "source_security_column",
            "historical_known_at",
            "historical_known_at_proven",
            "effective_at",
            "effective_at_proven",
            "blockers",
        }
    if role == "csindex_csi300_change_row_dispositions":
        return common_source | {
            "source_sheet_name",
            "source_sheet_ordinal",
            "source_row_number",
            "source_security_column",
            "index_code",
            "action",
            "raw_security_code",
            "raw_security_name",
            "canonical_security_code",
            "terminal_disposition",
            "blockers",
        }
    if role == "csindex_csi300_sheet_dispositions":
        return common_source | {
            "source_sheet_name",
            "source_sheet_ordinal",
            "csi300_reference_observed",
            "terminal_disposition",
            "local_row_disposition_count",
            "local_valid_candidate_count",
            "semantic_candidate_emitted",
            "blockers",
        }
    raise ValueError("csindex_semantic_artifact_role_invalid")


def _has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    return any(item.is_symlink() for item in (absolute, *absolute.parents))


def _sha256_text(value: object) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def _parse_attachment(
    attachment: range_capture.ReplayedRangeAttachment,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    extension = attachment.attachment_extension.lower()
    if hashlib.sha256(attachment.body).hexdigest() != attachment.attachment_sha256:
        raise ValueError("csindex_semantic_attachment_hash_mismatch")
    base = {
        "schema_version": SEMANTIC_INDEX_SCHEMA,
        "source_request_id": attachment.source_request_id,
        "attachment_url": attachment.attachment_url,
        "attachment_extension": extension,
        "attachment_sha256": attachment.attachment_sha256,
        "source_logical_payload_sha256": (
            attachment.source_logical_payload_sha256
        ),
        "source_announcement_ids": sorted(
            {
                str(row.get("announcement_id") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_id") or "")
            }
        ),
        "declared_announcement_publish_dates": sorted(
            {
                str(row.get("announcement_publish_date") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_publish_date") or "")
            }
        ),
        "historical_known_at_proven": False,
        "effective_at_proven": False,
        "legacy_xls_runtime_isolation_required": extension == "xls",
        "legacy_xls_runtime_isolation_proven": (
            _xls_worker_isolation_available() if extension == "xls" else None
        ),
        "legacy_xls_os_timeout_enforced": (
            _xls_worker_isolation_available() if extension == "xls" else None
        ),
        "runtime_isolation_blockers": (
            list(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
            if extension == "xls" and not _xls_worker_isolation_available()
            else []
        ),
        "pit_membership_authorized": False,
    }
    if extension in {"jpg", "jpeg", "png"}:
        return (
            base
            | {
                "semantic_disposition": "blocked_unsupported_format",
                "blocked_reason": "image_ocr_semantic_parser_not_implemented",
                "sheet_count": None,
                "csi300_bearing_sheet_count": 0,
                "candidate_count": 0,
            },
            [],
            [],
            [],
        )
    if extension not in {"xls", "xlsx"}:
        return (
            base
            | {
                "semantic_disposition": "blocked_unsupported_format",
                "blocked_reason": "attachment_format_not_supported",
                "sheet_count": None,
                "csi300_bearing_sheet_count": 0,
                "candidate_count": 0,
            },
            [],
            [],
            [],
        )
    try:
        sheets = (
            _read_xls(attachment.body)
            if extension == "xls"
            else _read_xlsx(attachment.body)
        )
    except _SemanticParseBlocked as exc:
        return (
            base
            | {
                "semantic_disposition": "blocked_parse_failure",
                "blocked_reason": str(exc),
                "sheet_count": None,
                "csi300_bearing_sheet_count": 0,
                "candidate_count": 0,
            },
            [],
            [],
            [],
        )
    (
        candidates,
        terminal_rows,
        terminal_sheets,
        saw_csi300,
        saw_supported_schema,
    ) = _extract_candidates(
        attachment, sheets
    )
    unsupported_terminal_sheets = [
        row
        for row in terminal_sheets
        if row["terminal_disposition"] == "blocked_unsupported_csi300_schema"
    ]
    invalid_terminal_rows = [
        row
        for row in terminal_rows
        if row["terminal_disposition"] == "blocked_invalid_security_code"
    ]
    if unsupported_terminal_sheets:
        candidates = []
        terminal_rows = [
            row
            | {
                "terminal_disposition": (
                    "blocked_attachment_has_unsupported_csi300_sheet"
                    if row["terminal_disposition"] == "candidate_extracted"
                    else row["terminal_disposition"]
                ),
                "blockers": sorted(
                    set(row["blockers"])
                    | {"sibling_csi300_sheet_schema_unsupported"}
                ),
            }
            for row in terminal_rows
        ]
        terminal_sheets = [
            (
                row
                if row["terminal_disposition"]
                == "blocked_unsupported_csi300_schema"
                else row
                | {
                    "terminal_disposition": (
                        "blocked_attachment_has_unsupported_csi300_sheet"
                    ),
                    "semantic_candidate_emitted": False,
                    "blockers": sorted(
                        set(row["blockers"])
                        | {"sibling_csi300_sheet_schema_unsupported"}
                    ),
                }
            )
            for row in terminal_sheets
        ]
        disposition = "blocked_unsupported_csi300_sheet"
        reason = "csi300_bearing_sheet_schema_unsupported"
    elif invalid_terminal_rows:
        candidates = []
        terminal_rows = [
            (
                row
                if row["terminal_disposition"]
                == "blocked_invalid_security_code"
                else row
                | {
                    "terminal_disposition": (
                        "blocked_attachment_has_invalid_change_shape"
                    ),
                    "blockers": sorted(
                        set(row["blockers"])
                        | {"sibling_change_shape_has_invalid_security_code"}
                    ),
                }
            )
            for row in terminal_rows
        ]
        terminal_sheets = [
            row
            | {
                "terminal_disposition": (
                    "blocked_supported_schema_invalid_security_code"
                    if row["terminal_disposition"]
                    == "supported_schema_invalid_security_code"
                    else "blocked_attachment_has_invalid_change_shape"
                ),
                "semantic_candidate_emitted": False,
                "blockers": sorted(
                    set(row["blockers"])
                    | {"sibling_change_shape_has_invalid_security_code"}
                ),
            }
            for row in terminal_sheets
        ]
        disposition = "blocked_invalid_change_rows"
        reason = "supported_change_schema_has_invalid_security_code"
    elif candidates:
        disposition = "csi300_change_candidates_extracted"
        reason: str | None = None
    elif saw_csi300 and not saw_supported_schema:
        disposition = "blocked_ambiguous_semantics"
        reason = f"{extension}_csi300_semantic_schema_unsupported"
    elif saw_csi300:
        disposition = "blocked_no_change_rows"
        reason = "supported_change_schema_without_csi300_rows"
    else:
        disposition = "not_csi300_membership_evidence"
        reason = "attachment_has_no_csi300_reference"
    return (
        base
        | {
            "semantic_disposition": disposition,
            "blocked_reason": reason,
            "sheet_count": len(sheets),
            "csi300_bearing_sheet_count": len(terminal_sheets),
            "candidate_count": len(candidates),
        },
        candidates,
        terminal_rows,
        terminal_sheets,
    )


def _read_xlsx(body: bytes) -> tuple[_Sheet, ...]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except (OSError, zipfile.BadZipFile) as exc:
        raise _SemanticParseBlocked("xlsx_container_invalid") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (
            len(infos) > MAX_ZIP_ENTRIES
            or len(set(names)) != len(names)
            or any(_unsafe_zip_name(name) for name in names)
            or sum(info.file_size for info in infos) > MAX_ZIP_UNCOMPRESSED_BYTES
            or any(info.flag_bits & 0x1 for info in infos)
        ):
            raise _SemanticParseBlocked("xlsx_container_limits_invalid")
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        if not required.issubset(names):
            raise _SemanticParseBlocked("xlsx_workbook_parts_missing")
        shared = _read_shared_strings(archive)
        workbook = _read_xml(archive, "xl/workbook.xml", "xlsx_workbook_invalid")
        relationships = _read_workbook_relationships(archive)
        sheet_parent = workbook.find(f"{{{_MAIN_NS}}}sheets")
        if sheet_parent is None:
            raise _SemanticParseBlocked("xlsx_sheet_inventory_missing")
        sheets: list[_Sheet] = []
        total_cells = 0
        for ordinal, sheet in enumerate(sheet_parent):
            name = str(sheet.attrib.get("name") or "").strip()
            relation_id = str(sheet.attrib.get(f"{{{_REL_NS}}}id") or "")
            target = relationships.get(relation_id)
            if not name or target is None:
                raise _SemanticParseBlocked("xlsx_sheet_relationship_invalid")
            rows, cell_count = _read_sheet_rows(archive, target, shared)
            total_cells += cell_count
            if total_cells > MAX_WORKBOOK_CELLS:
                raise _SemanticParseBlocked("xlsx_workbook_cell_limit_exceeded")
            sheets.append(_Sheet(name=name, ordinal=ordinal, rows=rows))
        if not sheets:
            raise _SemanticParseBlocked("xlsx_sheet_inventory_empty")
        return tuple(sheets)


def _read_xls(body: bytes) -> tuple[_Sheet, ...]:
    """Parse BIFF in a fresh worker with wall-clock and kernel limits."""

    if (
        not body.startswith(_OLE_COMPOUND_MAGIC)
        or len(body) > MAX_XLS_BODY_BYTES
    ):
        raise _SemanticParseBlocked("xls_container_limits_invalid")
    if not _xls_worker_isolation_available():
        raise _SemanticParseBlocked("xls_worker_resource_isolation_unavailable")
    return _run_xls_worker(body)


def _read_xls_in_process(body: bytes) -> tuple[_Sheet, ...]:
    """Worker-only xlrd parser; callers must establish kernel limits first."""

    if (
        not body.startswith(_OLE_COMPOUND_MAGIC)
        or len(body) > MAX_XLS_BODY_BYTES
    ):
        raise _SemanticParseBlocked("xls_container_limits_invalid")
    workbook: Any | None = None
    parser_log = io.StringIO()
    try:
        workbook = xlrd.open_workbook(
            file_contents=body,
            formatting_info=False,
            logfile=parser_log,
            on_demand=True,
            ragged_rows=True,
            ignore_workbook_corruption=False,
        )
        if not 0 < workbook.nsheets <= MAX_XLS_SHEETS:
            raise _SemanticParseBlocked("xls_sheet_inventory_invalid")
        sheets: list[_Sheet] = []
        total_cells = 0
        total_text_bytes = 0
        for ordinal in range(workbook.nsheets):
            source = workbook.sheet_by_index(ordinal)
            if (
                source.nrows > MAX_XLS_SHEET_ROWS
                or source.ncols > MAX_XLS_SHEET_COLUMNS
            ):
                raise _SemanticParseBlocked("xls_sheet_dimensions_exceeded")
            parsed_rows: list[tuple[int, Mapping[int, str]]] = []
            for row_ordinal in range(source.nrows):
                row_length = source.row_len(row_ordinal)
                if row_length > MAX_XLS_SHEET_COLUMNS:
                    raise _SemanticParseBlocked("xls_sheet_dimensions_exceeded")
                total_cells += row_length
                if total_cells > MAX_WORKBOOK_CELLS:
                    raise _SemanticParseBlocked(
                        "xls_workbook_cell_limit_exceeded"
                    )
                values: dict[int, str] = {}
                for column_ordinal in range(row_length):
                    value = _xls_cell_text(
                        source.cell(row_ordinal, column_ordinal)
                    )
                    encoded_size = len(value.encode("utf-8"))
                    if encoded_size > MAX_XLS_CELL_TEXT_BYTES:
                        raise _SemanticParseBlocked(
                            "xls_cell_text_limit_exceeded"
                        )
                    total_text_bytes += encoded_size
                    if total_text_bytes > MAX_XLS_WORKBOOK_TEXT_BYTES:
                        raise _SemanticParseBlocked(
                            "xls_workbook_text_limit_exceeded"
                        )
                    if value:
                        values[column_ordinal + 1] = value
                if values:
                    parsed_rows.append((row_ordinal + 1, values))
            sheets.append(
                _Sheet(
                    name=str(source.name).strip(),
                    ordinal=ordinal,
                    rows=tuple(parsed_rows),
                )
            )
        if parser_log.getvalue().strip():
            raise _SemanticParseBlocked("xls_parser_diagnostic_emitted")
        return tuple(sheets)
    except _SemanticParseBlocked:
        raise
    except (Exception, MemoryError) as exc:
        raise _SemanticParseBlocked("xls_workbook_invalid") from exc
    finally:
        if workbook is not None:
            workbook.release_resources()


def _xls_cell_text(cell: Any) -> str:
    if cell.ctype in {
        xlrd.XL_CELL_EMPTY,
        xlrd.XL_CELL_BLANK,
    }:
        return ""
    if cell.ctype == xlrd.XL_CELL_ERROR:
        raise _SemanticParseBlocked("xls_error_cell_unsupported")
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return str(cell.value).strip()
    if cell.ctype in {xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE}:
        value = float(cell.value)
        if not math.isfinite(value):
            raise _SemanticParseBlocked("xls_numeric_cell_invalid")
        return format(value, ".15g")
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "1" if bool(cell.value) else "0"
    raise _SemanticParseBlocked("xls_cell_type_unsupported")


def _xls_worker_isolation_available() -> bool:
    required = (
        "RLIMIT_AS",
        "RLIMIT_CORE",
        "RLIMIT_CPU",
        "RLIMIT_FSIZE",
        "RLIMIT_NOFILE",
    )
    return (
        os.name == "posix"
        and resource is not None
        and all(hasattr(resource, name) for name in required)
    )


def _xls_worker_limits() -> dict[str, Any]:
    return {
        "worker_schema": XLS_WORKER_SCHEMA,
        "process_model": "fresh_isolated_python_subprocess",
        "python_isolated_flag": True,
        "address_space_bytes": XLS_WORKER_ADDRESS_SPACE_BYTES,
        "cpu_soft_seconds": XLS_WORKER_CPU_SOFT_SECONDS,
        "cpu_hard_seconds": XLS_WORKER_CPU_HARD_SECONDS,
        "wall_timeout_seconds": XLS_WORKER_WALL_TIMEOUT_SECONDS,
        "file_size_bytes": MAX_XLS_WORKER_OUTPUT_BYTES,
        "open_file_limit": XLS_WORKER_OPEN_FILE_LIMIT,
        "core_dump_bytes": 0,
        "input_body_bytes": MAX_XLS_BODY_BYTES,
        "output_body_bytes": MAX_XLS_WORKER_OUTPUT_BYTES,
    }


def _xls_worker_contract() -> dict[str, Any]:
    return {
        "schema_version": XLS_WORKER_CONTRACT_SCHEMA,
        "worker_schema": XLS_WORKER_SCHEMA,
        "parser_identity": PARSER_IDENTITY,
        "worker_module_name": XLS_WORKER_MODULE_NAME,
        "module_source_sha256": _module_source_sha256(),
        "xlrd_version": xlrd.__version__,
        "xlrd_toolchain_root": _xlrd_toolchain_root(),
        "python_implementation": sys.implementation.name,
        "python_version": list(sys.version_info[:3]),
        "worker_limits": _xls_worker_limits(),
    }


def _xls_worker_contract_valid(value: object) -> bool:
    expected = _xls_worker_contract()
    python_version = value.get("python_version") if isinstance(value, Mapping) else None
    return bool(
        type(value) is dict
        and set(value) == set(expected)
        and value.get("schema_version") == XLS_WORKER_CONTRACT_SCHEMA
        and value.get("worker_schema") == XLS_WORKER_SCHEMA
        and value.get("parser_identity") == PARSER_IDENTITY
        and value.get("worker_module_name") == XLS_WORKER_MODULE_NAME
        and _sha256_text(value.get("module_source_sha256"))
        and type(value.get("xlrd_version")) is str
        and _sha256_text(value.get("xlrd_toolchain_root"))
        and type(value.get("python_implementation")) is str
        and type(python_version) is list
        and len(python_version) == 3
        and all(_exact_int(part, minimum=0) for part in python_version)
        and _xls_worker_limits_valid(value.get("worker_limits"))
        and value == expected
    )


def _run_xls_worker(body: bytes) -> tuple[_Sheet, ...]:
    command = (
        sys.executable,
        "-I",
        "-m",
        XLS_WORKER_MODULE_NAME,
        "--xls-isolated-worker",
    )
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
    }
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=error,
            close_fds=True,
            env=environment,
            start_new_session=True,
        )
        try:
            process.communicate(
                input=body,
                timeout=XLS_WORKER_WALL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise _SemanticParseBlocked("xls_worker_wall_timeout") from exc

        output.flush()
        error.flush()
        output_size = os.fstat(output.fileno()).st_size
        error_size = os.fstat(error.fileno()).st_size
        if (
            output_size > MAX_XLS_WORKER_OUTPUT_BYTES
            or error_size > MAX_XLS_WORKER_OUTPUT_BYTES
        ):
            raise _SemanticParseBlocked("xls_worker_output_limit_exceeded")
        output.seek(0)
        error.seek(0)
        payload = output.read(MAX_XLS_WORKER_OUTPUT_BYTES + 1)
        diagnostics = error.read(MAX_XLS_WORKER_OUTPUT_BYTES + 1)
    if process.returncode != 0:
        raise _SemanticParseBlocked("xls_worker_resource_limit_exceeded")
    if diagnostics:
        raise _SemanticParseBlocked("xls_worker_diagnostic_emitted")
    return _decode_xls_worker_output(payload)


def _decode_xls_worker_output(payload: bytes) -> tuple[_Sheet, ...]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _SemanticParseBlocked("xls_worker_output_invalid") from exc
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != XLS_WORKER_SCHEMA
        or not _xls_worker_contract_valid(value.get("worker_contract"))
    ):
        raise _SemanticParseBlocked("xls_worker_output_invalid")
    if set(value) == {
        "schema_version",
        "worker_contract",
        "status",
        "reason",
    }:
        reason = str(value.get("reason") or "")
        if value.get("status") != "blocked" or reason not in _XLS_WORKER_REASONS:
            raise _SemanticParseBlocked("xls_worker_output_invalid")
        raise _SemanticParseBlocked(reason)
    if set(value) != {
        "schema_version",
        "worker_contract",
        "status",
        "sheets",
    } or value.get(
        "status"
    ) != "ok":
        raise _SemanticParseBlocked("xls_worker_output_invalid")
    raw_sheets = value.get("sheets")
    if not isinstance(raw_sheets, list) or not 0 < len(raw_sheets) <= MAX_XLS_SHEETS:
        raise _SemanticParseBlocked("xls_worker_output_invalid")
    sheets: list[_Sheet] = []
    total_cells = 0
    total_text_bytes = 0
    for ordinal, raw_sheet in enumerate(raw_sheets):
        if (
            not isinstance(raw_sheet, Mapping)
            or set(raw_sheet) != {"name", "ordinal", "rows"}
            or type(raw_sheet.get("ordinal")) is not int
            or raw_sheet.get("ordinal") != ordinal
            or not isinstance(raw_sheet.get("name"), str)
            or not str(raw_sheet["name"]).strip()
            or not isinstance(raw_sheet.get("rows"), list)
            or len(raw_sheet["rows"]) > MAX_XLS_SHEET_ROWS
        ):
            raise _SemanticParseBlocked("xls_worker_output_invalid")
        rows: list[tuple[int, Mapping[int, str]]] = []
        previous_row = 0
        for raw_row in raw_sheet["rows"]:
            if (
                not isinstance(raw_row, list)
                or len(raw_row) != 2
                or type(raw_row[0]) is not int
                or raw_row[0] <= previous_row
                or raw_row[0] > MAX_XLS_SHEET_ROWS
                or not isinstance(raw_row[1], list)
            ):
                raise _SemanticParseBlocked("xls_worker_output_invalid")
            previous_row = raw_row[0]
            values: dict[int, str] = {}
            previous_column = 0
            for raw_cell in raw_row[1]:
                if (
                    not isinstance(raw_cell, list)
                    or len(raw_cell) != 2
                    or type(raw_cell[0]) is not int
                    or raw_cell[0] <= previous_column
                    or raw_cell[0] > MAX_XLS_SHEET_COLUMNS
                    or not isinstance(raw_cell[1], str)
                    or not raw_cell[1]
                ):
                    raise _SemanticParseBlocked("xls_worker_output_invalid")
                previous_column = raw_cell[0]
                encoded_size = len(raw_cell[1].encode("utf-8"))
                total_cells += 1
                total_text_bytes += encoded_size
                if (
                    encoded_size > MAX_XLS_CELL_TEXT_BYTES
                    or total_cells > MAX_WORKBOOK_CELLS
                    or total_text_bytes > MAX_XLS_WORKBOOK_TEXT_BYTES
                ):
                    raise _SemanticParseBlocked("xls_worker_output_limits_invalid")
                values[raw_cell[0]] = raw_cell[1]
            if not values:
                raise _SemanticParseBlocked("xls_worker_output_invalid")
            rows.append((raw_row[0], values))
        sheets.append(
            _Sheet(
                name=str(raw_sheet["name"]).strip(),
                ordinal=ordinal,
                rows=tuple(rows),
            )
        )
    return tuple(sheets)


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate_json_key")
    return value


_XLS_WORKER_REASONS = frozenset(
    {
        "xls_cell_text_limit_exceeded",
        "xls_cell_type_unsupported",
        "xls_container_limits_invalid",
        "xls_error_cell_unsupported",
        "xls_numeric_cell_invalid",
        "xls_parser_diagnostic_emitted",
        "xls_sheet_dimensions_exceeded",
        "xls_sheet_inventory_invalid",
        "xls_worker_internal_error",
        "xls_worker_limit_setup_failed",
        "xls_worker_output_limit_exceeded",
        "xls_worker_resource_limit_exceeded",
        "xls_workbook_cell_limit_exceeded",
        "xls_workbook_invalid",
        "xls_workbook_text_limit_exceeded",
    }
)


def _read_shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = _read_xml(
        archive,
        "xl/sharedStrings.xml",
        "xlsx_shared_strings_invalid",
    )
    values: list[str] = []
    for item in root.findall(f"{{{_MAIN_NS}}}si"):
        values.append(
            "".join(
                node.text or ""
                for node in item.iter(f"{{{_MAIN_NS}}}t")
            )
        )
        if len(values) > MAX_SHARED_STRINGS:
            raise _SemanticParseBlocked("xlsx_shared_string_limit_exceeded")
    return tuple(values)


def _read_workbook_relationships(
    archive: zipfile.ZipFile,
) -> dict[str, str]:
    root = _read_xml(
        archive,
        "xl/_rels/workbook.xml.rels",
        "xlsx_workbook_relationships_invalid",
    )
    relationships: dict[str, str] = {}
    for row in root.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
        relation_id = str(row.attrib.get("Id") or "")
        target = str(row.attrib.get("Target") or "")
        target_mode = str(row.attrib.get("TargetMode") or "")
        if target_mode == "External":
            continue
        normalized = posixpath.normpath(posixpath.join("xl", target.lstrip("/")))
        if target.startswith("/xl/"):
            normalized = posixpath.normpath(target.lstrip("/"))
        if (
            not relation_id
            or not normalized.startswith("xl/worksheets/")
            or _unsafe_zip_name(normalized)
            or normalized not in archive.namelist()
            or relation_id in relationships
        ):
            continue
        relationships[relation_id] = normalized
    return relationships


def _read_sheet_rows(
    archive: zipfile.ZipFile,
    target: str,
    shared: Sequence[str],
) -> tuple[tuple[tuple[int, Mapping[int, str]], ...], int]:
    root = _read_xml(archive, target, "xlsx_worksheet_invalid")
    sheet_data = root.find(f"{{{_MAIN_NS}}}sheetData")
    if sheet_data is None:
        return (), 0
    result: list[tuple[int, Mapping[int, str]]] = []
    cell_count = 0
    seen_rows: set[int] = set()
    for fallback_row, row in enumerate(sheet_data, start=1):
        row_number = _positive_int(row.attrib.get("r"), fallback=fallback_row)
        if row_number in seen_rows:
            raise _SemanticParseBlocked("xlsx_duplicate_row_number")
        seen_rows.add(row_number)
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{_MAIN_NS}}}c"):
            cell_count += 1
            reference = str(cell.attrib.get("r") or "")
            match = _CELL_REF.fullmatch(reference)
            if match is None:
                raise _SemanticParseBlocked("xlsx_cell_reference_invalid")
            column = _column_number(match.group(1))
            if column in values:
                raise _SemanticParseBlocked("xlsx_duplicate_cell_reference")
            values[column] = _cell_text(cell, shared)
        if values:
            result.append((row_number, values))
    return tuple(result), cell_count


def _cell_text(cell: ElementTree.Element, shared: Sequence[str]) -> str:
    cell_type = str(cell.attrib.get("t") or "")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter(f"{{{_MAIN_NS}}}t")
        ).strip()
    value = cell.find(f"{{{_MAIN_NS}}}v")
    text = "" if value is None else str(value.text or "").strip()
    if cell_type == "s":
        try:
            ordinal = int(text)
            return shared[ordinal].strip()
        except (ValueError, IndexError) as exc:
            raise _SemanticParseBlocked("xlsx_shared_string_reference_invalid") from exc
    if cell_type == "e":
        raise _SemanticParseBlocked("xlsx_error_cell_unsupported")
    return text


def _extract_candidates(
    attachment: range_capture.ReplayedRangeAttachment,
    sheets: Sequence[_Sheet],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
    bool,
]:
    candidates: list[dict[str, Any]] = []
    terminal_rows: list[dict[str, Any]] = []
    terminal_sheets: list[dict[str, Any]] = []
    csi300_sheet_keys: list[tuple[str, int]] = []
    saw_csi300 = False
    saw_supported_schema = False
    for sheet in sheets:
        sheet_has_csi300 = any(
            _row_has_csi300(values) for _row_number, values in sheet.rows
        )
        if sheet_has_csi300:
            saw_csi300 = True
            csi300_sheet_keys.append((attachment.source_request_id, sheet.ordinal))
        row_start = len(terminal_rows)
        candidate_start = len(candidates)
        normalized_sheet_name = _normalized_label(sheet.name)
        action = (
            "add"
            if normalized_sheet_name in _ADD_SHEETS
            else "remove"
            if normalized_sheet_name in _REMOVE_SHEETS
            else None
        )
        if action is None:
            combined_header = _find_combined_header(sheet.rows)
            if combined_header is None:
                if sheet_has_csi300:
                    terminal_sheets.append(
                        _sheet_disposition(
                            attachment,
                            sheet,
                            terminal_disposition=(
                                "blocked_unsupported_csi300_schema"
                            ),
                            local_row_disposition_count=0,
                            local_valid_candidate_count=0,
                            semantic_candidate_emitted=False,
                        )
                    )
                continue
            saw_supported_schema = True
            header_number, index_column, action_columns = combined_header
            for row_number, values in sheet.rows:
                if row_number <= header_number:
                    continue
                if _canonical_index_code(values.get(index_column, "")) != "000300":
                    continue
                for combined_action, security_column, name_column in action_columns:
                    raw_security_code = values.get(security_column, "")
                    raw_security_name = (
                        values.get(name_column, "").strip()
                        if name_column is not None
                        else ""
                    )
                    security_code = _canonical_security_code(
                        raw_security_code
                    )
                    if security_code is None:
                        terminal_rows.append(
                            _row_disposition(
                                attachment,
                                sheet,
                                row_number=row_number,
                                action=combined_action,
                                source_security_column=security_column,
                                raw_security_code=raw_security_code,
                                raw_security_name=raw_security_name,
                                canonical_security_code=None,
                                terminal_disposition=(
                                    "blocked_invalid_security_code"
                                ),
                            )
                        )
                        continue
                    candidates.append(
                        _candidate_row(
                            attachment,
                            sheet,
                            row_number=row_number,
                            action=combined_action,
                            source_security_column=security_column,
                            security_code=security_code,
                            security_name=raw_security_name,
                        )
                    )
                    terminal_rows.append(
                        _row_disposition(
                            attachment,
                            sheet,
                            row_number=row_number,
                            action=combined_action,
                            source_security_column=security_column,
                            raw_security_code=raw_security_code,
                            raw_security_name=raw_security_name,
                            canonical_security_code=security_code,
                            terminal_disposition="candidate_extracted",
                        )
                    )
        else:
            header = _find_split_header(sheet.rows)
            if header is None:
                if sheet_has_csi300:
                    terminal_sheets.append(
                        _sheet_disposition(
                            attachment,
                            sheet,
                            terminal_disposition=(
                                "blocked_unsupported_csi300_schema"
                            ),
                            local_row_disposition_count=0,
                            local_valid_candidate_count=0,
                            semantic_candidate_emitted=False,
                        )
                    )
                continue
            saw_supported_schema = True
            header_number, index_column, security_column, name_column = header
            for row_number, values in sheet.rows:
                if row_number <= header_number:
                    continue
                if _canonical_index_code(values.get(index_column, "")) != "000300":
                    continue
                raw_security_code = values.get(security_column, "")
                raw_security_name = (
                    values.get(name_column, "").strip()
                    if name_column is not None
                    else ""
                )
                security_code = _canonical_security_code(raw_security_code)
                if security_code is None:
                    terminal_rows.append(
                        _row_disposition(
                            attachment,
                            sheet,
                            row_number=row_number,
                            action=action,
                            source_security_column=security_column,
                            raw_security_code=raw_security_code,
                            raw_security_name=raw_security_name,
                            canonical_security_code=None,
                            terminal_disposition=(
                                "blocked_invalid_security_code"
                            ),
                        )
                    )
                    continue
                candidates.append(
                    _candidate_row(
                        attachment,
                        sheet,
                        row_number=row_number,
                        action=action,
                        source_security_column=security_column,
                        security_code=security_code,
                        security_name=raw_security_name,
                    )
                )
                terminal_rows.append(
                    _row_disposition(
                        attachment,
                        sheet,
                        row_number=row_number,
                        action=action,
                        source_security_column=security_column,
                        raw_security_code=raw_security_code,
                        raw_security_name=raw_security_name,
                        canonical_security_code=security_code,
                        terminal_disposition="candidate_extracted",
                    )
                )
        if sheet_has_csi300:
            local_rows = terminal_rows[row_start:]
            local_valid_candidates = len(candidates) - candidate_start
            has_invalid = any(
                row["terminal_disposition"]
                == "blocked_invalid_security_code"
                for row in local_rows
            )
            terminal_sheets.append(
                _sheet_disposition(
                    attachment,
                    sheet,
                    terminal_disposition=(
                        "supported_schema_invalid_security_code"
                        if has_invalid
                        else "supported_schema_candidate_rows_terminalized"
                        if local_rows
                        else "supported_schema_without_candidate_rows"
                    ),
                    local_row_disposition_count=len(local_rows),
                    local_valid_candidate_count=local_valid_candidates,
                    semantic_candidate_emitted=(
                        local_valid_candidates > 0 and not has_invalid
                    ),
                )
            )
    _validate_csi300_sheet_terminal_cover(
        csi300_sheet_keys,
        terminal_sheets,
    )
    return (
        candidates,
        terminal_rows,
        terminal_sheets,
        saw_csi300,
        saw_supported_schema,
    )


def _candidate_row(
    attachment: range_capture.ReplayedRangeAttachment,
    sheet: _Sheet,
    *,
    row_number: int,
    action: str,
    source_security_column: int,
    security_code: str,
    security_name: str,
) -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_SCHEMA,
        "source_request_id": attachment.source_request_id,
        "attachment_sha256": attachment.attachment_sha256,
        "attachment_url": attachment.attachment_url,
        "source_announcement_ids": sorted(
            {
                str(row.get("announcement_id") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_id") or "")
            }
        ),
        "declared_announcement_publish_dates": sorted(
            {
                str(row.get("announcement_publish_date") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_publish_date") or "")
            }
        ),
        "index_code": "000300",
        "action": action,
        "security_code": security_code,
        "security_name": security_name,
        "source_sheet_name": sheet.name,
        "source_sheet_ordinal": sheet.ordinal,
        "source_row_number": row_number,
        "source_security_column": source_security_column,
        "historical_known_at": None,
        "historical_known_at_proven": False,
        "effective_at": None,
        "effective_at_proven": False,
        "pit_membership_authorized": False,
        "blockers": _evidence_blockers(attachment),
    }


def _row_disposition(
    attachment: range_capture.ReplayedRangeAttachment,
    sheet: _Sheet,
    *,
    row_number: int,
    action: str,
    source_security_column: int,
    raw_security_code: str,
    raw_security_name: str,
    canonical_security_code: str | None,
    terminal_disposition: str,
) -> dict[str, Any]:
    blockers = _evidence_blockers(attachment)
    if terminal_disposition == "blocked_invalid_security_code":
        blockers = sorted(
            set(blockers) | {"candidate_shape_security_code_invalid"}
        )
    return {
        "schema_version": ROW_DISPOSITION_SCHEMA,
        "source_request_id": attachment.source_request_id,
        "attachment_sha256": attachment.attachment_sha256,
        "attachment_url": attachment.attachment_url,
        "source_announcement_ids": sorted(
            {
                str(row.get("announcement_id") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_id") or "")
            }
        ),
        "declared_announcement_publish_dates": sorted(
            {
                str(row.get("announcement_publish_date") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_publish_date") or "")
            }
        ),
        "source_sheet_name": sheet.name,
        "source_sheet_ordinal": sheet.ordinal,
        "source_row_number": row_number,
        "source_security_column": source_security_column,
        "index_code": "000300",
        "action": action,
        "raw_security_code": raw_security_code,
        "raw_security_name": raw_security_name,
        "canonical_security_code": canonical_security_code,
        "terminal_disposition": terminal_disposition,
        "pit_membership_authorized": False,
        "blockers": blockers,
    }


def _sheet_disposition(
    attachment: range_capture.ReplayedRangeAttachment,
    sheet: _Sheet,
    *,
    terminal_disposition: str,
    local_row_disposition_count: int,
    local_valid_candidate_count: int,
    semantic_candidate_emitted: bool,
) -> dict[str, Any]:
    blockers = _evidence_blockers(attachment)
    if terminal_disposition == "blocked_unsupported_csi300_schema":
        blockers = sorted(set(blockers) | {"csi300_sheet_schema_unsupported"})
    if terminal_disposition == "supported_schema_invalid_security_code":
        blockers = sorted(
            set(blockers) | {"candidate_shape_security_code_invalid"}
        )
    return {
        "schema_version": SHEET_DISPOSITION_SCHEMA,
        "source_request_id": attachment.source_request_id,
        "attachment_sha256": attachment.attachment_sha256,
        "attachment_url": attachment.attachment_url,
        "source_announcement_ids": sorted(
            {
                str(row.get("announcement_id") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_id") or "")
            }
        ),
        "declared_announcement_publish_dates": sorted(
            {
                str(row.get("announcement_publish_date") or "")
                for row in attachment.source_announcements
                if str(row.get("announcement_publish_date") or "")
            }
        ),
        "source_sheet_name": sheet.name,
        "source_sheet_ordinal": sheet.ordinal,
        "csi300_reference_observed": True,
        "terminal_disposition": terminal_disposition,
        "local_row_disposition_count": local_row_disposition_count,
        "local_valid_candidate_count": local_valid_candidate_count,
        "semantic_candidate_emitted": semantic_candidate_emitted,
        "pit_membership_authorized": False,
        "blockers": blockers,
    }


def _evidence_blockers(
    attachment: range_capture.ReplayedRangeAttachment,
) -> list[str]:
    blockers = list(_TEMPORAL_BLOCKERS)
    if (
        attachment.attachment_extension.lower() == "xls"
        and not _xls_worker_isolation_available()
    ):
        blockers.extend(_XLS_ISOLATION_UNAVAILABLE_BLOCKERS)
    return blockers


def _validate_csi300_sheet_terminal_cover(
    expected_keys: Sequence[tuple[str, int]],
    terminal_sheets: Sequence[Mapping[str, Any]],
) -> None:
    actual_keys = [
        (
            str(row.get("source_request_id") or ""),
            int(row.get("source_sheet_ordinal", -1)),
        )
        for row in terminal_sheets
    ]
    allowed = {
        "blocked_unsupported_csi300_schema",
        "supported_schema_candidate_rows_terminalized",
        "supported_schema_invalid_security_code",
        "supported_schema_without_candidate_rows",
    }
    dispositions = {
        str(row.get("terminal_disposition") or "") for row in terminal_sheets
    }
    if (
        sorted(expected_keys) != sorted(actual_keys)
        or len(actual_keys) != len(set(actual_keys))
        or not dispositions.issubset(allowed)
    ):
        raise ValueError("csindex_csi300_sheet_terminal_cover_invalid")


def _validate_candidate_terminal_closure(
    candidates: Sequence[Mapping[str, Any]],
    terminal_rows: Sequence[Mapping[str, Any]],
) -> None:
    allowed = {
        "candidate_extracted",
        "blocked_invalid_security_code",
        "blocked_attachment_has_invalid_change_shape",
        "blocked_attachment_has_unsupported_csi300_sheet",
    }
    dispositions = {
        str(row.get("terminal_disposition") or "") for row in terminal_rows
    }
    shape_keys = [
        (
            str(row.get("source_request_id") or ""),
            int(row.get("source_sheet_ordinal", -1)),
            int(row.get("source_row_number", -1)),
            str(row.get("action") or ""),
            int(row.get("source_security_column", -1)),
        )
        for row in terminal_rows
    ]
    if not dispositions.issubset(allowed) or len(shape_keys) != len(set(shape_keys)):
        raise ValueError("csindex_change_row_terminal_inventory_invalid")

    candidate_keys = sorted(
        (
            str(row.get("source_request_id") or ""),
            int(row.get("source_sheet_ordinal", -1)),
            int(row.get("source_row_number", -1)),
            str(row.get("action") or ""),
            int(row.get("source_security_column", -1)),
            str(row.get("security_code") or ""),
        )
        for row in candidates
    )
    extracted_terminal_keys = sorted(
        (
            str(row.get("source_request_id") or ""),
            int(row.get("source_sheet_ordinal", -1)),
            int(row.get("source_row_number", -1)),
            str(row.get("action") or ""),
            int(row.get("source_security_column", -1)),
            str(row.get("canonical_security_code") or ""),
        )
        for row in terminal_rows
        if row.get("terminal_disposition") == "candidate_extracted"
    )
    if candidate_keys != extracted_terminal_keys:
        raise ValueError("csindex_candidate_terminal_closure_invalid")


def _find_split_header(
    rows: Sequence[tuple[int, Mapping[int, str]]],
) -> tuple[int, int, int, int | None] | None:
    for row_number, values in rows[:20]:
        index_columns = [
            column
            for column, value in values.items()
            if _label_has_alias(value, _INDEX_HEADER)
        ]
        security_columns = [
            column
            for column, value in values.items()
            if _label_has_alias(value, _SECURITY_HEADER)
        ]
        name_columns = [
            column
            for column, value in values.items()
            if _label_has_alias(value, _SECURITY_NAME_HEADER)
        ]
        if len(index_columns) == 1 and len(security_columns) == 1:
            return (
                row_number,
                index_columns[0],
                security_columns[0],
                name_columns[0] if len(name_columns) == 1 else None,
            )
    return None


def _find_combined_header(
    rows: Sequence[tuple[int, Mapping[int, str]]],
) -> tuple[int, int, tuple[tuple[str, int, int | None], ...]] | None:
    for ordinal, (row_number, values) in enumerate(rows[:20]):
        index_columns = [
            column
            for column, value in values.items()
            if _label_has_alias(value, _INDEX_HEADER)
        ]
        action_starts = sorted(
            (
                column,
                "add"
                if _label_has_alias(value, _ADD_SHEETS)
                else "remove",
            )
            for column, value in values.items()
            if _label_has_alias(value, _ADD_SHEETS)
            or _label_has_alias(value, _REMOVE_SHEETS)
        )
        if len(index_columns) != 1 or not action_starts:
            continue
        for code_header_number, header_values in rows[ordinal + 1 : ordinal + 4]:
            action_columns: list[tuple[str, int, int | None]] = []
            for action_ordinal, (start, action) in enumerate(action_starts):
                end = (
                    action_starts[action_ordinal + 1][0]
                    if action_ordinal + 1 < len(action_starts)
                    else MAX_XLS_SHEET_COLUMNS + 1
                )
                code_columns = [
                    column
                    for column, value in header_values.items()
                    if start <= column < end
                    and _label_has_alias(value, _SECURITY_HEADER)
                ]
                name_columns = [
                    column
                    for column, value in header_values.items()
                    if start <= column < end
                    and _label_has_alias(value, _SECURITY_NAME_HEADER)
                ]
                if len(code_columns) == 1:
                    action_columns.append(
                        (
                            action,
                            code_columns[0],
                            name_columns[0] if len(name_columns) == 1 else None,
                        )
                    )
            if len(action_columns) == len(action_starts):
                return (
                    code_header_number,
                    index_columns[0],
                    tuple(action_columns),
                )
    return None


def _row_has_csi300(values: Mapping[int, str]) -> bool:
    return any(
        _canonical_index_code(value) == "000300"
        or _normalized_label(value) in {"沪深300", "CSI300"}
        for value in values.values()
    )


def _canonical_index_code(value: str) -> str | None:
    compact = value.strip().upper().removesuffix(".SH").removesuffix(".XSHG")
    if not _SECURITY_CODE.fullmatch(compact):
        return None
    integer = compact.split(".", maxsplit=1)[0]
    return integer.zfill(6) if len(integer) <= 6 else None


def _canonical_security_code(value: str) -> str | None:
    compact = value.strip()
    if not _SECURITY_CODE.fullmatch(compact):
        return None
    integer = compact.split(".", maxsplit=1)[0]
    return integer.zfill(6) if len(integer) <= 6 else None


def _normalized_label(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value).upper()


def _label_has_alias(value: str, aliases: frozenset[str]) -> bool:
    labels = {
        _normalized_label(part)
        for part in re.split(r"[\r\n/／]+", value)
        if part.strip()
    }
    labels.add(_normalized_label(value))
    return not labels.isdisjoint(aliases)


def _read_xml(
    archive: zipfile.ZipFile,
    name: str,
    reason: str,
) -> ElementTree.Element:
    try:
        info = archive.getinfo(name)
        if info.file_size > MAX_XML_BYTES:
            raise _SemanticParseBlocked("xlsx_xml_part_limit_exceeded")
        payload = archive.read(info)
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            raise _SemanticParseBlocked("xlsx_xml_entity_declaration_rejected")
        return ElementTree.fromstring(payload)
    except _SemanticParseBlocked:
        raise
    except (KeyError, OSError, ElementTree.ParseError, RuntimeError) as exc:
        raise _SemanticParseBlocked(reason) from exc


def _unsafe_zip_name(name: str) -> bool:
    return (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
    )


def _positive_int(value: Any, *, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        result = int(str(value))
    except ValueError as exc:
        raise _SemanticParseBlocked("xlsx_row_number_invalid") from exc
    if result <= 0:
        raise _SemanticParseBlocked("xlsx_row_number_invalid")
    return result


def _column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _module_source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _xlrd_toolchain_root() -> str:
    package_root = Path(str(xlrd.__file__)).resolve().parent
    source_files = sorted(package_root.rglob("*.py"))
    if not source_files:
        raise RuntimeError("xlrd_toolchain_source_inventory_empty")
    return canonical_hash(
        {
            "schema_version": "xlrd_python_toolchain_root_v1",
            "xlrd_version": xlrd.__version__,
            "source_files": [
                {
                    "relative_path": path.relative_to(package_root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in source_files
            ],
        }
    )


def _implementation_root() -> str:
    return canonical_hash(
        {
            "parser_identity": PARSER_IDENTITY,
            "module_source_sha256": _module_source_sha256(),
            "xlrd_toolchain_root": _xlrd_toolchain_root(),
            "xls_worker_limits": _xls_worker_limits(),
            "xls_worker_contract_root": canonical_hash(
                _xls_worker_contract()
            ),
            "python_runtime": {
                "implementation": sys.implementation.name,
                "version": list(sys.version_info[:3]),
            },
            "limits": {
                "max_zip_entries": MAX_ZIP_ENTRIES,
                "max_zip_uncompressed_bytes": MAX_ZIP_UNCOMPRESSED_BYTES,
                "max_xml_bytes": MAX_XML_BYTES,
                "max_workbook_cells": MAX_WORKBOOK_CELLS,
                "max_shared_strings": MAX_SHARED_STRINGS,
                "max_xls_body_bytes": MAX_XLS_BODY_BYTES,
                "max_xls_sheets": MAX_XLS_SHEETS,
                "max_xls_sheet_rows": MAX_XLS_SHEET_ROWS,
                "max_xls_sheet_columns": MAX_XLS_SHEET_COLUMNS,
                "max_xls_cell_text_bytes": MAX_XLS_CELL_TEXT_BYTES,
                "max_xls_workbook_text_bytes": MAX_XLS_WORKBOOK_TEXT_BYTES,
            },
            "xlrd_version": xlrd.__version__,
            "source": "\n".join(
                inspect.getsource(value)
                for value in (
                    _parse_attachment,
                    _read_xlsx,
                    _read_shared_strings,
                    _read_workbook_relationships,
                    _read_sheet_rows,
                    _cell_text,
                    _read_xls,
                    _read_xls_in_process,
                    _xls_cell_text,
                    _xls_worker_isolation_available,
                    _xls_worker_limits,
                    _xls_worker_contract,
                    _xls_worker_contract_valid,
                    _run_xls_worker,
                    _decode_xls_worker_output,
                    _apply_xls_worker_limits,
                    _xls_worker_response,
                    _extract_candidates,
                    _candidate_row,
                    _row_disposition,
                    _sheet_disposition,
                    _evidence_blockers,
                    _validate_csi300_sheet_terminal_cover,
                    _validate_candidate_terminal_closure,
                    _find_split_header,
                    _find_combined_header,
                    _canonical_index_code,
                    _canonical_security_code,
                    _label_has_alias,
                )
            ),
        }
    )


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _apply_xls_worker_limits() -> None:
    if not _xls_worker_isolation_available() or resource is None:
        raise RuntimeError("xls_worker_resource_isolation_unavailable")

    def apply_limit(kind: int, soft: int, hard: int) -> None:
        _current_soft, current_hard = resource.getrlimit(kind)
        if current_hard != resource.RLIM_INFINITY:
            hard = min(hard, int(current_hard))
        soft = min(soft, hard)
        resource.setrlimit(kind, (soft, hard))

    apply_limit(resource.RLIMIT_CORE, 0, 0)
    apply_limit(
        resource.RLIMIT_FSIZE,
        MAX_XLS_WORKER_OUTPUT_BYTES,
        MAX_XLS_WORKER_OUTPUT_BYTES,
    )
    apply_limit(
        resource.RLIMIT_NOFILE,
        XLS_WORKER_OPEN_FILE_LIMIT,
        XLS_WORKER_OPEN_FILE_LIMIT,
    )
    apply_limit(
        resource.RLIMIT_AS,
        XLS_WORKER_ADDRESS_SPACE_BYTES,
        XLS_WORKER_ADDRESS_SPACE_BYTES,
    )
    apply_limit(
        resource.RLIMIT_CPU,
        XLS_WORKER_CPU_SOFT_SECONDS,
        XLS_WORKER_CPU_HARD_SECONDS,
    )


def _xls_worker_response() -> dict[str, Any]:
    worker_contract = _xls_worker_contract()
    try:
        _apply_xls_worker_limits()
    except Exception:
        return {
            "schema_version": XLS_WORKER_SCHEMA,
            "worker_contract": worker_contract,
            "status": "blocked",
            "reason": "xls_worker_limit_setup_failed",
        }
    try:
        body = sys.stdin.buffer.read(MAX_XLS_BODY_BYTES + 1)
        if len(body) > MAX_XLS_BODY_BYTES:
            raise _SemanticParseBlocked("xls_container_limits_invalid")
        sheets = _read_xls_in_process(body)
        return {
            "schema_version": XLS_WORKER_SCHEMA,
            "worker_contract": worker_contract,
            "status": "ok",
            "sheets": [
                {
                    "name": sheet.name,
                    "ordinal": sheet.ordinal,
                    "rows": [
                        [
                            row_number,
                            [
                                [column, value]
                                for column, value in sorted(values.items())
                            ],
                        ]
                        for row_number, values in sheet.rows
                    ],
                }
                for sheet in sheets
            ],
        }
    except _SemanticParseBlocked as exc:
        reason = str(exc)
        return {
            "schema_version": XLS_WORKER_SCHEMA,
            "worker_contract": worker_contract,
            "status": "blocked",
            "reason": (
                reason if reason in _XLS_WORKER_REASONS else "xls_worker_internal_error"
            ),
        }
    except (Exception, MemoryError):
        return {
            "schema_version": XLS_WORKER_SCHEMA,
            "worker_contract": worker_contract,
            "status": "blocked",
            "reason": "xls_worker_resource_limit_exceeded",
        }


def _xls_worker_main() -> int:
    response = _xls_worker_response()
    payload = _json_bytes(response)
    if len(payload) > MAX_XLS_WORKER_OUTPUT_BYTES:
        payload = _json_bytes(
            {
                "schema_version": XLS_WORKER_SCHEMA,
                "worker_contract": _xls_worker_contract(),
                "status": "blocked",
                "reason": "xls_worker_output_limit_exceeded",
            }
        )
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build immutable, fail-closed CSI attachment semantic evidence."
        )
    )
    parser.add_argument("--capture")
    parser.add_argument("--output-root")
    parser.add_argument("--validate")
    parser.add_argument("--source-capture")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        result = validate_csindex_attachment_semantic_evidence(
            args.validate,
            source_capture=args.source_capture,
        )
    else:
        if not args.capture or not args.output_root:
            raise SystemExit("--capture and --output-root are required")
        result = build_csindex_attachment_semantic_evidence(
            args.capture,
            args.output_root,
        )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return (
        2
        if result.get("validation_status")
        == "blocked_source_resolution_required"
        else 0
    )


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess.
    if sys.argv[1:] == ["--xls-isolated-worker"]:
        raise SystemExit(_xls_worker_main())
    raise SystemExit(main())
