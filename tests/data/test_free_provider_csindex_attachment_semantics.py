from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest


def _column_name(ordinal: int) -> str:
    result = ""
    while ordinal:
        ordinal, remainder = divmod(ordinal - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _worksheet(rows: list[list[str]]) -> bytes:
    encoded_rows: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(
            (
                f'<c r="{_column_name(column)}{row_number}" t="inlineStr">'
                f"<is><t>{escape(value)}</t></is></c>"
            )
            for column, value in enumerate(row, start=1)
        )
        encoded_rows.append(f'<row r="{row_number}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        + "".join(encoded_rows)
        + "</sheetData></worksheet>"
    ).encode()


def _xlsx(sheets: list[tuple[str, list[list[str]]]]) -> bytes:
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{ordinal}" r:id="rId{ordinal}"/>'
        for ordinal, (name, _rows) in enumerate(sheets, start=1)
    )
    relationships = "".join(
        (
            f'<Relationship Id="rId{ordinal}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{ordinal}.xml"/>'
        )
        for ordinal in range(1, len(sheets) + 1)
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/'
                'spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
                f'2006/relationships"><sheets>{workbook_sheets}</sheets></workbook>'
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                f'package/2006/relationships">{relationships}</Relationships>'
            ),
        )
        for ordinal, (_name, rows) in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{ordinal}.xml",
                _worksheet(rows),
            )
    return stream.getvalue()


def _attachment(semantics: object, body: bytes, extension: str, ordinal: int = 1):
    return semantics.range_capture.ReplayedRangeAttachment(
        source_request_id=f"request-{ordinal}",
        attachment_url=f"https://oss-ch.csindex.com.cn/{ordinal}.{extension}",
        attachment_extension=extension,
        attachment_sha256=hashlib.sha256(body).hexdigest(),
        source_logical_payload_sha256=str(ordinal).zfill(64),
        source_announcements=(
            {
                "announcement_id": str(1000 + ordinal),
                "announcement_publish_date": "2019-12-02",
            },
        ),
        body=body,
    )


def test_semantic_replay_extracts_only_explicit_csi300_change_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    body = _xlsx(
        [
            (
                "调入",
                [
                    ["指数代码", "指数简称", "证券代码", "证券简称"],
                    ["000300", "沪深300", "600000", "浦发银行"],
                    ["000905", "中证500", "600001", "邯郸钢铁"],
                ],
            ),
            (
                "调出",
                [
                    ["指数代码", "指数简称", "股票代码", "股票名称"],
                    ["300", "沪深300", "1", "平安银行"],
                ],
            ),
        ]
    )
    attachments = (_attachment(semantics, body, "xlsx"),)
    monkeypatch.setattr(
        semantics.range_capture,
        "replay_csindex_range_attachment_bodies",
        lambda _path: (attachments, "a" * 64),
    )

    first, first_root = semantics.replay_csindex_range_attachment_semantics(
        Path("capture.json")
    )
    second, second_root = semantics.replay_csindex_range_attachment_semantics(
        Path("capture.json")
    )
    candidates = [
        json.loads(line)
        for line in first["csindex_csi300_change_candidates"].splitlines()
    ]
    index = json.loads(first["csindex_attachment_semantic_index"])
    manifest = json.loads(first["semantic_manifest"])

    assert first == second
    assert first_root == second_root
    assert len(first_root) == 64
    assert [(row["action"], row["security_code"]) for row in candidates] == [
        ("add", "600000"),
        ("remove", "000001"),
    ]
    assert all(row["effective_at"] is None for row in candidates)
    assert all(row["pit_membership_authorized"] is False for row in candidates)
    assert index["semantic_disposition"] == "csi300_change_candidates_extracted"
    assert index["blocked_reason"] is None
    assert manifest["candidate_count"] == 2
    assert manifest["historical_known_at_proven"] is False
    assert manifest["event_chain_complete"] is False
    assert manifest["data_admission_eligible"] is False
    assert manifest["alpha_search_authorized"] is False


def test_semantic_replay_classifies_every_unsupported_or_ambiguous_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    ambiguous = _xlsx(
        [("Sheet1", [["指数代码", "调出", "调入"], ["000300", "1", "2"]])]
    )
    malformed = b"PK\x03\x04not-an-xlsx"
    attachments = (
        _attachment(semantics, ambiguous, "xlsx", 1),
        _attachment(semantics, b"legacy", "xls", 2),
        _attachment(semantics, b"image", "png", 3),
        _attachment(semantics, malformed, "xlsx", 4),
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "replay_csindex_range_attachment_bodies",
        lambda _path: (attachments, "b" * 64),
    )

    artifacts, _root = semantics.replay_csindex_range_attachment_semantics("capture")
    rows = {
        row["source_request_id"]: row
        for row in (
            json.loads(line)
            for line in artifacts["csindex_attachment_semantic_index"].splitlines()
        )
    }

    assert rows["request-1"]["blocked_reason"] == (
        "xlsx_csi300_semantic_schema_unsupported"
    )
    assert rows["request-2"]["blocked_reason"] == (
        "legacy_xls_semantic_parser_not_implemented"
    )
    assert rows["request-3"]["blocked_reason"] == (
        "image_ocr_semantic_parser_not_implemented"
    )
    assert rows["request-4"]["blocked_reason"] == "xlsx_container_invalid"
    assert artifacts["csindex_csi300_change_candidates"] == b""


def test_semantic_parser_rejects_body_hash_drift() -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    attachment = semantics.range_capture.ReplayedRangeAttachment(
        source_request_id="request",
        attachment_url="https://oss-ch.csindex.com.cn/file.xlsx",
        attachment_extension="xlsx",
        attachment_sha256="0" * 64,
        source_logical_payload_sha256="1" * 64,
        source_announcements=(),
        body=b"drift",
    )

    with pytest.raises(ValueError, match="csindex_semantic_attachment_hash_mismatch"):
        semantics._parse_attachment(attachment)
