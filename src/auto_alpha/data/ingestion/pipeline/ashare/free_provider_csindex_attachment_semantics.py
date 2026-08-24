"""Fail-closed semantic replay for governed CSI attachment captures.

This module turns verified attachment bytes into *candidate* CSI300 change
rows.  It does not establish historical known-at, effective-at, completeness,
membership, or weights.  Unsupported and ambiguous documents remain explicit
blocked rows instead of being guessed from filenames or announcement titles.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

from auto_alpha.platform.artifacts.storage import canonical_hash

from . import free_provider_csindex_range_attachment as range_capture


SEMANTIC_SCHEMA = "csindex_attachment_semantic_replay_v1"
SEMANTIC_INDEX_SCHEMA = "csindex_attachment_semantic_index_v1"
CANDIDATE_SCHEMA = "csindex_csi300_change_candidate_v1"
PARSER_IDENTITY = "bounded_stdlib_xlsx_csi300_change_parser_v1"
MAX_ZIP_ENTRIES = 2_048
MAX_ZIP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_WORKBOOK_CELLS = 2_000_000
MAX_SHARED_STRINGS = 500_000
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


class _SemanticParseBlocked(ValueError):
    pass


@dataclass(frozen=True)
class _Sheet:
    name: str
    ordinal: int
    rows: tuple[tuple[int, Mapping[int, str]], ...]


def replay_csindex_range_attachment_semantics(
    path: str | Path,
) -> tuple[dict[str, bytes], str]:
    """Replay deterministic semantic artifacts from one signed range capture."""

    attachments, body_replay_root = (
        range_capture.replay_csindex_range_attachment_bodies(path)
    )
    semantic_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for attachment in attachments:
        semantic, candidates = _parse_attachment(attachment)
        semantic_rows.append(semantic)
        candidate_rows.extend(candidates)

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
    semantic_payload = _jsonl_bytes(semantic_rows)
    candidate_payload = _jsonl_bytes(candidate_rows)
    disposition_counts: dict[str, int] = {}
    blocked_reason_counts: dict[str, int] = {}
    for row in semantic_rows:
        disposition = str(row["semantic_disposition"])
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
        reason = row.get("blocked_reason")
        if reason is not None:
            key = str(reason)
            blocked_reason_counts[key] = blocked_reason_counts.get(key, 0) + 1
    source_capture_hashes = sorted(
        {
            attachment.attachment_sha256
            for attachment in attachments
        }
    )
    manifest_semantic = {
        "schema_version": SEMANTIC_SCHEMA,
        "parser_identity": PARSER_IDENTITY,
        "parser_implementation_root": _implementation_root(),
        "source_body_replay_root": body_replay_root,
        "source_attachment_count": len(attachments),
        "source_attachment_hash_set_root": canonical_hash(source_capture_hashes),
        "semantic_index_count": len(semantic_rows),
        "semantic_index_sha256": hashlib.sha256(semantic_payload).hexdigest(),
        "candidate_count": len(candidate_rows),
        "candidate_sha256": hashlib.sha256(candidate_payload).hexdigest(),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "blocked_reason_counts": dict(sorted(blocked_reason_counts.items())),
        "historical_known_at_proven": False,
        "effective_at_proven": False,
        "event_chain_complete": False,
        "seed_membership_proven": False,
        "historical_weights_proven": False,
        "pit_membership_authorized": False,
        "data_admission_eligible": False,
        "alpha_search_authorized": False,
        "blockers": list(_TEMPORAL_BLOCKERS),
    }
    manifest = manifest_semantic | {
        "content_hash": canonical_hash(manifest_semantic)
    }
    manifest_payload = _json_bytes(manifest)
    artifacts = {
        "csindex_attachment_semantic_index": semantic_payload,
        "csindex_csi300_change_candidates": candidate_payload,
        "semantic_manifest": manifest_payload,
    }
    replay_root = canonical_hash(
        {
            "schema_version": "csindex_attachment_semantic_artifact_set_v1",
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


def _parse_attachment(
    attachment: range_capture.ReplayedRangeAttachment,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        "pit_membership_authorized": False,
    }
    if extension in {"xls"}:
        return (
            base
            | {
                "semantic_disposition": "blocked_unsupported_format",
                "blocked_reason": "legacy_xls_semantic_parser_not_implemented",
                "sheet_count": None,
                "candidate_count": 0,
            },
            [],
        )
    if extension in {"jpg", "jpeg", "png"}:
        return (
            base
            | {
                "semantic_disposition": "blocked_unsupported_format",
                "blocked_reason": "image_ocr_semantic_parser_not_implemented",
                "sheet_count": None,
                "candidate_count": 0,
            },
            [],
        )
    if extension != "xlsx":
        return (
            base
            | {
                "semantic_disposition": "blocked_unsupported_format",
                "blocked_reason": "attachment_format_not_supported",
                "sheet_count": None,
                "candidate_count": 0,
            },
            [],
        )
    try:
        sheets = _read_xlsx(attachment.body)
    except _SemanticParseBlocked as exc:
        return (
            base
            | {
                "semantic_disposition": "blocked_parse_failure",
                "blocked_reason": str(exc),
                "sheet_count": None,
                "candidate_count": 0,
            },
            [],
        )
    candidates, saw_csi300, saw_supported_schema = _extract_candidates(
        attachment,
        sheets,
    )
    if candidates:
        disposition = "csi300_change_candidates_extracted"
        reason: str | None = None
    elif saw_csi300 and not saw_supported_schema:
        disposition = "blocked_ambiguous_semantics"
        reason = "xlsx_csi300_semantic_schema_unsupported"
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
            "candidate_count": len(candidates),
        },
        candidates,
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
        return ""
    return text


def _extract_candidates(
    attachment: range_capture.ReplayedRangeAttachment,
    sheets: Sequence[_Sheet],
) -> tuple[list[dict[str, Any]], bool, bool]:
    candidates: list[dict[str, Any]] = []
    saw_csi300 = False
    saw_supported_schema = False
    for sheet in sheets:
        normalized_sheet_name = _normalized_label(sheet.name)
        action = (
            "add"
            if normalized_sheet_name in _ADD_SHEETS
            else "remove"
            if normalized_sheet_name in _REMOVE_SHEETS
            else None
        )
        saw_csi300 = saw_csi300 or any(
            _row_has_csi300(values) for _row_number, values in sheet.rows
        )
        if action is None:
            continue
        header = _find_split_header(sheet.rows)
        if header is None:
            continue
        saw_supported_schema = True
        header_number, index_column, security_column, name_column = header
        for row_number, values in sheet.rows:
            if row_number <= header_number:
                continue
            if _canonical_index_code(values.get(index_column, "")) != "000300":
                continue
            security_code = _canonical_security_code(
                values.get(security_column, "")
            )
            if security_code is None:
                continue
            candidates.append(
                {
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
                    "security_name": (
                        values.get(name_column, "").strip()
                        if name_column is not None
                        else ""
                    ),
                    "source_sheet_name": sheet.name,
                    "source_sheet_ordinal": sheet.ordinal,
                    "source_row_number": row_number,
                    "historical_known_at": None,
                    "historical_known_at_proven": False,
                    "effective_at": None,
                    "effective_at_proven": False,
                    "pit_membership_authorized": False,
                    "blockers": list(_TEMPORAL_BLOCKERS),
                }
            )
    return candidates, saw_csi300, saw_supported_schema


def _find_split_header(
    rows: Sequence[tuple[int, Mapping[int, str]]],
) -> tuple[int, int, int, int | None] | None:
    for row_number, values in rows[:20]:
        labels = {column: _normalized_label(value) for column, value in values.items()}
        index_columns = [
            column for column, label in labels.items() if label in _INDEX_HEADER
        ]
        security_columns = [
            column for column, label in labels.items() if label in _SECURITY_HEADER
        ]
        name_columns = [
            column
            for column, label in labels.items()
            if label in _SECURITY_NAME_HEADER
        ]
        if len(index_columns) == 1 and len(security_columns) == 1:
            return (
                row_number,
                index_columns[0],
                security_columns[0],
                name_columns[0] if len(name_columns) == 1 else None,
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


def _implementation_root() -> str:
    return canonical_hash(
        {
            "parser_identity": PARSER_IDENTITY,
            "limits": {
                "max_zip_entries": MAX_ZIP_ENTRIES,
                "max_zip_uncompressed_bytes": MAX_ZIP_UNCOMPRESSED_BYTES,
                "max_xml_bytes": MAX_XML_BYTES,
                "max_workbook_cells": MAX_WORKBOOK_CELLS,
                "max_shared_strings": MAX_SHARED_STRINGS,
            },
            "source": "\n".join(
                inspect.getsource(value)
                for value in (
                    _parse_attachment,
                    _read_xlsx,
                    _read_shared_strings,
                    _read_workbook_relationships,
                    _read_sheet_rows,
                    _cell_text,
                    _extract_candidates,
                    _find_split_header,
                    _canonical_index_code,
                    _canonical_security_code,
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
