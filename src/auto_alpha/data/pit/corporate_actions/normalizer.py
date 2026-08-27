"""Normalize provider records and replay CNINFO corporate-action documents.

The document seam keeps byte verification, text extraction, semantic parsing,
event versioning, and chain adjudication in one module.  It deliberately emits
blocked research evidence; parsing a document never grants Data Admission.
"""

from __future__ import annotations

import base64
import binascii
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import html.parser as html_parser_module
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import platform
import re
import resource
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_generation,
    sha256_file,
    validate_generation,
)

from .models import CorporateActionEvent, CorporateActionType


CNINFO_CORPORATE_ACTION_PARSER_IDENTITY = (
    "cninfo_corporate_action_semantic_replay_v1"
)
_CNINFO_EVIDENCE_SCHEMA = "cninfo_corporate_action_semantic_evidence_v1"
_CNINFO_EVIDENCE_MANIFEST = "cninfo_corporate_action_semantic_manifest.json"
_CNINFO_EVIDENCE_PREFIX = "cninfo_corporate_action_semantics"
_CNINFO_EVIDENCE_FILES = {
    "source_documents": "source_documents.jsonl",
    "event_versions": "event_versions.jsonl",
    "event_chains": "event_chains.jsonl",
    "document_results": "document_results.jsonl",
    "blockers": "parse_blockers.jsonl",
    "governance_blockers": "governance_blockers.jsonl",
}
_CNINFO_SAFETY_FLAGS = frozenset(
    {
        "profile_activation_authorized",
        "alpha_search_authorized",
        "holdout_activation_authorized",
        "paper_trading_authorized",
        "shadow_trading_authorized",
        "live_trading_authorized",
    }
)
_CNINFO_VALIDATION_BOUNDARY = (
    "semantic_artifact_replay_without_source_document_bodies"
)
_CNINFO_SOURCE_DOCUMENT_FIELDS = frozenset(
    {
        "announcement_id",
        "announcement_time",
        "announcement_time_precision_proven",
        "announcement_title",
        "security_id",
        "sec_code",
        "org_id",
        "adjunct_url",
        "document_format",
        "document_sha256",
        "document_size_bytes",
        "source_request_id",
        "source_request_semantic_hash",
        "source_raw_envelope_sha256",
        "source_raw_payload_sha256",
        "source_inventory_content_hash",
        "source_document_closure_root",
        "source_parent_generation_id",
        "source_parent_content_hash",
        "source_parent_terminal_signature",
        "source_parent_publication_signature",
        "document_record_id",
        "document_body_replay_verified",
        "source_scope_roles",
        "source_inventory_scope_root",
        "source_governed_evidence_eligible",
    }
)
_CNINFO_EVENT_VERSION_FIELDS = frozenset(
    {
        "event_id",
        "security_id",
        "fiscal_period_end",
        "stage",
        "known_at",
        "known_at_utc",
        "known_timing",
        "publication_time_precision_proven",
        "effective_at",
        "record_date",
        "pay_date",
        "div_listdate",
        "cash_div_per_share",
        "stock_bonus_ratio",
        "stock_transfer_ratio",
        "stock_distribution_ratio",
        "economic_terms_complete",
        "adjustment_semantic_complete",
        "event_ledger_semantic_complete",
        "ts_code",
        "identity_timeline_projection_required",
        "source_announcement_id",
        "source_document_sha256",
        "source_text_sha256",
        "source_title",
        "source_sec_code",
        "source_org_id",
        "source_adjunct_url",
        "source_lineage_root",
        "source_document_closure_root",
        "source_governed_evidence_eligible",
        "text_extraction_replay_verified",
        "event_version_id",
        "supersedes_event_version_id",
        "parser_semantic_complete",
        "semantic_candidate_eligible",
        "pit_evidence_eligible",
        "independent_event_coverage_verdict_required",
    }
)
_CNINFO_EVENT_CHAIN_FIELDS = frozenset(
    {
        "event_id",
        "security_id",
        "fiscal_period_end",
        "ordered_event_version_ids",
        "stages_observed",
        "terminal_event_version_id",
        "chain_complete",
        "blockers",
    }
)
_CNINFO_DERIVATION_SCALAR_FIELDS = frozenset(
    {
        "schema_version",
        "parser_identity",
        "parser_implementation_root",
        "text_extractor_contract",
        "text_extractor_identity",
        "text_extractor_implementation_root",
        "resource_budget",
        "source_document_count",
        "source_document_root",
        "parsed_document_count",
        "blocked_document_count",
        "out_of_scope_document_count",
        "event_version_count",
        "event_chain_count",
        "technical_replay_complete",
        "validation_boundary",
        "source_document_bodies_archived",
        "data_admission_eligible",
        "independent_admission_verdict_required",
        "safety",
    }
)
_CNINFO_TEXT_EXTRACTOR_SCHEMA = "cninfo_text_extractor_contract_v2"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_DOCUMENT_BYTES = 132 * 1024 * 1024
_MAX_TEXT_CHARS = 16 * 1024 * 1024
_MAX_DOCUMENTS_PER_SEMANTIC_SHARD = 60_000
_MAX_PDF_PROCESS_ADDRESS_BYTES = 1024 * 1024 * 1024
_MAX_PDF_PROCESS_FILE_BYTES = _MAX_TEXT_CHARS * 4
_MAX_TOOL_PROBE_BYTES = 1024 * 1024
_PDF_EXTRACTOR_ENV = {
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
}
_CNINFO_POSTPROCESS_RECORD_FIELDS = (
    "schema_version",
    "ordinal",
    "announcement_id",
    "announcement_time",
    "announcement_time_precision_proven",
    "announcement_title",
    "announcement_type",
    "column_id",
    "matched_leaves",
    "source_scope_roles",
    "sec_code",
    "sec_name",
    "org_id",
    "security_id",
    "adjunct_url",
    "declared_adjunct_size_kb",
    "document_format",
    "document_sha256",
    "document_size_bytes",
    "source_request_id",
    "source_request_semantic_hash",
    "source_raw_envelope_sha256",
    "source_raw_payload_sha256",
    "source_parent_generation_id",
    "source_parent_content_hash",
    "source_parent_terminal_signature",
    "source_parent_publication_signature",
    "source_inventory_records",
    "source_inventory_content_hash",
    "source_inventory_scope_root",
    "source_document_closure_root",
    "source_lineage_complete",
    "source_governed_evidence_eligible",
    "closure_complete",
    "closure_downstream_eligible",
    "closure_blockers",
    "governance_blockers",
    "data_admission_eligible",
    "pit_evidence_eligible",
    "independent_data_admission_verdict_required",
)
_STAGE_ORDER = {
    "proposal": 0,
    "shareholder_approval": 1,
    "implementation": 2,
    "correction": 3,
    "withdrawal": 4,
}
_CASH_PATTERN = re.compile(
    r"每\s*(?P<base>\d+(?:\.\d+)?)\s*股"
    r"[^。；;\n]{0,80}?"
    r"派(?:发)?(?:现金红利|现金|红利)?(?:人民币)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*元"
)
_BONUS_PATTERN = re.compile(
    r"每\s*(?P<base>\d+(?:\.\d+)?)\s*股"
    r"[^。；;\n]{0,80}?送(?:红股)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*股"
)
_TRANSFER_PATTERN = re.compile(
    r"每\s*(?P<base>\d+(?:\.\d+)?)\s*股"
    r"[^。；;\n]{0,80}?转增\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*股"
)
_DATE_LABELS = {
    "record_date": ("股权登记日", "登记日"),
    "effective_at": ("除权除息日", "除息日", "除权日"),
    "pay_date": ("现金红利发放日", "红利发放日", "派息日"),
    "div_listdate": ("新增股份上市日", "红股上市日"),
}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class _BoundedHTMLTextParser(HTMLParser):
    def __init__(self, max_text_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_text_chars = max_text_chars
        self._ignored_depth = 0
        self._chunks: list[str] = []
        self._text_chars = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data:
            return
        self._text_chars += len(data)
        if self._text_chars > self._max_text_chars:
            raise ValueError("cninfo_document_text_limit_exceeded")
        self._chunks.append(data)

    def text(self) -> str:
        return _normalize_text(" ".join(self._chunks))


def _pdf_extractor_resource_limits(
    *,
    max_output_bytes: int,
    cpu_seconds: int,
) -> Callable[[], None]:
    def apply() -> None:
        resource.setrlimit(
            resource.RLIMIT_AS,
            (
                _MAX_PDF_PROCESS_ADDRESS_BYTES,
                _MAX_PDF_PROCESS_ADDRESS_BYTES,
            ),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (max_output_bytes, max_output_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (cpu_seconds, cpu_seconds),
        )

    return apply


def _fixed_tool_output(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int = _MAX_TOOL_PROBE_BYTES,
) -> tuple[int, bytes]:
    """Run a tool probe with fixed environment and file-backed bounded output."""

    if not argv or timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError("cninfo_text_extractor_probe_invalid")
    with tempfile.TemporaryDirectory(
        prefix="auto-alpha-cninfo-extractor-probe."
    ) as temporary:
        output_path = Path(temporary) / "tool-output.bin"
        try:
            with output_path.open("w+b") as output:
                completed = subprocess.run(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    timeout=timeout_seconds,
                    check=False,
                    env=dict(_PDF_EXTRACTOR_ENV),
                    preexec_fn=_pdf_extractor_resource_limits(
                        max_output_bytes=max_output_bytes,
                        cpu_seconds=max(1, int(timeout_seconds) + 1),
                    ),
                )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("cninfo_text_extractor_probe_timeout") from exc
        try:
            size = output_path.stat().st_size
            if size > max_output_bytes:
                raise ValueError("cninfo_text_extractor_probe_output_limit")
            output_bytes = output_path.read_bytes()
        except OSError as exc:
            raise ValueError("cninfo_text_extractor_probe_invalid") from exc
    return completed.returncode, output_bytes


def _pdftotext_dynamic_libraries(binary: Path) -> list[dict[str, str]]:
    """Return exact ELF library files used by the configured extractor."""

    ldd = Path("/usr/bin/ldd")
    if not ldd.is_file() or ldd.is_symlink():
        raise ValueError("cninfo_pdf_dynamic_library_probe_invalid")
    returncode, output = _fixed_tool_output(
        [str(ldd), str(binary)],
        timeout_seconds=10,
    )
    text = output.decode("utf-8", errors="strict")
    if returncode != 0 or "not found" in text:
        raise ValueError("cninfo_pdf_dynamic_library_probe_invalid")
    paths: set[Path] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.search(r"=>\s+(?P<path>/[^\s]+)\s+\(", line)
        if match is None:
            match = re.match(r"(?P<path>/[^\s]+)\s+\(", line)
        if match is None:
            continue
        path = Path(match.group("path"))
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError("cninfo_pdf_dynamic_library_probe_invalid") from exc
        if not resolved.is_file():
            raise ValueError("cninfo_pdf_dynamic_library_probe_invalid")
        paths.add(resolved)
    if not paths:
        raise ValueError("cninfo_pdf_dynamic_library_probe_invalid")
    return [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in sorted(paths, key=lambda value: str(value))
    ]


def _python_runtime_contract() -> dict[str, Any]:
    if platform.python_implementation() != "CPython":
        raise ValueError("cninfo_python_runtime_unsupported")
    executable = Path(sys.executable).resolve(strict=True)
    html_module = Path(str(html_parser_module.__file__)).resolve(strict=True)
    if not executable.is_file() or not html_module.is_file():
        raise ValueError("cninfo_python_runtime_invalid")
    return {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "cache_tag": str(sys.implementation.cache_tag or ""),
        "executable_path": str(executable),
        "executable_sha256": sha256_file(executable),
        "stdlib_html_parser_path": str(html_module),
        "stdlib_html_parser_sha256": sha256_file(html_module),
    }


def normalize_corporate_action_records(
    records: Sequence[dict[str, Any]],
    apply_statuses: Sequence[str] = ("实施",),
    cash_field: str = "cash_div",
) -> list[CorporateActionEvent]:
    statuses = tuple(str(status) for status in apply_statuses)
    events: list[CorporateActionEvent] = []
    for record in records:
        ts_code = str(record.get("ts_code") or "")
        if not ts_code:
            continue
        status = str(record.get("div_proc") or record.get("raw_status") or "")
        implemented = any(status_value and status_value in status for status_value in statuses)
        cash_div = _float(record.get(cash_field))
        cash_tax = _float(record.get("cash_div_tax"))
        stock_bonus = _float(record.get("stk_div") if record.get("stk_div") not in {None, ""} else record.get("stk_bo_rate"))
        stock_transfer = _float(record.get("stk_co_rate"))
        stock_ratio = max(0.0, stock_bonus) + max(0.0, stock_transfer)
        action_type = _action_type(implemented, cash_div, stock_bonus, stock_transfer)
        ann_date = _date(record.get("ann_date"))
        imp_ann_date = _date(record.get("imp_ann_date"))
        ex_date = _date(record.get("ex_date"))
        event = CorporateActionEvent(
            action_id=_action_id(record),
            ts_code=ts_code,
            action_type=action_type.value,
            status=status or "unknown",
            end_date=_date(record.get("end_date")),
            ann_date=ann_date,
            imp_ann_date=imp_ann_date,
            record_date=_date(record.get("record_date")),
            ex_date=ex_date,
            pay_date=_date(record.get("pay_date")),
            div_listdate=_date(record.get("div_listdate")),
            cash_div_per_share=max(0.0, cash_div),
            cash_div_tax_per_share=max(0.0, cash_tax),
            stock_bonus_ratio=max(0.0, stock_bonus),
            stock_transfer_ratio=max(0.0, stock_transfer),
            stock_distribution_ratio=stock_ratio,
            availability_date=imp_ann_date or ann_date,
            effective_date=ex_date,
            source_record=dict(record),
            unit_assumption="per_share_from_provider",
            metadata={
                "implemented": implemented,
                "cash_field": cash_field,
                "base_date": _date(record.get("base_date")),
                "base_share": _float(record.get("base_share")),
                "source": record.get("source"),
            },
        )
        events.append(event)
    return sorted(events, key=lambda event: (event.ts_code, event.effective_date or "", event.action_id))


def extract_cninfo_document_text(
    body: bytes,
    document_format: str,
    *,
    max_document_bytes: int = _MAX_DOCUMENT_BYTES,
    max_text_chars: int = _MAX_TEXT_CHARS,
    pdf_timeout_seconds: float = 30.0,
    pdftotext_binary: str | Path = "/usr/bin/pdftotext",
) -> str:
    """Extract bounded text without accepting or rewriting source bytes."""

    if not isinstance(body, bytes) or not body:
        raise ValueError("cninfo_document_body_invalid")
    if (
        type(max_document_bytes) is not int
        or max_document_bytes <= 0
        or max_document_bytes > _MAX_DOCUMENT_BYTES
        or len(body) > max_document_bytes
    ):
        raise ValueError("cninfo_document_body_limit_exceeded")
    if (
        type(max_text_chars) is not int
        or max_text_chars <= 0
        or max_text_chars > _MAX_TEXT_CHARS
    ):
        raise ValueError("cninfo_document_text_limit_invalid")
    if (
        not isinstance(pdf_timeout_seconds, (int, float))
        or isinstance(pdf_timeout_seconds, bool)
        or pdf_timeout_seconds <= 0
        or pdf_timeout_seconds > 30.0
    ):
        raise ValueError("cninfo_pdf_text_extractor_invalid")
    normalized_format = str(document_format).strip().lower()
    if normalized_format == "html":
        text = _decode_cninfo_html(body)
        parser = _BoundedHTMLTextParser(max_text_chars)
        try:
            parser.feed(text)
            parser.close()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("cninfo_html_text_extraction_failed") from exc
        extracted = parser.text()
    elif normalized_format == "pdf":
        binary = Path(pdftotext_binary)
        if (
            not binary.is_absolute()
            or not binary.is_file()
            or binary.is_symlink()
            or pdf_timeout_seconds <= 0
        ):
            raise ValueError("cninfo_pdf_text_extractor_invalid")
        with tempfile.TemporaryDirectory(
            prefix="auto-alpha-cninfo-pdf-text."
        ) as temporary:
            input_path = Path(temporary) / "document.pdf"
            output_path = Path(temporary) / "document.txt"
            stderr_path = Path(temporary) / "pdftotext.stderr"
            input_path.write_bytes(body)
            try:
                with stderr_path.open("w+b") as stderr_file:
                    completed = subprocess.run(
                        [
                            str(binary),
                            "-layout",
                            "-nopgbrk",
                            str(input_path),
                            str(output_path),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=stderr_file,
                        timeout=pdf_timeout_seconds,
                        check=False,
                        env=dict(_PDF_EXTRACTOR_ENV),
                        preexec_fn=_pdf_extractor_resource_limits(
                            max_output_bytes=max_text_chars * 4,
                            cpu_seconds=max(
                                1, int(pdf_timeout_seconds) + 1
                            ),
                        ),
                    )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("cninfo_pdf_text_extraction_timeout") from exc
            if stderr_path.stat().st_size > max_text_chars * 4:
                raise ValueError("cninfo_pdf_text_extraction_output_limit")
            if completed.returncode != 0 or not output_path.is_file():
                raise ValueError("cninfo_pdf_text_extraction_failed")
            if output_path.stat().st_size > max_text_chars * 4:
                raise ValueError("cninfo_document_text_limit_exceeded")
            extracted = _normalize_text(
                output_path.read_text(encoding="utf-8", errors="replace")
            )
        if len(extracted) > max_text_chars:
            raise ValueError("cninfo_document_text_limit_exceeded")
    else:
        raise ValueError("cninfo_document_format_unsupported")
    if not extracted:
        raise ValueError("cninfo_document_text_empty")
    return extracted


def build_cninfo_text_extractor_contract(
    pdftotext_binary: str | Path = "/usr/bin/pdftotext",
) -> dict[str, Any]:
    """Bind the local PDF toolchain used by deterministic semantic replay."""

    binary = Path(pdftotext_binary)
    if not binary.is_absolute() or not binary.is_file() or binary.is_symlink():
        raise ValueError("cninfo_pdf_text_extractor_invalid")
    try:
        returncode, version_bytes = _fixed_tool_output(
            [str(binary), "-v"], timeout_seconds=5
        )
    except (OSError, ValueError) as exc:
        raise ValueError("cninfo_pdf_text_extractor_probe_invalid") from exc
    version_output = version_bytes.decode("utf-8", errors="replace")
    version_line = version_output.splitlines()[0] if version_output else ""
    if returncode != 0 or not version_line.startswith(
        "pdftotext version "
    ):
        raise ValueError("cninfo_pdf_text_extractor_probe_invalid")
    semantic = {
        "schema_version": _CNINFO_TEXT_EXTRACTOR_SCHEMA,
        "extractor_identity": "poppler_pdftotext_and_stdlib_html_v2",
        "supported_formats": ["html", "pdf"],
        "pdf_binary_sha256": sha256_file(binary),
        "pdf_binary_version": version_line,
        "pdf_dynamic_libraries": _pdftotext_dynamic_libraries(binary),
        "pdf_argv": ["-layout", "-nopgbrk", "{input}", "{output}"],
        "fixed_process_environment": dict(_PDF_EXTRACTOR_ENV),
        "max_document_bytes": _MAX_DOCUMENT_BYTES,
        "max_text_chars": _MAX_TEXT_CHARS,
        "pdf_timeout_seconds": 30.0,
        "pdf_process_address_space_bytes": _MAX_PDF_PROCESS_ADDRESS_BYTES,
        "pdf_process_file_bytes": _MAX_PDF_PROCESS_FILE_BYTES,
        "python_runtime": _python_runtime_contract(),
        "parser_implementation_root": _cninfo_parser_implementation_root(),
    }
    content_hash = canonical_hash(semantic)
    return semantic | {
        "content_hash": content_hash,
        "contract_id": "cninfo_text_extractor_" + content_hash[:24],
        "runtime_binary_path": str(binary),
    }


def validate_cninfo_text_extractor_contract(
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify contract content and the exact local binary before extraction."""

    supplied = dict(contract)
    runtime_path = Path(str(supplied.get("runtime_binary_path") or ""))
    try:
        expected = build_cninfo_text_extractor_contract(runtime_path)
    except (OSError, ValueError):
        raise ValueError("cninfo_text_extractor_contract_invalid")
    if supplied != expected:
        raise ValueError("cninfo_text_extractor_contract_invalid")
    return supplied


def parse_cninfo_corporate_action_documents(
    documents: Iterable[Mapping[str, Any]],
    *,
    text_extractor_contract: Mapping[str, Any] | None = None,
    max_documents: int = _MAX_DOCUMENTS_PER_SEMANTIC_SHARD,
    max_text_chars: int = _MAX_TEXT_CHARS,
) -> dict[str, Any]:
    """Build deterministic event-version candidates from verified documents.

    ``security_id`` must already come from the governed identity/lifecycle
    timeline.  ``sec_code`` and ``org_id`` are retained only as diagnostics and
    can never substitute for that stable identity.
    """

    if (
        type(max_documents) is not int
        or max_documents <= 0
        or max_documents > _MAX_DOCUMENTS_PER_SEMANTIC_SHARD
        or type(max_text_chars) is not int
        or max_text_chars <= 0
        or max_text_chars > _MAX_TEXT_CHARS
    ):
        raise ValueError("cninfo_semantic_resource_budget_invalid")
    parser_root = _cninfo_parser_implementation_root()
    extractor_contract = validate_cninfo_text_extractor_contract(
        text_extractor_contract or build_cninfo_text_extractor_contract()
    )
    extractor_semantic = {
        key: value
        for key, value in extractor_contract.items()
        if key != "runtime_binary_path"
    }

    def extractor(body: bytes, document_format: str) -> str:
        return extract_cninfo_document_text(
            body,
            document_format,
            max_text_chars=max_text_chars,
            pdf_timeout_seconds=float(
                extractor_contract["pdf_timeout_seconds"]
            ),
            pdftotext_binary=str(
                extractor_contract["runtime_binary_path"]
            ),
        )
    source_semantics: list[dict[str, Any]] = []
    seen_announcement_ids: set[str] = set()
    document_results: list[dict[str, Any]] = []
    event_versions: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    governance_blockers: list[dict[str, str]] = []

    for source_row in documents:
        if len(source_semantics) >= max_documents:
            raise ValueError("cninfo_semantic_document_budget_exceeded")
        row = dict(source_row)
        announcement_id = str(row.get("announcement_id") or "")
        if announcement_id in seen_announcement_ids:
            raise ValueError("cninfo_duplicate_announcement_id")
        seen_announcement_ids.add(announcement_id)
        source_semantics.append(_cninfo_source_semantic(row))
        result, event, row_blockers, row_governance = _parse_cninfo_document(
            row,
            extractor=extractor,
            duplicate_announcement_id=False,
            text_extraction_replay_verified=True,
            max_text_chars=max_text_chars,
        )
        document_results.append(result)
        blockers.extend(row_blockers)
        governance_blockers.extend(row_governance)
        if event is not None:
            event_versions.append(event)

    event_versions, event_chains = _link_cninfo_event_versions(event_versions)
    final_ids = {
        str(row["source_announcement_id"]): str(row["event_version_id"])
        for row in event_versions
    }
    for result in document_results:
        announcement_id = str(result["announcement_id"])
        if announcement_id in final_ids:
            result["event_version_id"] = final_ids[announcement_id]
    source_semantics.sort(
        key=lambda row: (row["announcement_id"], row["document_sha256"])
    )
    document_results.sort(
        key=lambda row: (row["announcement_id"], row["document_sha256"])
    )
    blockers = sorted(
        blockers,
        key=lambda row: (row["announcement_id"], row["code"]),
    )
    governance_blockers = sorted(
        governance_blockers,
        key=lambda row: (row["announcement_id"], row["code"]),
    )
    payload: dict[str, Any] = {
        "schema_version": "cninfo_corporate_action_semantic_replay_v1",
        "parser_identity": CNINFO_CORPORATE_ACTION_PARSER_IDENTITY,
        "parser_implementation_root": parser_root,
        "text_extractor_contract": extractor_semantic,
        "text_extractor_identity": extractor_contract["extractor_identity"],
        "text_extractor_implementation_root": extractor_contract[
            "content_hash"
        ],
        "resource_budget": {
            "max_documents": max_documents,
            "max_text_chars_per_document": max_text_chars,
            "max_document_bytes": _MAX_DOCUMENT_BYTES,
        },
        "source_document_count": len(source_semantics),
        "source_document_root": canonical_hash(source_semantics),
        "parsed_document_count": sum(
            row["status"] == "parsed" for row in document_results
        ),
        "blocked_document_count": sum(
            row["status"] == "blocked" for row in document_results
        ),
        "out_of_scope_document_count": sum(
            row["status"] == "out_of_scope" for row in document_results
        ),
        "event_version_count": len(event_versions),
        "event_chain_count": len(event_chains),
        "technical_replay_complete": not blockers,
        "validation_boundary": _CNINFO_VALIDATION_BOUNDARY,
        "source_document_bodies_archived": False,
        "source_documents": source_semantics,
        "event_versions": event_versions,
        "event_chains": event_chains,
        "document_results": document_results,
        "blockers": blockers,
        "governance_blockers": governance_blockers,
        "data_admission_eligible": False,
        "independent_admission_verdict_required": True,
        "safety": {
            "profile_activation_authorized": False,
            "alpha_search_authorized": False,
            "holdout_activation_authorized": False,
            "paper_trading_authorized": False,
            "shadow_trading_authorized": False,
            "live_trading_authorized": False,
        },
    }
    payload["content_hash"] = canonical_hash(payload)
    payload["generation_id"] = (
        "cninfo_corporate_action_semantics_" + payload["content_hash"][:24]
    )
    return payload


def bind_cninfo_documents_to_security_identity_intervals(
    documents: Iterable[Mapping[str, Any]],
    identity_interval_evidence: str | Path,
) -> Iterable[dict[str, Any]]:
    """Project parser inputs to one stable identity without granting PIT.

    The identity owner validates the immutable interval generation first.  A
    CNINFO code is matched on the announcement date, never against a current
    security master.  Missing, conflicting, or caller-supplied identities are
    retained as explicit blockers and the parser will subsequently fail
    closed for that document.
    """

    from auto_alpha.data.pit.engine.security_master import (
        validate_security_identity_lifecycle_intervals,
    )

    evidence = validate_security_identity_lifecycle_intervals(
        identity_interval_evidence
    )
    intervals_by_code: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for interval in evidence.get("intervals") or ():
        if not isinstance(interval, Mapping):
            raise ValueError("cninfo_identity_interval_invalid")
        security_code = str(interval.get("security_code") or "")
        base_code = security_code.partition(".")[0]
        if (
            not re.fullmatch(r"[0-9]{6}", base_code)
            or interval.get("identity_resolved") is not True
            or not str(interval.get("security_id") or "")
        ):
            raise ValueError("cninfo_identity_interval_invalid")
        intervals_by_code[base_code].append(interval)

    for source in documents:
        row = dict(source)
        known = _announcement_known_at(
            row.get("announcement_time"),
            precision_proven=bool(
                row.get("announcement_time_precision_proven") is True
            ),
        )
        announcement_date = known["date"] if known is not None else None
        raw_code = str(row.get("sec_code") or "").strip()
        base_code = raw_code.partition(".")[0]
        matches = [
            interval
            for interval in intervals_by_code.get(base_code, ())
            if announcement_date is not None
            and str(interval.get("trade_date_start") or "")
            <= announcement_date
            <= str(interval.get("trade_date_end") or "")
        ]
        stable_ids = {
            str(interval.get("security_id") or "") for interval in matches
        }
        blockers: set[str] = set()
        existing_security_id = str(row.get("security_id") or "")
        if known is None:
            blockers.add("identity_projection_announcement_time_invalid")
        if not re.fullmatch(r"[0-9]{6}", base_code):
            blockers.add("identity_projection_security_code_invalid")
        if len(stable_ids) != 1:
            blockers.add(
                "identity_projection_unresolved"
                if not stable_ids
                else "identity_projection_conflict"
            )
            projected_security_id = ""
        else:
            projected_security_id = next(iter(stable_ids))
        if (
            existing_security_id
            and projected_security_id
            and existing_security_id != projected_security_id
        ):
            blockers.add("identity_projection_existing_identity_conflict")
            projected_security_id = ""
        row["security_id"] = projected_security_id
        row["identity_projection_complete"] = not blockers
        row["identity_projection_blockers"] = sorted(blockers)
        row["identity_timeline_content_hash"] = str(
            evidence.get("content_hash") or ""
        )
        row["identity_timeline_intervals_root"] = str(
            evidence.get("intervals_root") or ""
        )
        row["identity_timeline_independent_admission_pending"] = True
        row["source_governed_evidence_eligible"] = False
        yield row


def _current_cninfo_extractor_semantic() -> dict[str, Any]:
    contract = build_cninfo_text_extractor_contract()
    return {
        key: value
        for key, value in contract.items()
        if key != "runtime_binary_path"
    }


def _cninfo_event_version_semantics_valid(row: Mapping[str, Any]) -> bool:
    if set(row) != _CNINFO_EVENT_VERSION_FIELDS:
        return False
    stage = row.get("stage")
    if stage not in _STAGE_ORDER:
        return False
    security_id = str(row.get("security_id") or "")
    fiscal_period_end = str(row.get("fiscal_period_end") or "")
    event_id = str(row.get("event_id") or "")
    if (
        not security_id
        or re.fullmatch(r"(?:19|20)\d{6}", fiscal_period_end) is None
        or event_id
        != "cae_"
        + canonical_hash(
            {
                "security_id": security_id,
                "fiscal_period_end": fiscal_period_end,
            }
        )[:24]
        or re.fullmatch(r"caev_[0-9a-f]{24}", str(row.get("event_version_id") or ""))
        is None
    ):
        return False
    for field in (
        "source_document_sha256",
        "source_text_sha256",
        "source_lineage_root",
        "source_document_closure_root",
    ):
        if _HEX_64.fullmatch(str(row.get(field) or "")) is None:
            return False
    if (
        not str(row.get("source_announcement_id") or "").isdigit()
        or not str(row.get("source_title") or "")
        or row.get("ts_code") is not None
        or row.get("identity_timeline_projection_required") is not True
        or row.get("source_governed_evidence_eligible") is not False
        or row.get("text_extraction_replay_verified") is not True
        or row.get("semantic_candidate_eligible") is not False
        or row.get("pit_evidence_eligible") is not False
        or row.get("independent_event_coverage_verdict_required") is not True
    ):
        return False
    boolean_fields = (
        "publication_time_precision_proven",
        "economic_terms_complete",
        "adjustment_semantic_complete",
        "event_ledger_semantic_complete",
        "parser_semantic_complete",
    )
    if any(type(row.get(field)) is not bool for field in boolean_fields):
        return False
    known_at = str(row.get("known_at") or "")
    known_at_utc = str(row.get("known_at_utc") or "")
    known_timing = str(row.get("known_timing") or "")
    try:
        known_instant = datetime.fromisoformat(
            known_at_utc.replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if (
        known_instant.tzinfo is None
        or known_instant.astimezone(_SHANGHAI).strftime("%Y%m%d")
        != known_at
        or known_timing not in {"before_open", "intraday", "after_close"}
        or (
            row.get("publication_time_precision_proven") is False
            and known_timing != "after_close"
        )
    ):
        return False
    dates: dict[str, str | None] = {}
    for field in ("effective_at", "record_date", "pay_date", "div_listdate"):
        value = row.get(field)
        if value is not None and re.fullmatch(r"(?:19|20)\d{6}", str(value)) is None:
            return False
        dates[field] = str(value) if value is not None else None
    terms: dict[str, Decimal | None] = {}
    for field in (
        "cash_div_per_share",
        "stock_bonus_ratio",
        "stock_transfer_ratio",
        "stock_distribution_ratio",
    ):
        value = row.get(field)
        try:
            parsed = Decimal(str(value)) if value is not None else None
        except InvalidOperation:
            return False
        if parsed is not None and (not parsed.is_finite() or parsed < 0):
            return False
        terms[field] = parsed
    cash = terms["cash_div_per_share"]
    bonus = terms["stock_bonus_ratio"]
    transfer = terms["stock_transfer_ratio"]
    distribution = terms["stock_distribution_ratio"]
    terms_complete = all(value is not None for value in (cash, bonus, transfer))
    expected_distribution = (
        bonus + transfer
        if bonus is not None and transfer is not None
        else None
    )
    adjustment_complete = bool(
        terms_complete
        and dates["effective_at"] is not None
        and stage in {"implementation", "correction", "withdrawal"}
    )
    record_order = bool(
        dates["record_date"] is not None
        and dates["effective_at"] is not None
        and dates["record_date"] < dates["effective_at"]
    )
    pay_order = bool(
        dates["pay_date"] is None
        or (
            dates["effective_at"] is not None
            and dates["effective_at"] <= dates["pay_date"]
        )
    )
    listing_order = bool(
        dates["div_listdate"] is None
        or (
            dates["effective_at"] is not None
            and dates["effective_at"] <= dates["div_listdate"]
        )
    )
    ledger_complete = bool(
        adjustment_complete
        and record_order
        and pay_order
        and listing_order
        and cash is not None
        and (cash <= 0 or dates["pay_date"] is not None)
        and expected_distribution is not None
        and (
            expected_distribution <= 0
            or dates["div_listdate"] is not None
        )
    )
    known_after_effective = _not_known_before_effective(
        known_at=known_at,
        known_timing=known_timing,
        effective_at=dates["effective_at"],
    )
    parser_complete = bool(
        stage in {"implementation", "correction"}
        and dates["effective_at"]
        and not known_after_effective
        and terms_complete
        and adjustment_complete
        and ledger_complete
    )
    return bool(
        row.get("economic_terms_complete") is terms_complete
        and distribution == expected_distribution
        and row.get("adjustment_semantic_complete") is adjustment_complete
        and row.get("event_ledger_semantic_complete") is ledger_complete
        and row.get("parser_semantic_complete") is parser_complete
    )


def _cninfo_source_documents_valid(
    rows: Sequence[Mapping[str, Any]],
    document_results: Sequence[Mapping[str, Any]],
    event_versions: Sequence[Mapping[str, Any]],
) -> bool:
    if any(set(row) != _CNINFO_SOURCE_DOCUMENT_FIELDS for row in rows):
        return False
    if list(rows) != sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row["announcement_id"]),
            str(row["document_sha256"]),
        ),
    ):
        return False
    source_by_announcement: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        announcement_id = str(row.get("announcement_id") or "")
        roles = row.get("source_scope_roles")
        if (
            announcement_id in source_by_announcement
            or type(row.get("announcement_time_precision_proven")) is not bool
            or type(row.get("document_size_bytes")) is not int
            or not isinstance(roles, list)
            or roles != sorted(roles)
            or any(not isinstance(role, str) for role in roles)
            or row.get("document_body_replay_verified") is not True
            or row.get("source_governed_evidence_eligible") is not False
        ):
            return False
        source_by_announcement[announcement_id] = row
    if len(document_results) != len(rows):
        return False
    for result in document_results:
        source = source_by_announcement.get(
            str(result.get("announcement_id") or "")
        )
        if (
            source is None
            or result.get("document_sha256")
            != source.get("document_sha256")
        ):
            return False
    for event in event_versions:
        source = source_by_announcement.get(
            str(event.get("source_announcement_id") or "")
        )
        if source is None:
            return False
        expected_lineage_root = canonical_hash(
            {
                key: value
                for key, value in source.items()
                if key.startswith("source_")
            }
        )
        if (
            event.get("security_id") != source.get("security_id")
            or event.get("source_document_sha256")
            != source.get("document_sha256")
            or event.get("source_title")
            != _normalize_text(str(source.get("announcement_title") or ""))
            or event.get("source_sec_code") != source.get("sec_code")
            or event.get("source_org_id") != source.get("org_id")
            or event.get("source_adjunct_url") != source.get("adjunct_url")
            or event.get("source_document_closure_root")
            != source.get("source_document_closure_root")
            or event.get("source_lineage_root") != expected_lineage_root
        ):
            return False
    return True


def _cninfo_document_results_valid(
    rows: Sequence[Mapping[str, Any]],
    event_versions: Sequence[Mapping[str, Any]],
) -> bool:
    event_by_announcement: dict[str, Mapping[str, Any]] = {}
    for event in event_versions:
        announcement_id = str(event.get("source_announcement_id") or "")
        if announcement_id in event_by_announcement:
            return False
        event_by_announcement[announcement_id] = event
    seen_announcements: set[str] = set()
    for row in rows:
        base_fields = {
            "announcement_id",
            "document_sha256",
            "status",
            "blocker_codes",
        }
        announcement_id = str(row.get("announcement_id") or "")
        status = row.get("status")
        blocker_codes = row.get("blocker_codes")
        if (
            announcement_id in seen_announcements
            or not isinstance(row.get("announcement_id"), str)
            or not isinstance(row.get("document_sha256"), str)
            or status not in {"parsed", "blocked", "out_of_scope"}
            or not isinstance(blocker_codes, list)
            or any(not isinstance(code, str) or not code for code in blocker_codes)
            or len(set(blocker_codes)) != len(blocker_codes)
        ):
            return False
        seen_announcements.add(announcement_id)
        event = event_by_announcement.get(announcement_id)
        event_fields = {
            "stage",
            "event_id",
            "event_version_id",
            "text_sha256",
            "text_char_count",
        }
        if event is not None:
            if (
                set(row) != base_fields | event_fields
                or status not in {"parsed", "blocked"}
                or row.get("stage") != event.get("stage")
                or row.get("event_id") != event.get("event_id")
                or row.get("event_version_id") != event.get("event_version_id")
                or row.get("text_sha256") != event.get("source_text_sha256")
                or row.get("document_sha256")
                != event.get("source_document_sha256")
                or type(row.get("text_char_count")) is not int
                or int(row.get("text_char_count")) <= 0
            ):
                return False
        elif status == "out_of_scope":
            roles = row.get("source_scope_roles")
            if (
                set(row) != base_fields | {"source_scope_roles"}
                or blocker_codes
                or not isinstance(roles, list)
                or roles != sorted(set(roles))
                or any(not isinstance(role, str) or not role for role in roles)
            ):
                return False
        elif set(row) != base_fields or status != "blocked":
            return False
        if status == "parsed" and blocker_codes:
            return False
        if status == "blocked" and not blocker_codes:
            return False
        if status == "out_of_scope" and blocker_codes:
            return False
    return bool(
        list(rows)
        == sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                str(row["announcement_id"]),
                str(row["document_sha256"]),
            ),
        )
        and set(event_by_announcement).issubset(seen_announcements)
    )


def _validate_cninfo_derivation_semantic(
    semantic: Mapping[str, Any],
) -> None:
    expected_fields = _CNINFO_DERIVATION_SCALAR_FIELDS | frozenset(
        _CNINFO_EVIDENCE_FILES
    )
    if set(semantic) != expected_fields:
        raise ValueError("cninfo_corporate_action_semantics_invalid")
    try:
        current_extractor = _current_cninfo_extractor_semantic()
    except (OSError, ValueError):
        raise ValueError("cninfo_corporate_action_semantics_invalid")
    resource_budget = semantic.get("resource_budget")
    safety = semantic.get("safety")
    if (
        semantic.get("schema_version")
        != "cninfo_corporate_action_semantic_replay_v1"
        or semantic.get("parser_identity")
        != CNINFO_CORPORATE_ACTION_PARSER_IDENTITY
        or semantic.get("parser_implementation_root")
        != _cninfo_parser_implementation_root()
        or semantic.get("text_extractor_contract") != current_extractor
        or semantic.get("text_extractor_identity")
        != current_extractor["extractor_identity"]
        or semantic.get("text_extractor_implementation_root")
        != current_extractor["content_hash"]
        or semantic.get("validation_boundary")
        != _CNINFO_VALIDATION_BOUNDARY
        or semantic.get("source_document_bodies_archived") is not False
        or semantic.get("data_admission_eligible") is not False
        or semantic.get("independent_admission_verdict_required") is not True
        or not isinstance(resource_budget, Mapping)
        or set(resource_budget)
        != {
            "max_documents",
            "max_text_chars_per_document",
            "max_document_bytes",
        }
        or type(resource_budget.get("max_documents")) is not int
        or not 0
        < int(resource_budget["max_documents"])
        <= _MAX_DOCUMENTS_PER_SEMANTIC_SHARD
        or type(resource_budget.get("max_text_chars_per_document")) is not int
        or not 0
        < int(resource_budget["max_text_chars_per_document"])
        <= _MAX_TEXT_CHARS
        or resource_budget.get("max_document_bytes") != _MAX_DOCUMENT_BYTES
        or not isinstance(safety, Mapping)
        or set(safety) != _CNINFO_SAFETY_FLAGS
        or any(value is not False for value in safety.values())
        or _HEX_64.fullmatch(str(semantic.get("source_document_root") or ""))
        is None
    ):
        raise ValueError("cninfo_corporate_action_semantics_invalid")
    rows_by_role: dict[str, list[dict[str, Any]]] = {}
    for role in _CNINFO_EVIDENCE_FILES:
        rows = semantic.get(role)
        if not isinstance(rows, list) or any(
            not isinstance(row, Mapping) for row in rows
        ):
            raise ValueError("cninfo_corporate_action_semantics_invalid")
        rows_by_role[role] = [dict(row) for row in rows]
    event_versions = rows_by_role["event_versions"]
    event_chains = rows_by_role["event_chains"]
    document_results = rows_by_role["document_results"]
    source_documents = rows_by_role["source_documents"]
    blockers = rows_by_role["blockers"]
    governance_blockers = rows_by_role["governance_blockers"]
    if (
        any(not _cninfo_event_version_semantics_valid(row) for row in event_versions)
        or any(set(row) != _CNINFO_EVENT_CHAIN_FIELDS for row in event_chains)
        or not _cninfo_document_results_valid(document_results, event_versions)
        or not _cninfo_source_documents_valid(
            source_documents,
            document_results,
            event_versions,
        )
        or any(set(row) != {"announcement_id", "code"} for row in blockers)
        or any(
            set(row) != {"announcement_id", "code"}
            for row in governance_blockers
        )
    ):
        raise ValueError("cninfo_corporate_action_semantics_invalid")
    unlinked = [
        dict(row) | {"supersedes_event_version_id": None}
        for row in event_versions
    ]
    expected_versions, expected_chains = _link_cninfo_event_versions(unlinked)
    expected_blockers = sorted(
        (
            {
                "announcement_id": str(result["announcement_id"]),
                "code": str(code),
            }
            for result in document_results
            for code in result["blocker_codes"]
        ),
        key=lambda row: (row["announcement_id"], row["code"]),
    )
    expected_governance = sorted(
        (
            {
                "announcement_id": str(result["announcement_id"]),
                "code": "independent_source_admission_pending",
            }
            for result in document_results
            if result["status"] != "out_of_scope"
        ),
        key=lambda row: (row["announcement_id"], row["code"]),
    )
    status_counts = {
        status: sum(result["status"] == status for result in document_results)
        for status in ("parsed", "blocked", "out_of_scope")
    }
    exact_int_counts = {
        "source_document_count": len(document_results),
        "parsed_document_count": status_counts["parsed"],
        "blocked_document_count": status_counts["blocked"],
        "out_of_scope_document_count": status_counts["out_of_scope"],
        "event_version_count": len(event_versions),
        "event_chain_count": len(event_chains),
    }
    if (
        any(type(semantic.get(key)) is not int for key in exact_int_counts)
        or any(semantic.get(key) != value for key, value in exact_int_counts.items())
        or len(document_results) > int(resource_budget["max_documents"])
        or semantic.get("source_document_root")
        != canonical_hash(source_documents)
        or event_versions != expected_versions
        or event_chains != expected_chains
        or blockers != expected_blockers
        or governance_blockers != expected_governance
        or type(semantic.get("technical_replay_complete")) is not bool
        or semantic.get("technical_replay_complete") is not (not blockers)
    ):
        raise ValueError("cninfo_corporate_action_semantics_invalid")


def publish_cninfo_corporate_action_semantics(
    evidence: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    """Archive one parser result as an immutable, replayable generation."""

    derivation = dict(evidence)
    derivation_hash = str(derivation.pop("content_hash", ""))
    derivation_generation_id = str(derivation.pop("generation_id", ""))
    if (
        set(evidence)
        != _CNINFO_DERIVATION_SCALAR_FIELDS
        | frozenset(_CNINFO_EVIDENCE_FILES)
        | {"content_hash", "generation_id"}
        or _HEX_64.fullmatch(derivation_hash) is None
        or canonical_hash(derivation) != derivation_hash
        or derivation_generation_id
        != "cninfo_corporate_action_semantics_" + derivation_hash[:24]
    ):
        raise ValueError("cninfo_corporate_action_semantics_invalid")
    _validate_cninfo_derivation_semantic(derivation)
    artifacts: dict[str, bytes] = {}
    artifact_contract: dict[str, dict[str, Any]] = {}
    for role, filename in _CNINFO_EVIDENCE_FILES.items():
        rows = evidence.get(role)
        if not isinstance(rows, list) or any(
            not isinstance(row, Mapping) for row in rows
        ):
            raise ValueError(
                f"cninfo_corporate_action_semantics_{role}_invalid"
            )
        payload = _semantic_jsonl_bytes(rows)
        artifacts[filename] = payload
        artifact_contract[role] = {
            "relative_path": filename,
            "row_count": len(rows),
            "rows_root": canonical_hash(rows),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    semantic = {
        key: value
        for key, value in derivation.items()
        if key not in _CNINFO_EVIDENCE_FILES
    } | {
        "schema_version": _CNINFO_EVIDENCE_SCHEMA,
        "derivation_schema_version": derivation["schema_version"],
        "derivation_content_hash": derivation_hash,
        "artifacts": artifact_contract,
        "blocker_code_counts": _issue_code_counts(
            evidence.get("blockers") or []
        ),
        "governance_blocker_code_counts": _issue_code_counts(
            evidence.get("governance_blockers") or []
        ),
    }
    return publish_generation(
        output_root,
        prefix=_CNINFO_EVIDENCE_PREFIX,
        manifest_name=_CNINFO_EVIDENCE_MANIFEST,
        semantic=semantic,
        extra_files=artifacts,
    )


def validate_cninfo_corporate_action_semantics(
    path: str | Path,
) -> dict[str, Any]:
    """Replay archived semantics without claiming source-body extraction.

    Source document bodies deliberately are not archived in this generation.
    Validation therefore proves canonical artifact bytes, current parser and
    extractor identities, and all internal semantic relations; it does not
    independently re-extract CNINFO source bodies or grant Data Admission.
    """

    manifest = validate_generation(
        path,
        schema=_CNINFO_EVIDENCE_SCHEMA,
        manifest_name=_CNINFO_EVIDENCE_MANIFEST,
    )
    root = Path(str(manifest["manifest_path"])).parent
    expected_files = {
        _CNINFO_EVIDENCE_MANIFEST,
        *_CNINFO_EVIDENCE_FILES.values(),
    }
    observed_files = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    safety = manifest.get("safety") or {}
    expected_manifest_fields = (
        (_CNINFO_DERIVATION_SCALAR_FIELDS - {"schema_version"})
        | {
            "schema_version",
            "derivation_schema_version",
            "derivation_content_hash",
            "artifacts",
            "blocker_code_counts",
            "governance_blocker_code_counts",
            "content_hash",
            "generation_id",
            "manifest_path",
        }
    )
    invalid = bool(
        set(manifest) != expected_manifest_fields
        or observed_files != expected_files
        or any(item.is_symlink() for item in root.rglob("*"))
        or manifest.get("data_admission_eligible") is not False
        or manifest.get("independent_admission_verdict_required") is not True
        or set(safety) != _CNINFO_SAFETY_FLAGS
        or any(value is not False for value in safety.values())
    )
    artifact_contract = manifest.get("artifacts") or {}
    rows_by_role: dict[str, list[dict[str, Any]]] = {}
    for role, filename in _CNINFO_EVIDENCE_FILES.items():
        contract = artifact_contract.get(role) or {}
        file_path = root / filename
        file_valid = file_path.is_file() and not file_path.is_symlink()
        try:
            rows = _semantic_jsonl_rows(file_path) if file_valid else []
        except (OSError, ValueError, json.JSONDecodeError):
            invalid = True
            rows = []
        rows_by_role[role] = rows
        canonical_bytes = _semantic_jsonl_bytes(rows)
        invalid = bool(
            invalid
            or not file_valid
            or (file_path.read_bytes() if file_valid else b"")
            != canonical_bytes
            or contract.get("relative_path") != filename
            or set(contract)
            != {
                "relative_path",
                "row_count",
                "rows_root",
                "sha256",
                "size_bytes",
            }
            or contract.get("row_count") != len(rows)
            or contract.get("rows_root") != canonical_hash(rows)
            or contract.get("sha256")
            != (sha256_file(file_path) if file_valid else None)
            or contract.get("size_bytes")
            != (file_path.stat().st_size if file_valid else None)
        )
    invalid = bool(
        invalid
        or manifest.get("event_version_count")
        != len(rows_by_role["event_versions"])
        or manifest.get("event_chain_count")
        != len(rows_by_role["event_chains"])
        or manifest.get("source_document_count")
        != len(rows_by_role["document_results"])
        or manifest.get("source_document_count")
        != len(rows_by_role["source_documents"])
        or manifest.get("blocker_code_counts")
        != _issue_code_counts(rows_by_role["blockers"])
        or manifest.get("governance_blocker_code_counts")
        != _issue_code_counts(rows_by_role["governance_blockers"])
    )
    reconstructed = {
        key: value
        for key, value in manifest.items()
        if key
        not in {
            "artifacts",
            "blocker_code_counts",
            "content_hash",
            "derivation_content_hash",
            "derivation_schema_version",
            "generation_id",
            "governance_blocker_code_counts",
            "manifest_path",
        }
    }
    reconstructed["schema_version"] = manifest.get(
        "derivation_schema_version"
    )
    reconstructed.update(rows_by_role)
    try:
        _validate_cninfo_derivation_semantic(reconstructed)
    except (OSError, ValueError):
        invalid = True
    invalid = bool(
        invalid
        or _HEX_64.fullmatch(
            str(manifest.get("derivation_content_hash") or "")
        )
        is None
        or canonical_hash(reconstructed)
        != manifest.get("derivation_content_hash")
    )
    if invalid:
        raise ValueError("cninfo_corporate_action_semantic_evidence_invalid")
    return manifest


def project_cninfo_corporate_action_event_versions(
    event_versions: Sequence[Mapping[str, Any]],
    identity_timeline_intervals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join semantic events to a PIT identity axis without granting admission."""

    intervals_by_security: dict[str, list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for row in identity_timeline_intervals:
        intervals_by_security[str(row.get("security_id") or "")].append(row)
    projected: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    seen_versions: set[str] = set()
    for source in sorted(
        (dict(row) for row in event_versions),
        key=lambda row: (
            str(row.get("event_id") or ""),
            str(row.get("known_at_utc") or ""),
            str(row.get("event_version_id") or ""),
        ),
    ):
        version_id = str(source.get("event_version_id") or "")
        if not version_id or version_id in seen_versions:
            raise ValueError("cninfo_corporate_action_event_version_invalid")
        seen_versions.add(version_id)
        if source.get("stage") not in {
            "implementation",
            "correction",
            "withdrawal",
        }:
            continue
        effective_at = str(source.get("effective_at") or "")
        matching_intervals = [
            row
            for row in intervals_by_security.get(
                str(source.get("security_id") or ""), []
            )
            if str(row.get("trade_date_start") or "")
            <= effective_at
            <= str(row.get("trade_date_end") or "")
        ]
        identity = matching_intervals[0] if len(matching_intervals) == 1 else None
        code = str((identity or {}).get("security_code") or "")
        identity_valid = bool(
            identity is not None
            and identity.get("identity_resolved") is True
            and identity.get("identity_unique") is True
            and identity.get("active_on_trade_date") is True
            and re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", code)
        )
        terms_complete = bool(source.get("economic_terms_complete") is True)
        source_semantic_eligible = bool(
            source.get("semantic_candidate_eligible") is True
        )
        if not identity_valid:
            blockers.append(
                {
                    "event_version_id": version_id,
                    "code": "corporate_action_identity_projection_unresolved",
                }
            )
        if not terms_complete:
            blockers.append(
                {
                    "event_version_id": version_id,
                    "code": "corporate_action_economic_terms_incomplete",
                }
            )
        if not source_semantic_eligible:
            blockers.append(
                {
                    "event_version_id": version_id,
                    "code": "corporate_action_source_semantic_candidate_ineligible",
                }
            )
        projected.append(
            {
                "event_id": source.get("event_id"),
                "event_version_id": version_id,
                "supersedes_event_version_id": source.get(
                    "supersedes_event_version_id"
                ),
                "security_id": source.get("security_id"),
                "ts_code": code if identity_valid else None,
                "stage": source.get("stage"),
                "fiscal_period_end": source.get("fiscal_period_end"),
                "known_at": source.get("known_at"),
                "known_timing": source.get("known_timing"),
                "effective_at": source.get("effective_at"),
                "record_date": source.get("record_date"),
                "pay_date": source.get("pay_date"),
                "div_listdate": source.get("div_listdate"),
                "cash_div_per_share": source.get("cash_div_per_share"),
                "stock_bonus_ratio": source.get("stock_bonus_ratio"),
                "stock_transfer_ratio": source.get("stock_transfer_ratio"),
                "stock_distribution_ratio": source.get(
                    "stock_distribution_ratio"
                ),
                "source_document_sha256": source.get(
                    "source_document_sha256"
                ),
                "source_lineage_root": source.get("source_lineage_root"),
                "source_governed_evidence_eligible": source.get(
                    "source_governed_evidence_eligible"
                ),
                "text_extraction_replay_verified": source.get(
                    "text_extraction_replay_verified"
                ),
                "source_semantic_candidate_eligible": (
                    source_semantic_eligible
                ),
                "downstream_semantic_complete": bool(
                    identity_valid
                    and terms_complete
                    and source_semantic_eligible
                ),
                "pit_evidence_eligible": False,
                "independent_admission_verdict_required": True,
            }
        )
    blockers.sort(
        key=lambda row: (row["event_version_id"], row["code"])
    )
    semantic = {
        "schema_version": "cninfo_corporate_action_pit_projection_v1",
        "event_versions_input_root": canonical_hash(
            [dict(row) for row in event_versions]
        ),
        "identity_timeline_intervals_input_root": canonical_hash(
            [dict(row) for row in identity_timeline_intervals]
        ),
        "projected_event_count": len(projected),
        "projected_events_root": canonical_hash(projected),
        "blockers": blockers,
        "projection_complete": not blockers,
        "data_admission_eligible": False,
        "independent_admission_verdict_required": True,
    }
    return semantic | {
        "events": projected,
        "content_hash": canonical_hash(semantic),
    }


def _cninfo_source_semantic(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "announcement_id": str(row.get("announcement_id") or ""),
        "announcement_time": row.get("announcement_time"),
        "announcement_time_precision_proven": bool(
            row.get("announcement_time_precision_proven") is True
        ),
        "announcement_title": str(row.get("announcement_title") or ""),
        "security_id": str(row.get("security_id") or ""),
        "sec_code": str(row.get("sec_code") or ""),
        "org_id": str(row.get("org_id") or ""),
        "adjunct_url": str(row.get("adjunct_url") or ""),
        "document_format": str(row.get("document_format") or ""),
        "document_sha256": str(row.get("document_sha256") or ""),
        "document_size_bytes": row.get("document_size_bytes"),
        "source_request_id": str(row.get("source_request_id") or ""),
        "source_request_semantic_hash": str(
            row.get("source_request_semantic_hash") or ""
        ),
        "source_raw_envelope_sha256": str(
            row.get("source_raw_envelope_sha256") or ""
        ),
        "source_raw_payload_sha256": str(
            row.get("source_raw_payload_sha256")
            or row.get("source_payload_sha256")
            or ""
        ),
        "source_inventory_content_hash": str(
            row.get("source_inventory_content_hash") or ""
        ),
        "source_document_closure_root": str(
            row.get("source_document_closure_root") or ""
        ),
        "source_parent_generation_id": str(
            row.get("source_parent_generation_id") or ""
        ),
        "source_parent_content_hash": str(
            row.get("source_parent_content_hash") or ""
        ),
        "source_parent_terminal_signature": str(
            row.get("source_parent_terminal_signature") or ""
        ),
        "source_parent_publication_signature": str(
            row.get("source_parent_publication_signature") or ""
        ),
        "document_record_id": str(row.get("document_record_id") or ""),
        "document_body_replay_verified": bool(
            row.get("document_body_replay_verified") is True
        ),
        "source_scope_roles": sorted(
            str(value) for value in (row.get("source_scope_roles") or ())
        ),
        "source_inventory_scope_root": str(
            row.get("source_inventory_scope_root") or ""
        ),
        "source_governed_evidence_eligible": bool(
            row.get("source_governed_evidence_eligible") is True
        ),
    }


def _semantic_jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(
            dict(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )


def _semantic_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("cninfo_semantic_jsonl_object_required")
        rows.append(value)
    return rows


def _issue_code_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        code = str(row.get("code") or "")
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _cninfo_source_lineage_complete(row: Mapping[str, Any]) -> bool:
    record_semantic = {
        field: row.get(field) for field in _CNINFO_POSTPROCESS_RECORD_FIELDS
    }
    # The immutable postprocess record intentionally leaves identity blank;
    # the governed identity projection adds it only after this record is
    # content-addressed.
    record_semantic["security_id"] = ""
    record_id = str(row.get("document_record_id") or "")
    return bool(
        all(
            field in row
            for field in _CNINFO_POSTPROCESS_RECORD_FIELDS
            if field != "security_id"
        )
        and record_semantic.get("schema_version")
        == "cninfo_document_postprocess_record_v1"
        and _HEX_64.fullmatch(record_id)
        and canonical_hash(record_semantic) == record_id
        and row.get("document_body_replay_verified") is True
        and str(row.get("source_request_id") or "")
        and _HEX_64.fullmatch(
            str(row.get("source_request_semantic_hash") or "")
        )
        and _HEX_64.fullmatch(
            str(row.get("source_raw_envelope_sha256") or "")
        )
        and _HEX_64.fullmatch(
            str(
                row.get("source_raw_payload_sha256")
                or row.get("source_payload_sha256")
                or ""
            )
        )
        and _HEX_64.fullmatch(
            str(row.get("source_inventory_content_hash") or "")
        )
        and _HEX_64.fullmatch(
            str(row.get("source_document_closure_root") or "")
        )
        and str(row.get("source_parent_generation_id") or "")
        and _HEX_64.fullmatch(
            str(row.get("source_parent_content_hash") or "")
        )
        and _valid_receipt_signature(
            row.get("source_parent_terminal_signature")
        )
        and _valid_receipt_signature(
            row.get("source_parent_publication_signature")
        )
        and row.get("data_admission_eligible") is False
        and row.get("pit_evidence_eligible") is False
        and row.get("independent_data_admission_verdict_required") is True
        and row.get("source_governed_evidence_eligible") is False
        and row.get("source_lineage_complete") is True
        and row.get("closure_complete") is True
        and row.get("closure_downstream_eligible") is True
        and row.get("closure_blockers") == []
    )


def _valid_receipt_signature(value: Any) -> bool:
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(decoded) == 256


def _validated_cninfo_source_scope_roles(
    row: Mapping[str, Any],
) -> tuple[str, ...] | None:
    declared_roles = row.get("source_scope_roles")
    source_records = row.get("source_inventory_records")
    declared_root = str(row.get("source_inventory_scope_root") or "")
    if declared_roles is None and source_records is None and not declared_root:
        return None
    if (
        not isinstance(declared_roles, list)
        or any(not isinstance(value, str) or not value for value in declared_roles)
        or declared_roles != sorted(set(declared_roles))
        or not isinstance(source_records, list)
        or not source_records
        or any(not isinstance(value, Mapping) for value in source_records)
        or _HEX_64.fullmatch(declared_root) is None
    ):
        raise ValueError("cninfo_source_inventory_scope_invalid")
    observed_roles: set[str] = set()
    observed_leaves: set[str] = set()
    inventory_hashes: list[str] = []
    for source in source_records:
        leaves = source.get("matched_leaves")
        inventory_hash = str(source.get("inventory_content_hash") or "")
        if (
            not isinstance(leaves, list)
            or not leaves
            or any(not isinstance(value, str) or not value for value in leaves)
            or _HEX_64.fullmatch(inventory_hash) is None
        ):
            raise ValueError("cninfo_source_inventory_scope_invalid")
        observed_leaves.update(leaves)
        inventory_hashes.append(inventory_hash)
        observed_roles.update(
            re.sub(r"_[0-9]{6}(?:_.*)?$", "", leaf) for leaf in leaves
        )
    roles = tuple(sorted(observed_roles))
    semantic = {
        "source_inventory_records": [dict(value) for value in source_records],
        "source_scope_roles": list(roles),
    }
    if (
        list(roles) != declared_roles
        or sorted(observed_leaves) != row.get("matched_leaves")
        or canonical_hash(sorted(inventory_hashes))
        != row.get("source_inventory_content_hash")
        or canonical_hash(semantic) != declared_root
    ):
        raise ValueError("cninfo_source_inventory_scope_invalid")
    return roles


def _parse_cninfo_document(
    row: Mapping[str, Any],
    *,
    extractor: Callable[[bytes, str], str],
    duplicate_announcement_id: bool,
    text_extraction_replay_verified: bool,
    max_text_chars: int,
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    list[dict[str, str]],
    list[dict[str, str]],
]:
    announcement_id = str(row.get("announcement_id") or "").strip()
    base_result: dict[str, Any] = {
        "announcement_id": announcement_id,
        "document_sha256": str(row.get("document_sha256") or ""),
    }
    declared_format = str(row.get("document_format") or "").strip().lower()
    source_lineage_complete = _cninfo_source_lineage_complete(row)
    governance: list[dict[str, str]] = [
        {
            "announcement_id": announcement_id,
            "code": "independent_source_admission_pending",
        }
    ]

    def blocked(code: str) -> tuple[
        dict[str, Any],
        None,
        list[dict[str, str]],
        list[dict[str, str]],
    ]:
        issue = {"announcement_id": announcement_id, "code": code}
        return (
            base_result | {"status": "blocked", "blocker_codes": [code]},
            None,
            [issue],
            governance,
        )

    if not announcement_id or not announcement_id.isdigit():
        return blocked("announcement_id_invalid")
    if duplicate_announcement_id:
        return blocked("duplicate_announcement_id")
    body = row.get("body")
    if not isinstance(body, bytes) or not body:
        return blocked("document_body_missing")
    declared_sha256 = str(row.get("document_sha256") or "").strip()
    if (
        _HEX_64.fullmatch(declared_sha256) is None
        or hashlib.sha256(body).hexdigest() != declared_sha256
    ):
        return blocked("document_sha256_mismatch")
    if (
        type(row.get("document_size_bytes")) is not int
        or row.get("document_size_bytes") != len(body)
    ):
        return blocked("document_size_bytes_mismatch")
    if str(row.get("source_raw_payload_sha256") or "") != declared_sha256:
        return blocked("source_raw_payload_sha256_mismatch")
    title = _normalize_text(str(row.get("announcement_title") or ""))
    if not title:
        return blocked("announcement_title_missing")
    publication_time_precision_proven = bool(
        row.get("announcement_time_precision_proven") is True
    )
    known = _announcement_known_at(
        row.get("announcement_time"),
        precision_proven=publication_time_precision_proven,
    )
    if known is None:
        return blocked("announcement_time_invalid")
    try:
        scope_roles = _validated_cninfo_source_scope_roles(row)
    except ValueError:
        return blocked("source_inventory_scope_invalid")
    if not source_lineage_complete:
        return blocked("source_lineage_incomplete")
    if scope_roles is not None and not {
        "corporate_actions",
        "corrections",
    }.intersection(scope_roles):
        return (
            base_result
            | {
                "status": "out_of_scope",
                "blocker_codes": [],
                "source_scope_roles": list(scope_roles),
            },
            None,
            [],
            [],
        )
    security_id = str(row.get("security_id") or "").strip()
    if not security_id:
        return blocked("security_identity_unresolved")
    document_format = declared_format
    try:
        extracted = extractor(body, document_format)
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) else "cninfo_document_text_extraction_failed"
        if not code or " " in code:
            code = "cninfo_document_text_extraction_failed"
        return blocked(code)
    if not isinstance(extracted, str):
        return blocked("cninfo_document_text_type_invalid")
    if len(extracted) > max_text_chars:
        return blocked("cninfo_document_text_limit_exceeded")
    text = _normalize_text(extracted)
    if not text:
        return blocked("cninfo_document_text_empty")
    stage = _corporate_action_stage(title, text)
    if stage is None:
        return blocked("corporate_action_stage_unresolved")
    fiscal_period_end = _fiscal_period_end(title, text)
    if fiscal_period_end is None:
        return blocked("corporate_action_fiscal_period_unresolved")
    economic_text = _economic_terms_scope(text, stage)
    try:
        cash = _single_distribution_ratio(
            economic_text,
            _CASH_PATTERN,
            exclude_actual_net=True,
        )
        bonus = _single_distribution_ratio(economic_text, _BONUS_PATTERN)
        transfer = _single_distribution_ratio(
            economic_text,
            _TRANSFER_PATTERN,
        )
    except ValueError:
        return blocked("corporate_action_economic_terms_conflict")
    if cash is None and bonus is None and transfer is None and stage != "withdrawal":
        return blocked("corporate_action_economic_terms_unresolved")
    if stage == "withdrawal":
        cash = bonus = transfer = Decimal(0)
    terms_complete = all(
        value is not None for value in (cash, bonus, transfer)
    )
    dates = {
        field: _labeled_date(text, labels)
        for field, labels in _DATE_LABELS.items()
    }
    if stage in {"implementation", "correction"} and dates["effective_at"] is None:
        return blocked("corporate_action_effective_date_unresolved")
    stock_distribution = (
        bonus + transfer
        if bonus is not None and transfer is not None
        else None
    )
    adjustment_semantic_complete = bool(
        terms_complete
        and dates["effective_at"] is not None
        and stage in {"implementation", "correction", "withdrawal"}
    )
    record_effective_order_valid = bool(
        dates["record_date"] is not None
        and dates["effective_at"] is not None
        and dates["record_date"] < dates["effective_at"]
    )
    pay_order_valid = bool(
        dates["pay_date"] is None
        or (
            dates["effective_at"] is not None
            and dates["effective_at"] <= dates["pay_date"]
        )
    )
    listing_order_valid = bool(
        dates["div_listdate"] is None
        or (
            dates["effective_at"] is not None
            and dates["effective_at"] <= dates["div_listdate"]
        )
    )
    event_ledger_semantic_complete = bool(
        adjustment_semantic_complete
        and record_effective_order_valid
        and pay_order_valid
        and listing_order_valid
        and (cash is not None and (cash <= 0 or dates["pay_date"] is not None))
        and (
            stock_distribution is not None
            and (
                stock_distribution <= 0
                or dates["div_listdate"] is not None
            )
        )
    )
    event_id = "cae_" + canonical_hash(
        {"security_id": security_id, "fiscal_period_end": fiscal_period_end}
    )[:24]
    semantic = {
        "event_id": event_id,
        "security_id": security_id,
        "fiscal_period_end": fiscal_period_end,
        "stage": stage,
        "known_at": known["date"],
        "known_at_utc": known["utc"],
        "known_timing": known["timing"],
        "publication_time_precision_proven": (
            publication_time_precision_proven
        ),
        "effective_at": dates["effective_at"],
        "record_date": dates["record_date"],
        "pay_date": dates["pay_date"],
        "div_listdate": dates["div_listdate"],
        "cash_div_per_share": _decimal_string(cash),
        "stock_bonus_ratio": _decimal_string(bonus),
        "stock_transfer_ratio": _decimal_string(transfer),
        "stock_distribution_ratio": (
            _decimal_string(stock_distribution)
            if stock_distribution is not None
            else None
        ),
        "economic_terms_complete": terms_complete,
        "adjustment_semantic_complete": adjustment_semantic_complete,
        "event_ledger_semantic_complete": event_ledger_semantic_complete,
        "ts_code": None,
        "identity_timeline_projection_required": True,
        "source_announcement_id": announcement_id,
        "source_document_sha256": declared_sha256,
        "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_title": title,
        "source_sec_code": str(row.get("sec_code") or ""),
        "source_org_id": str(row.get("org_id") or ""),
        "source_adjunct_url": str(row.get("adjunct_url") or ""),
        "source_lineage_root": canonical_hash(
            {
                key: value
                for key, value in _cninfo_source_semantic(row).items()
                if key.startswith("source_")
            }
        ),
        "source_document_closure_root": str(
            row.get("source_document_closure_root") or ""
        ),
        "source_governed_evidence_eligible": False,
        "text_extraction_replay_verified": bool(
            text_extraction_replay_verified
        ),
    }
    event_version_id = "caev_" + canonical_hash(semantic)[:24]
    known_after_effective = _not_known_before_effective(
        known_at=str(semantic["known_at"]),
        known_timing=str(semantic["known_timing"]),
        effective_at=semantic["effective_at"],
    )
    parser_semantic_complete = bool(
        stage in {"implementation", "correction"}
        and semantic["effective_at"]
        and not known_after_effective
        and semantic["economic_terms_complete"]
        and semantic["adjustment_semantic_complete"]
        and semantic["event_ledger_semantic_complete"]
        and source_lineage_complete
        and semantic["text_extraction_replay_verified"]
    )
    event = semantic | {
        "event_version_id": event_version_id,
        "supersedes_event_version_id": None,
        "parser_semantic_complete": parser_semantic_complete,
        "semantic_candidate_eligible": False,
        "pit_evidence_eligible": False,
        "independent_event_coverage_verdict_required": True,
    }
    semantic_blockers: list[str] = []
    if not terms_complete:
        semantic_blockers.append("corporate_action_economic_terms_incomplete")
    if adjustment_semantic_complete and dates["record_date"] is None:
        semantic_blockers.append("corporate_action_record_date_unresolved")
    if (
        adjustment_semantic_complete
        and dates["record_date"] is not None
        and not record_effective_order_valid
    ):
        semantic_blockers.append(
            "corporate_action_record_effective_date_order_invalid"
        )
    if (
        stage in {"implementation", "correction"}
        and cash is not None
        and cash > 0
        and dates["pay_date"] is None
    ):
        semantic_blockers.append("corporate_action_cash_pay_date_unresolved")
    if (
        stage in {"implementation", "correction"}
        and dates["pay_date"] is not None
        and not pay_order_valid
    ):
        semantic_blockers.append(
            "corporate_action_effective_pay_date_order_invalid"
        )
    if (
        stage in {"implementation", "correction"}
        and stock_distribution is not None
        and stock_distribution > 0
        and dates["div_listdate"] is None
    ):
        semantic_blockers.append(
            "corporate_action_stock_listing_date_unresolved"
        )
    if (
        stage in {"implementation", "correction"}
        and dates["div_listdate"] is not None
        and not listing_order_valid
    ):
        semantic_blockers.append(
            "corporate_action_effective_listing_date_order_invalid"
        )
    if known_after_effective:
        semantic_blockers.append("corporate_action_known_after_effective")
        event["parser_semantic_complete"] = False
    parse_issues = [
        {"announcement_id": announcement_id, "code": code}
        for code in semantic_blockers
    ]
    status = "blocked" if semantic_blockers else "parsed"
    result = base_result | {
        "status": status,
        "blocker_codes": semantic_blockers,
        "stage": stage,
        "event_id": event_id,
        "event_version_id": event_version_id,
        "text_sha256": semantic["source_text_sha256"],
        "text_char_count": len(text),
    }
    return result, event, parse_issues, governance


def _link_cninfo_event_versions(
    event_versions: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in event_versions:
        grouped[str(row["event_id"])].append(dict(row))
    linked: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    for event_id in sorted(grouped):
        rows = sorted(
            grouped[event_id],
            key=lambda row: (
                str(row["known_at_utc"]),
                _STAGE_ORDER.get(str(row["stage"]), 99),
                str(row["event_version_id"]),
            ),
        )
        previous_version_id: str | None = None
        for row in rows:
            row["supersedes_event_version_id"] = previous_version_id
            row["event_version_id"] = "caev_" + canonical_hash(
                _cninfo_event_version_identity(row)
            )[:24]
            previous_version_id = str(row["event_version_id"])
            linked.append(row)
        ordered_stages = [str(row["stage"]) for row in rows]
        stages = list(dict.fromkeys(ordered_stages))
        chain_blockers: list[str] = []
        required = {"proposal", "shareholder_approval", "implementation"}
        if not required.issubset(stages):
            chain_blockers.append("corporate_action_stage_chain_incomplete")
        if len(stages) != len(ordered_stages):
            chain_blockers.append("corporate_action_stage_duplicate")
        known_instants = [str(row["known_at_utc"]) for row in rows]
        if any(
            current >= following
            for current, following in zip(
                known_instants, known_instants[1:], strict=False
            )
        ):
            chain_blockers.append(
                "corporate_action_chain_known_time_not_strictly_monotonic"
            )
        stage_orders = [_STAGE_ORDER.get(stage, 99) for stage in ordered_stages]
        if any(
            current >= following
            for current, following in zip(
                stage_orders, stage_orders[1:], strict=False
            )
        ):
            chain_blockers.append(
                "corporate_action_chain_stage_not_strictly_monotonic"
            )
        if "correction" in stages and (
            "implementation" not in stages
            or ordered_stages.index("correction") == 0
            or ordered_stages[ordered_stages.index("correction") - 1]
            != "implementation"
        ):
            chain_blockers.append("corporate_action_correction_target_missing")
        if "withdrawal" in stages:
            chain_blockers.append("corporate_action_chain_withdrawn")
        final = rows[-1]
        if final.get("stage") not in {"implementation", "correction"}:
            chain_blockers.append(
                "corporate_action_final_implementation_missing"
            )
        elif final.get("parser_semantic_complete") is not True:
            chain_blockers.append(
                "corporate_action_final_semantics_incomplete"
            )
        chain_blockers = sorted(set(chain_blockers))
        chains.append(
            {
                "event_id": event_id,
                "security_id": rows[0]["security_id"],
                "fiscal_period_end": rows[0]["fiscal_period_end"],
                "ordered_event_version_ids": [
                    str(row["event_version_id"]) for row in rows
                ],
                "stages_observed": stages,
                "terminal_event_version_id": str(
                    final["event_version_id"]
                ),
                "chain_complete": not chain_blockers,
                "blockers": chain_blockers,
            }
        )
    linked.sort(
        key=lambda row: (
            str(row["event_id"]),
            str(row["known_at_utc"]),
            str(row["event_version_id"]),
        )
    )
    return linked, chains


def _cninfo_event_version_identity(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        not in {
            "event_version_id",
            "independent_event_coverage_verdict_required",
            "pit_evidence_eligible",
            "semantic_candidate_eligible",
        }
    }


def _decode_cninfo_html(body: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("cninfo_html_encoding_unsupported")


def _normalize_text(value: str) -> str:
    return " ".join(
        str(value)
        .replace("\u3000", " ")
        .replace("\xa0", " ")
        .split()
    )


def _announcement_known_at(
    value: Any,
    *,
    precision_proven: bool,
) -> dict[str, str] | None:
    if type(value) is not int or value <= 0:
        return None
    try:
        instant = datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    local = instant.astimezone(_SHANGHAI)
    timing = "after_close"
    if precision_proven:
        clock = (local.hour, local.minute, local.second)
        if clock < (9, 15, 0):
            timing = "before_open"
        elif clock < (15, 0, 0):
            timing = "intraday"
    return {
        "date": local.strftime("%Y%m%d"),
        "utc": instant.isoformat().replace("+00:00", "Z"),
        "timing": timing,
    }


def _not_known_before_effective(
    *,
    known_at: str,
    known_timing: str,
    effective_at: Any,
) -> bool:
    effective = str(effective_at or "")
    return bool(
        effective
        and (
            known_at > effective
            or (known_at == effective and known_timing != "before_open")
        )
    )


def _corporate_action_stage(title: str, text: str = "") -> str | None:
    action_terms = ("分配", "分红", "派息", "权益分派", "利润分配")
    if not any(term in title or term in text for term in action_terms):
        return None
    if any(term in title for term in ("更正", "修订", "补充")):
        return "correction"
    if any(term in title for term in ("取消", "终止")):
        return "withdrawal"
    if "实施" in title:
        return "implementation"
    if "股东大会" in title and any(
        term in title for term in ("决议", "审议", "通过")
    ):
        return "shareholder_approval"
    if any(term in title for term in ("预案", "方案", "董事会")):
        return "proposal"
    return None


def _fiscal_period_end(title: str, text: str = "") -> str | None:
    patterns = (
        (r"(?P<year>19\d{2}|20\d{2})\s*年\s*(?:半年度|中期)", "0630"),
        (r"(?P<year>19\d{2}|20\d{2})\s*年\s*(?:第一季度|一季度)", "0331"),
        (r"(?P<year>19\d{2}|20\d{2})\s*年\s*(?:第三季度|三季度)", "0930"),
        (r"(?P<year>19\d{2}|20\d{2})\s*年(?:度)?", "1231"),
    )
    for pattern, suffix in patterns:
        match = re.search(pattern, title)
        if match is not None:
            return match.group("year") + suffix
    candidates: set[str] = set()
    for pattern, suffix in patterns:
        for match in re.finditer(pattern, text[:4000]):
            candidates.add(match.group("year") + suffix)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _economic_terms_scope(text: str, stage: str) -> str:
    if stage == "correction":
        markers = [match.end() for match in re.finditer(r"更正后[：:]?", text)]
        if markers:
            return text[markers[-1] : markers[-1] + 4000]
    headings = tuple(
        match.end()
        for match in re.finditer(
            r"(?:权益分派方案|利润分配方案|分红派息方案)(?:为)?[：:]?",
            text,
        )
    )
    if headings:
        return text[headings[0] : headings[0] + 4000]
    return text


def _single_distribution_ratio(
    text: str,
    pattern: re.Pattern[str],
    *,
    exclude_actual_net: bool = False,
) -> Decimal | None:
    ratios: set[Decimal] = set()
    for match in pattern.finditer(text):
        if exclude_actual_net and "实际" in text[max(0, match.start() - 20) : match.start()]:
            continue
        try:
            base = Decimal(match.group("base"))
            value = Decimal(match.group("value"))
        except InvalidOperation as exc:
            raise ValueError("distribution_decimal_invalid") from exc
        if base <= 0 or value < 0:
            raise ValueError("distribution_ratio_invalid")
        ratios.add(value / base)
    if len(ratios) > 1:
        raise ValueError("distribution_ratio_conflict")
    return next(iter(ratios)) if ratios else None


def _labeled_date(text: str, labels: Sequence[str]) -> str | None:
    values: set[str] = set()
    for label in labels:
        pattern = re.compile(
            re.escape(label)
            + r"\s*(?:为|是)?\s*[：:]?\s*"
            + r"(?P<year>20\d{2}|19\d{2})\s*[年./-]\s*"
            + r"(?P<month>\d{1,2})\s*[月./-]\s*"
            + r"(?P<day>\d{1,2})\s*日?"
        )
        for match in pattern.finditer(text):
            try:
                parsed = datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            values.add(parsed.strftime("%Y%m%d"))
    return next(iter(values)) if len(values) == 1 else None


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.000000000001")))


def _cninfo_parser_implementation_root() -> str:
    return canonical_hash(
        {
            "parser_identity": CNINFO_CORPORATE_ACTION_PARSER_IDENTITY,
            "source_module_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        }
    )


def _action_id(record: dict[str, Any]) -> str:
    payload = {
        "ts_code": record.get("ts_code"),
        "ann_date": record.get("ann_date"),
        "end_date": record.get("end_date"),
        "ex_date": record.get("ex_date"),
        "pay_date": record.get("pay_date"),
        "div_proc": record.get("div_proc") or record.get("raw_status"),
        "cash_div": record.get("cash_div"),
        "cash_div_tax": record.get("cash_div_tax"),
        "stk_div": record.get("stk_div"),
        "stk_bo_rate": record.get("stk_bo_rate"),
        "stk_co_rate": record.get("stk_co_rate"),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"ca_{digest[:16]}"


def _action_type(
    implemented: bool,
    cash_div: float,
    stock_bonus: float,
    stock_transfer: float,
) -> CorporateActionType:
    if not implemented:
        return CorporateActionType.PROPOSAL_ONLY
    has_cash = cash_div > 0
    has_stock_bonus = stock_bonus > 0
    has_stock_transfer = stock_transfer > 0
    if has_cash and (has_stock_bonus or has_stock_transfer):
        return CorporateActionType.COMBINED_DISTRIBUTION
    if has_cash:
        return CorporateActionType.CASH_DIVIDEND
    if has_stock_bonus:
        return CorporateActionType.STOCK_BONUS
    if has_stock_transfer:
        return CorporateActionType.STOCK_TRANSFER
    return CorporateActionType.UNKNOWN


def _float(value: Any) -> float:
    try:
        if value in {None, ""}:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text if len(text) == 8 and text.isdigit() else None
