"""Task 055-G governed Fee Schedule v2 production workflow.

The workflow is intentionally staged and content addressed:

fee plan -> document acquisition -> document verification -> rule extraction
-> schedule publication -> independent verification.

Statutory rule values are never accepted from callers. They are parsed from
the acquired official document bytes by allowlisted parsers and are re-parsed
during schedule validation. Modeled execution costs are derived from the
immutable Task 055-A policy seal.
"""

from __future__ import annotations

import hashlib
import html
import http.client
import inspect
import json
import math
import os
import re
import shutil
import ssl
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlparse

from task_055_a.policy import PREREGISTERED_SCENARIOS


class FeeWorkflowError(RuntimeError):
    """Fail-closed Fee Schedule workflow error."""


FEE_PLAN_SCHEMA = "task055g_fee_plan_v2"
FEE_ACQUISITION_SCHEMA = "task055g_fee_document_acquisition_v2"
FEE_DOCUMENT_VERIFICATION_SCHEMA = "task055g_fee_document_verification_v2"
FEE_EXTRACTION_SCHEMA = "task055g_fee_rule_extraction_v2"
FEE_SCHEDULE_SCHEMA = "task055g_fee_schedule_v2"
FEE_INDEPENDENT_VERIFICATION_SCHEMA = "task055g_fee_independent_verification_v2"
TASK055A_POLICY_SCHEMA = "task055a_portfolio_diagnostic_policy_seal_v1"

STATUTORY_COMPONENTS = (
    "stamp_duty",
    "transfer_fee",
    "handling_fee",
    "securities_management_fee",
)
MODELED_COMPONENTS = ("commission", "slippage", "impact")
ALL_COMPONENTS = STATUTORY_COMPONENTS + MODELED_COMPONENTS
MARKETS = ("SSE", "SZSE")
SIDES = ("BUY", "SELL")
MAX_OFFICIAL_DOCUMENTS = 20

OFFICIAL_HOSTS = {
    "www.gov.cn",
    "gov.cn",
    "www.chinatax.gov.cn",
    "chinatax.gov.cn",
    "www.mof.gov.cn",
    "mof.gov.cn",
    "www.sse.com.cn",
    "sse.com.cn",
    "www.szse.cn",
    "szse.cn",
    "www.chinaclear.cn",
    "chinaclear.cn",
    "www.csrc.gov.cn",
    "csrc.gov.cn",
}
OFFICIAL_SUFFIXES = (
    ".gov.cn",
    ".chinatax.gov.cn",
    ".mof.gov.cn",
    ".sse.com.cn",
    ".szse.cn",
    ".chinaclear.cn",
    ".csrc.gov.cn",
)

PARSER_COMPONENTS = {
    "cn_stamp_duty_rate_v1": "stamp_duty",
    "cn_transfer_fee_rate_v1": "transfer_fee",
    "cn_handling_fee_rate_v1": "handling_fee",
    "cn_securities_management_fee_rate_v1": "securities_management_fee",
    "cn_stamp_duty_baseline_2008_v2": "stamp_duty",
    "cn_stamp_duty_half_2023_v2": "stamp_duty",
    "cn_transfer_fee_2015_v2": "transfer_fee",
    "cn_transfer_fee_2022_v2": "transfer_fee",
    "cn_handling_fee_2015_v2": "handling_fee",
    "cn_handling_fee_2023_v2": "handling_fee",
    "cn_securities_management_fee_2012_v2": "securities_management_fee",
}
PRODUCTION_PARSERS = {
    "cn_stamp_duty_baseline_2008_v2",
    "cn_stamp_duty_half_2023_v2",
    "cn_transfer_fee_2015_v2",
    "cn_transfer_fee_2022_v2",
    "cn_handling_fee_2015_v2",
    "cn_handling_fee_2023_v2",
    "cn_securities_management_fee_2012_v2",
}
COMPONENT_KEYWORDS = {
    "stamp_duty": ("证券交易印花税", "股票交易印花税", "印花税"),
    "transfer_fee": ("股票交易过户费", "证券交易过户费", "过户费"),
    "handling_fee": ("证券交易经手费", "股票交易经手费", "经手费"),
    "securities_management_fee": ("证券交易监管费", "证券管理费", "证管费"),
}
FORBIDDEN_EXTRACTOR_FIELDS = {
    "rate",
    "effective_start",
    "effective_end",
    "side",
    "sides",
    "market",
    "markets",
    "basis",
    "rounding",
    "minimum_cny",
    "explicit_zero",
    "clause_text",
}


def official_fee_workflow_spec() -> dict[str, list[dict[str, Any]]]:
    """Return the fixed production document and parser plan.

    Callers may select the output root, but cannot inject statutory rates,
    effective dates, market scope, direction, or calculation values.
    """

    documents = [
        {
            "document_id": "stamp_history_context",
            "publisher": "国家税务总局",
            "request_url": "https://www.chinatax.gov.cn/chinatax/n810219/n810780/c5211220/content.html",
        },
        {
            "document_id": "stamp_tax_law",
            "publisher": "国家税务总局法规库",
            "request_url": "https://fgk.chinatax.gov.cn/zcfgk/c100009/c5193058/content.html",
        },
        {
            "document_id": "stamp_half_2023",
            "publisher": "国家税务总局山西省税务局",
            "request_url": "https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448",
        },
        {
            "document_id": "fee_reform_2015",
            "publisher": "中国证券登记结算有限责任公司",
            "request_url": "https://www.chinaclear.cn/zdjs/gszb/201507/c0fea37f8d154509b588903e05c965b0.shtml",
        },
        {
            "document_id": "transfer_fee_2022",
            "publisher": "中国证券登记结算有限责任公司",
            "request_url": "https://www.chinaclear.cn/zdjs/gszb/202204/837e3c5031104aa099d6597ba381342a.shtml",
        },
        {
            "document_id": "handling_fee_2023",
            "publisher": "中国证券监督管理委员会",
            "request_url": "https://www.csrc.gov.cn/csrc/c100028/c7426794/content.shtml",
        },
        {
            "document_id": "management_fee_2012",
            "publisher": "中国证券监督管理委员会",
            "request_url": "https://www.csrc.gov.cn/csrc/c100028/c1002446/content.shtml",
        },
    ]
    extractors = [
        {
            "extractor_id": "stamp_2008",
            "document_id": "stamp_history_context",
            "supporting_document_ids": ["stamp_tax_law"],
            "parser_id": "cn_stamp_duty_baseline_2008_v2",
        },
        {
            "extractor_id": "stamp_2023",
            "document_id": "stamp_half_2023",
            "supporting_document_ids": ["stamp_history_context", "stamp_tax_law"],
            "parser_id": "cn_stamp_duty_half_2023_v2",
        },
        {
            "extractor_id": "transfer_2015",
            "document_id": "fee_reform_2015",
            "parser_id": "cn_transfer_fee_2015_v2",
        },
        {
            "extractor_id": "transfer_2022",
            "document_id": "transfer_fee_2022",
            "parser_id": "cn_transfer_fee_2022_v2",
        },
        {
            "extractor_id": "handling_2015",
            "document_id": "fee_reform_2015",
            "parser_id": "cn_handling_fee_2015_v2",
        },
        {
            "extractor_id": "handling_2023",
            "document_id": "handling_fee_2023",
            "parser_id": "cn_handling_fee_2023_v2",
        },
        {
            "extractor_id": "management_2012",
            "document_id": "management_fee_2012",
            "parser_id": "cn_securities_management_fee_2012_v2",
        },
    ]
    return {"documents": documents, "extractors": extractors}


def build_fee_plan(
    *,
    output_root: str | Path,
    policy_seal: str | Path,
    simulation_start: str,
    simulation_end: str,
    documents: Iterable[Mapping[str, Any]],
    extractors: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Seal the official URL and parser plan before any network access."""

    start = _date(simulation_start)
    end = _date(simulation_end)
    if start > end:
        raise FeeWorkflowError("fee_plan_interval_invalid")
    policy_path, policy = _validate_policy_seal(policy_seal)
    normalized_documents = _normalize_document_specs(documents)
    normalized_extractors = _normalize_extractor_specs(extractors, normalized_documents)
    source_hashes = semantic_source_hashes()
    policy_relative = "inputs/task055a_policy_seal.json"
    policy_sha = _sha256_file(policy_path)
    semantic = {
        "schema_version": FEE_PLAN_SCHEMA,
        "status": "sealed",
        "simulation_start": start,
        "simulation_end": end,
        "policy_seal_hash": policy["content_hash"],
        "policy_seal_sha256": policy_sha,
        "policy_seal_relative_path": policy_relative,
        "documents": normalized_documents,
        "extractors": normalized_extractors,
        "max_documents": MAX_OFFICIAL_DOCUMENTS,
        "network_contract": {
            "https_only": True,
            "same_host_redirect_only": True,
            "presealed_urls_only": True,
            "max_documents": MAX_OFFICIAL_DOCUMENTS,
        },
        "semantic_source_hashes": source_hashes,
        "builder_semantic_hash": canonical_hash(source_hashes),
    }
    return _publish_generation(
        output_root=output_root,
        prefix="fee_plan",
        manifest_name="fee_plan.json",
        semantic=semantic,
        files={policy_relative: policy_path},
    )


def validate_fee_plan(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "fee_plan.json")
    manifest = _load_json(manifest_path)
    _validate_manifest_hash(manifest, FEE_PLAN_SCHEMA, "sealed")
    if manifest.get("semantic_source_hashes") != semantic_source_hashes():
        raise FeeWorkflowError("fee_plan_semantic_source_hash_mismatch")
    if manifest.get("builder_semantic_hash") != canonical_hash(semantic_source_hashes()):
        raise FeeWorkflowError("fee_plan_builder_semantic_hash_mismatch")
    if manifest.get("network_contract") != {
        "https_only": True,
        "same_host_redirect_only": True,
        "presealed_urls_only": True,
        "max_documents": MAX_OFFICIAL_DOCUMENTS,
    }:
        raise FeeWorkflowError("fee_plan_network_contract_invalid")
    documents = _normalize_document_specs(manifest.get("documents") or ())
    extractors = _normalize_extractor_specs(manifest.get("extractors") or (), documents)
    if documents != manifest.get("documents") or extractors != manifest.get("extractors"):
        raise FeeWorkflowError("fee_plan_normalization_mismatch")
    policy_relative = _relative_path(manifest.get("policy_seal_relative_path"))
    policy_path = _contained_file(manifest_path.parent, policy_relative)
    if _sha256_file(policy_path) != manifest.get("policy_seal_sha256"):
        raise FeeWorkflowError("fee_plan_policy_seal_sha_mismatch")
    _, policy = _validate_policy_seal(policy_path)
    if policy.get("content_hash") != manifest.get("policy_seal_hash"):
        raise FeeWorkflowError("fee_plan_policy_seal_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def acquire_fee_documents(
    *,
    plan: str | Path,
    output_root: str | Path,
    allow_network: bool,
    fetcher: Callable[[str], Mapping[str, Any]] | None = None,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    """Acquire exactly the presealed official HTTPS documents."""

    fee_plan = validate_fee_plan(plan)
    if not allow_network:
        raise FeeWorkflowError("fee_document_network_authorization_required")
    if fetcher is not None and not allow_synthetic_test_fixture:
        raise FeeWorkflowError("synthetic_fee_transport_forbidden")
    evidence_scope = "synthetic_test_fixture" if fetcher is not None else "real_official_https"
    documents: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    file_payloads: dict[str, bytes | Path] = {}
    for logical_index, spec in enumerate(fee_plan["documents"]):
        request_url = str(spec["request_url"])
        result = dict(fetcher(request_url)) if fetcher is not None else _fetch_https(request_url)
        body = bytes(result.get("body") or b"")
        if not body:
            raise FeeWorkflowError("fee_document_empty_body")
        final_url = str(result.get("final_url") or request_url)
        _validate_same_host_redirect(request_url, final_url)
        status = int(result.get("http_status") or 0)
        if status != 200:
            raise FeeWorkflowError(f"fee_document_http_status_invalid:{status}")
        if result.get("tls_verified") is not True or result.get("hostname_verified") is not True:
            raise FeeWorkflowError("fee_document_tls_or_hostname_invalid")
        certificate_sha = str(result.get("peer_certificate_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", certificate_sha):
            raise FeeWorkflowError("fee_document_certificate_evidence_invalid")
        redirect_chain = [str(value) for value in (result.get("redirect_chain") or [request_url, final_url])]
        if not redirect_chain or redirect_chain[0] != request_url or redirect_chain[-1] != final_url:
            raise FeeWorkflowError("fee_document_redirect_chain_invalid")
        for redirect_url in redirect_chain:
            _validate_same_host_redirect(request_url, redirect_url)
        body_sha = hashlib.sha256(body).hexdigest()
        suffix = Path(urlparse(final_url).path).suffix.lower()
        if suffix not in {".html", ".htm", ".pdf", ".txt"}:
            suffix = ".bin"
        relative_path = f"documents/{spec['document_id']}{suffix}"
        headers = {str(key).lower(): str(value) for key, value in dict(result.get("response_headers") or {}).items()}
        transport = {
            "logical_index": logical_index,
            "document_id": spec["document_id"],
            "request_url": request_url,
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "http_status": status,
            "tls_verified": True,
            "hostname_verified": True,
            "peer_certificate_sha256": certificate_sha,
            "retrieved_at": str(result.get("retrieved_at") or ""),
            "response_headers_sha256": canonical_hash(headers),
            "body_sha256": body_sha,
            "body_size_bytes": len(body),
            "evidence_scope": evidence_scope,
        }
        if not transport["retrieved_at"]:
            raise FeeWorkflowError("fee_document_retrieved_at_missing")
        transport["transport_receipt_hash"] = canonical_hash(transport)
        ledger.append(transport)
        documents.append(
            {
                "document_id": spec["document_id"],
                "publisher": spec["publisher"],
                "request_url": request_url,
                "final_url": final_url,
                "artifact_relative_path": relative_path,
                "sha256": body_sha,
                "size_bytes": len(body),
                "transport_receipt_hash": transport["transport_receipt_hash"],
            }
        )
        file_payloads[relative_path] = body
    ledger_bytes = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        for row in ledger
    )
    file_payloads["transport_ledger.jsonl"] = ledger_bytes
    semantic = {
        "schema_version": FEE_ACQUISITION_SCHEMA,
        "status": "passed",
        "evidence_scope": evidence_scope,
        "plan_content_hash": fee_plan["content_hash"],
        "policy_seal_hash": fee_plan["policy_seal_hash"],
        "documents": sorted(documents, key=lambda row: row["document_id"]),
        "transport_ledger_relative_path": "transport_ledger.jsonl",
        "transport_ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "transport_ledger_root": canonical_hash(ledger),
        "document_merkle_root": canonical_hash(
            sorted(
                ({"document_id": row["document_id"], "sha256": row["sha256"], "size_bytes": row["size_bytes"]} for row in documents),
                key=lambda row: row["document_id"],
            )
        ),
        "source_hash": _source_hash(Path(__file__)),
    }
    return _publish_generation(
        output_root=output_root,
        prefix="fee_document_acquisition",
        manifest_name="fee_document_acquisition.json",
        semantic=semantic,
        files=file_payloads,
    )


def validate_fee_document_acquisition(
    path: str | Path,
    *,
    plan: str | Path | None = None,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "fee_document_acquisition.json")
    manifest = _load_json(manifest_path)
    _validate_manifest_hash(manifest, FEE_ACQUISITION_SCHEMA, "passed")
    if manifest.get("source_hash") != _source_hash(Path(__file__)):
        raise FeeWorkflowError("fee_acquisition_source_hash_mismatch")
    scope = str(manifest.get("evidence_scope") or "")
    if scope != "real_official_https" and not allow_synthetic_test_fixture:
        raise FeeWorkflowError("synthetic_fee_document_acquisition_forbidden")
    fee_plan = validate_fee_plan(plan) if plan is not None else None
    if fee_plan is not None:
        if fee_plan["content_hash"] != manifest.get("plan_content_hash"):
            raise FeeWorkflowError("fee_acquisition_plan_hash_mismatch")
        if fee_plan["policy_seal_hash"] != manifest.get("policy_seal_hash"):
            raise FeeWorkflowError("fee_acquisition_policy_hash_mismatch")
    ledger_path = _contained_file(manifest_path.parent, _relative_path(manifest.get("transport_ledger_relative_path")))
    if _sha256_file(ledger_path) != manifest.get("transport_ledger_sha256"):
        raise FeeWorkflowError("fee_transport_ledger_sha_mismatch")
    ledger = _load_jsonl(ledger_path)
    if canonical_hash(ledger) != manifest.get("transport_ledger_root"):
        raise FeeWorkflowError("fee_transport_ledger_root_mismatch")
    if len(ledger) != len(manifest.get("documents") or ()):
        raise FeeWorkflowError("fee_transport_ledger_count_mismatch")
    ledger_by_id = {str(row.get("document_id")): row for row in ledger}
    documents = list(manifest.get("documents") or ())
    if len({str(row.get("document_id")) for row in documents}) != len(documents):
        raise FeeWorkflowError("fee_acquisition_document_identity_duplicate")
    if fee_plan is not None:
        expected_documents = {
            row["document_id"]: {
                "publisher": row["publisher"],
                "request_url": row["request_url"],
            }
            for row in fee_plan["documents"]
        }
        acquired_documents = {
            str(row.get("document_id")): {
                "publisher": str(row.get("publisher") or ""),
                "request_url": str(row.get("request_url") or ""),
            }
            for row in documents
        }
        if acquired_documents != expected_documents:
            raise FeeWorkflowError("fee_acquisition_presealed_document_set_mismatch")
    for row in documents:
        document_id = str(row.get("document_id") or "")
        transport = ledger_by_id.get(document_id)
        if transport is None:
            raise FeeWorkflowError("fee_acquisition_transport_receipt_missing")
        unsigned = {key: value for key, value in transport.items() if key != "transport_receipt_hash"}
        if transport.get("transport_receipt_hash") != canonical_hash(unsigned):
            raise FeeWorkflowError("fee_acquisition_transport_receipt_hash_invalid")
        _validate_same_host_redirect(str(row.get("request_url") or ""), str(row.get("final_url") or ""))
        if transport.get("request_url") != row.get("request_url") or transport.get("final_url") != row.get("final_url"):
            raise FeeWorkflowError("fee_acquisition_transport_url_mismatch")
        if transport.get("http_status") != 200 or transport.get("tls_verified") is not True or transport.get("hostname_verified") is not True:
            raise FeeWorkflowError("fee_acquisition_transport_security_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(transport.get("peer_certificate_sha256") or "")):
            raise FeeWorkflowError("fee_acquisition_certificate_evidence_invalid")
        if not transport.get("retrieved_at") or not transport.get("response_headers_sha256"):
            raise FeeWorkflowError("fee_acquisition_transport_metadata_missing")
        redirect_chain = [str(value) for value in (transport.get("redirect_chain") or ())]
        if not redirect_chain or redirect_chain[0] != row.get("request_url") or redirect_chain[-1] != row.get("final_url"):
            raise FeeWorkflowError("fee_acquisition_redirect_chain_invalid")
        for redirect_url in redirect_chain:
            _validate_same_host_redirect(str(row.get("request_url") or ""), redirect_url)
        if transport.get("evidence_scope") != scope:
            raise FeeWorkflowError("fee_acquisition_evidence_scope_mismatch")
        artifact = _contained_file(manifest_path.parent, _relative_path(row.get("artifact_relative_path")))
        if artifact.is_symlink() or _sha256_file(artifact) != row.get("sha256"):
            raise FeeWorkflowError("fee_acquisition_document_sha_mismatch")
        if artifact.stat().st_size != int(row.get("size_bytes") or -1):
            raise FeeWorkflowError("fee_acquisition_document_size_mismatch")
        if transport.get("body_sha256") != row.get("sha256") or transport.get("body_size_bytes") != row.get("size_bytes"):
            raise FeeWorkflowError("fee_acquisition_transport_body_mismatch")
        if transport.get("transport_receipt_hash") != row.get("transport_receipt_hash"):
            raise FeeWorkflowError("fee_acquisition_document_receipt_mismatch")
    merkle = canonical_hash(
        sorted(
            ({"document_id": row["document_id"], "sha256": row["sha256"], "size_bytes": row["size_bytes"]} for row in documents),
            key=lambda row: row["document_id"],
        )
    )
    if merkle != manifest.get("document_merkle_root"):
        raise FeeWorkflowError("fee_acquisition_document_merkle_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def publish_fee_document_verification(
    *,
    output_root: str | Path,
    plan: str | Path,
    acquisition: str | Path,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    fee_plan = validate_fee_plan(plan)
    acquired = validate_fee_document_acquisition(
        acquisition,
        plan=plan,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    semantic = {
        "schema_version": FEE_DOCUMENT_VERIFICATION_SCHEMA,
        "status": "passed",
        "plan_content_hash": fee_plan["content_hash"],
        "acquisition_content_hash": acquired["content_hash"],
        "document_merkle_root": acquired["document_merkle_root"],
        "transport_ledger_root": acquired["transport_ledger_root"],
        "evidence_scope": acquired["evidence_scope"],
        "verifier_source_hash": _source_hash(Path(__file__)),
    }
    return _publish_generation(
        output_root=output_root,
        prefix="fee_document_verification",
        manifest_name="fee_document_verification.json",
        semantic=semantic,
    )


def validate_fee_document_verification(
    path: str | Path,
    *,
    plan: str | Path,
    acquisition: str | Path,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "fee_document_verification.json")
    manifest = _load_json(manifest_path)
    _validate_manifest_hash(manifest, FEE_DOCUMENT_VERIFICATION_SCHEMA, "passed")
    fee_plan = validate_fee_plan(plan)
    acquired = validate_fee_document_acquisition(
        acquisition,
        plan=plan,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    expected = {
        "plan_content_hash": fee_plan["content_hash"],
        "acquisition_content_hash": acquired["content_hash"],
        "document_merkle_root": acquired["document_merkle_root"],
        "transport_ledger_root": acquired["transport_ledger_root"],
        "evidence_scope": acquired["evidence_scope"],
        "verifier_source_hash": _source_hash(Path(__file__)),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise FeeWorkflowError(f"fee_document_verification_{key}_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def extract_fee_rules(
    *,
    output_root: str | Path,
    plan: str | Path,
    acquisition: str | Path,
    document_verification: str | Path,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    fee_plan = validate_fee_plan(plan)
    acquired = validate_fee_document_acquisition(
        acquisition,
        plan=plan,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    verification = validate_fee_document_verification(
        document_verification,
        plan=plan,
        acquisition=acquisition,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    acquisition_path = Path(acquired["manifest_path"])
    document_by_id = {str(row["document_id"]): row for row in acquired["documents"]}
    assertions = []
    for extractor in fee_plan["extractors"]:
        document = document_by_id[extractor["document_id"]]
        document_path = _contained_file(acquisition_path.parent, document["artifact_relative_path"])
        assertions.append(
            _extract_assertion(
                document_path,
                document,
                extractor,
                document_by_id=document_by_id,
                acquisition_root=acquisition_path.parent,
            )
        )
    assertions.sort(key=lambda row: row["assertion_id"])
    semantic = {
        "schema_version": FEE_EXTRACTION_SCHEMA,
        "status": "passed",
        "plan_content_hash": fee_plan["content_hash"],
        "acquisition_content_hash": acquired["content_hash"],
        "document_verification_content_hash": verification["content_hash"],
        "policy_seal_hash": fee_plan["policy_seal_hash"],
        "evidence_scope": acquired["evidence_scope"],
        "parser_source_hash": _source_hash(Path(__file__)),
        "assertions": assertions,
        "assertion_root": canonical_hash(assertions),
    }
    return _publish_generation(
        output_root=output_root,
        prefix="fee_rule_extraction",
        manifest_name="fee_rule_extraction.json",
        semantic=semantic,
    )


def validate_fee_rule_extraction(
    path: str | Path,
    *,
    plan: str | Path,
    acquisition: str | Path,
    document_verification: str | Path,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "fee_rule_extraction.json")
    manifest = _load_json(manifest_path)
    _validate_manifest_hash(manifest, FEE_EXTRACTION_SCHEMA, "passed")
    fee_plan = validate_fee_plan(plan)
    acquired = validate_fee_document_acquisition(
        acquisition,
        plan=plan,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    verification = validate_fee_document_verification(
        document_verification,
        plan=plan,
        acquisition=acquisition,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    if manifest.get("parser_source_hash") != _source_hash(Path(__file__)):
        raise FeeWorkflowError("fee_extraction_parser_source_hash_mismatch")
    expected_parents = {
        "plan_content_hash": fee_plan["content_hash"],
        "acquisition_content_hash": acquired["content_hash"],
        "document_verification_content_hash": verification["content_hash"],
        "policy_seal_hash": fee_plan["policy_seal_hash"],
        "evidence_scope": acquired["evidence_scope"],
    }
    for key, value in expected_parents.items():
        if manifest.get(key) != value:
            raise FeeWorkflowError(f"fee_extraction_{key}_mismatch")
    acquisition_path = Path(acquired["manifest_path"])
    document_by_id = {str(row["document_id"]): row for row in acquired["documents"]}
    expected_assertions = []
    for extractor in fee_plan["extractors"]:
        document = document_by_id[extractor["document_id"]]
        document_path = _contained_file(acquisition_path.parent, document["artifact_relative_path"])
        expected_assertions.append(
            _extract_assertion(
                document_path,
                document,
                extractor,
                document_by_id=document_by_id,
                acquisition_root=acquisition_path.parent,
            )
        )
    expected_assertions.sort(key=lambda row: row["assertion_id"])
    if expected_assertions != manifest.get("assertions"):
        raise FeeWorkflowError("fee_extraction_assertion_reparse_mismatch")
    if canonical_hash(expected_assertions) != manifest.get("assertion_root"):
        raise FeeWorkflowError("fee_extraction_assertion_root_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def publish_fee_schedule_v2(
    *,
    output_root: str | Path,
    plan: str | Path,
    acquisition: str | Path,
    document_verification: str | Path,
    extraction: str | Path,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    fee_plan = validate_fee_plan(plan)
    acquired = validate_fee_document_acquisition(
        acquisition,
        plan=plan,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    verification = validate_fee_document_verification(
        document_verification,
        plan=plan,
        acquisition=acquisition,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    extracted = validate_fee_rule_extraction(
        extraction,
        plan=plan,
        acquisition=acquisition,
        document_verification=document_verification,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    if acquired["evidence_scope"] != "real_official_https" and not allow_synthetic_test_fixture:
        raise FeeWorkflowError("synthetic_fee_schedule_publish_forbidden")
    policy_path = _contained_file(Path(fee_plan["manifest_path"]).parent, fee_plan["policy_seal_relative_path"])
    _, policy = _validate_policy_seal(policy_path)
    statutory_rules = _derive_statutory_rules(
        extracted["assertions"],
        simulation_start=fee_plan["simulation_start"],
        simulation_end=fee_plan["simulation_end"],
    )
    modeled_rules = _derive_modeled_rules(
        policy,
        simulation_start=fee_plan["simulation_start"],
        simulation_end=fee_plan["simulation_end"],
    )
    rules = sorted(
        statutory_rules + modeled_rules,
        key=lambda row: (row["component"], row["market"], row["side"], row["effective_start"], row["rule_id"]),
    )
    _validate_rule_coverage(rules, fee_plan["simulation_start"], fee_plan["simulation_end"])
    source_hashes = semantic_source_hashes()
    schedule_semantic = {
        "schema_version": FEE_SCHEDULE_SCHEMA,
        "status": "passed",
        "evidence_scope": acquired["evidence_scope"],
        "simulation_start": fee_plan["simulation_start"],
        "simulation_end": fee_plan["simulation_end"],
        "plan_content_hash": fee_plan["content_hash"],
        "document_acquisition_content_hash": acquired["content_hash"],
        "document_verification_content_hash": verification["content_hash"],
        "rule_extraction_content_hash": extracted["content_hash"],
        "transport_ledger_root": acquired["transport_ledger_root"],
        "document_merkle_root": acquired["document_merkle_root"],
        "assertion_root": extracted["assertion_root"],
        "policy_seal_hash": fee_plan["policy_seal_hash"],
        "policy_seal_sha256": fee_plan["policy_seal_sha256"],
        "semantic_source_hashes": source_hashes,
        "builder_semantic_hash": canonical_hash(source_hashes),
        "statutory_components": list(STATUTORY_COMPONENTS),
        "modeled_components": list(MODELED_COMPONENTS),
        "modeled_evidence_level": "uncalibrated_modeled",
        "certification_ready": False,
        "rules": rules,
        "rules_root": canonical_hash(rules),
        "native_artifacts": {
            "plan": "native_plan/fee_plan.json",
            "policy_seal": "native_plan/inputs/task055a_policy_seal.json",
            "acquisition": "native_acquisition/fee_document_acquisition.json",
            "transport_ledger": "native_acquisition/transport_ledger.jsonl",
            "document_verification": "native_verification/fee_document_verification.json",
            "extraction": "native_extraction/fee_rule_extraction.json",
        },
    }
    files = _schedule_native_files(
        fee_plan=fee_plan,
        acquired=acquired,
        verification=verification,
        extracted=extracted,
    )
    return _publish_generation(
        output_root=output_root,
        prefix="fee_schedule_v2",
        manifest_name="fee_schedule_v2_manifest.json",
        semantic=schedule_semantic,
        files=files,
    )


def validate_fee_schedule_v2(
    path: str | Path,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "fee_schedule_v2_manifest.json")
    manifest = _load_json(manifest_path)
    _validate_manifest_hash(manifest, FEE_SCHEDULE_SCHEMA, "passed")
    if manifest.get("evidence_scope") != "real_official_https" and not allow_synthetic_test_fixture:
        raise FeeWorkflowError("synthetic_fee_schedule_forbidden")
    if manifest.get("semantic_source_hashes") != semantic_source_hashes():
        raise FeeWorkflowError("fee_schedule_semantic_source_hash_mismatch")
    if manifest.get("builder_semantic_hash") != canonical_hash(semantic_source_hashes()):
        raise FeeWorkflowError("fee_schedule_builder_semantic_hash_mismatch")
    native = dict(manifest.get("native_artifacts") or {})
    required_native = {"plan", "policy_seal", "acquisition", "transport_ledger", "document_verification", "extraction"}
    if set(native) != required_native:
        raise FeeWorkflowError("fee_schedule_native_artifact_contract_invalid")
    root = manifest_path.parent
    plan_path = _contained_file(root, native["plan"])
    policy_path = _contained_file(root, native["policy_seal"])
    acquisition_path = _contained_file(root, native["acquisition"])
    verification_path = _contained_file(root, native["document_verification"])
    extraction_path = _contained_file(root, native["extraction"])
    fee_plan = validate_fee_plan(plan_path)
    acquired = validate_fee_document_acquisition(
        acquisition_path,
        plan=plan_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    verification = validate_fee_document_verification(
        verification_path,
        plan=plan_path,
        acquisition=acquisition_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    extracted = validate_fee_rule_extraction(
        extraction_path,
        plan=plan_path,
        acquisition=acquisition_path,
        document_verification=verification_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    _, policy = _validate_policy_seal(policy_path)
    expected_rules = sorted(
        _derive_statutory_rules(
            extracted["assertions"],
            simulation_start=fee_plan["simulation_start"],
            simulation_end=fee_plan["simulation_end"],
        )
        + _derive_modeled_rules(
            policy,
            simulation_start=fee_plan["simulation_start"],
            simulation_end=fee_plan["simulation_end"],
        ),
        key=lambda row: (row["component"], row["market"], row["side"], row["effective_start"], row["rule_id"]),
    )
    _validate_rule_coverage(expected_rules, fee_plan["simulation_start"], fee_plan["simulation_end"])
    if expected_rules != manifest.get("rules"):
        raise FeeWorkflowError("fee_schedule_rules_rebuild_mismatch")
    if canonical_hash(expected_rules) != manifest.get("rules_root"):
        raise FeeWorkflowError("fee_schedule_rules_root_mismatch")
    expected_lineage = {
        "plan_content_hash": fee_plan["content_hash"],
        "document_acquisition_content_hash": acquired["content_hash"],
        "document_verification_content_hash": verification["content_hash"],
        "rule_extraction_content_hash": extracted["content_hash"],
        "transport_ledger_root": acquired["transport_ledger_root"],
        "document_merkle_root": acquired["document_merkle_root"],
        "assertion_root": extracted["assertion_root"],
        "policy_seal_hash": policy["content_hash"],
        "policy_seal_sha256": _sha256_file(policy_path),
    }
    for key, value in expected_lineage.items():
        if manifest.get(key) != value:
            raise FeeWorkflowError(f"fee_schedule_{key}_mismatch")
    if manifest.get("certification_ready") is not False or manifest.get("modeled_evidence_level") != "uncalibrated_modeled":
        raise FeeWorkflowError("fee_schedule_evidence_boundary_invalid")
    return manifest | {"manifest_path": str(manifest_path)}


def independent_verify_fee_schedule(
    *,
    schedule: str | Path,
    output_root: str | Path | None = None,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    """Rebuild rules from cloned source bytes and publish an attestation."""

    validated = validate_fee_schedule_v2(
        schedule,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    manifest_path = Path(validated["manifest_path"])
    native = validated["native_artifacts"]
    plan_path = _contained_file(manifest_path.parent, native["plan"])
    acquisition_path = _contained_file(manifest_path.parent, native["acquisition"])
    verification_path = _contained_file(manifest_path.parent, native["document_verification"])
    extraction_path = _contained_file(manifest_path.parent, native["extraction"])
    fee_plan = validate_fee_plan(plan_path)
    acquired = validate_fee_document_acquisition(
        acquisition_path,
        plan=plan_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    extracted = validate_fee_rule_extraction(
        extraction_path,
        plan=plan_path,
        acquisition=acquisition_path,
        document_verification=verification_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    assertion_receipts = _independent_assertion_receipts(fee_plan, acquired, extracted)
    semantic = {
        "schema_version": FEE_INDEPENDENT_VERIFICATION_SCHEMA,
        "status": "passed",
        "schedule_content_hash": validated["content_hash"],
        "policy_seal_hash": validated["policy_seal_hash"],
        "document_acquisition_content_hash": acquired["content_hash"],
        "document_merkle_root": acquired["document_merkle_root"],
        "transport_ledger_root": acquired["transport_ledger_root"],
        "assertion_receipt_root": canonical_hash(assertion_receipts),
        "rules_root": validated["rules_root"],
        "rule_count": len(validated["rules"]),
        "coverage": {
            "simulation_start": validated["simulation_start"],
            "simulation_end": validated["simulation_end"],
            "markets": list(MARKETS),
            "sides": list(SIDES),
            "components": list(ALL_COMPONENTS),
            "gaps": 0,
            "overlaps": 0,
        },
        "certification_ready": False,
        "verifier_source_hash": _source_hash(Path(__file__)),
    }
    if output_root is None:
        content_hash = canonical_hash(semantic)
        return semantic | {
            "content_hash": content_hash,
            "generation_id": f"fee_independent_verification_{content_hash[:24]}",
        }
    return _publish_generation(
        output_root=output_root,
        prefix="fee_independent_verification",
        manifest_name="fee_independent_verification.json",
        semantic=semantic,
    )


def run_fee_dag(
    *,
    output_root: str | Path,
    policy_seal: str | Path,
    simulation_start: str,
    simulation_end: str,
    documents: Iterable[Mapping[str, Any]],
    extractors: Iterable[Mapping[str, Any]],
    allow_network: bool,
    fetcher: Callable[[str], Mapping[str, Any]] | None = None,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    """Execute the fixed production stage order with parent validation."""

    root = Path(output_root)
    plan = build_fee_plan(
        output_root=root / "fee_plan",
        policy_seal=policy_seal,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
        documents=documents,
        extractors=extractors,
    )
    acquisition = acquire_fee_documents(
        plan=plan["manifest_path"],
        output_root=root / "fee_document_acquisition",
        allow_network=allow_network,
        fetcher=fetcher,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    verification = publish_fee_document_verification(
        output_root=root / "fee_document_verification",
        plan=plan["manifest_path"],
        acquisition=acquisition["manifest_path"],
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    extraction = extract_fee_rules(
        output_root=root / "fee_rule_extraction",
        plan=plan["manifest_path"],
        acquisition=acquisition["manifest_path"],
        document_verification=verification["manifest_path"],
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    schedule = publish_fee_schedule_v2(
        output_root=root / "fee_schedule",
        plan=plan["manifest_path"],
        acquisition=acquisition["manifest_path"],
        document_verification=verification["manifest_path"],
        extraction=extraction["manifest_path"],
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    independent = independent_verify_fee_schedule(
        schedule=schedule["manifest_path"],
        output_root=root / "fee_independent_verification",
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    return {
        "plan": plan,
        "acquisition": acquisition,
        "document_verification": verification,
        "extraction": extraction,
        "schedule": schedule,
        "independent_verification": independent,
    }


class FeeScheduleCalculator:
    """Strict calculator backed only by a validated Task 055-G schedule."""

    def __init__(self, schedule: str | Path, *, allow_synthetic_test_fixture: bool = False) -> None:
        self.schedule = validate_fee_schedule_v2(
            schedule,
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for rule in self.schedule["rules"]:
            grouped[(rule["component"], rule["market"], rule["side"])].append(dict(rule))
        self.rules = {
            key: tuple(sorted(rows, key=lambda row: row["effective_start"]))
            for key, rows in grouped.items()
        }

    def calculate(
        self,
        *,
        date: str,
        market: str,
        side: str,
        notional: float,
        shares: int,
        zero_all_costs: bool,
        modeled_multiplier: float,
    ) -> dict[str, float]:
        trade_date = _date(date)
        normalized_market = str(market).upper()
        normalized_side = str(side).upper()
        if normalized_market not in MARKETS or normalized_side not in SIDES:
            raise FeeWorkflowError("fee_calculation_scope_invalid")
        if not math.isfinite(float(notional)) or float(notional) < 0 or int(shares) < 0:
            raise FeeWorkflowError("fee_calculation_input_invalid")
        if not math.isfinite(float(modeled_multiplier)) or float(modeled_multiplier) < 0:
            raise FeeWorkflowError("fee_calculation_modeled_multiplier_invalid")
        if zero_all_costs:
            return {component: 0.0 for component in ALL_COMPONENTS} | {"total": 0.0}
        values: dict[str, float] = {}
        for component in ALL_COMPONENTS:
            matches = [
                row
                for row in self.rules.get((component, normalized_market, normalized_side), ())
                if row["effective_start"] <= trade_date <= row["effective_end"]
            ]
            if len(matches) != 1:
                raise FeeWorkflowError(f"fee_rule_match_invalid:{component}:{normalized_market}:{normalized_side}:{trade_date}")
            rule = matches[0]
            base = Decimal(str(notional)) if rule["basis"] == "notional" else Decimal(int(shares))
            value = Decimal("0") if rule["explicit_zero"] else max(
                Decimal(str(rule["minimum_cny"])),
                base * Decimal(str(rule["rate"])),
            )
            if component in MODELED_COMPONENTS:
                value *= Decimal(str(modeled_multiplier))
            values[component] = _round_decimal(value, rule["rounding"])
        values["total"] = _round_decimal(sum((Decimal(str(value)) for value in values.values()), Decimal("0")), "cent_half_up")
        return values


def semantic_source_hashes() -> dict[str, str]:
    import task_055_a.policy as policy_module
    import task_055_a.simulator as simulator_module
    import task_055_a.verifier as verifier_module

    return {
        "task_055_a.policy": _module_source_hash(policy_module),
        "task_055_a.simulator": _module_source_hash(simulator_module),
        "task_055_a.verifier": _module_source_hash(verifier_module),
        "task_055_g.fees": _source_hash(Path(__file__)),
    }


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_document_specs(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for raw in rows:
        row = dict(raw)
        allowed = {"document_id", "publisher", "request_url"}
        if set(row) != allowed:
            raise FeeWorkflowError("fee_document_spec_fields_invalid")
        document_id = str(row.get("document_id") or "")
        publisher = str(row.get("publisher") or "")
        request_url = str(row.get("request_url") or "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,96}", document_id) or not publisher:
            raise FeeWorkflowError("fee_document_spec_identity_invalid")
        _validate_official_url(request_url)
        result.append({"document_id": document_id, "publisher": publisher, "request_url": request_url})
    result.sort(key=lambda row: row["document_id"])
    if not result or len(result) > MAX_OFFICIAL_DOCUMENTS:
        raise FeeWorkflowError("fee_document_spec_count_invalid")
    if len({row["document_id"] for row in result}) != len(result):
        raise FeeWorkflowError("fee_document_spec_duplicate")
    return result


def _normalize_extractor_specs(
    rows: Iterable[Mapping[str, Any]],
    documents: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    document_ids = {str(row["document_id"]) for row in documents}
    result = []
    for raw in rows:
        row = dict(raw)
        if FORBIDDEN_EXTRACTOR_FIELDS & set(row):
            raise FeeWorkflowError("fee_extractor_free_mapping_forbidden")
        allowed = {
            "extractor_id",
            "document_id",
            "supporting_document_ids",
            "parser_id",
            "occurrence",
        }
        if not set(row) <= allowed or not {"extractor_id", "document_id", "parser_id"} <= set(row):
            raise FeeWorkflowError("fee_extractor_fields_invalid")
        extractor_id = str(row.get("extractor_id") or "")
        document_id = str(row.get("document_id") or "")
        parser_id = str(row.get("parser_id") or "")
        occurrence = int(row.get("occurrence", 0))
        supporting_document_ids = sorted(
            {str(value) for value in (row.get("supporting_document_ids") or ())}
        )
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", extractor_id):
            raise FeeWorkflowError("fee_extractor_identity_invalid")
        if (
            document_id not in document_ids
            or parser_id not in PARSER_COMPONENTS
            or occurrence < 0
            or document_id in supporting_document_ids
            or any(value not in document_ids for value in supporting_document_ids)
        ):
            raise FeeWorkflowError("fee_extractor_contract_invalid")
        result.append(
            {
                "extractor_id": extractor_id,
                "document_id": document_id,
                "supporting_document_ids": supporting_document_ids,
                "parser_id": parser_id,
                "occurrence": occurrence,
            }
        )
    result.sort(key=lambda row: row["extractor_id"])
    if not result or len({row["extractor_id"] for row in result}) != len(result):
        raise FeeWorkflowError("fee_extractor_identity_duplicate_or_empty")
    return result


def _extract_assertion(
    document_path: Path,
    document: Mapping[str, Any],
    extractor: Mapping[str, Any],
    *,
    document_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    acquisition_root: Path | None = None,
) -> dict[str, Any]:
    if _sha256_file(document_path) != document.get("sha256"):
        raise FeeWorkflowError("fee_extraction_document_sha_mismatch")
    parser_id = str(extractor["parser_id"])
    component = PARSER_COMPONENTS[parser_id]
    if parser_id in PRODUCTION_PARSERS:
        if document_by_id is None or acquisition_root is None:
            raise FeeWorkflowError("production_fee_parser_context_missing")
        return _extract_production_assertion(
            document_path=document_path,
            document=document,
            extractor=extractor,
            document_by_id=document_by_id,
            acquisition_root=acquisition_root,
        )
    pages = _document_pages(document_path)
    candidates: list[dict[str, Any]] = []
    for page_index, page_text in enumerate(pages):
        candidates.extend(_component_clause_candidates(page_text, page_index, component))
    candidates.sort(key=lambda row: (row["page_index"], row["text_start"], row["text_end"]))
    occurrence = int(extractor["occurrence"])
    if occurrence >= len(candidates):
        raise FeeWorkflowError(f"fee_extraction_clause_occurrence_missing:{extractor['extractor_id']}")
    candidate = candidates[occurrence]
    parsed = _parse_statutory_clause(candidate["clause_text"], component)
    assertion = {
        "assertion_id": str(extractor["extractor_id"]),
        "extractor_id": str(extractor["extractor_id"]),
        "document_id": str(document["document_id"]),
        "document_sha256": str(document["sha256"]),
        "parser_id": parser_id,
        "parser_source_hash": _source_hash(Path(__file__)),
        "locator": {
            "page_index": candidate["page_index"],
            "text_start": candidate["text_start"],
            "text_end": candidate["text_end"],
        },
        "normalized_clause_hash": canonical_hash(_normalize_clause(candidate["clause_text"])),
        "source_documents": [
            {
                "role": "primary",
                "document_id": str(document["document_id"]),
                "document_sha256": str(document["sha256"]),
                "locator": {
                    "page_index": candidate["page_index"],
                    "text_start": candidate["text_start"],
                    "text_end": candidate["text_end"],
                },
                "normalized_clause_hash": canonical_hash(_normalize_clause(candidate["clause_text"])),
            }
        ],
        "parsed": parsed,
    }
    assertion["assertion_hash"] = canonical_hash(assertion)
    return assertion


def _extract_production_assertion(
    *,
    document_path: Path,
    document: Mapping[str, Any],
    extractor: Mapping[str, Any],
    document_by_id: Mapping[str, Mapping[str, Any]],
    acquisition_root: Path,
) -> dict[str, Any]:
    parser_id = str(extractor["parser_id"])
    contexts: dict[str, dict[str, Any]] = {}
    for document_id in [str(document["document_id"]), *extractor.get("supporting_document_ids", ())]:
        source = document_by_id.get(document_id)
        if source is None:
            raise FeeWorkflowError(f"production_fee_supporting_document_missing:{document_id}")
        path = _contained_file(acquisition_root, source["artifact_relative_path"])
        if _sha256_file(path) != source.get("sha256"):
            raise FeeWorkflowError(f"production_fee_supporting_document_sha_mismatch:{document_id}")
        pages = _document_pages(path)
        contexts[document_id] = {"document": source, "path": path, "pages": pages}

    if parser_id == "cn_stamp_duty_baseline_2008_v2":
        history = contexts[str(document["document_id"])]
        rate_clause = _locate_clause(history["pages"], ("2008年4月24日", "1‰", "印花税"))
        direction_clause = _locate_clause(history["pages"], ("2008年9月19日", "单边", "印花税"))
        law = contexts["stamp_tax_law"]
        law_side_clause = _locate_clause(law["pages"], ("出让方", "受让方"))
        law_basis_clause = _locate_clause(law["pages"], ("证券交易", "成交金额"))
        rate_match = re.search(r"下调为\s*(\d+(?:\.\d+)?)\s*(‰|%|％)", rate_clause["clause_text"])
        if rate_match is None:
            raise FeeWorkflowError("stamp_2008_rate_transition_missing")
        rate = _rate_from_number_unit(rate_match.group(1), rate_match.group(2))
        parsed = _production_parsed_rule(
            component="stamp_duty",
            effective_start="20080919",
            rate=rate,
            sides="seller_only",
            evidence_class="governed_official_retrospective_continuity",
            payer_scope="seller_transferor",
            source_tokens={
                "effective_date": "2008年9月19日",
                "rate": rate_match.group(0),
                "market": "A股|依法设立的证券交易所",
                "side": "单边|出让方征收|不对受让方征收",
                "basis": "成交金额",
            },
        )
        sources = [
            _source_document_receipt(history["document"], rate_clause, "rate_baseline"),
            _source_document_receipt(history["document"], direction_clause, "effective_direction_history"),
            _source_document_receipt(law["document"], law_side_clause, "direction_law"),
            _source_document_receipt(law["document"], law_basis_clause, "basis_law"),
        ]
    elif parser_id == "cn_stamp_duty_half_2023_v2":
        half = contexts[str(document["document_id"])]
        half_clause = _locate_clause(half["pages"], ("2023年8月28日", "印花税", "减半征收"))
        history = contexts["stamp_history_context"]
        baseline_clause = _locate_clause(history["pages"], ("2008年4月24日", "1‰", "印花税"))
        law = contexts["stamp_tax_law"]
        law_side_clause = _locate_clause(law["pages"], ("出让方", "受让方"))
        law_basis_clause = _locate_clause(law["pages"], ("证券交易", "成交金额"))
        baseline_match = re.search(r"下调为\s*(\d+(?:\.\d+)?)\s*(‰|%|％)", baseline_clause["clause_text"])
        if baseline_match is None:
            raise FeeWorkflowError("stamp_2023_baseline_rate_missing")
        baseline_rate = _rate_from_number_unit(baseline_match.group(1), baseline_match.group(2))
        rate = baseline_rate / Decimal("2")
        parsed = _production_parsed_rule(
            component="stamp_duty",
            effective_start="20230828",
            rate=rate,
            sides="seller_only",
            evidence_class="governed_official_derived_by_explicit_halving",
            payer_scope="seller_transferor",
            source_tokens={
                "effective_date": "2023年8月28日",
                "rate": f"{baseline_match.group(0)}|减半征收",
                "market": "证券交易|A股历史范围",
                "side": "出让方征收|不对受让方征收",
                "basis": "成交金额",
            },
        )
        sources = [
            _source_document_receipt(half["document"], half_clause, "rate_transform_and_effective_date"),
            _source_document_receipt(history["document"], baseline_clause, "rate_baseline"),
            _source_document_receipt(law["document"], law_side_clause, "direction_law"),
            _source_document_receipt(law["document"], law_basis_clause, "basis_law"),
        ]
    elif parser_id in {
        "cn_transfer_fee_2015_v2",
        "cn_transfer_fee_2022_v2",
        "cn_handling_fee_2015_v2",
        "cn_handling_fee_2023_v2",
    }:
        context = contexts[str(document["document_id"])]
        component = PARSER_COMPONENTS[parser_id]
        keyword = "过户费" if component == "transfer_fee" else "经手费"
        clause = _locate_clause(context["pages"], (keyword, "成交金额", "双"))
        rate_match = re.search(
            r"(?:调整为|下调为|统一下调为|标准由[^。；]*?至)\s*(?:按|按照)?\s*成交金额(?:的)?\s*(\d+(?:\.\d+)?)\s*(‰|%|％)",
            _normalize_document_text(clause["clause_text"]),
        )
        if rate_match is None:
            matches = list(_RATE_PATTERN.finditer(clause["clause_text"]))
            if not matches:
                raise FeeWorkflowError(f"production_fee_rate_missing:{parser_id}")
            selected = matches[-1]
            rate, token = _parse_rate_match(selected)
        else:
            rate = _rate_from_number_unit(rate_match.group(1), rate_match.group(2))
            token = rate_match.group(0)
        effective_start = {
            "cn_transfer_fee_2015_v2": "20150801",
            "cn_transfer_fee_2022_v2": "20220429",
            "cn_handling_fee_2015_v2": "20150801",
            "cn_handling_fee_2023_v2": "20230828",
        }[parser_id]
        date_clause = _locate_effective_date_clause(context["pages"], effective_start)
        parsed = _production_parsed_rule(
            component=component,
            effective_start=effective_start,
            rate=rate,
            sides="bilateral",
            evidence_class="governed_official",
            payer_scope=(
                "buyer_and_seller_direct_registration_charge"
                if component == "transfer_fee"
                else "exchange_member_charge_engineering_pass_through"
            ),
            source_tokens={
                "effective_date": effective_start,
                "rate": token,
                "market": "沪深市场|沪、深证券交易所",
                "side": "双边|双向",
                "basis": "成交金额",
            },
        )
        sources = [_source_document_receipt(context["document"], clause, "primary_rule_clause")]
        if _normalize_clause(date_clause["clause_text"]) != _normalize_clause(clause["clause_text"]):
            sources.append(
                _source_document_receipt(
                    context["document"], date_clause, "effective_date_clause"
                )
            )
    elif parser_id == "cn_securities_management_fee_2012_v2":
        context = contexts[str(document["document_id"])]
        clause = _locate_clause(context["pages"], ("证券交易监管费", "上海、深圳证券交易所", "0.02"))
        effective_clause = _locate_clause(context["pages"], ("从今年开始", "监管费"))
        matches = list(_RATE_PATTERN.finditer(clause["clause_text"]))
        if not matches:
            raise FeeWorkflowError("management_fee_rate_missing")
        rate, token = _parse_rate_match(matches[-1])
        if "2012" not in _normalize_clause("\n".join(context["pages"])):
            raise FeeWorkflowError("management_fee_effective_year_missing")
        parsed = _production_parsed_rule(
            component="securities_management_fee",
            effective_start="20120101",
            rate=rate,
            sides="bilateral",
            evidence_class="governed_official_exchange_pass_through_allocation",
            payer_scope="exchange_annual_charge_allocated_to_trade_sides_for_engineering",
            source_tokens={
                "effective_date": "从今年开始|页面日期2012-07-13",
                "rate": token,
                "market": "上海、深圳证券交易所",
                "side": "年交易额聚合工程双边分配",
                "basis": "股票年交易额",
            },
        )
        sources = [
            _source_document_receipt(context["document"], clause, "primary_rule_clause"),
            _source_document_receipt(context["document"], effective_clause, "effective_year_clause"),
        ]
    else:
        raise FeeWorkflowError(f"production_fee_parser_unknown:{parser_id}")

    primary = sources[0]
    assertion = {
        "assertion_id": str(extractor["extractor_id"]),
        "extractor_id": str(extractor["extractor_id"]),
        "document_id": str(document["document_id"]),
        "document_sha256": str(document["sha256"]),
        "parser_id": parser_id,
        "parser_source_hash": _source_hash(Path(__file__)),
        "locator": dict(primary["locator"]),
        "normalized_clause_hash": canonical_hash(
            [row["normalized_clause_hash"] for row in sources]
        ),
        "source_documents": sources,
        "parsed": parsed,
    }
    assertion["assertion_hash"] = canonical_hash(assertion)
    return assertion


def _production_parsed_rule(
    *,
    component: str,
    effective_start: str,
    rate: Decimal,
    sides: str,
    evidence_class: str,
    payer_scope: str,
    source_tokens: Mapping[str, str],
) -> dict[str, Any]:
    if sides == "seller_only":
        side_rates = {
            "BUY": {"rate": "0", "explicit_zero": True},
            "SELL": {"rate": _decimal_text(rate), "explicit_zero": False},
        }
    elif sides == "bilateral":
        side_rates = {
            "BUY": {"rate": _decimal_text(rate), "explicit_zero": False},
            "SELL": {"rate": _decimal_text(rate), "explicit_zero": False},
        }
    else:
        raise FeeWorkflowError("production_fee_side_contract_invalid")
    return {
        "component": component,
        "effective_start": _date(effective_start),
        "markets": ["SSE", "SZSE"],
        "side_rates": side_rates,
        "basis": "notional",
        "rounding": "cent_half_up",
        "minimum_cny": "0",
        "statutory_evidence_class": evidence_class,
        "payer_scope": payer_scope,
        "calculation_contract": {
            "rounding_evidence_class": "task055a_ledger_cent_precision_policy",
            "minimum_evidence_class": "percentage_rule_without_declared_minimum_engineering_zero",
            "official_clause_does_not_claim_broker_specific_rounding_or_minimum": True,
        },
        "source_tokens": dict(source_tokens)
        | {
            "rounding": "Task055A ledger cent precision",
            "minimum": "no official minimum stated; engineering zero",
        },
    }


def _source_document_receipt(
    document: Mapping[str, Any], clause: Mapping[str, Any], role: str
) -> dict[str, Any]:
    return {
        "role": role,
        "document_id": str(document["document_id"]),
        "document_sha256": str(document["sha256"]),
        "locator": {
            "page_index": int(clause["page_index"]),
            "text_start": int(clause["text_start"]),
            "text_end": int(clause["text_end"]),
        },
        "normalized_clause_hash": canonical_hash(_normalize_clause(clause["clause_text"])),
    }


def _locate_clause(pages: Sequence[str], required: Sequence[str]) -> dict[str, Any]:
    for page_index, page in enumerate(pages):
        spans: list[tuple[int, int]] = []
        start = 0
        for match in re.finditer(r"[。！？；;\n]", page):
            spans.append((start, match.end()))
            start = match.end()
        if start < len(page):
            spans.append((start, len(page)))
        for width in (1, 2, 3, 4):
            for index in range(len(spans)):
                end_index = min(len(spans), index + width)
                clause_start = spans[index][0]
                clause_end = spans[end_index - 1][1]
                clause = page[clause_start:clause_end].strip()
                normalized = _normalize_clause(clause)
                if all(_normalize_clause(token) in normalized for token in required):
                    return {
                        "page_index": page_index,
                        "text_start": clause_start,
                        "text_end": clause_end,
                        "clause_text": clause,
                    }
    raise FeeWorkflowError(f"production_fee_clause_missing:{'|'.join(required)}")


def _rate_from_number_unit(number: str, unit: str) -> Decimal:
    divisor = Decimal("100") if unit in {"%", "％"} else Decimal("1000")
    return Decimal(number) / divisor


def _locate_effective_date_clause(
    pages: Sequence[str], effective_start: str
) -> dict[str, Any]:
    year, month, day = effective_start[:4], str(int(effective_start[4:6])), str(int(effective_start[6:8]))
    explicit = f"{year}年{month}月{day}日"
    partial = f"{month}月{day}日"
    for token in (explicit, partial):
        try:
            clause = _locate_clause(pages, (token,))
        except FeeWorkflowError:
            continue
        if token == explicit or year in _normalize_clause("\n".join(pages)):
            return clause
    raise FeeWorkflowError(f"production_fee_effective_date_evidence_missing:{effective_start}")


def _component_clause_candidates(text: str, page_index: int, component: str) -> list[dict[str, Any]]:
    keywords = COMPONENT_KEYWORDS[component]
    spans = []
    start = 0
    for match in re.finditer(r"[。！？；;\n]", text):
        end = match.end()
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    candidates = []
    for index, (segment_start, segment_end) in enumerate(spans):
        segment = text[segment_start:segment_end]
        if not any(keyword in segment for keyword in keywords):
            continue
        if _DATE_PATTERN.search(segment) and _RATE_PATTERN.search(segment):
            context_start, context_end = segment_start, segment_end
        else:
            context_start = spans[max(0, index - 1)][0]
            context_end = segment_end
        clause = text[context_start:context_end].strip()
        if not _DATE_PATTERN.search(clause) or not _RATE_PATTERN.search(clause):
            continue
        candidates.append(
            {
                "page_index": page_index,
                "text_start": context_start,
                "text_end": context_end,
                "clause_text": clause,
            }
        )
    unique = []
    seen = set()
    for row in candidates:
        key = canonical_hash(_normalize_clause(row["clause_text"]))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


_DATE_PATTERN = re.compile(r"(?P<year>20\d{2}|19\d{2})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?")
_RATE_PATTERN = re.compile(
    r"(?:(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>%|％|‰)|(?P<fraction>百分之|千分之|万分之|十万分之)\s*(?P<fraction_number>\d+(?:\.\d+)?))"
)


def _parse_statutory_clause(clause: str, component: str) -> dict[str, Any]:
    normalized = _normalize_clause(clause)
    date_match = _DATE_PATTERN.search(clause)
    if date_match is None:
        raise FeeWorkflowError("fee_clause_effective_date_missing")
    effective_start = f"{int(date_match.group('year')):04d}{int(date_match.group('month')):02d}{int(date_match.group('day')):02d}"
    keyword_position = min(
        (normalized.find(keyword) for keyword in COMPONENT_KEYWORDS[component] if keyword in normalized),
        default=-1,
    )
    if keyword_position < 0:
        raise FeeWorkflowError("fee_clause_component_keyword_missing")
    rate_matches = list(_RATE_PATTERN.finditer(normalized))
    if not rate_matches:
        raise FeeWorkflowError("fee_clause_rate_missing")
    rate_match = min(rate_matches, key=lambda match: (0 if match.start() >= keyword_position else 1, abs(match.start() - keyword_position)))
    rate, rate_token = _parse_rate_match(rate_match)
    markets, market_token = _parse_markets(normalized)
    basis, basis_token = _parse_basis(normalized)
    rounding, rounding_token = _parse_rounding(normalized)
    minimum_cny, minimum_token = _parse_minimum(normalized)
    side_rates, side_token = _parse_side_rates(normalized, component, rate)
    return {
        "component": component,
        "effective_start": effective_start,
        "markets": markets,
        "side_rates": side_rates,
        "basis": basis,
        "rounding": rounding,
        "minimum_cny": _decimal_text(minimum_cny),
        "source_tokens": {
            "effective_date": date_match.group(0),
            "rate": rate_token,
            "market": market_token,
            "side": side_token,
            "basis": basis_token,
            "rounding": rounding_token,
            "minimum": minimum_token,
        },
    }


def _parse_rate_match(match: re.Match[str]) -> tuple[Decimal, str]:
    token = match.group(0)
    if match.group("number") is not None:
        number = Decimal(match.group("number"))
        unit = match.group("unit")
        divisor = Decimal("100") if unit in {"%", "％"} else Decimal("1000")
    else:
        number = Decimal(match.group("fraction_number"))
        divisor = {
            "百分之": Decimal("100"),
            "千分之": Decimal("1000"),
            "万分之": Decimal("10000"),
            "十万分之": Decimal("100000"),
        }[match.group("fraction")]
    return number / divisor, token


def _parse_markets(clause: str) -> tuple[list[str], str]:
    bilateral_tokens = (
        "沪深市场",
        "沪深交易所",
        "上海和深圳市场",
        "上海、深圳市场",
        "上海证券交易所和深圳证券交易所",
        "上海证券交易所、深圳证券交易所",
    )
    for token in bilateral_tokens:
        if token in clause:
            return ["SSE", "SZSE"], token
    sse = next((token for token in ("上海证券交易所", "上交所", "上海市场") if token in clause), None)
    szse = next((token for token in ("深圳证券交易所", "深交所", "深圳市场") if token in clause), None)
    if sse and szse:
        return ["SSE", "SZSE"], f"{sse}|{szse}"
    if sse:
        return ["SSE"], sse
    if szse:
        return ["SZSE"], szse
    raise FeeWorkflowError("fee_clause_market_scope_missing")


def _parse_basis(clause: str) -> tuple[str, str]:
    for token in ("成交金额", "交易金额", "成交额"):
        if token in clause:
            return "notional", token
    for token in ("成交股数", "股份数量", "成交数量"):
        if token in clause:
            return "shares", token
    raise FeeWorkflowError("fee_clause_basis_missing")


def _parse_rounding(clause: str) -> tuple[str, str]:
    if "四舍五入" in clause and any(token in clause for token in ("分", "0.01元", "角分")):
        return "cent_half_up", "四舍五入至分"
    for token in ("不取整", "不作取整", "按实际计算值"):
        if token in clause:
            return "none", token
    raise FeeWorkflowError("fee_clause_rounding_missing")


def _parse_minimum(clause: str) -> tuple[Decimal, str]:
    for token in ("不设最低收费", "无最低收费", "不设最低费用", "无最低费用"):
        if token in clause:
            return Decimal("0"), token
    match = re.search(r"最低(?:收费|费用)?(?:为|按)?\s*(\d+(?:\.\d+)?)\s*元", clause)
    if match:
        return Decimal(match.group(1)), match.group(0)
    raise FeeWorkflowError("fee_clause_minimum_missing")


def _parse_side_rates(clause: str, component: str, rate: Decimal) -> tuple[dict[str, dict[str, Any]], str]:
    if component == "stamp_duty":
        seller = any(token in clause for token in ("卖方单边", "向卖方", "出让方", "卖出方"))
        buyer_zero = any(token in clause for token in ("买方不征收", "受让方不征收", "买入方不征收"))
        if seller and buyer_zero:
            return {
                "BUY": {"rate": "0", "explicit_zero": True},
                "SELL": {"rate": _decimal_text(rate), "explicit_zero": False},
            }, "卖方征收|买方不征收"
        raise FeeWorkflowError("fee_clause_stamp_direction_missing")
    for token in ("双向收取", "向买卖双方收取", "买卖双方均收取", "买卖双方分别收取"):
        if token in clause:
            return {
                "BUY": {"rate": _decimal_text(rate), "explicit_zero": False},
                "SELL": {"rate": _decimal_text(rate), "explicit_zero": False},
            }, token
    raise FeeWorkflowError("fee_clause_bilateral_direction_missing")


def _derive_statutory_rules(
    assertions: Sequence[Mapping[str, Any]],
    *,
    simulation_start: str,
    simulation_end: str,
) -> list[dict[str, Any]]:
    start = _date(simulation_start)
    end = _date(simulation_end)
    events: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for assertion in assertions:
        parsed = dict(assertion["parsed"])
        component = str(parsed["component"])
        for market in parsed["markets"]:
            for side, side_rule in dict(parsed["side_rates"]).items():
                events[(component, market, side)].append(
                    {
                        "effective_start": str(parsed["effective_start"]),
                        "rate": str(side_rule["rate"]),
                        "explicit_zero": bool(side_rule["explicit_zero"]),
                        "basis": str(parsed["basis"]),
                        "rounding": str(parsed["rounding"]),
                        "minimum_cny": str(parsed["minimum_cny"]),
                        "assertion_id": str(assertion["assertion_id"]),
                        "assertion_hash": str(assertion["assertion_hash"]),
                        "document_id": str(assertion["document_id"]),
                        "document_sha256": str(assertion["document_sha256"]),
                        "normalized_clause_hash": str(assertion["normalized_clause_hash"]),
                        "source_documents": list(assertion.get("source_documents") or ()),
                        "statutory_evidence_class": str(
                            parsed.get("statutory_evidence_class") or "governed_official"
                        ),
                        "payer_scope": str(parsed.get("payer_scope") or "unspecified"),
                        "calculation_contract": dict(parsed.get("calculation_contract") or {}),
                    }
                )
    rules = []
    for component in STATUTORY_COMPONENTS:
        for market in MARKETS:
            for side in SIDES:
                key = (component, market, side)
                scoped = sorted(events.get(key, ()), key=lambda row: (row["effective_start"], row["assertion_id"]))
                if not scoped:
                    raise FeeWorkflowError(f"fee_statutory_initial_rule_missing:{component}:{market}:{side}")
                by_date: dict[str, dict[str, Any]] = {}
                for event in scoped:
                    previous = by_date.get(event["effective_start"])
                    if previous is not None and previous != event:
                        raise FeeWorkflowError(f"fee_statutory_same_date_conflict:{component}:{market}:{side}:{event['effective_start']}")
                    by_date[event["effective_start"]] = event
                scoped = [by_date[value] for value in sorted(by_date)]
                prior = [event for event in scoped if event["effective_start"] <= start]
                if not prior:
                    raise FeeWorkflowError(f"fee_statutory_initial_rule_missing:{component}:{market}:{side}")
                applicable = [prior[-1]] + [event for event in scoped if start < event["effective_start"] <= end]
                for index, event in enumerate(applicable):
                    interval_start = start if index == 0 else event["effective_start"]
                    interval_end = end if index + 1 == len(applicable) else _previous_date(applicable[index + 1]["effective_start"])
                    rules.append(
                        {
                            "rule_id": f"{component}:{market}:{side}:{interval_start}:{interval_end}",
                            "component": component,
                            "market": market,
                            "side": side,
                            "effective_start": interval_start,
                            "effective_end": interval_end,
                            "rate": event["rate"],
                            "basis": event["basis"],
                            "rounding": event["rounding"],
                            "minimum_cny": event["minimum_cny"],
                            "explicit_zero": event["explicit_zero"],
                            "evidence_class": event["statutory_evidence_class"],
                            "payer_scope": event["payer_scope"],
                            "calculation_contract": event["calculation_contract"],
                            "assertion_id": event["assertion_id"],
                            "assertion_hash": event["assertion_hash"],
                            "document_id": event["document_id"],
                            "document_sha256": event["document_sha256"],
                            "normalized_clause_hash": event["normalized_clause_hash"],
                            "source_documents": event["source_documents"],
                            "source_effective_start": event["effective_start"],
                        }
                    )
    return rules


def _derive_modeled_rules(
    policy: Mapping[str, Any],
    *,
    simulation_start: str,
    simulation_end: str,
) -> list[dict[str, Any]]:
    baseline = dict(policy["scenarios"]["baseline"])
    modeled = {
        "commission": {
            "rate": _decimal_text(Decimal(str(baseline["commission_rate"]))),
            "minimum_cny": _decimal_text(Decimal(str(baseline["minimum_commission"]))),
            "policy_field": "commission_rate|minimum_commission",
            "inclusion_contract": "exclusive_of_statutory_components",
        },
        "slippage": {
            "rate": _decimal_text(Decimal(str(baseline["slippage_bps"])) / Decimal("10000")),
            "minimum_cny": "0",
            "policy_field": "slippage_bps",
            "inclusion_contract": "not_a_fee_component",
        },
        "impact": {
            "rate": _decimal_text(Decimal(str(baseline["impact_bps"])) / Decimal("10000")),
            "minimum_cny": "0",
            "policy_field": "impact_bps",
            "inclusion_contract": "not_a_fee_component",
        },
    }
    rules = []
    for component, values in modeled.items():
        for market in MARKETS:
            for side in SIDES:
                rules.append(
                    {
                        "rule_id": f"{component}:{market}:{side}:{simulation_start}:{simulation_end}",
                        "component": component,
                        "market": market,
                        "side": side,
                        "effective_start": simulation_start,
                        "effective_end": simulation_end,
                        "rate": values["rate"],
                        "basis": "notional",
                        "rounding": "cent_half_up",
                        "minimum_cny": values["minimum_cny"],
                        "explicit_zero": False,
                        "evidence_class": "uncalibrated_modeled",
                        "policy_seal_hash": policy["content_hash"],
                        "policy_field": values["policy_field"],
                        "inclusion_contract": values["inclusion_contract"],
                    }
                )
    return rules


def _validate_rule_coverage(rules: Sequence[Mapping[str, Any]], simulation_start: str, simulation_end: str) -> None:
    start = _date(simulation_start)
    end = _date(simulation_end)
    if len({str(row.get("rule_id")) for row in rules}) != len(rules):
        raise FeeWorkflowError("fee_rule_identity_duplicate")
    for component in ALL_COMPONENTS:
        for market in MARKETS:
            for side in SIDES:
                scoped = sorted(
                    (
                        dict(row)
                        for row in rules
                        if row.get("component") == component and row.get("market") == market and row.get("side") == side
                    ),
                    key=lambda row: row["effective_start"],
                )
                cursor = start
                if not scoped:
                    raise FeeWorkflowError(f"fee_rule_coverage_gap:{component}:{market}:{side}:{start}")
                for row in scoped:
                    if row["effective_start"] != cursor:
                        code = "fee_rule_overlap" if row["effective_start"] < cursor else "fee_rule_coverage_gap"
                        raise FeeWorkflowError(f"{code}:{component}:{market}:{side}:{cursor}")
                    if row["effective_end"] < row["effective_start"]:
                        raise FeeWorkflowError("fee_rule_interval_invalid")
                    rate = Decimal(str(row["rate"]))
                    minimum = Decimal(str(row["minimum_cny"]))
                    if rate < 0 or minimum < 0 or not rate.is_finite() or not minimum.is_finite():
                        raise FeeWorkflowError("fee_rule_numeric_invalid")
                    if row["basis"] not in {"notional", "shares"} or row["rounding"] not in {"cent_half_up", "none"}:
                        raise FeeWorkflowError("fee_rule_calculation_contract_invalid")
                    if bool(row["explicit_zero"]) != (rate == 0):
                        raise FeeWorkflowError("fee_rule_explicit_zero_contract_invalid")
                    if component in STATUTORY_COMPONENTS and not str(
                        row.get("evidence_class") or ""
                    ).startswith("governed_official"):
                        raise FeeWorkflowError("fee_rule_statutory_evidence_invalid")
                    if component in MODELED_COMPONENTS and row.get("evidence_class") != "uncalibrated_modeled":
                        raise FeeWorkflowError("fee_rule_modeled_evidence_invalid")
                    cursor = _next_date(row["effective_end"])
                if cursor <= end:
                    raise FeeWorkflowError(f"fee_rule_coverage_gap:{component}:{market}:{side}:{cursor}")


def _schedule_native_files(
    *,
    fee_plan: Mapping[str, Any],
    acquired: Mapping[str, Any],
    verification: Mapping[str, Any],
    extracted: Mapping[str, Any],
) -> dict[str, bytes | Path]:
    plan_path = Path(fee_plan["manifest_path"])
    acquisition_path = Path(acquired["manifest_path"])
    verification_path = Path(verification["manifest_path"])
    extraction_path = Path(extracted["manifest_path"])
    files: dict[str, bytes | Path] = {
        "native_plan/fee_plan.json": plan_path,
        "native_plan/inputs/task055a_policy_seal.json": _contained_file(plan_path.parent, fee_plan["policy_seal_relative_path"]),
        "native_acquisition/fee_document_acquisition.json": acquisition_path,
        "native_acquisition/transport_ledger.jsonl": _contained_file(
            acquisition_path.parent,
            acquired["transport_ledger_relative_path"],
        ),
        "native_verification/fee_document_verification.json": verification_path,
        "native_extraction/fee_rule_extraction.json": extraction_path,
    }
    for document in acquired["documents"]:
        relative = _relative_path(document["artifact_relative_path"])
        files[f"native_acquisition/{relative}"] = _contained_file(acquisition_path.parent, relative)
    return files


def _independent_assertion_receipts(
    fee_plan: Mapping[str, Any],
    acquired: Mapping[str, Any],
    extracted: Mapping[str, Any],
) -> list[dict[str, Any]]:
    acquisition_path = Path(acquired["manifest_path"])
    documents = {row["document_id"]: row for row in acquired["documents"]}
    assertions = {row["extractor_id"]: row for row in extracted["assertions"]}
    receipts = []
    for extractor in fee_plan["extractors"]:
        document = documents[extractor["document_id"]]
        document_path = _contained_file(acquisition_path.parent, document["artifact_relative_path"])
        rebuilt = _extract_assertion(
            document_path,
            document,
            extractor,
            document_by_id=documents,
            acquisition_root=acquisition_path.parent,
        )
        if rebuilt != assertions.get(extractor["extractor_id"]):
            raise FeeWorkflowError("fee_independent_assertion_mismatch")
        receipts.append(
            {
                "extractor_id": extractor["extractor_id"],
                "document_sha256": document["sha256"],
                "assertion_hash": rebuilt["assertion_hash"],
                "normalized_clause_hash": rebuilt["normalized_clause_hash"],
            }
        )
    return sorted(receipts, key=lambda row: row["extractor_id"])


def _validate_policy_seal(path: str | Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = _resolve_manifest(path, "policy_seal.json")
    payload = _load_json(manifest_path)
    if payload.get("schema_version") != TASK055A_POLICY_SCHEMA:
        raise FeeWorkflowError("task055a_policy_seal_schema_invalid")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id", "manifest_path"}}
    if payload.get("content_hash") != canonical_hash(semantic):
        raise FeeWorkflowError("task055a_policy_seal_content_hash_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("content_hash") or "")):
        raise FeeWorkflowError("task055a_policy_seal_hash_invalid")
    expected_scenarios = {name: PREREGISTERED_SCENARIOS[name].to_dict() for name in PREREGISTERED_SCENARIOS}
    if payload.get("scenarios") != expected_scenarios:
        raise FeeWorkflowError("task055a_policy_seal_scenarios_mismatch")
    exact_ids = list(payload.get("exact20_ids") or ())
    if len(exact_ids) != 20 or len(set(exact_ids)) != 20:
        raise FeeWorkflowError("task055a_policy_seal_exact20_invalid")
    if payload.get("candidate_identity_root") != canonical_hash(exact_ids):
        raise FeeWorkflowError("task055a_policy_seal_identity_root_invalid")
    if payload.get("signal_cutoff") != "20240528" or payload.get("execution_endpoint") != "20240530":
        raise FeeWorkflowError("task055a_policy_seal_time_contract_invalid")
    if (
        payload.get("immutable") is not True
        or payload.get("selection_data_reused") is not True
        or payload.get("untouched_holdout") is not False
        or payload.get("evidence_level") != "retrospective_modeled_daily_bar_proxy"
    ):
        raise FeeWorkflowError("task055a_policy_seal_evidence_boundary_invalid")
    for field in ("observation_boundary_hash", "simulation_bundle_hash", "code_semantic_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get(field) or "")):
            raise FeeWorkflowError(f"task055a_policy_seal_{field}_invalid")
    return manifest_path, payload


def _document_pages(path: Path) -> list[str]:
    data = path.read_bytes()
    if data.startswith(b"%PDF"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise FeeWorkflowError("fee_pdf_parser_unavailable") from exc
        reader = PdfReader(path)
        pages = [_normalize_document_text(page.extract_text() or "") for page in reader.pages]
    else:
        decoded = _decode_official_text(data)
        decoded = re.sub(r"<script\b[^>]*>.*?</script>", " ", decoded, flags=re.I | re.S)
        decoded = re.sub(r"<style\b[^>]*>.*?</style>", " ", decoded, flags=re.I | re.S)
        decoded = re.sub(r"<(?:br|/p|/div|/li|/tr|/h\d)\b[^>]*>", "\n", decoded, flags=re.I)
        decoded = re.sub(r"<[^>]+>", " ", decoded)
        pages = [_normalize_document_text(html.unescape(decoded))]
    if not pages or all(len(page) < 40 for page in pages):
        raise FeeWorkflowError("fee_document_text_insufficient")
    return pages


def _normalize_document_text(value: str) -> str:
    value = value.replace("％", "%").replace("\u3000", " ").replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n+", "\n", value)
    return value.strip()


def _decode_official_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    raise FeeWorkflowError("fee_document_encoding_unsupported")


def _normalize_clause(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("％", "%")


def _fetch_https(request_url: str, *, max_redirects: int = 3) -> dict[str, Any]:
    _validate_official_url(request_url)
    context = ssl.create_default_context()
    current = request_url
    redirects = [current]
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=30, context=context)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("GET", target, headers={"User-Agent": "Auto-alpha-Task055G/1.0"})
        response = connection.getresponse()
        certificate = connection.sock.getpeercert(binary_form=True) if connection.sock else b""
        status = int(response.status)
        headers = {str(key).lower(): str(value) for key, value in response.getheaders()}
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            response.read()
            connection.close()
            if not location:
                raise FeeWorkflowError("fee_document_redirect_without_location")
            next_url = urljoin(current, location)
            _validate_same_host_redirect(request_url, next_url)
            redirects.append(next_url)
            current = next_url
            continue
        body = response.read()
        connection.close()
        return {
            "body": body,
            "final_url": current,
            "redirect_chain": redirects,
            "http_status": status,
            "tls_verified": True,
            "hostname_verified": True,
            "peer_certificate_sha256": hashlib.sha256(certificate).hexdigest(),
            "retrieved_at": datetime.now().astimezone().isoformat(),
            "response_headers": headers,
        }
    raise FeeWorkflowError("fee_document_redirect_limit_exceeded")


def _validate_official_url(url: str) -> None:
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or not _official_host_allowed(host):
        raise FeeWorkflowError("fee_document_official_https_required")
    if parsed.username or parsed.password or parsed.fragment:
        raise FeeWorkflowError("fee_document_url_authority_invalid")


def _validate_same_host_redirect(request_url: str, final_url: str) -> None:
    _validate_official_url(request_url)
    _validate_official_url(final_url)
    if (urlparse(request_url).hostname or "").lower() != (urlparse(final_url).hostname or "").lower():
        raise FeeWorkflowError("fee_document_cross_host_redirect_forbidden")


def _official_host_allowed(host: str) -> bool:
    value = str(host).lower().rstrip(".")
    return value in OFFICIAL_HOSTS or any(value.endswith(suffix) for suffix in OFFICIAL_SUFFIXES)


def _publish_generation(
    *,
    output_root: str | Path,
    prefix: str,
    manifest_name: str,
    semantic: Mapping[str, Any],
    files: Mapping[str, bytes | Path] | None = None,
) -> dict[str, Any]:
    content_hash = canonical_hash(semantic)
    generation_id = f"{prefix}_{content_hash[:24]}"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "generations" / generation_id
    if target.exists():
        manifest = _load_json(target / manifest_name)
        if manifest.get("content_hash") != content_hash:
            raise FeeWorkflowError("fee_generation_existing_content_mismatch")
        for relative, source in (files or {}).items():
            existing = _contained_file(target, relative)
            expected_sha = (
                hashlib.sha256(source).hexdigest()
                if isinstance(source, bytes)
                else _sha256_file(Path(source))
            )
            if _sha256_file(existing) != expected_sha:
                raise FeeWorkflowError("fee_generation_existing_file_mismatch")
    else:
        staging = Path(tempfile.mkdtemp(prefix=f".{prefix}.", dir=root))
        try:
            for relative, source in (files or {}).items():
                destination = staging / _relative_path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(source, bytes):
                    destination.write_bytes(source)
                else:
                    source_path = Path(source)
                    if not source_path.is_file() or source_path.is_symlink():
                        raise FeeWorkflowError("fee_generation_source_file_invalid")
                    shutil.copyfile(source_path, destination)
            manifest = dict(semantic) | {"content_hash": content_hash, "generation_id": generation_id}
            (staging / manifest_name).write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    _atomic_json(
        root / "current.json",
        {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/{manifest_name}",
        },
    )
    return _load_json(target / manifest_name) | {"manifest_path": str(target / manifest_name)}


def _validate_manifest_hash(manifest: Mapping[str, Any], schema: str, status: str) -> None:
    if manifest.get("schema_version") != schema or manifest.get("status") != status:
        raise FeeWorkflowError("fee_manifest_schema_or_status_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id", "manifest_path"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise FeeWorkflowError("fee_manifest_content_hash_invalid")


def _resolve_manifest(path: str | Path, manifest_name: str) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        relative = _relative_path(_load_json(pointer).get("manifest"))
        return _contained_file(value, relative)
    candidate = value / manifest_name
    if candidate.is_file():
        return candidate
    raise FeeWorkflowError(f"fee_manifest_missing:{manifest_name}")


def _contained_file(root: Path, relative: str | Path) -> Path:
    relative_path = Path(_relative_path(relative))
    root_resolved = root.resolve()
    candidate = (root_resolved / relative_path).resolve()
    if candidate == root_resolved or root_resolved not in candidate.parents:
        raise FeeWorkflowError("fee_artifact_path_escape")
    if not candidate.is_file() or candidate.is_symlink():
        raise FeeWorkflowError("fee_artifact_file_missing_or_symlink")
    return candidate


def _relative_path(value: Any) -> str:
    path = Path(str(value or ""))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise FeeWorkflowError("fee_relative_path_invalid")
    return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FeeWorkflowError("fee_json_object_required")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise FeeWorkflowError("fee_jsonl_object_required")
            rows.append(payload)
    return rows


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_source_hash(module: Any) -> str:
    source = inspect.getsourcefile(module)
    if not source:
        raise FeeWorkflowError("fee_semantic_source_missing")
    return _source_hash(Path(source))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date(value: Any) -> str:
    text = str(value or "").replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", text):
        raise FeeWorkflowError("fee_date_invalid")
    datetime.strptime(text, "%Y%m%d")
    return text


def _next_date(value: str) -> str:
    return (datetime.strptime(value, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")


def _previous_date(value: str) -> str:
    return (datetime.strptime(value, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise FeeWorkflowError("fee_decimal_non_finite")
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if text in {"-0", ""} else text


def _round_decimal(value: Decimal, rounding: str) -> float:
    if rounding == "none":
        return float(value)
    if rounding != "cent_half_up":
        raise FeeWorkflowError("fee_rounding_policy_unknown")
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
