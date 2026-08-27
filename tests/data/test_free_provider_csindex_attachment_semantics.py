from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Mapping
from xml.sax.saxutils import escape

import pytest


_SYNTHETIC_COMBINED_XLS_ZLIB_BASE64 = (
    "eNrtWM1PE0EU/+1Hv1CgVOCgMWlI/AA8lGxIvAhWNJGLJKCRg5dqt6Hho6apRk6u"
    "oJ4wMZqQ7E3+BC9e9FBvHjbRPwGNiV5MPGjiQRzfvJ3dtgQSMBzA7Csz7+P33pv"
    "pzO5jOh/ed62vvTz6EZtoBAb+iBTiTTaNWjJQ0iBcCCkGPEVNRHSgKJWkjYzH8L"
    "rdS+Rp/+R+X4COz+Y5ZHijbwx/y51V+z+tPyEM6ON+CD14J/f8/lPNfyhiyFfLhb"
    "l9ArzRdwr04kVLqjiuFmYq84Ug5DAkojVC1tHNjj9FtukFqWdb7D8i+17ZNWy1z"
    "oO+/6/N9v5t7Ge2sQ9sY09taW8L5pNs2J/rJtKOISTvcuLMM47J/IiTYA4HzLud"
    "mHiGT/BfKlk7uXqqTpZStpDgSJPqHId7SKFerxNz4LoCrlUiVw8e+ZfoI6RjiXRP"
    "cJxQOf18jkrn+AOSj4zzKF4IFy7pwqVYyis8F1ZJwPI8spO3bC61EqUvuVhdXQU"
    "5wHLJYNEfNZcb2SyKJcMjdOBYzH+hpmZsuza0ApPWTsNvPCYMOESN1nN8oWjfy4"
    "5VinaoXCnM27TAF+05u1auLJCYLxbLUmwHpuxbd6rl2iKHNOsyKgGMTY1nrVyOpE"
    "l7vnLXLsY43C4qbDiXI8tEbcauCipzSSyb8iUHvhC/7c8JfmlMt5TGAS58g9RrW"
    "GLZaTwGOCF3mdb1ASFvzW76F0rU47Nl9n7I/ckw5uvoqSb5dCh/H+1vkteo4AJF+"
    "NVJpzI8rSrY+mjANVwjhGL0DraYqpABG/xtgE7uN9Tz28nZfE0nTQ81kzRDaRpj"
    "ZqgZpMVCzWzBYgozaAZ6OB7q1fMS13nMeIjrAU7fz8dl7kSImwp3JvryPi7zJxV"
    "uNPJfXuH8BudPhXgjPxQu87eFeJB/SeU3OL/ER/QMXiU4sKnmHFeros5BUA9JRBFF"
    "FFFEEUUU0UGjSVToU6OfM5ewQLyKxV3F99KxKbhL0HYYE9wXSbpOo1cxi5s8j9ld"
    "zz9DB73m+4wdB6b3bg3/aXzsj/E1Pv6Dj7Ym32/4PxoSap9k24iuyf5b+gvDgSo+"
)


def _synthetic_combined_xls() -> bytes:
    return zlib.decompress(base64.b64decode(_SYNTHETIC_COMBINED_XLS_ZLIB_BASE64))


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


def _xlsx_with_error_index_cell() -> bytes:
    source = _xlsx(
        [
            (
                "调入",
                [
                    ["指数代码", "证券代码", "证券简称"],
                    ["000300", "600000", "valid"],
                    ["000300", "600001", "must-not-survive"],
                ],
            )
        ]
    )
    target = b'<c r="A3" t="inlineStr"><is><t>000300</t></is></c>'
    replacement = b'<c r="A3" t="e"><v>#N/A</v></c>'
    output = io.BytesIO()
    replaced = False
    with zipfile.ZipFile(io.BytesIO(source)) as reader:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as writer:
            for info in reader.infolist():
                payload = reader.read(info)
                if info.filename == "xl/worksheets/sheet1.xml":
                    replaced = target in payload
                    payload = payload.replace(target, replacement)
                writer.writestr(info, payload)
    assert replaced
    return output.getvalue()


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


def _source_capture_binding(semantics: object) -> dict[str, object]:
    content_hash = "a" * 64
    details_hash = "b" * 64
    inventory = {
        "csindex_range_attachment_index": {
            "relative_path": "normalized/attachment_index.jsonl",
            "sha256": "c" * 64,
            "size_bytes": 1,
        },
        "csindex_range_wire_exchange_index": {
            "relative_path": "normalized/wire_exchange_index.jsonl",
            "sha256": "d" * 64,
            "size_bytes": 1,
        },
        "csindex_range_blocked_reference_index": {
            "relative_path": "normalized/blocked_reference_index.jsonl",
            "sha256": "e" * 64,
            "size_bytes": 1,
        },
        "normalized_manifest": {
            "relative_path": "normalized/normalized_manifest.json",
            "sha256": "f" * 64,
            "size_bytes": 1,
        },
    }
    generation_id = f"free_provider_backfill_{content_hash[:24]}"
    binding = {
        "schema_version": semantics.SOURCE_CAPTURE_BINDING_SCHEMA,
        "provider": "csindex",
        "capture_profile": semantics.range_capture.CAPTURE_PROFILE,
        "generation_id": generation_id,
        "content_hash": content_hash,
        "manifest_name": semantics.range_capture.capture_module.MANIFEST_NAME,
        "manifest_relative_reference": (
            f"generations/{generation_id}/"
            f"{semantics.range_capture.capture_module.MANIFEST_NAME}"
        ),
        "manifest_sha256": "0" * 64,
        "capture_status": "succeeded",
        "publication_signature_verified": True,
        "normalized_artifacts_trusted": True,
        "specialized_validator_verified": True,
        "specialized_validation_mode": (
            semantics.SPECIALIZED_VALIDATION_MODE
        ),
        "specialized_validator_identity_root": (
            semantics._source_verifier_implementation_root()
        ),
        "planner_root_proof_mode": (
            semantics.CURRENT_PLANNER_ROOT_PROOF_MODE
        ),
        "signed_source_identity_root": "9" * 64,
        "legacy_attachment_input_root": "a" * 64,
        "legacy_attachment_request_plan_root": "b" * 64,
        "strong_details_ancestry_verified": True,
        "contract_id": "1" * 64,
        "contract_sha256": "2" * 64,
        "request_plan_hash": "3" * 64,
        "request_plan_sha256": "4" * 64,
        "request_count": 2,
        "normalized_replay_root": "5" * 64,
        "normalized_artifact_inventory": inventory,
        "normalized_artifact_inventory_root": semantics.canonical_hash(
            inventory
        ),
        "declared_capture_implementation_root": (
            semantics.range_capture._implementation_root()
        ),
        "input_capture_content_hash": "7" * 64,
        "source_details_generation_id": (
            f"free_provider_backfill_{details_hash[:24]}"
        ),
        "source_details_content_hash": details_hash,
        "capture_public_key_sha256": (
            semantics.range_capture.APPROVED_CAPTURE_KEY_SHA256
        ),
        "permission_context_id": (
            semantics.range_capture.DEFAULT_PERMISSION_CONTEXT
        ),
        "source_namespace_id": "8" * 64,
        "source_reference_resolution_policy": (
            "caller_supplied_source_capture_or_controlled_admission_root"
        ),
        "independent_data_admission_requires_source_reference_resolution": True,
    }
    identity = {
        "declared_capture_implementation_root": binding[
            "declared_capture_implementation_root"
        ],
        "capture_content_hash": binding["content_hash"],
        "contract_id": binding["contract_id"],
        "request_plan_hash": binding["request_plan_hash"],
        "source_binding_content_hash": binding[
            "input_capture_content_hash"
        ],
        "legacy_attachment_input_root": binding[
            "legacy_attachment_input_root"
        ],
        "legacy_attachment_request_plan_root": binding[
            "legacy_attachment_request_plan_root"
        ],
        "details_content_hash": binding["source_details_content_hash"],
    }
    binding["signed_source_identity_root"] = semantics.canonical_hash(identity)
    return binding


def _source_reference(
    semantics: object,
    binding: dict[str, object],
    attachments: tuple[object, ...],
) -> dict[str, object]:
    return {
        "schema_version": semantics.SOURCE_REFERENCE_SCHEMA,
        "source_capture_binding": binding,
        "signed_source_manifest": {},
        "source_contract": {},
        "source_request_plan": {
            "requests": [
                {
                    "request_id": row.source_request_id,
                    "url": row.attachment_url,
                }
                for row in attachments
            ]
        },
        "normalized_attachment_index_rows": [
            {
                "source_request_id": row.source_request_id,
                "attachment_url": row.attachment_url,
                "attachment_extension": row.attachment_extension,
                "attachment_sha256": row.attachment_sha256,
                "source_logical_payload_sha256": (
                    row.source_logical_payload_sha256
                ),
                "source_announcements": [
                    dict(item) for item in row.source_announcements
                ],
            }
            for row in attachments
        ],
    }


def _build_single_attachment_generation(
    semantics: object,
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    body = _xlsx(
        [
            (
                "调入",
                [
                    ["指数代码", "证券代码", "证券简称"],
                    ["000300", "600000", "浦发银行"],
                ],
            )
        ]
    )
    attachment = _attachment(semantics, body, "xlsx")
    binding = _source_capture_binding(semantics)
    binding["request_count"] = 1
    reference = _source_reference(semantics, binding, (attachment,))
    monkeypatch.setattr(
        semantics,
        "_validate_source_reference_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path, **_kwargs: (
            iter((attachment,)),
            "9" * 64,
            binding,
            reference,
        ),
    )
    return semantics.build_csindex_attachment_semantic_evidence(
        "signed-range-capture",
        tmp_path / "published",
    )


def _forge_semantic_manifest(
    semantics: object,
    *,
    published: Mapping[str, object],
    destination: Path,
    mutate: object,
) -> Path:
    original = Path(str(published["manifest_path"])).parent
    working = destination / "working"
    shutil.copytree(original, working)
    for item in working.rglob("*"):
        item.chmod(0o750 if item.is_dir() else 0o640)
    working.chmod(0o750)
    manifest_path = working / semantics.SEMANTIC_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest["artifact_set_root"] = semantics.canonical_hash(
        manifest["artifact_inventory"]
    )
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    content_hash = semantics.canonical_hash(semantic)
    generation_id = (
        f"{semantics.SEMANTIC_GENERATION_PREFIX}_{content_hash[:24]}"
    )
    manifest["content_hash"] = content_hash
    manifest["generation_id"] = generation_id
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged = destination / generation_id
    working.rename(forged)
    for item in forged.rglob("*"):
        item.chmod(0o550 if item.is_dir() else 0o440)
    forged.chmod(0o550)
    return forged / semantics.SEMANTIC_MANIFEST_NAME


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
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "a" * 64, {}, {}),
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
    terminal_rows = [
        json.loads(line)
        for line in first[
            "csindex_csi300_change_row_dispositions"
        ].splitlines()
    ]
    terminal_sheets = [
        json.loads(line)
        for line in first["csindex_csi300_sheet_dispositions"].splitlines()
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
    assert manifest["row_disposition_count"] == 2
    assert manifest["csi300_bearing_sheet_count"] == 2
    assert {
        row["terminal_disposition"] for row in terminal_rows
    } == {"candidate_extracted"}
    assert {
        row["terminal_disposition"] for row in terminal_sheets
    } == {"supported_schema_candidate_rows_terminalized"}
    assert manifest["historical_known_at_proven"] is False
    assert manifest["event_chain_complete"] is False
    assert manifest["data_admission_eligible"] is False
    assert manifest["alpha_search_authorized"] is False


def test_semantic_replay_extracts_combined_schema_from_real_biff_xls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    body = _synthetic_combined_xls()
    attachments = (_attachment(semantics, body, "xls"),)
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "c" * 64, {}, {}),
    )

    first, first_root = semantics.replay_csindex_range_attachment_semantics(
        "capture"
    )
    second, second_root = semantics.replay_csindex_range_attachment_semantics(
        "capture"
    )
    candidates = [
        json.loads(line)
        for line in first["csindex_csi300_change_candidates"].splitlines()
    ]
    index = json.loads(first["csindex_attachment_semantic_index"])
    manifest = json.loads(first["semantic_manifest"])
    terminal_rows = [
        json.loads(line)
        for line in first[
            "csindex_csi300_change_row_dispositions"
        ].splitlines()
    ]

    assert first == second
    assert first_root == second_root
    assert [(row["action"], row["security_code"]) for row in candidates] == [
        ("add", "600000"),
        ("remove", "000001"),
    ]
    assert index["attachment_extension"] == "xls"
    assert index["semantic_disposition"] == "csi300_change_candidates_extracted"
    assert index["sheet_count"] == 1
    assert manifest["parser_components"]["xls"] == "xlrd"
    assert manifest["xls_attachment_count"] == 1
    assert manifest["xls_parser_runtime_isolation_proven"] is True
    assert manifest["xls_parser_os_timeout_enforced"] is True
    assert manifest["runtime_isolation_blockers"] == []
    assert manifest["xls_worker_limits"]["process_model"] == (
        "fresh_isolated_python_subprocess"
    )
    assert manifest["xls_worker_limits"]["wall_timeout_seconds"] == 20
    assert index["legacy_xls_runtime_isolation_proven"] is True
    assert index["legacy_xls_os_timeout_enforced"] is True
    assert all(
        not any("runtime_isolation" in blocker for blocker in row["blockers"])
        for row in candidates + terminal_rows
    )
    assert manifest["pit_membership_authorized"] is False


def test_supported_change_schema_terminalizes_invalid_codes_and_fails_closed(
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
                    ["指数代码", "证券代码", "证券简称"],
                    ["000300", "600000", "valid"],
                    ["000300", "", "missing"],
                    ["000300", "not-a-code", "invalid"],
                ],
            )
        ]
    )
    attachments = (_attachment(semantics, body, "xlsx"),)
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "d" * 64, {}, {}),
    )

    artifacts, _root = semantics.replay_csindex_range_attachment_semantics(
        "capture"
    )
    index = json.loads(artifacts["csindex_attachment_semantic_index"])
    terminal_rows = [
        json.loads(line)
        for line in artifacts[
            "csindex_csi300_change_row_dispositions"
        ].splitlines()
    ]
    manifest = json.loads(artifacts["semantic_manifest"])

    assert artifacts["csindex_csi300_change_candidates"] == b""
    assert index["semantic_disposition"] == "blocked_invalid_change_rows"
    assert index["blocked_reason"] == (
        "supported_change_schema_has_invalid_security_code"
    )
    assert index["candidate_count"] == 0
    assert len(terminal_rows) == 3
    assert [row["source_row_number"] for row in terminal_rows] == [2, 3, 4]
    assert [row["terminal_disposition"] for row in terminal_rows] == [
        "blocked_attachment_has_invalid_change_shape",
        "blocked_invalid_security_code",
        "blocked_invalid_security_code",
    ]
    assert [row["raw_security_code"] for row in terminal_rows] == [
        "600000",
        "",
        "not-a-code",
    ]
    assert all(row["pit_membership_authorized"] is False for row in terminal_rows)
    assert manifest["row_disposition_count"] == 3
    assert manifest["candidate_count"] == 0


def test_xlsx_error_cell_blocks_entire_attachment_before_candidate_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    body = _xlsx_with_error_index_cell()
    attachments = (_attachment(semantics, body, "xlsx"),)
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "1" * 64, {}, {}),
    )

    artifacts, _root = semantics.replay_csindex_range_attachment_semantics(
        "capture"
    )
    index = json.loads(artifacts["csindex_attachment_semantic_index"])
    manifest = json.loads(artifacts["semantic_manifest"])

    assert index["semantic_disposition"] == "blocked_parse_failure"
    assert index["blocked_reason"] == "xlsx_error_cell_unsupported"
    assert index["candidate_count"] == 0
    assert artifacts["csindex_csi300_change_candidates"] == b""
    assert artifacts["csindex_csi300_change_row_dispositions"] == b""
    assert artifacts["csindex_csi300_sheet_dispositions"] == b""
    assert manifest["candidate_count"] == 0


def test_xls_worker_timeout_kills_worker_and_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    class TimedOutProcess:
        returncode = -9

        def __init__(self) -> None:
            self.communicate_count = 0
            self.killed = False

        def communicate(self, **_kwargs: object) -> tuple[None, None]:
            self.communicate_count += 1
            if self.communicate_count == 1:
                raise semantics.subprocess.TimeoutExpired("worker", 20)
            return None, None

        def kill(self) -> None:
            self.killed = True

    process = TimedOutProcess()
    monkeypatch.setattr(
        semantics.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_worker_wall_timeout",
    ):
        semantics._run_xls_worker(_synthetic_combined_xls())
    assert process.killed is True
    assert process.communicate_count == 2


def test_xls_without_kernel_isolation_is_explicitly_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    body = _synthetic_combined_xls()
    attachments = (_attachment(semantics, body, "xls"),)
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "e" * 64, {}, {}),
    )
    monkeypatch.setattr(
        semantics,
        "_xls_worker_isolation_available",
        lambda: False,
    )

    artifacts, _root = semantics.replay_csindex_range_attachment_semantics(
        "capture"
    )
    index = json.loads(artifacts["csindex_attachment_semantic_index"])
    manifest = json.loads(artifacts["semantic_manifest"])

    assert index["semantic_disposition"] == "blocked_parse_failure"
    assert index["blocked_reason"] == "xls_worker_resource_isolation_unavailable"
    assert index["legacy_xls_runtime_isolation_proven"] is False
    assert manifest["xls_parser_runtime_isolation_proven"] is False
    assert manifest["xls_parser_os_timeout_enforced"] is False
    assert manifest["runtime_isolation_blockers"] == [
        "legacy_xls_worker_rlimit_isolation_unavailable",
        "legacy_xls_worker_wall_timeout_unavailable",
    ]
    assert artifacts["csindex_csi300_change_candidates"] == b""


def test_mixed_supported_and_unsupported_csi300_sheets_fail_atomically(
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
                    ["指数代码", "证券代码", "证券简称"],
                    ["000300", "600000", "valid"],
                ],
            ),
            (
                "Other",
                [
                    ["指数代码", "调出", "调入"],
                    ["000300", "1", "2"],
                ],
            ),
        ]
    )
    attachments = (_attachment(semantics, body, "xlsx"),)
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "f" * 64, {}, {}),
    )

    artifacts, _root = semantics.replay_csindex_range_attachment_semantics(
        "capture"
    )
    index = json.loads(artifacts["csindex_attachment_semantic_index"])
    row_terminals = [
        json.loads(line)
        for line in artifacts[
            "csindex_csi300_change_row_dispositions"
        ].splitlines()
    ]
    sheet_terminals = [
        json.loads(line)
        for line in artifacts["csindex_csi300_sheet_dispositions"].splitlines()
    ]
    manifest = json.loads(artifacts["semantic_manifest"])

    assert artifacts["csindex_csi300_change_candidates"] == b""
    assert index["semantic_disposition"] == "blocked_unsupported_csi300_sheet"
    assert index["blocked_reason"] == "csi300_bearing_sheet_schema_unsupported"
    assert index["csi300_bearing_sheet_count"] == 2
    assert len(row_terminals) == 1
    assert row_terminals[0]["terminal_disposition"] == (
        "blocked_attachment_has_unsupported_csi300_sheet"
    )
    assert [row["terminal_disposition"] for row in sheet_terminals] == [
        "blocked_attachment_has_unsupported_csi300_sheet",
        "blocked_unsupported_csi300_schema",
    ]
    assert all(
        row["semantic_candidate_emitted"] is False for row in sheet_terminals
    )
    assert manifest["csi300_bearing_sheet_count"] == 2
    assert manifest["candidate_count"] == 0


def test_implementation_root_binds_module_and_xlrd_toolchain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    module_hash = semantics._module_source_sha256()
    toolchain_root = semantics._xlrd_toolchain_root()
    baseline = semantics._implementation_root()
    assert len(module_hash) == 64
    assert len(toolchain_root) == 64

    monkeypatch.setattr(
        semantics,
        "_module_source_sha256",
        lambda: "0" * 64,
    )
    assert semantics._implementation_root() != baseline

    monkeypatch.setattr(
        semantics,
        "_module_source_sha256",
        lambda: module_hash,
    )
    monkeypatch.setattr(
        semantics,
        "_xlrd_toolchain_root",
        lambda: "1" * 64,
    )
    assert semantics._implementation_root() != baseline

    monkeypatch.setattr(
        semantics,
        "_xlrd_toolchain_root",
        lambda: toolchain_root,
    )
    monkeypatch.setattr(
        semantics,
        "XLS_WORKER_WALL_TIMEOUT_SECONDS",
        semantics.XLS_WORKER_WALL_TIMEOUT_SECONDS + 1,
    )
    assert semantics._implementation_root() != baseline


def test_legacy_xls_parser_rejects_invalid_container_and_resource_overrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_container_limits_invalid",
    ):
        semantics._read_xls(b"not-an-ole-workbook")

    body = _synthetic_combined_xls()
    monkeypatch.setattr(semantics, "MAX_XLS_BODY_BYTES", len(body) - 1)
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_container_limits_invalid",
    ):
        semantics._read_xls_in_process(body)


def test_legacy_xls_parser_enforces_sheet_cell_and_text_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    class FakeCell:
        ctype = semantics.xlrd.XL_CELL_TEXT
        value = "x"

    class FakeSheet:
        name = "Sheet1"
        nrows = 1
        ncols = 1

        def row_len(self, _row: int) -> int:
            return 1

        def cell(self, _row: int, _column: int) -> FakeCell:
            return FakeCell()

    class FakeWorkbook:
        nsheets = 1

        def __init__(self, sheet: FakeSheet | None = None) -> None:
            self.sheet = sheet or FakeSheet()
            self.released = False

        def sheet_by_index(self, _ordinal: int) -> FakeSheet:
            return self.sheet

        def release_resources(self) -> None:
            self.released = True

    body = semantics._OLE_COMPOUND_MAGIC

    too_many_sheets = FakeWorkbook()
    too_many_sheets.nsheets = semantics.MAX_XLS_SHEETS + 1
    monkeypatch.setattr(
        semantics.xlrd,
        "open_workbook",
        lambda **_kwargs: too_many_sheets,
    )
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_sheet_inventory_invalid",
    ):
        semantics._read_xls_in_process(body)
    assert too_many_sheets.released is True

    oversized_sheet = FakeSheet()
    oversized_sheet.nrows = semantics.MAX_XLS_SHEET_ROWS + 1
    workbook = FakeWorkbook(oversized_sheet)
    monkeypatch.setattr(
        semantics.xlrd,
        "open_workbook",
        lambda **_kwargs: workbook,
    )
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_sheet_dimensions_exceeded",
    ):
        semantics._read_xls_in_process(body)
    assert workbook.released is True

    workbook = FakeWorkbook()
    monkeypatch.setattr(
        semantics.xlrd,
        "open_workbook",
        lambda **_kwargs: workbook,
    )
    monkeypatch.setattr(semantics, "MAX_WORKBOOK_CELLS", 0)
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_workbook_cell_limit_exceeded",
    ):
        semantics._read_xls_in_process(body)
    assert workbook.released is True

    monkeypatch.setattr(semantics, "MAX_WORKBOOK_CELLS", 1)
    monkeypatch.setattr(semantics, "MAX_XLS_CELL_TEXT_BYTES", 0)
    workbook = FakeWorkbook()
    monkeypatch.setattr(
        semantics.xlrd,
        "open_workbook",
        lambda **_kwargs: workbook,
    )
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_cell_text_limit_exceeded",
    ):
        semantics._read_xls_in_process(body)
    assert workbook.released is True


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
        semantics,
        "_iter_verified_range_attachments",
        lambda _path: (attachments, "b" * 64, {}, {}),
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
        "csi300_bearing_sheet_schema_unsupported"
    )
    assert rows["request-2"]["blocked_reason"] == "xls_container_limits_invalid"
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


def test_production_publisher_consumes_one_attachment_before_requesting_next(
    tmp_path: Path,
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
                    ["指数代码", "证券代码", "证券简称"],
                    ["000300", "600000", "浦发银行"],
                ],
            )
        ]
    )
    first = _attachment(semantics, body, "xlsx", 1)
    second = _attachment(semantics, body, "xlsx", 2)
    parsed: list[str] = []
    real_parser = semantics._parse_attachment

    def parse(attachment):
        result = real_parser(attachment)
        parsed.append(attachment.source_request_id)
        return result

    def attachments():
        yield first
        assert parsed == ["request-1"]
        yield second

    binding = _source_capture_binding(semantics)
    reference = _source_reference(semantics, binding, (first, second))
    monkeypatch.setattr(semantics, "_parse_attachment", parse)
    monkeypatch.setattr(
        semantics,
        "_validate_source_reference_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics,
        "_deep_validate_semantic_source_replay",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path, **_kwargs: (
            attachments(),
            "8" * 64,
            binding,
            reference,
        ),
    )
    output = tmp_path / "semantic-evidence"
    published = semantics.build_csindex_attachment_semantic_evidence(
        "signed-range-capture",
        output,
    )
    validated = semantics.validate_csindex_attachment_semantic_evidence(
        published["manifest_path"]
    )

    assert parsed == ["request-1", "request-2"]
    assert validated["source_attachment_count"] == 2
    assert validated["candidate_count"] == 2
    assert validated["bounded_processing"] == {
        "attachment_iteration": "one_verified_body_at_a_time",
        "resident_attachment_scope_limit": 1,
        "per_attachment_body_limit_bytes": (
            semantics.range_capture.ATTACHMENT_BODY_MAX_BYTES
        ),
        "per_logical_envelope_limit_bytes": (
            semantics.range_capture.MAX_LOGICAL_ENVELOPE_BYTES
        ),
        "contract_total_response_limit_bytes": (
            semantics.range_capture.MAX_TOTAL_RESPONSE_BYTES
        ),
    }
    assert all(value is False for value in validated["safety"].values())
    assert validated["source_capture_binding"] == _source_capture_binding(
        semantics
    )
    assert validated["validation_status"] == (
        "blocked_source_resolution_required"
    )
    assert validated["source_capture_binding_root"] == semantics.canonical_hash(
        validated["source_capture_binding"]
    )
    assert validated["source_capture_binding"][
        "independent_data_admission_requires_source_reference_resolution"
    ] is True
    assert Path(published["manifest_path"]).parent.stat().st_mode & 0o222 == 0


def test_source_capture_binding_rejects_cross_identity_and_unknown_fields() -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    binding = _source_capture_binding(semantics)
    semantics._validate_source_capture_binding(binding)

    wrong_reference = dict(binding)
    wrong_reference["manifest_relative_reference"] = (
        "generations/free_provider_backfill_deadbeef/"
        "free_provider_backfill_manifest.json"
    )
    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_capture_binding_invalid",
    ):
        semantics._validate_source_capture_binding(wrong_reference)

    unknown_field = dict(binding) | {"self_asserted_admission": True}
    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_capture_binding_invalid",
    ):
        semantics._validate_source_capture_binding(unknown_field)

    arbitrary_current_root = dict(binding)
    arbitrary_current_root["declared_capture_implementation_root"] = "6" * 64
    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_capture_binding_invalid",
    ):
        semantics._validate_source_capture_binding(arbitrary_current_root)


def test_historical_signed_identity_uses_only_current_semantic_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    historical_root = next(
        iter(semantics.HISTORICAL_RANGE_IMPLEMENTATION_ROOTS)
    )
    validated = {
        "status": "succeeded",
        "capture_profile": semantics.range_capture.CAPTURE_PROFILE,
        "request_count": semantics.range_capture.EXPECTED_REQUEST_COUNT,
        "strong_details_ancestry_verified": True,
        "range_protocol_verified": True,
        "normalized_artifacts_trusted": True,
        "publication_signature_verified": True,
        "planner_root_proof_mode": (
            semantics.HISTORICAL_PLANNER_ROOT_PROOF_MODE
        ),
        "signed_source_identity_root": "9" * 64,
        "historical_known_at_proven": False,
        "pit_membership_authorized": False,
    }
    calls: list[tuple[str, str]] = []

    def current_verifier(
        _manifest: Path,
        *,
        profile: str,
        declared_implementation_root: str,
    ) -> dict[str, object]:
        calls.append((profile, declared_implementation_root))
        return validated

    monkeypatch.setattr(
        semantics,
        "_validate_signed_range_source_current_semantics",
        current_verifier,
    )

    result, mode = semantics._specialized_source_validation(
        Path("/wheel-install/without-repository-metadata/manifest.json"),
        profile=semantics.range_capture.CAPTURE_PROFILE,
        declared_implementation_root=historical_root,
    )

    assert result == validated
    assert mode == semantics.SPECIALIZED_VALIDATION_MODE
    assert calls == [
        (semantics.range_capture.CAPTURE_PROFILE, historical_root)
    ]
    assert not hasattr(semantics, "_repository_root")
    assert not hasattr(
        semantics, "_run_historical_specialized_validator_worker"
    )
    assert not hasattr(semantics, "HISTORICAL_RANGE_IMPLEMENTATION_REVISIONS")


@pytest.mark.parametrize(
    "field",
    (
        "capture_content_hash",
        "contract_id",
        "request_plan_hash",
        "source_binding_content_hash",
        "legacy_attachment_input_root",
        "legacy_attachment_request_plan_root",
        "details_content_hash",
    ),
)
def test_historical_planner_roots_require_exact_signed_source_identity(
    field: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    profile = semantics.range_capture.CAPTURE_PROFILE
    identity = dict(
        semantics.HISTORICAL_SIGNED_RANGE_SOURCE_IDENTITIES[profile]
    )
    validated = {
        "content_hash": identity["capture_content_hash"],
        "contract_id": identity["contract_id"],
        "request_plan_hash": identity["request_plan_hash"],
    }
    binding = {
        "content_hash": identity["source_binding_content_hash"],
        "legacy_attachment_input_root": identity[
            "legacy_attachment_input_root"
        ],
        "legacy_attachment_request_plan_root": identity[
            "legacy_attachment_request_plan_root"
        ],
        "details_content_hash": identity["details_content_hash"],
    }
    mode, root = semantics._planner_root_proof(
        validated,
        profile=profile,
        declared_implementation_root=identity[
            "declared_capture_implementation_root"
        ],
        binding=binding,
    )
    assert mode == semantics.HISTORICAL_PLANNER_ROOT_PROOF_MODE
    assert root == semantics.canonical_hash(identity)

    if field in validated:
        validated[field] = "0" * 64
    elif field == "capture_content_hash":
        validated["content_hash"] = "0" * 64
    elif field == "source_binding_content_hash":
        binding["content_hash"] = "0" * 64
    else:
        binding[field] = "0" * 64
    with pytest.raises(
        ValueError,
        match="csindex_semantic_historical_source_identity_invalid",
    ):
        semantics._planner_root_proof(
            validated,
            profile=profile,
            declared_implementation_root=identity[
                "declared_capture_implementation_root"
            ],
            binding=binding,
        )


def test_current_source_verifier_rejects_unlisted_declared_root_before_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    replay_called = False

    def forbidden_replay(_path: Path) -> dict[str, object]:
        nonlocal replay_called
        replay_called = True
        raise AssertionError("unlisted root reached source replay")

    monkeypatch.setattr(
        semantics.range_capture,
        "validate_free_provider_backfill",
        forbidden_replay,
    )
    with pytest.raises(
        ValueError,
        match="csindex_semantic_declared_capture_implementation_root_invalid",
    ):
        semantics._validate_signed_range_source_current_semantics(
            Path("signed-capture.json"),
            profile=semantics.range_capture.CAPTURE_PROFILE,
            declared_implementation_root="6" * 64,
        )
    assert replay_called is False


@pytest.mark.parametrize(
    "attack",
    ("top_level_extra", "schema_drift", "request_row_extra"),
)
def test_source_request_plan_rejects_synchronized_plan_shape_attacks(
    attack: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    request = semantics.range_capture.ProviderProbeRequest(
        request_id="request-one",
        provider="csindex",
        method="GET",
        url="https://oss-ch.csindex.com.cn/one.xlsx",
    ).semantic()
    requests = [request]
    plan: dict[str, object] = {
        "schema_version": (
            semantics.range_capture.capture_module.PLAN_SCHEMA
        ),
        "request_plan_hash": semantics.canonical_hash(requests),
        "requests": requests,
    }
    if attack == "top_level_extra":
        plan["self_asserted_complete"] = True
    elif attack == "schema_drift":
        plan["schema_version"] = "attacker_plan_v1"
    else:
        request["self_asserted_complete"] = True
        plan["request_plan_hash"] = semantics.canonical_hash(requests)

    with pytest.raises(
        (TypeError, ValueError),
        match=(
            "csindex_semantic_source_request_plan_invalid"
            "|provider_probe_request_semantic_invalid"
        ),
    ):
        semantics._validated_source_requests_from_plan(plan)


@pytest.mark.parametrize(
    "source_name",
    (
        "free_provider_backfill_manifest.json",
        "activity_contract.json",
        "request_plan.json",
    ),
)
def test_source_json_reader_rejects_duplicate_key_smuggling(
    tmp_path: Path,
    source_name: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    source = tmp_path / source_name
    source.write_bytes(b'{"schema_version":"evil","schema_version":"signed"}')
    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_json_invalid",
    ):
        semantics._read_exact_source_json(source)


@pytest.mark.parametrize("extra_kind", ("empty_directory", "fifo"))
def test_source_generation_tree_rejects_extra_and_special_entries(
    tmp_path: Path,
    extra_kind: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    for name in (
        semantics.range_capture.capture_module.CONTRACT_NAME,
        semantics.range_capture.capture_module.PLAN_NAME,
        semantics.range_capture.capture_module.JOURNAL_NAME,
        semantics.range_capture.capture_module.CATALOG_NAME,
        semantics.range_capture.capture_module.MANIFEST_NAME,
    ):
        (tmp_path / name).write_bytes(b"" if name.endswith(".jsonl") else b"{}")
    extra = tmp_path / "self_asserted_evidence"
    if extra_kind == "empty_directory":
        extra.mkdir()
    else:
        os.mkfifo(extra)
    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_generation_tree_invalid",
    ):
        semantics._validate_source_generation_tree(
            tmp_path,
            source_manifest={
                "normalized_artifacts": [],
                "pause_artifacts": [],
            },
        )


def test_semantic_validator_rejects_oversized_source_reference_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    published = _build_single_attachment_generation(
        semantics,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    generation = Path(published["manifest_path"]).parent
    reference = generation / semantics.SOURCE_REFERENCE_NAME
    generation.chmod(0o750)
    reference.chmod(0o640)
    with reference.open("r+b") as handle:
        handle.truncate(semantics.MAX_SOURCE_REFERENCE_BYTES + 1)
    reference.chmod(0o440)
    generation.chmod(0o550)

    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_reference_artifact_invalid",
    ):
        semantics.validate_csindex_attachment_semantic_evidence(
            published["manifest_path"]
        )


def test_semantic_jsonl_rejects_total_size_cap_before_row_decode(
    tmp_path: Path,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    artifact = tmp_path / "oversized.jsonl"
    with artifact.open("xb") as handle:
        handle.truncate(semantics.MAX_SEMANTIC_JSONL_BYTES + 1)
    with pytest.raises(
        ValueError,
        match="csindex_semantic_jsonl_framing_invalid",
    ):
        semantics._validate_semantic_jsonl(
            artifact,
            role="csindex_csi300_change_candidates",
            source_context={},
        )


def test_semantic_jsonl_rejects_single_line_size_cap_before_decode(
    tmp_path: Path,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    artifact = tmp_path / "oversized-line.jsonl"
    artifact.write_bytes(
        b'{"payload":"'
        + b"a" * semantics.MAX_SEMANTIC_JSONL_LINE_BYTES
        + b'"}\n'
    )
    with pytest.raises(
        ValueError,
        match="csindex_semantic_jsonl_framing_invalid",
    ):
        semantics._validate_semantic_jsonl(
            artifact,
            role="csindex_csi300_change_candidates",
            source_context={},
        )


def test_synchronized_source_contract_and_root_self_assertion_is_rejected() -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    binding = _source_capture_binding(semantics)
    binding["declared_capture_implementation_root"] = "6" * 64
    source_reference = _source_reference(semantics, binding, ())
    source_reference["source_contract"] = {
        "adapter_identity": {
            "capture_profile": semantics.range_capture.CAPTURE_PROFILE,
            "implementation_root": "6" * 64,
        }
    }

    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_capture_binding_invalid",
    ):
        semantics._validate_source_reference_payload(
            source_reference,
            expected_binding=binding,
        )


def test_source_reference_rejects_attacker_pem_and_self_signed_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )
    from auto_alpha.platform.governance.network.signing import (
        EphemeralReceiptSigner,
    )

    attacker = EphemeralReceiptSigner.generate()
    binding = _source_capture_binding(semantics)
    manifest_semantic = {
        "schema_version": semantics.range_capture.capture_module.SCHEMA_VERSION,
        "contract_id": binding["contract_id"],
        "request_plan_hash": binding["request_plan_hash"],
        "request_count": binding["request_count"],
        "normalized_artifacts": [],
    }
    content_hash = semantics.canonical_hash(manifest_semantic)
    generation_id = (
        f"{semantics.range_capture.capture_module.GENERATION_PREFIX}_"
        f"{content_hash[:24]}"
    )
    signed_manifest = manifest_semantic | {
        "content_hash": content_hash,
        "generation_id": generation_id,
        "capture_publication_signature": attacker.sign(
            semantics.range_capture.capture_module._canonical_bytes(
                manifest_semantic
                | {
                    "content_hash": content_hash,
                    "generation_id": generation_id,
                }
            )
        ),
    }
    source_reference = _source_reference(semantics, binding, ())
    source_reference["signed_source_manifest"] = signed_manifest
    source_reference["source_contract"] = {
        "capture_public_key_pem_b64": base64.b64encode(
            attacker.public_key_pem
        ).decode("ascii"),
        "capture_public_key_sha256": (
            semantics.range_capture.APPROVED_CAPTURE_KEY_SHA256
        ),
    }
    current_range_root = semantics.range_capture._implementation_root()
    monkeypatch.setattr(
        semantics.range_capture,
        "_validate_authorized_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_implementation_root",
        lambda: current_range_root,
    )

    with pytest.raises(
        ValueError,
        match="csindex_semantic_source_reference_public_key_invalid",
    ):
        semantics._validate_source_reference_payload(
            source_reference,
            expected_binding=binding,
        )


def test_semantic_owned_source_reader_reconstructs_only_the_requested_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    root = tmp_path / "capture"
    normalized = root / "normalized"
    normalized.mkdir(parents=True)
    current_range_root = semantics.range_capture._implementation_root()
    requests = [
        semantics.range_capture.ProviderProbeRequest(
            request_id=f"request-{ordinal}",
            provider="csindex",
            method="GET",
            url=f"https://oss-ch.csindex.com.cn/{ordinal}.xlsx",
        )
        for ordinal in (1, 2)
    ]
    details_hash = "b" * 64
    (root / "activity_contract.json").write_text(
        json.dumps(
            {
                "adapter_identity": {
                    "capture_profile": semantics.range_capture.CAPTURE_PROFILE,
                    "implementation_root": current_range_root,
                    "input_capture_content_hash": "7" * 64,
                    "source_details_generation_id": (
                        f"free_provider_backfill_{details_hash[:24]}"
                    ),
                    "source_details_content_hash": details_hash,
                },
                "capture_public_key_sha256": (
                    semantics.range_capture.APPROVED_CAPTURE_KEY_SHA256
                ),
                "permission_context_id": (
                    semantics.range_capture.DEFAULT_PERMISSION_CONTEXT
                ),
                "output_namespace_id": "8" * 64,
            }
        ),
        encoding="utf-8",
    )
    (root / "request_plan.json").write_text(
        json.dumps({"requests": [row.semantic() for row in requests]}),
        encoding="utf-8",
    )
    terminal_rows = [
        {
            "event_type": "capture_attempt_terminal",
            "request_id": request.request_id,
            "raw_envelope_relative_path": f"raw/{request.request_id}.json",
            "attempt_id": f"attempt-{ordinal}",
            "retry_ordinal": 0,
        }
        for ordinal, request in enumerate(requests, start=1)
    ]
    (root / "capture_journal.jsonl").write_bytes(
        b"".join(semantics._json_bytes(row) for row in terminal_rows)
    )
    bodies = {
        request.request_id: f"body-{request.request_id}".encode()
        for request in requests
    }
    raw = {
        request.request_id: f"raw-{request.request_id}".encode()
        for request in requests
    }
    index_rows = []
    for request in requests:
        index_rows.append(
            {
                "source_request_id": request.request_id,
                "attachment_url": request.url,
                "attachment_extension": "xlsx",
                "attachment_sha256": hashlib.sha256(
                    bodies[request.request_id]
                ).hexdigest(),
                "source_logical_payload_sha256": hashlib.sha256(
                    raw[request.request_id]
                ).hexdigest(),
                "attachment_size_bytes": len(bodies[request.request_id]),
                "source_announcements": [],
            }
        )
    (normalized / "attachment_index.jsonl").write_bytes(
        b"".join(semantics._json_bytes(row) for row in index_rows)
    )
    normalized_payloads = {
        "csindex_range_attachment_index": (
            normalized / "attachment_index.jsonl"
        ).read_bytes(),
        "csindex_range_wire_exchange_index": b"",
        "csindex_range_blocked_reference_index": b"",
        "normalized_manifest": b"{}\n",
    }
    relative_paths = {
        "csindex_range_attachment_index": "normalized/attachment_index.jsonl",
        "csindex_range_wire_exchange_index": (
            "normalized/wire_exchange_index.jsonl"
        ),
        "csindex_range_blocked_reference_index": (
            "normalized/blocked_reference_index.jsonl"
        ),
        "normalized_manifest": "normalized/normalized_manifest.json",
    }
    for role, relative_path in relative_paths.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(normalized_payloads[role])
    manifest = root / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    content_hash = "a" * 64
    range_source_binding = {
        "content_hash": "7" * 64,
        "legacy_attachment_input_root": "a" * 64,
        "legacy_attachment_request_plan_root": "b" * 64,
    }
    source_identity = {
        "declared_capture_implementation_root": current_range_root,
        "capture_content_hash": content_hash,
        "contract_id": "1" * 64,
        "request_plan_hash": "3" * 64,
        "source_binding_content_hash": "7" * 64,
        "legacy_attachment_input_root": "a" * 64,
        "legacy_attachment_request_plan_root": "b" * 64,
        "details_content_hash": details_hash,
    }
    validated = {
        "status": "succeeded",
        "manifest_path": str(manifest),
        "generation_id": f"free_provider_backfill_{content_hash[:24]}",
        "content_hash": content_hash,
        "contract_id": "1" * 64,
        "request_plan_hash": "3" * 64,
        "request_count": 2,
        "range_protocol_verified": True,
        "publication_signature_verified": True,
        "normalized_artifacts_trusted": True,
        "planner_root_proof_mode": (
            semantics.CURRENT_PLANNER_ROOT_PROOF_MODE
        ),
        "signed_source_identity_root": semantics.canonical_hash(
            source_identity
        ),
        "normalized_artifacts": [
            {
                "role": role,
                "relative_path": relative_paths[role],
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for role, payload in normalized_payloads.items()
        ],
    }
    monkeypatch.setattr(
        semantics.range_capture,
        "validate_free_provider_backfill",
        lambda _path: validated,
    )
    monkeypatch.setattr(
        semantics,
        "_specialized_source_validation",
        lambda *_args, **_kwargs: (
            validated,
            semantics.SPECIALIZED_VALIDATION_MODE,
        ),
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_validate_authorized_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_request_plan_evidence",
        lambda _requests: ([], range_source_binding),
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "replay_csindex_range_attachment_capture",
        lambda _path: (
            normalized_payloads,
            "b" * 64,
        ),
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_capture_public_key_from_terminal",
        lambda _terminal: b"public-key",
    )
    monkeypatch.setattr(
        semantics,
        "_validate_source_reference_payload",
        lambda *_args, **_kwargs: None,
    )
    reads: list[str] = []

    def read_wrapper(path: Path):
        request_id = path.stem
        reads.append(request_id)
        return {"request_id": request_id}

    monkeypatch.setattr(
        semantics.range_capture,
        "_read_exact_json",
        read_wrapper,
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_raw_logical_payload",
        lambda wrapper, **_kwargs: raw[wrapper["request_id"]],
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_validate_and_assemble_logical",
        lambda payload, **_kwargs: (
            bodies[payload.decode().removeprefix("raw-")],
            (),
            "full_get",
            None,
        ),
    )
    monkeypatch.setattr(
        semantics.range_capture,
        "_implementation_root",
        lambda: current_range_root,
    )

    attachments, replay_root, source_binding, source_reference = (
        semantics._iter_verified_range_attachments(manifest)
    )
    assert len(replay_root) == 64
    assert source_binding["content_hash"] == "a" * 64
    assert source_binding[
        "independent_data_admission_requires_source_reference_resolution"
    ] is True
    assert source_reference["source_capture_binding"] == source_binding
    assert reads == []
    first = next(attachments)
    assert first.source_request_id == "request-1"
    assert reads == ["request-1"]
    second = next(attachments)
    assert second.source_request_id == "request-2"
    assert reads == ["request-1", "request-2"]
    with pytest.raises(StopIteration):
        next(attachments)


def test_semantic_validator_rejects_tamper_even_after_manifest_rehash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    body = _xlsx(
        [
            (
                "调出",
                [
                    ["指数代码", "证券代码"],
                    ["000300", "000001"],
                ],
            )
        ]
    )
    attachment = _attachment(semantics, body, "xlsx")
    binding = _source_capture_binding(semantics)
    reference = _source_reference(semantics, binding, (attachment,))
    binding["request_count"] = 1
    reference["source_capture_binding"] = binding
    monkeypatch.setattr(
        semantics,
        "_validate_source_reference_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics,
        "_deep_validate_semantic_source_replay",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path, **_kwargs: (
            iter((attachment,)),
            "9" * 64,
            binding,
            reference,
        ),
    )
    published = semantics.build_csindex_attachment_semantic_evidence(
        "signed-range-capture",
        tmp_path / "published",
    )
    original = Path(published["manifest_path"]).parent
    forged_parent = tmp_path / "forged"
    forged_parent.mkdir()
    working = forged_parent / "working"
    shutil.copytree(original, working)
    working.chmod(0o750)
    manifest_path = working / semantics.SEMANTIC_MANIFEST_NAME
    manifest_path.chmod(0o640)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["safety"]["data_admission_eligible"] = True
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    forged_hash = semantics.canonical_hash(semantic)
    forged_id = f"{semantics.SEMANTIC_GENERATION_PREFIX}_{forged_hash[:24]}"
    manifest["content_hash"] = forged_hash
    manifest["generation_id"] = forged_id
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged = forged_parent / forged_id
    working.rename(forged)
    for item in forged.rglob("*"):
        item.chmod(0o550 if item.is_dir() else 0o440)
    forged.chmod(0o550)

    with pytest.raises(
        ValueError,
        match="csindex_semantic_evidence_manifest_invalid",
    ):
        semantics.validate_csindex_attachment_semantic_evidence(
            forged / semantics.SEMANTIC_MANIFEST_NAME
        )


@pytest.mark.parametrize(
    "attack",
    (
        "manifest_count_bool",
        "inventory_count_bool",
        "count_map_bool",
        "inventory_unknown_field",
        "inventory_missing_field",
        "bounded_limit_bool",
        "worker_limit_bool",
        "safety_int",
    ),
)
def test_semantic_validator_rejects_self_consistently_resigned_type_and_tree_attacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    published = _build_single_attachment_generation(
        semantics,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    def mutate(manifest: dict[str, object]) -> None:
        if attack == "manifest_count_bool":
            manifest["candidate_count"] = True
        elif attack == "inventory_count_bool":
            manifest["artifact_inventory"][
                "csindex_csi300_change_candidates"
            ]["row_count"] = True
        elif attack == "count_map_bool":
            disposition_counts = manifest["disposition_counts"]
            key = next(iter(disposition_counts))
            disposition_counts[key] = True
        elif attack == "inventory_unknown_field":
            manifest["artifact_inventory"][
                "csindex_csi300_change_candidates"
            ]["self_asserted_complete"] = True
        elif attack == "inventory_missing_field":
            manifest["artifact_inventory"][
                "csindex_csi300_change_candidates"
            ].pop("sha256")
        elif attack == "bounded_limit_bool":
            manifest["bounded_processing"][
                "resident_attachment_scope_limit"
            ] = True
        elif attack == "worker_limit_bool":
            manifest["xls_worker_limits"]["python_isolated_flag"] = 1
        else:
            manifest["safety"]["data_admission_eligible"] = 0

    forged_manifest = _forge_semantic_manifest(
        semantics,
        published=published,
        destination=tmp_path / f"forged-{attack}",
        mutate=mutate,
    )
    with pytest.raises(
        ValueError,
        match=(
            "csindex_semantic_evidence_manifest_invalid"
            "|csindex_semantic_artifact_inventory_invalid"
        ),
    ):
        semantics.validate_csindex_attachment_semantic_evidence(
            forged_manifest
        )


@pytest.mark.parametrize("entry_kind", ("empty_directory", "fifo"))
def test_semantic_validator_rejects_unexpected_non_regular_generation_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    published = _build_single_attachment_generation(
        semantics,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    generation = Path(published["manifest_path"]).parent
    generation.chmod(0o750)
    unexpected = generation / f"unexpected-{entry_kind}"
    if entry_kind == "empty_directory":
        unexpected.mkdir()
        unexpected.chmod(0o550)
    else:
        os.mkfifo(unexpected)
        unexpected.chmod(0o440)
    generation.chmod(0o550)

    with pytest.raises(
        ValueError,
        match="csindex_semantic_evidence_manifest_invalid",
    ):
        semantics.validate_csindex_attachment_semantic_evidence(
            published["manifest_path"]
        )


def test_deep_replay_rejects_synchronized_candidate_terminal_tamper_and_rehash(
    tmp_path: Path,
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
                    ["指数代码", "证券代码", "证券简称"],
                    ["000300", "600000", "浦发银行"],
                ],
            )
        ]
    )
    attachment = _attachment(semantics, body, "xlsx")
    binding = _source_capture_binding(semantics)
    binding["request_count"] = 1
    reference = _source_reference(semantics, binding, (attachment,))
    monkeypatch.setattr(
        semantics,
        "_validate_source_reference_payload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        semantics,
        "_iter_verified_range_attachments",
        lambda _path, **_kwargs: (
            iter((attachment,)),
            "9" * 64,
            binding,
            reference,
        ),
    )
    published = semantics.build_csindex_attachment_semantic_evidence(
        "signed-range-capture",
        tmp_path / "published",
    )
    assert published["deep_source_replay_verified"] is True
    original = Path(published["manifest_path"]).parent
    working = tmp_path / "forged-working"
    shutil.copytree(original, working)
    for item in working.rglob("*"):
        item.chmod(0o750 if item.is_dir() else 0o640)
    working.chmod(0o750)

    candidate_path = working / semantics.SEMANTIC_FILE_NAMES[
        "csindex_csi300_change_candidates"
    ]
    candidate = json.loads(candidate_path.read_bytes())
    candidate["security_code"] = "600001"
    candidate_path.write_bytes(semantics._json_bytes(candidate))
    row_path = working / semantics.SEMANTIC_FILE_NAMES[
        "csindex_csi300_change_row_dispositions"
    ]
    terminal = json.loads(row_path.read_bytes())
    terminal["raw_security_code"] = "600001"
    terminal["canonical_security_code"] = "600001"
    row_path.write_bytes(semantics._json_bytes(terminal))

    manifest_path = working / semantics.SEMANTIC_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for role, artifact_path in (
        ("csindex_csi300_change_candidates", candidate_path),
        ("csindex_csi300_change_row_dispositions", row_path),
    ):
        artifact = manifest["artifact_inventory"][role]
        artifact["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        artifact["size_bytes"] = artifact_path.stat().st_size
    manifest["artifact_set_root"] = semantics.canonical_hash(
        manifest["artifact_inventory"]
    )
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    forged_hash = semantics.canonical_hash(semantic)
    forged_id = f"{semantics.SEMANTIC_GENERATION_PREFIX}_{forged_hash[:24]}"
    manifest["content_hash"] = forged_hash
    manifest["generation_id"] = forged_id
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged = tmp_path / forged_id
    working.rename(forged)
    for item in forged.rglob("*"):
        item.chmod(0o550 if item.is_dir() else 0o440)
    forged.chmod(0o550)

    with pytest.raises(
        ValueError,
        match="csindex_semantic_deep_source_replay_mismatch",
    ):
        semantics.validate_csindex_attachment_semantic_evidence(
            forged / semantics.SEMANTIC_MANIFEST_NAME,
            source_capture="signed-range-capture",
        )


def test_role_validator_rejects_order_enum_type_and_source_tuple_attacks(
    tmp_path: Path,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    body = _xlsx(
        [
            (
                "调入",
                [
                    ["指数代码", "证券代码"],
                    ["000300", "600000"],
                ],
            )
        ]
    )
    attachments = (
        _attachment(semantics, body, "xlsx", 1),
        _attachment(semantics, body, "xlsx", 2),
    )
    candidates = [
        semantics._parse_attachment(attachment)[1][0]
        for attachment in attachments
    ]
    binding = _source_capture_binding(semantics)
    reference = _source_reference(semantics, binding, attachments)
    context = semantics._source_row_context(reference)
    path = tmp_path / "candidates.jsonl"

    path.write_bytes(
        b"".join(semantics._json_bytes(row) for row in reversed(candidates))
    )
    with pytest.raises(ValueError, match="csindex_semantic_row_order_invalid"):
        semantics._validate_semantic_jsonl(
            path,
            role="csindex_csi300_change_candidates",
            source_context=context,
        )

    invalid_enum = dict(candidates[0]) | {"action": "buy"}
    path.write_bytes(semantics._json_bytes(invalid_enum))
    with pytest.raises(ValueError, match="csindex_semantic_row_type_invalid"):
        semantics._validate_semantic_jsonl(
            path,
            role="csindex_csi300_change_candidates",
            source_context=context,
        )

    semantic_index = semantics._parse_attachment(attachments[0])[0]
    semantic_index["legacy_xls_runtime_isolation_proven"] = 0
    semantic_index["legacy_xls_os_timeout_enforced"] = 1
    path.write_bytes(semantics._json_bytes(semantic_index))
    with pytest.raises(ValueError, match="csindex_semantic_row_type_invalid"):
        semantics._validate_semantic_jsonl(
            path,
            role="csindex_attachment_semantic_index",
            source_context=context,
        )

    invalid_type = dict(candidates[0]) | {"source_row_number": True}
    path.write_bytes(semantics._json_bytes(invalid_type))
    with pytest.raises(ValueError, match="csindex_semantic_row_type_invalid"):
        semantics._validate_semantic_jsonl(
            path,
            role="csindex_csi300_change_candidates",
            source_context=context,
        )

    wrong_source = dict(candidates[0]) | {
        "attachment_url": "https://oss-ch.csindex.com.cn/forged.xlsx"
    }
    path.write_bytes(semantics._json_bytes(wrong_source))
    with pytest.raises(ValueError, match="csindex_semantic_source_tuple_invalid"):
        semantics._validate_semantic_jsonl(
            path,
            role="csindex_csi300_change_candidates",
            source_context=context,
        )


@pytest.mark.parametrize(
    "drift",
    (
        "module_source_sha256",
        "xlrd_version",
        "python_version",
        "worker_limits",
    ),
)
def test_xls_worker_output_binds_exact_parser_and_resource_contract(
    drift: str,
) -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    contract = semantics._xls_worker_contract()
    assert contract["worker_module_name"] == semantics.__spec__.name
    assert contract["worker_module_name"] != "__main__"
    honest = {
        "schema_version": semantics.XLS_WORKER_SCHEMA,
        "worker_contract": contract,
        "status": "blocked",
        "reason": "xls_workbook_invalid",
    }
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_workbook_invalid",
    ):
        semantics._decode_xls_worker_output(semantics._json_bytes(honest))

    drifted = json.loads(json.dumps(honest))
    if drift == "module_source_sha256":
        drifted["worker_contract"]["module_source_sha256"] = "0" * 64
    elif drift == "xlrd_version":
        drifted["worker_contract"]["xlrd_version"] += ".attacker"
    elif drift == "python_version":
        drifted["worker_contract"]["python_version"][2] += 1
    else:
        drifted["worker_contract"]["worker_limits"][
            "cpu_soft_seconds"
        ] += 1
    with pytest.raises(
        semantics._SemanticParseBlocked,
        match="xls_worker_output_invalid",
    ):
        semantics._decode_xls_worker_output(semantics._json_bytes(drifted))


def test_canonical_xls_worker_subprocess_reports_importable_module_identity() -> None:
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    completed = subprocess.run(
        (
            sys.executable,
            "-I",
            "-m",
            semantics.XLS_WORKER_MODULE_NAME,
            "--xls-isolated-worker",
        ),
        input=b"not-an-xls-workbook",
        capture_output=True,
        check=False,
        timeout=semantics.XLS_WORKER_WALL_TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    contract = payload["worker_contract"]
    assert contract["worker_module_name"] == semantics.XLS_WORKER_MODULE_NAME
    assert contract["worker_module_name"] != "__main__"
    assert semantics._xls_worker_contract_valid(contract) is True


def test_csindex_and_market_evidence_commands_are_registered_and_importable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from importlib import import_module

    from auto_alpha.cli import COMMANDS
    from auto_alpha.data.ingestion.pipeline.ashare import (
        free_provider_csindex_attachment_semantics as semantics,
    )

    assert COMMANDS[("data", "free-csindex-semantics")].module == semantics.__name__
    market_module = COMMANDS[("data", "free-market-evidence")].module
    assert callable(import_module(market_module).main)
    monkeypatch.setattr(
        semantics,
        "build_csindex_attachment_semantic_evidence",
        lambda capture, output: {
            "technical_processing_status": "succeeded",
            "capture": capture,
            "output": str(output),
            "data_admission_eligible": False,
        },
    )
    assert semantics.main(
        ["--capture", "signed", "--output-root", "evidence"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["data_admission_eligible"] is False
