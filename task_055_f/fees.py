"""Evidence-backed Fee Schedule v2 used by the Task 055-F simulator path."""

from __future__ import annotations

import html
import http.client
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
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

from .contracts import FEE_DOCUMENT_ACQUISITION_SCHEMA, FEE_SCHEDULE_SCHEMA
from .read_ledger import canonical_hash, sha256_file


class FeeScheduleError(RuntimeError):
    pass


STATUTORY_COMPONENTS = {
    "stamp_duty",
    "transfer_fee",
    "handling_fee",
    "securities_management_fee",
}
MODELED_COMPONENTS = {"commission", "slippage", "impact"}
ALL_COMPONENTS = STATUTORY_COMPONENTS | MODELED_COMPONENTS
MODELED_INCLUSION_CONTRACTS = {
    "commission": "exclusive_of_statutory_components",
    "slippage": "not_a_fee_component",
    "impact": "not_a_fee_component",
}
MARKETS = {"SSE", "SZSE"}
SIDES = {"BUY", "SELL"}
OFFICIAL_HOSTS = {
    "www.gov.cn",
    "www.chinatax.gov.cn",
    "www.mof.gov.cn",
    "www.sse.com.cn",
    "www.szse.cn",
    "www.chinaclear.cn",
    "www.csrc.gov.cn",
}
OFFICIAL_HOST_SUFFIXES = (".chinatax.gov.cn", ".mof.gov.cn", ".gov.cn")


def acquire_official_fee_documents(
    *,
    output_root: str | Path,
    documents: Iterable[Mapping[str, Any]],
    allow_network: bool,
    max_documents: int = 20,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    """Fetch allowlisted official documents and publish native receipts.

    The production path derives TLS, redirect, response-header, and body
    evidence inside this function. Callers cannot supply retrieval receipts.
    ``fetcher`` exists only for deterministic tests and is recorded as a
    synthetic evidence scope that production schedule validation rejects.
    """

    specs = [dict(row) for row in documents]
    if not allow_network:
        raise FeeScheduleError("fee_document_network_authorization_required")
    if not specs or len(specs) > int(max_documents) or len(specs) > 20:
        raise FeeScheduleError("fee_document_request_count_invalid")
    identities = [str(row.get("document_id") or "") for row in specs]
    if any(not value for value in identities) or len(set(identities)) != len(identities):
        raise FeeScheduleError("fee_document_identity_invalid")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055f.fee_docs.", dir=root))
    acquired: list[dict[str, Any]] = []
    try:
        for spec in specs:
            request_url = str(spec.get("request_url") or "")
            _validate_official_url(request_url)
            result = (
                _fetch_https_document(request_url)
                if fetcher is None
                else dict(fetcher(request_url))
            )
            body = bytes(result.get("body") or b"")
            if not body:
                raise FeeScheduleError("fee_document_empty_body")
            final_url = str(result.get("final_url") or request_url)
            _validate_same_host_redirect(request_url, final_url)
            body_sha = __import__("hashlib").sha256(body).hexdigest()
            suffix = Path(urlparse(final_url).path).suffix.lower()
            if suffix not in {".html", ".htm", ".pdf", ".txt"}:
                suffix = ".bin"
            relative = f"documents/{spec['document_id']}{suffix}"
            artifact = staging / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(body)
            receipt = {
                "producer": "task_055_f.fees.acquire_official_fee_documents",
                "evidence_scope": "real_official_https" if fetcher is None else "synthetic_test_transport",
                "request_url": request_url,
                "final_url": final_url,
                "redirect_chain": list(result.get("redirect_chain") or [request_url, final_url]),
                "http_status": int(result.get("http_status") or 0),
                "tls_verified": bool(result.get("tls_verified")),
                "hostname_verified": bool(result.get("hostname_verified")),
                "peer_certificate_sha256": str(result.get("peer_certificate_sha256") or ""),
                "body_sha256": body_sha,
                "retrieved_at": str(result.get("retrieved_at") or ""),
                "response_headers_sha256": canonical_hash(dict(result.get("response_headers") or {})),
                "source_code_hash": _fee_fetcher_source_hash(),
            }
            receipt["receipt_hash"] = canonical_hash(receipt)
            _validate_retrieval_receipt(
                receipt,
                expected_sha=body_sha,
                allow_synthetic_test_fixture=fetcher is not None,
            )
            acquired.append(
                {
                    "document_id": str(spec["document_id"]),
                    "publisher": str(spec.get("publisher") or ""),
                    "request_url": request_url,
                    "final_url": final_url,
                    "artifact_relative_path": relative,
                    "sha256": body_sha,
                    "size_bytes": len(body),
                    "retrieval_receipt": receipt,
                    "retrieval_receipt_hash": receipt["receipt_hash"],
                }
            )
        semantic = {
            "schema_version": FEE_DOCUMENT_ACQUISITION_SCHEMA,
            "status": "passed",
            "evidence_scope": "real_official_https" if fetcher is None else "synthetic_test_fixture",
            "source_code_hash": _fee_fetcher_source_hash(),
            "documents": sorted(acquired, key=lambda row: row["document_id"]),
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"official_fee_documents_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        (staging / "official_fee_document_acquisition.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/official_fee_document_acquisition.json",
            },
        )
        return manifest | {"manifest_path": str(target / "official_fee_document_acquisition.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_official_fee_document_acquisition(
    path: str | Path,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    manifest_path = _resolve_acquisition_manifest(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != FEE_DOCUMENT_ACQUISITION_SCHEMA or manifest.get("status") != "passed":
        raise FeeScheduleError("fee_document_acquisition_manifest_invalid")
    if manifest.get("source_code_hash") != _fee_fetcher_source_hash():
        raise FeeScheduleError("fee_document_acquisition_source_hash_mismatch")
    if manifest.get("evidence_scope") != "real_official_https" and not allow_synthetic_test_fixture:
        raise FeeScheduleError("synthetic_fee_document_acquisition_forbidden")
    documents = list(manifest.get("documents") or ())
    if not documents or len({row.get("document_id") for row in documents}) != len(documents):
        raise FeeScheduleError("fee_document_acquisition_documents_invalid")
    root = manifest_path.parent
    for row in documents:
        relative = _relative_path(row.get("artifact_relative_path"))
        artifact = (root / relative).resolve()
        if root.resolve() not in artifact.parents or not artifact.is_file() or artifact.is_symlink():
            raise FeeScheduleError("fee_document_acquisition_artifact_invalid")
        if sha256_file(artifact) != row.get("sha256") or artifact.stat().st_size != int(row.get("size_bytes") or -1):
            raise FeeScheduleError("fee_document_acquisition_artifact_integrity_invalid")
        _validate_retrieval_receipt(
            row.get("retrieval_receipt") or {},
            expected_sha=str(row.get("sha256") or ""),
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise FeeScheduleError("fee_document_acquisition_content_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def publish_fee_schedule_v2(
    *,
    output_root: str | Path,
    document_acquisition_manifest: str | Path | None = None,
    document_root: str | Path | None = None,
    documents: Iterable[Mapping[str, Any]] | None = None,
    rules: Iterable[Mapping[str, Any]],
    simulation_start: str,
    simulation_end: str,
    policy_seal_hash: str,
    builder_code_hash: str,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    acquisition = None
    if document_acquisition_manifest is not None:
        acquisition = validate_official_fee_document_acquisition(
            document_acquisition_manifest,
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
        acquisition_path = Path(acquisition["manifest_path"])
        document_root_path = acquisition_path.parent
        normalized_documents = [
            _normalize_acquired_document(
                row,
                document_root_path,
                allow_synthetic_test_fixture=allow_synthetic_test_fixture,
            )
            for row in acquisition["documents"]
        ]
    else:
        if not allow_synthetic_test_fixture or document_root is None or documents is None:
            raise FeeScheduleError("native_fee_document_acquisition_required")
        document_root_path = Path(document_root).resolve()
        normalized_documents = [_normalize_document(row, document_root_path, allow_synthetic_test_fixture=True) for row in documents]
    document_by_id = {row["document_id"]: row for row in normalized_documents}
    if len(document_by_id) != len(normalized_documents):
        raise FeeScheduleError("fee_document_identity_duplicate")
    normalized_rules = [_normalize_rule(row) for row in rules]
    normalized_rules.sort(
        key=lambda row: (
            row["component"],
            row["market"],
            row["side"],
            row["effective_start"],
            row["rule_id"],
        )
    )
    _validate_rules(
        normalized_rules,
        document_by_id,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
        document_root=document_root_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    semantic = {
        "schema_version": FEE_SCHEDULE_SCHEMA,
        "status": "passed",
        "simulation_start": _date(simulation_start),
        "simulation_end": _date(simulation_end),
        "policy_seal_hash": str(policy_seal_hash),
        "builder_code_hash": str(builder_code_hash),
        "evidence_scope": "synthetic_test_fixture" if acquisition is None else acquisition["evidence_scope"],
        "document_acquisition_content_hash": None if acquisition is None else acquisition["content_hash"],
        "statutory_components": sorted(STATUTORY_COMPONENTS),
        "modeled_components": sorted(MODELED_COMPONENTS),
        "documents": normalized_documents,
        "rules": normalized_rules,
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"fee_schedule_v2_{content_hash[:24]}"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055f.fees.", dir=root))
    try:
        for document in normalized_documents:
            source = document_root_path / document["source_relative_path"]
            target = staging / "documents" / document["artifact_relative_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        (staging / "fee_schedule_v2_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/fee_schedule_v2_manifest.json",
            },
        )
        return manifest | {"manifest_path": str(target / "fee_schedule_v2_manifest.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_fee_schedule_v2(path: str | Path) -> dict[str, Any]:
    return _validate_fee_schedule_v2(path, allow_synthetic_test_fixture=False)


def validate_synthetic_fee_schedule_v2(path: str | Path) -> dict[str, Any]:
    return _validate_fee_schedule_v2(path, allow_synthetic_test_fixture=True)


def _validate_fee_schedule_v2(
    path: str | Path,
    *,
    allow_synthetic_test_fixture: bool,
) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != FEE_SCHEDULE_SCHEMA or manifest.get("status") != "passed":
        raise FeeScheduleError("fee_schedule_schema_or_status_invalid")
    if manifest.get("evidence_scope") != "real_official_https" and not allow_synthetic_test_fixture:
        raise FeeScheduleError("synthetic_fee_schedule_forbidden")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise FeeScheduleError("fee_schedule_content_hash_mismatch")
    document_by_id = {str(row["document_id"]): dict(row) for row in manifest.get("documents") or ()}
    if len(document_by_id) != len(manifest.get("documents") or ()):
        raise FeeScheduleError("fee_document_identity_duplicate")
    _validate_rules(
        [dict(row) for row in manifest.get("rules") or ()],
        document_by_id,
        simulation_start=str(manifest["simulation_start"]),
        simulation_end=str(manifest["simulation_end"]),
        manifest_root=manifest_path.parent,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    return manifest | {"manifest_path": str(manifest_path)}


class FeeScheduleCalculator:
    """Prevalidated per-fill fee calculator with no embedded fallback."""

    def __init__(self, schedule_path: str | Path, *, allow_synthetic_test_fixture: bool = False) -> None:
        self.schedule = _validate_fee_schedule_v2(
            schedule_path,
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for rule in self.schedule["rules"]:
            grouped[(rule["component"], rule["market"], rule["side"])].append(dict(rule))
        self.rules = {key: tuple(sorted(rows, key=lambda row: row["effective_start"])) for key, rows in grouped.items()}

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
        if market not in MARKETS or side not in SIDES:
            raise FeeScheduleError("fee_calculation_scope_invalid")
        if not math.isfinite(notional) or notional < 0 or shares < 0:
            raise FeeScheduleError("fee_calculation_input_invalid")
        if zero_all_costs:
            return {component: 0.0 for component in sorted(ALL_COMPONENTS)} | {"total": 0.0}
        result: dict[str, float] = {}
        for component in sorted(ALL_COMPONENTS):
            matching = [
                rule
                for rule in self.rules.get((component, market, side), ())
                if rule["effective_start"] <= trade_date <= rule["effective_end"]
            ]
            if len(matching) != 1:
                raise FeeScheduleError(f"fee_rule_match_invalid:{component}:{market}:{side}:{trade_date}")
            rule = matching[0]
            base = notional if rule["basis"] == "notional" else float(shares)
            value = 0.0 if rule["explicit_zero"] else max(rule["minimum_cny"], base * rule["rate"])
            if component in MODELED_COMPONENTS:
                value *= float(modeled_multiplier)
            result[component] = _round_fee(value, rule["rounding"])
        result["total"] = _round_fee(sum(result.values()), "cent_half_up")
        return result


def _normalize_document(
    row: Mapping[str, Any],
    root: Path,
    *,
    allow_synthetic_test_fixture: bool,
) -> dict[str, Any]:
    source_relative = _relative_path(row.get("source_relative_path"))
    source = (root / source_relative).resolve()
    if source != root and root not in source.parents:
        raise FeeScheduleError("fee_document_path_escape")
    if not source.is_file() or source.is_symlink():
        raise FeeScheduleError("fee_document_missing_or_symlink")
    actual_sha = sha256_file(source)
    if actual_sha != row.get("sha256"):
        raise FeeScheduleError("fee_document_sha_mismatch")
    receipt = dict(row.get("retrieval_receipt") or {})
    _validate_retrieval_receipt(
        receipt,
        expected_sha=actual_sha,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    return {
        "document_id": str(row.get("document_id") or ""),
        "publisher": str(row.get("publisher") or ""),
        "request_url": str(row.get("request_url") or ""),
        "final_url": str(row.get("final_url") or ""),
        "source_relative_path": source_relative,
        "artifact_relative_path": f"{str(row.get('document_id') or '')}{source.suffix.lower() or '.bin'}",
        "sha256": actual_sha,
        "retrieval_receipt": receipt,
        "retrieval_receipt_hash": canonical_hash(receipt),
    }


def _normalize_acquired_document(
    row: Mapping[str, Any],
    root: Path,
    *,
    allow_synthetic_test_fixture: bool,
) -> dict[str, Any]:
    source_relative = _relative_path(row.get("artifact_relative_path"))
    source = (root / source_relative).resolve()
    if root != source and root not in source.parents:
        raise FeeScheduleError("fee_document_path_escape")
    if not source.is_file() or source.is_symlink() or sha256_file(source) != row.get("sha256"):
        raise FeeScheduleError("fee_document_acquired_source_invalid")
    receipt = dict(row.get("retrieval_receipt") or {})
    _validate_retrieval_receipt(
        receipt,
        expected_sha=str(row["sha256"]),
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    return {
        "document_id": str(row.get("document_id") or ""),
        "publisher": str(row.get("publisher") or ""),
        "request_url": str(row.get("request_url") or ""),
        "final_url": str(row.get("final_url") or ""),
        "source_relative_path": source_relative,
        "artifact_relative_path": f"{str(row.get('document_id') or '')}{source.suffix.lower() or '.bin'}",
        "sha256": str(row["sha256"]),
        "retrieval_receipt": receipt,
        "retrieval_receipt_hash": canonical_hash(receipt),
    }


def _normalize_rule(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": str(row.get("rule_id") or ""),
        "component": str(row.get("component") or ""),
        "market": str(row.get("market") or ""),
        "side": str(row.get("side") or "").upper(),
        "effective_start": _date(row.get("effective_start")),
        "effective_end": _date(row.get("effective_end")),
        "rate": float(row.get("rate", 0.0)),
        "basis": str(row.get("basis") or "notional"),
        "rounding": str(row.get("rounding") or "cent_half_up"),
        "minimum_cny": float(row.get("minimum_cny", 0.0)),
        "explicit_zero": bool(row.get("explicit_zero", False)),
        "evidence_class": str(row.get("evidence_class") or ""),
        "document_id": str(row.get("document_id") or ""),
        "page_or_clause": str(row.get("page_or_clause") or ""),
        "clause_text": str(row.get("clause_text") or ""),
        "rate_text": str(row.get("rate_text") or ""),
        "effective_date_text": str(row.get("effective_date_text") or ""),
        "direction_text": str(row.get("direction_text") or ""),
        "model_name": str(row.get("model_name") or ""),
        "model_version": str(row.get("model_version") or ""),
        "calibration_status": str(row.get("calibration_status") or ""),
        "inclusion_contract": str(row.get("inclusion_contract") or ""),
    }


def _validate_rules(
    rules: list[Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
    *,
    simulation_start: str,
    simulation_end: str,
    document_root: Path | None = None,
    manifest_root: Path | None = None,
    allow_synthetic_test_fixture: bool = False,
) -> None:
    start = _date(simulation_start)
    end = _date(simulation_end)
    if not rules or len({str(rule.get("rule_id")) for rule in rules}) != len(rules):
        raise FeeScheduleError("fee_rule_identity_invalid")
    for rule in rules:
        component = str(rule.get("component"))
        if component not in ALL_COMPONENTS or rule.get("market") not in MARKETS or rule.get("side") not in SIDES:
            raise FeeScheduleError("fee_rule_scope_invalid")
        if rule.get("basis") not in {"notional", "shares"} or rule.get("rounding") not in {"cent_half_up", "none"}:
            raise FeeScheduleError("fee_rule_calculation_contract_invalid")
        if not math.isfinite(float(rule.get("rate", -1))) or float(rule.get("rate", -1)) < 0:
            raise FeeScheduleError("fee_rule_rate_invalid")
        if not math.isfinite(float(rule.get("minimum_cny", -1))) or float(rule.get("minimum_cny", -1)) < 0:
            raise FeeScheduleError("fee_rule_minimum_invalid")
        if str(rule.get("effective_start")) > str(rule.get("effective_end")):
            raise FeeScheduleError("fee_rule_interval_invalid")
        if rule.get("explicit_zero") and (float(rule.get("rate", 0)) != 0 or float(rule.get("minimum_cny", 0)) != 0):
            raise FeeScheduleError("fee_explicit_zero_invalid")
        if component in STATUTORY_COMPONENTS:
            if rule.get("evidence_class") != "governed_official":
                raise FeeScheduleError("statutory_fee_evidence_class_invalid")
            document_id = str(rule.get("document_id") or "")
            if document_id not in documents:
                raise FeeScheduleError("statutory_fee_document_missing")
            document = documents[document_id]
            document_path = _document_path(document, document_root=document_root, manifest_root=manifest_root)
            if sha256_file(document_path) != document.get("sha256"):
                raise FeeScheduleError("statutory_fee_document_sha_mismatch")
            _validate_retrieval_receipt(
                document.get("retrieval_receipt") or {},
                expected_sha=str(document["sha256"]),
                allow_synthetic_test_fixture=allow_synthetic_test_fixture,
            )
            text = _document_text(document_path)
            clause = _normalize_text(str(rule.get("clause_text") or ""))
            if not clause or clause not in text:
                raise FeeScheduleError("statutory_fee_clause_not_found")
            for field in ("rate_text", "effective_date_text", "direction_text"):
                token = _normalize_text(str(rule.get(field) or ""))
                if not token or token not in clause:
                    raise FeeScheduleError(f"statutory_fee_clause_token_missing:{field}")
            if not str(rule.get("page_or_clause") or ""):
                raise FeeScheduleError("statutory_fee_clause_locator_missing")
        else:
            if (
                rule.get("evidence_class") != "modeled"
                or not rule.get("model_name")
                or not rule.get("model_version")
                or rule.get("calibration_status") != "uncalibrated_modeled"
                or rule.get("inclusion_contract") != MODELED_INCLUSION_CONTRACTS[component]
            ):
                raise FeeScheduleError("modeled_fee_contract_invalid")
            if rule.get("document_id"):
                raise FeeScheduleError("modeled_fee_must_not_claim_official_document")
    for component in ALL_COMPONENTS:
        for market in MARKETS:
            for side in SIDES:
                scoped = sorted(
                    [
                        rule
                        for rule in rules
                        if rule.get("component") == component
                        and rule.get("market") == market
                        and rule.get("side") == side
                        and str(rule.get("effective_end")) >= start
                        and str(rule.get("effective_start")) <= end
                    ],
                    key=lambda rule: str(rule["effective_start"]),
                )
                cursor = start
                if not scoped:
                    raise FeeScheduleError(f"fee_rule_coverage_gap:{component}:{market}:{side}:{cursor}")
                for rule in scoped:
                    effective_start = max(start, str(rule["effective_start"]))
                    effective_end = min(end, str(rule["effective_end"]))
                    if effective_start != cursor:
                        code = "fee_rule_overlap" if effective_start < cursor else "fee_rule_coverage_gap"
                        raise FeeScheduleError(f"{code}:{component}:{market}:{side}:{cursor}")
                    cursor = _next_date(effective_end)
                if cursor <= end:
                    raise FeeScheduleError(f"fee_rule_coverage_gap:{component}:{market}:{side}:{cursor}")


def _validate_retrieval_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_sha: str,
    allow_synthetic_test_fixture: bool = False,
) -> None:
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if not unsigned or receipt.get("receipt_hash") != canonical_hash(unsigned):
        raise FeeScheduleError("fee_document_retrieval_receipt_invalid")
    request_url = str(receipt.get("request_url") or "")
    final_url = str(receipt.get("final_url") or "")
    request_host = (urlparse(request_url).hostname or "").lower()
    final_host = (urlparse(final_url).hostname or "").lower()
    if (
        not request_url.startswith("https://")
        or not final_url.startswith("https://")
        or not _official_host_allowed(request_host)
        or not _official_host_allowed(final_host)
        or request_host != final_host
    ):
        raise FeeScheduleError("fee_document_origin_invalid")
    scope = str(receipt.get("evidence_scope") or "")
    if scope != "real_official_https" and not allow_synthetic_test_fixture:
        raise FeeScheduleError("fee_document_receipt_scope_invalid")
    if receipt.get("producer") != "task_055_f.fees.acquire_official_fee_documents":
        raise FeeScheduleError("fee_document_receipt_producer_invalid")
    if receipt.get("source_code_hash") != _fee_fetcher_source_hash():
        raise FeeScheduleError("fee_document_receipt_source_hash_invalid")
    if receipt.get("tls_verified") is not True or receipt.get("hostname_verified") is not True:
        raise FeeScheduleError("fee_document_tls_attestation_invalid")
    if int(receipt.get("http_status") or 0) != 200 or receipt.get("body_sha256") != expected_sha:
        raise FeeScheduleError("fee_document_http_or_body_receipt_invalid")
    if (
        not receipt.get("retrieved_at")
        or not receipt.get("response_headers_sha256")
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("peer_certificate_sha256") or ""))
    ):
        raise FeeScheduleError("fee_document_retrieval_metadata_missing")


def _fetch_https_document(request_url: str, *, max_redirects: int = 3) -> dict[str, Any]:
    current = request_url
    chain = [current]
    context = ssl.create_default_context()
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        _validate_official_url(current)
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, context=context, timeout=30)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("GET", target, headers={"User-Agent": "Auto-alpha-Task055F/1.0"})
        response = connection.getresponse()
        certificate = connection.sock.getpeercert(binary_form=True) if connection.sock else b""
        status = int(response.status)
        headers = {str(key).lower(): str(value) for key, value in response.getheaders()}
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            response.read()
            connection.close()
            if not location:
                raise FeeScheduleError("fee_document_redirect_without_location")
            next_url = urljoin(current, location)
            _validate_same_host_redirect(request_url, next_url)
            current = next_url
            chain.append(current)
            continue
        body = response.read()
        connection.close()
        if status != 200:
            raise FeeScheduleError(f"fee_document_http_status_invalid:{status}")
        return {
            "body": body,
            "final_url": current,
            "redirect_chain": chain,
            "http_status": status,
            "tls_verified": True,
            "hostname_verified": True,
            "peer_certificate_sha256": __import__("hashlib").sha256(certificate).hexdigest(),
            "retrieved_at": datetime.now().astimezone().isoformat(),
            "response_headers": headers,
        }
    raise FeeScheduleError("fee_document_redirect_limit_exceeded")


def _validate_official_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or not _official_host_allowed(parsed.hostname.lower()):
        raise FeeScheduleError("fee_document_origin_invalid")
    if parsed.username or parsed.password or parsed.fragment:
        raise FeeScheduleError("fee_document_url_authority_invalid")


def _validate_same_host_redirect(request_url: str, final_url: str) -> None:
    _validate_official_url(final_url)
    if (urlparse(request_url).hostname or "").lower() != (urlparse(final_url).hostname or "").lower():
        raise FeeScheduleError("fee_document_cross_host_redirect_forbidden")


def _official_host_allowed(host: str) -> bool:
    normalized = str(host or "").lower().rstrip(".")
    return normalized in OFFICIAL_HOSTS or any(normalized.endswith(suffix) for suffix in OFFICIAL_HOST_SUFFIXES)


def _fee_fetcher_source_hash() -> str:
    return __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest()


def _document_path(
    document: Mapping[str, Any],
    *,
    document_root: Path | None,
    manifest_root: Path | None,
) -> Path:
    if document_root is not None:
        return document_root / str(document["source_relative_path"])
    if manifest_root is not None:
        return manifest_root / "documents" / str(document["artifact_relative_path"])
    raise FeeScheduleError("fee_document_root_missing")


def _document_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"%PDF"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise FeeScheduleError("pdf_fee_document_requires_pypdf") from exc
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        decoded = data.decode("utf-8", errors="ignore")
        decoded = re.sub(r"<script\b[^>]*>.*?</script>", " ", decoded, flags=re.I | re.S)
        decoded = re.sub(r"<style\b[^>]*>.*?</style>", " ", decoded, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", decoded)
        text = html.unescape(text)
    normalized = _normalize_text(text)
    if len(normalized) < 100:
        raise FeeScheduleError("fee_document_content_too_short")
    return normalized


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("％", "%").replace("‰", "‰")


def _date(value: Any) -> str:
    text = str(value or "").replace("-", "")
    if not re.fullmatch(r"\d{8}", text):
        raise FeeScheduleError("fee_date_invalid")
    return text


def _next_date(value: str) -> str:
    return (datetime.strptime(value, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")


def _round_fee(value: float, policy: str) -> float:
    if policy == "none":
        return float(value)
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _relative_path(value: Any) -> str:
    path = Path(str(value or ""))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise FeeScheduleError("fee_document_relative_path_invalid")
    return str(path)


def _resolve_manifest(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / "fee_schedule_v2_manifest.json"
    if candidate.is_file():
        return candidate
    raise FeeScheduleError("fee_schedule_manifest_missing")


def _resolve_acquisition_manifest(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / "official_fee_document_acquisition.json"
    if candidate.is_file():
        return candidate
    raise FeeScheduleError("fee_document_acquisition_manifest_missing")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
