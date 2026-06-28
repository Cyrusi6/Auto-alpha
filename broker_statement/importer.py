"""Broker statement importer and normalized artifact reader."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_jsonl_artifact

from .mapping import normalize_record
from .models import BrokerStatementImportResult, BrokerStatementManifest, BrokerStatementParseIssue
from .report import write_statement_import_report
from .schema import available_source_files, load_schema, normalized_path
from .validator import validate_statement


def import_statement(
    source_dir: str | Path,
    output_dir: str | Path,
    schema_config: str | Path | None = None,
    account_id: str | None = None,
    broker_name: str | None = None,
    trade_date: str | None = None,
    as_of_date: str | None = None,
    schema_name: str = "generic_broker_statement",
    strict: bool = False,
) -> BrokerStatementImportResult:
    source = Path(source_dir)
    output = Path(output_dir)
    schema = load_schema(schema_name, schema_config)
    files = available_source_files(source)
    parse_issues: list[BrokerStatementParseIssue] = []
    normalized: dict[str, list[dict[str, Any]]] = {}
    for dataset, path in files.items():
        rows, issues = _read_rows(path)
        parse_issues.extend(issues)
        records: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            record, issues = normalize_record(
                dataset,
                row,
                schema,
                account_id=account_id,
                broker_name=broker_name,
                trade_date=trade_date,
                as_of_date=as_of_date,
                file_name=path.name,
                line_number=index,
            )
            parse_issues.extend(issues)
            records.append(record)
        normalized[dataset] = records
    for required in schema.required_files:
        dataset = required.replace("external_", "")
        if dataset not in files:
            parse_issues.append(BrokerStatementParseIssue("warning", "missing_required_file", f"required file is missing: {required}"))
    statement_id = _statement_id(account_id or "", broker_name or "", trade_date or "", as_of_date or "", files)
    imported_at = _utc_now()
    paths: dict[str, str] = {}
    output.mkdir(parents=True, exist_ok=True)
    for dataset, records in normalized.items():
        path = normalized_path(output, dataset)
        write_jsonl_artifact(path, records, artifact_type=f"normalized_external_{dataset}", producer="broker_statement")
        paths[f"normalized_external_{dataset}_path"] = str(path)
    validation = validate_statement(statement_id, normalized, parse_issues, as_of_date=as_of_date or "", strict=strict)
    manifest = BrokerStatementManifest(
        statement_id=statement_id,
        account_id=str(account_id or _first_value(normalized, "account_id") or ""),
        broker_name=str(broker_name or _first_value(normalized, "broker_name") or ""),
        schema_name=schema.schema_name,
        trade_date=str(trade_date or _first_value(normalized, "trade_date") or ""),
        as_of_date=str(as_of_date or _first_value(normalized, "as_of_date") or ""),
        source_dir=str(source),
        source_file_hashes={dataset: _file_fingerprint(path) for dataset, path in files.items()},
        imported_at=imported_at,
        record_counts={dataset: len(records) for dataset, records in normalized.items()},
        parse_issue_count=validation.issue_count,
        warning_count=validation.warning_count,
        metadata={
            "notice": schema.notice,
            "synthetic": _source_synthetic(source),
        },
    )
    status = "error" if validation.error_count else ("warning" if validation.warning_count else "ok")
    result = BrokerStatementImportResult(
        statement_id=statement_id,
        status=status,
        manifest=manifest,
        validation=validation,
        paths=paths,
        synthetic=bool(manifest.metadata.get("synthetic")),
    )
    report_paths = write_statement_import_report(result, output)
    result = BrokerStatementImportResult(
        statement_id=result.statement_id,
        status=result.status,
        manifest=result.manifest,
        validation=result.validation,
        paths={**paths, **{key: str(value) for key, value in report_paths.items()}},
        synthetic=result.synthetic,
    )
    return result


def read_normalized_statement(statement_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    root = Path(statement_dir)
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(root.glob("normalized_external_*.jsonl")):
        dataset = path.name.removeprefix("normalized_external_").removesuffix(".jsonl")
        result[dataset] = _read_jsonl(path)
    return result


def _read_rows(path: Path) -> tuple[list[dict[str, Any]], list[BrokerStatementParseIssue]]:
    issues: list[BrokerStatementParseIssue] = []
    try:
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)], issues
        if path.suffix == ".jsonl":
            return _read_jsonl(path), issues
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [dict(item) for item in payload if isinstance(item, dict)], issues
            if isinstance(payload, dict):
                if isinstance(payload.get("records"), list):
                    return [dict(item) for item in payload["records"] if isinstance(item, dict)], issues
                return [payload], issues
    except Exception as exc:  # noqa: BLE001 - turn parser errors into structured issues
        issues.append(BrokerStatementParseIssue("error", "schema_parse_error", str(exc), file_name=path.name))
    return [], issues


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _file_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
        "mtime": int(stat.st_mtime),
    }


def _statement_id(account_id: str, broker_name: str, trade_date: str, as_of_date: str, files: dict[str, Path]) -> str:
    source = "|".join([account_id, broker_name, trade_date, as_of_date] + [f"{key}:{path.name}" for key, path in sorted(files.items())])
    return "stmt_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _source_synthetic(source_dir: Path) -> bool:
    manifest = source_dir / "synthetic_statement_manifest.json"
    if not manifest.exists():
        return False
    try:
        return bool(json.loads(manifest.read_text(encoding="utf-8")).get("synthetic"))
    except json.JSONDecodeError:
        return False


def _first_value(normalized: dict[str, list[dict[str, Any]]], key: str) -> Any:
    for rows in normalized.values():
        for row in rows:
            if row.get(key) not in {"", None}:
                return row.get(key)
    return None


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
