"""Broker statement schema, import, synthesis, validation, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrokerStatementSchema:
    schema_name: str
    field_mapping: dict[str, dict[str, str]] = field(default_factory=dict)
    required_files: list[str] = field(default_factory=list)
    optional_files: list[str] = field(default_factory=list)
    date_format: str = "YYYYMMDD"
    amount_unit: str = "yuan"
    price_unit: str = "yuan"
    shares_unit: str = "share"
    notice: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStatementManifest:
    statement_id: str
    account_id: str
    broker_name: str
    schema_name: str
    trade_date: str
    as_of_date: str
    source_dir: str
    source_file_hashes: dict[str, dict[str, Any]]
    imported_at: str
    record_counts: dict[str, int]
    parse_issue_count: int = 0
    warning_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStatementParseIssue:
    severity: str
    code: str
    message: str
    file_name: str = ""
    line_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerStatementValidationReport:
    statement_id: str
    status: str
    issue_count: int
    error_count: int
    warning_count: int
    issues: list[BrokerStatementParseIssue] = field(default_factory=list)
    dataset_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "status": self.status,
            "issue_count": int(self.issue_count),
            "error_count": int(self.error_count),
            "warning_count": int(self.warning_count),
            "issues": [issue.to_dict() for issue in self.issues],
            "dataset_counts": dict(self.dataset_counts),
        }


@dataclass(frozen=True)
class BrokerStatementImportResult:
    statement_id: str
    status: str
    manifest: BrokerStatementManifest
    validation: BrokerStatementValidationReport
    paths: dict[str, str]
    synthetic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "status": self.status,
            "manifest": self.manifest.to_dict(),
            "validation": self.validation.to_dict(),
            "paths": dict(self.paths),
            "synthetic": bool(self.synthetic),
        }


@dataclass(frozen=True)
class ExternalBrokerOrder:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_order_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerTrade:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_trade_id: str = ""
    external_order_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    total_fee: float = 0.0
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerFill:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_fill_id: str = ""
    broker_fill_id: str = ""
    broker_order_id: str = ""
    client_order_id: str = ""
    ts_code: str = ""
    side: str = ""
    price: float = 0.0
    shares: int = 0
    value: float = 0.0
    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    market_impact: float = 0.0
    other_fee: float = 0.0
    total_fee: float = 0.0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerPosition:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    ts_code: str
    position_shares: int
    available_shares: int = 0
    cost_basis: float = 0.0
    market_value: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerCashBalance:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    cash_balance: float
    available_cash: float = 0.0
    withdrawable_cash: float = 0.0
    frozen_cash: float = 0.0
    unsettled_receivable: float = 0.0
    unsettled_payable: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerSettlementItem:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_settlement_id: str = ""
    source_id: str = ""
    ts_code: str = ""
    event_type: str = ""
    settlement_date: str = ""
    available_date: str = ""
    cash_amount: float = 0.0
    shares: int = 0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerCorporateActionItem:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    external_action_id: str = ""
    action_id: str = ""
    ts_code: str = ""
    event_type: str = ""
    cash_amount: float = 0.0
    shares: int = 0
    status: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalBrokerAccountSnapshot:
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    cash_balance: float
    positions_value: float
    equity: float
    position_count: int
    fill_count: int
    settlement_count: int = 0
    corporate_action_count: int = 0
    synthetic: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import json
from pathlib import Path
from typing import Any



DATASET_FILES = {
    "orders": ["external_orders.csv", "external_orders.jsonl"],
    "trades": ["external_trades.csv", "external_trades.jsonl"],
    "fills": ["external_fills.csv", "external_fills.jsonl"],
    "positions": ["external_positions.csv", "external_positions.jsonl"],
    "cash": ["external_cash.csv", "external_cash.json", "external_cash.jsonl"],
    "settlements": ["external_settlements.csv", "external_settlements.jsonl"],
    "corporate_actions": ["external_corporate_actions.csv", "external_corporate_actions.jsonl"],
}


NORMALIZED_FILES = {
    "orders": "normalized_external_orders.jsonl",
    "trades": "normalized_external_trades.jsonl",
    "fills": "normalized_external_fills.jsonl",
    "positions": "normalized_external_positions.jsonl",
    "cash": "normalized_external_cash.jsonl",
    "settlements": "normalized_external_settlements.jsonl",
    "corporate_actions": "normalized_external_corporate_actions.jsonl",
}


GENERIC_FIELDS = {
    "account_id": "account_id",
    "broker_name": "broker_name",
    "trade_date": "trade_date",
    "as_of_date": "as_of_date",
    "external_order_id": "external_order_id",
    "external_trade_id": "external_trade_id",
    "external_fill_id": "external_fill_id",
    "external_settlement_id": "external_settlement_id",
    "external_action_id": "external_action_id",
    "broker_order_id": "broker_order_id",
    "broker_fill_id": "broker_fill_id",
    "client_order_id": "client_order_id",
    "settlement_event_id": "settlement_event_id",
    "source_id": "source_id",
    "source_type": "source_type",
    "action_id": "action_id",
    "ts_code": "ts_code",
    "side": "side",
    "price": "price",
    "shares": "shares",
    "value": "value",
    "commission": "commission",
    "stamp_duty": "stamp_duty",
    "transfer_fee": "transfer_fee",
    "slippage": "slippage",
    "market_impact": "market_impact",
    "other_fee": "other_fee",
    "total_fee": "total_fee",
    "cash_balance": "cash_balance",
    "available_cash": "available_cash",
    "withdrawable_cash": "withdrawable_cash",
    "frozen_cash": "frozen_cash",
    "unsettled_receivable": "unsettled_receivable",
    "unsettled_payable": "unsettled_payable",
    "position_shares": "position_shares",
    "available_shares": "available_shares",
    "cost_basis": "cost_basis",
    "market_value": "market_value",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl": "unrealized_pnl",
    "settlement_date": "settlement_date",
    "available_date": "available_date",
    "event_type": "event_type",
    "status": "status",
    "reason": "reason",
}


def default_schema(schema_name: str = "generic_broker_statement") -> BrokerStatementSchema:
    if schema_name == "qmt_statement_skeleton":
        return BrokerStatementSchema(
            schema_name=schema_name,
            field_mapping={dataset: dict(GENERIC_FIELDS) for dataset in DATASET_FILES},
            required_files=["external_cash", "external_positions"],
            optional_files=list(DATASET_FILES),
            notice=(
                "qmt_statement_skeleton is only a configurable local field-mapping skeleton; "
                "it does not guarantee compatibility with real QMT or any broker counterparty file."
            ),
        )
    if schema_name != "generic_broker_statement":
        raise ValueError(f"unsupported broker statement schema: {schema_name}")
    return BrokerStatementSchema(
        schema_name=schema_name,
        field_mapping={dataset: dict(GENERIC_FIELDS) for dataset in DATASET_FILES},
        required_files=["external_cash", "external_positions"],
        optional_files=list(DATASET_FILES),
        notice="generic_broker_statement is an internal local schema for paper reconciliation.",
    )


def load_schema(schema_name: str = "generic_broker_statement", schema_config: str | Path | None = None) -> BrokerStatementSchema:
    schema = default_schema(schema_name)
    if schema_config is None:
        return schema
    payload = json.loads(Path(schema_config).read_text(encoding="utf-8"))
    field_mapping = {key: dict(value) for key, value in schema.field_mapping.items()}
    for dataset, mapping in dict(payload.get("field_mapping") or {}).items():
        current = field_mapping.setdefault(str(dataset), {})
        current.update({str(k): str(v) for k, v in dict(mapping).items()})
    return BrokerStatementSchema(
        schema_name=str(payload.get("schema_name") or schema.schema_name),
        field_mapping=field_mapping,
        required_files=list(payload.get("required_files") or schema.required_files),
        optional_files=list(payload.get("optional_files") or schema.optional_files),
        date_format=str(payload.get("date_format") or schema.date_format),
        amount_unit=str(payload.get("amount_unit") or schema.amount_unit),
        price_unit=str(payload.get("price_unit") or schema.price_unit),
        shares_unit=str(payload.get("shares_unit") or schema.shares_unit),
        notice=str(payload.get("notice") or schema.notice),
    )


def dataset_for_filename(filename: str) -> str | None:
    for dataset, names in DATASET_FILES.items():
        if filename in names:
            return dataset
    return None


def available_source_files(source_dir: str | Path) -> dict[str, Path]:
    root = Path(source_dir)
    result: dict[str, Path] = {}
    for dataset, names in DATASET_FILES.items():
        for name in names:
            path = root / name
            if path.exists():
                result[dataset] = path
                break
    return result


def normalized_path(output_dir: str | Path, dataset: str) -> Path:
    return Path(output_dir) / NORMALIZED_FILES[dataset]

from typing import Any



FLOAT_FIELDS = {
    "price",
    "value",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "slippage",
    "market_impact",
    "other_fee",
    "total_fee",
    "cash_balance",
    "available_cash",
    "withdrawable_cash",
    "frozen_cash",
    "unsettled_receivable",
    "unsettled_payable",
    "cost_basis",
    "market_value",
    "realized_pnl",
    "unrealized_pnl",
}

INT_FIELDS = {"shares", "position_shares", "available_shares"}
DATE_FIELDS = {"trade_date", "as_of_date", "settlement_date", "available_date"}


def normalize_record(
    dataset: str,
    row: dict[str, Any],
    schema: BrokerStatementSchema,
    *,
    account_id: str | None,
    broker_name: str | None,
    trade_date: str | None,
    as_of_date: str | None,
    file_name: str,
    line_number: int | None,
) -> tuple[dict[str, Any], list[BrokerStatementParseIssue]]:
    mapping = schema.field_mapping.get(dataset, {})
    issues: list[BrokerStatementParseIssue] = []
    normalized: dict[str, Any] = {}
    for target, source in mapping.items():
        if source in row and row.get(source) not in {"", None}:
            normalized[target] = row.get(source)
    normalized.setdefault("account_id", account_id or row.get("account_id") or "")
    normalized.setdefault("broker_name", broker_name or row.get("broker_name") or "")
    normalized.setdefault("trade_date", trade_date or row.get("trade_date") or "")
    normalized.setdefault("as_of_date", as_of_date or row.get("as_of_date") or normalized.get("trade_date") or "")
    normalized["side"] = _normalize_side(normalized.get("side"))
    normalized["status"] = _normalize_status(normalized.get("status"))
    for field in DATE_FIELDS:
        if field in normalized:
            normalized[field] = _normalize_date(normalized.get(field))
    for field in FLOAT_FIELDS:
        if field in normalized:
            value, issue = _to_float(normalized.get(field), field, file_name, line_number)
            normalized[field] = value
            if issue:
                issues.append(issue)
    for field in INT_FIELDS:
        if field in normalized:
            value, issue = _to_int(normalized.get(field), field, file_name, line_number)
            normalized[field] = value
            if issue:
                issues.append(issue)
    if dataset in {"fills", "trades"}:
        normalized.setdefault("total_fee", _fee_total(normalized))
    return normalized, issues


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    aliases = {"B": "BUY", "BUY": "BUY", "买": "BUY", "买入": "BUY", "S": "SELL", "SELL": "SELL", "卖": "SELL", "卖出": "SELL"}
    return aliases.get(text, text)


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().upper()
    aliases = {
        "成交": "FILLED",
        "已成": "FILLED",
        "FILLED": "FILLED",
        "PARTIAL": "PARTIAL",
        "部分成交": "PARTIAL",
        "REJECTED": "REJECTED",
        "废单": "REJECTED",
        "CANCELLED": "CANCELLED",
        "已撤": "CANCELLED",
    }
    return aliases.get(text, text)


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if "-" in text:
        parts = text.split("T", 1)[0].split("-")
        if len(parts) == 3:
            return "".join(parts)
    return text


def _to_float(value: Any, field: str, file_name: str, line_number: int | None) -> tuple[float, BrokerStatementParseIssue | None]:
    try:
        if value in {"", None}:
            return 0.0, None
        return float(value), None
    except (TypeError, ValueError):
        return 0.0, BrokerStatementParseIssue("error", "malformed_number", f"field {field} is not numeric", file_name, line_number)


def _to_int(value: Any, field: str, file_name: str, line_number: int | None) -> tuple[int, BrokerStatementParseIssue | None]:
    try:
        if value in {"", None}:
            return 0, None
        return int(float(value)), None
    except (TypeError, ValueError):
        return 0, BrokerStatementParseIssue("error", "malformed_number", f"field {field} is not integer", file_name, line_number)


def _fee_total(row: dict[str, Any]) -> float:
    if float(row.get("total_fee", 0.0) or 0.0):
        return float(row.get("total_fee", 0.0) or 0.0)
    fields = ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee"]
    return float(sum(float(row.get(field, 0.0) or 0.0) for field in fields))

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_statement_import_report(result: BrokerStatementImportResult, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = write_json_artifact(
        root / "broker_statement_manifest.json",
        result.manifest.to_dict(),
        artifact_type="broker_statement_manifest",
        producer="broker_statement",
    )
    report_path = write_json_artifact(
        root / "broker_statement_import_report.json",
        {
            "statement_id": result.statement_id,
            "status": result.status,
            "schema_name": result.manifest.schema_name,
            "account_id": result.manifest.account_id,
            "broker_name": result.manifest.broker_name,
            "trade_date": result.manifest.trade_date,
            "as_of_date": result.manifest.as_of_date,
            "record_counts": result.manifest.record_counts,
            "parse_issue_count": result.manifest.parse_issue_count,
            "warning_count": result.manifest.warning_count,
            "synthetic": result.synthetic,
            "paths": result.paths,
        },
        artifact_type="broker_statement_import_report",
        producer="broker_statement",
    )
    validation_path = write_json_artifact(
        root / "broker_statement_validation_report.json",
        result.validation.to_dict(),
        artifact_type="broker_statement_validation_report",
        producer="broker_statement",
    )
    issues_path = write_jsonl_artifact(
        root / "broker_statement_parse_issues.jsonl",
        [issue.to_dict() for issue in result.validation.issues],
        artifact_type="broker_statement_parse_issues",
        producer="broker_statement",
    )
    md_path = root / "broker_statement_import_report.md"
    md_path.write_text(_markdown(result), encoding="utf-8")
    return {
        "broker_statement_manifest_path": manifest_path,
        "broker_statement_import_report_path": report_path,
        "broker_statement_validation_report_path": validation_path,
        "broker_statement_parse_issues_path": issues_path,
        "broker_statement_import_report_md_path": md_path,
    }


def _markdown(result: BrokerStatementImportResult) -> str:
    manifest = result.manifest
    lines = [
        "# Broker Statement Import Report",
        "",
        f"- statement_id: `{result.statement_id}`",
        f"- status: `{result.status}`",
        f"- schema: `{manifest.schema_name}`",
        f"- account_id: `{manifest.account_id}`",
        f"- broker_name: `{manifest.broker_name}`",
        f"- trade_date: `{manifest.trade_date}`",
        f"- as_of_date: `{manifest.as_of_date}`",
        f"- synthetic: `{result.synthetic}`",
        "",
        "## Record Counts",
        "",
        "| dataset | records |",
        "| --- | ---: |",
    ]
    for dataset, count in sorted(manifest.record_counts.items()):
        lines.append(f"| {dataset} | {count} |")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- errors: `{result.validation.error_count}`",
            f"- warnings: `{result.validation.warning_count}`",
        ]
    )
    return "\n".join(lines) + "\n"

from pathlib import Path
from typing import Any



def validate_statement(
    statement_id: str,
    normalized: dict[str, list[dict[str, Any]]],
    parse_issues: list[BrokerStatementParseIssue],
    *,
    as_of_date: str,
    strict: bool = False,
) -> BrokerStatementValidationReport:
    issues = list(parse_issues)
    counts = {dataset: len(rows) for dataset, rows in normalized.items()}
    if not normalized.get("cash"):
        issues.append(BrokerStatementParseIssue("error" if strict else "warning", "missing_cash_statement", "cash statement is missing"))
    if not normalized.get("positions"):
        issues.append(
            BrokerStatementParseIssue("error" if strict else "warning", "missing_position_statement", "position statement is missing")
        )
    _duplicate_id_check(issues, normalized.get("orders", []), "external_order_id", "duplicate_external_order_id")
    _duplicate_id_check(issues, normalized.get("trades", []), "external_trade_id", "duplicate_external_trade_id")
    _duplicate_id_check(issues, normalized.get("fills", []), "external_fill_id", "duplicate_external_fill_id")
    for dataset, rows in normalized.items():
        for index, row in enumerate(rows, start=1):
            if str(row.get("as_of_date") or "") and str(row.get("as_of_date")) != as_of_date:
                issues.append(
                    BrokerStatementParseIssue(
                        "warning",
                        "trade_date_as_of_date_mismatch",
                        f"{dataset} row has as_of_date {row.get('as_of_date')} instead of {as_of_date}",
                        line_number=index,
                    )
                )
            if dataset in {"orders", "trades", "fills"} and row.get("side") not in {"BUY", "SELL", ""}:
                issues.append(BrokerStatementParseIssue("error", "invalid_side", "side must be BUY or SELL", line_number=index))
            if dataset in {"orders", "trades", "fills"} and row.get("status") not in {"FILLED", "PARTIAL", "REJECTED", "CANCELLED", ""}:
                issues.append(BrokerStatementParseIssue("warning", "invalid_status", "status is not a known local broker status", line_number=index))
            if dataset == "positions" and int(row.get("position_shares", 0) or 0) < 0:
                issues.append(BrokerStatementParseIssue("error", "negative_position_shares", "position shares cannot be negative", line_number=index))
            if dataset == "cash" and float(row.get("cash_balance", 0.0) or 0.0) < 0:
                issues.append(BrokerStatementParseIssue("warning", "negative_cash_balance", "cash balance is negative", line_number=index))
    error_count = sum(1 for issue in issues if issue.severity in {"error", "blocker"})
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    status = "error" if error_count else ("warning" if warning_count else "ok")
    return BrokerStatementValidationReport(
        statement_id=statement_id,
        status=status,
        issue_count=len(issues),
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        dataset_counts=counts,
    )


def _duplicate_id_check(issues: list[BrokerStatementParseIssue], rows: list[dict[str, Any]], field: str, code: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = str(row.get(field) or "")
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    for value in sorted(duplicates):
        issues.append(BrokerStatementParseIssue("error", code, f"duplicate external id: {value}", metadata={"field": field, "value": value}))


def issue_counts_by_code(issues: list[BrokerStatementParseIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return counts


def validate_statement_dir(path: str | Path, statement_id: str = "statement", as_of_date: str = "", strict: bool = False) -> BrokerStatementValidationReport:
    from auto_alpha.execution.broker.statements import read_normalized_statement

    return validate_statement(statement_id, read_normalized_statement(path), [], as_of_date=as_of_date, strict=strict)

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_jsonl_artifact



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
    imported_at = _statements_importer_utc_now()
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
        result[dataset] = _statements_importer_read_jsonl(path)
    return result


def _read_rows(path: Path) -> tuple[list[dict[str, Any]], list[BrokerStatementParseIssue]]:
    issues: list[BrokerStatementParseIssue] = []
    try:
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)], issues
        if path.suffix == ".jsonl":
            return _statements_importer_read_jsonl(path), issues
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


def _statements_importer_read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _statements_importer_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.execution.broker.adapter import LocalBrokerStore
from auto_alpha.execution.trading.paper import LocalPaperAccount


def synthesize_statement_from_internal(
    output_dir: str | Path,
    broker_store_dir: str | Path | None = None,
    broker_batch_id: str | None = None,
    paper_account_dir: str | Path | None = None,
    settlement_dir: str | Path | None = None,
    account_id: str = "paper_ashare",
    broker_name: str = "synthetic_broker",
    trade_date: str = "",
    as_of_date: str = "",
    inject_cash_diff: float = 0.0,
    inject_position_diff: list[str] | None = None,
    drop_fill: list[str] | None = None,
    duplicate_fill: list[str] | None = None,
    inject_fee_diff: list[str] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    account = LocalPaperAccount(paper_account_dir or output / "missing_account").load_state()
    store = LocalBrokerStore(broker_store_dir or output / "missing_broker")
    broker_fills = [fill.to_dict() for fill in store.load_fills(batch_id=broker_batch_id)]
    drop_ids = set(drop_fill or [])
    duplicate_ids = set(duplicate_fill or [])
    fee_diffs = _parse_id_amounts(inject_fee_diff or [])
    fills: list[dict[str, Any]] = []
    for fill in broker_fills:
        fill_id = str(fill.get("broker_fill_id") or "")
        if fill_id in drop_ids:
            continue
        record = _fill_record(fill, account_id, broker_name, trade_date, as_of_date)
        if fill_id in fee_diffs:
            record["other_fee"] = float(record.get("other_fee", 0.0) or 0.0) + fee_diffs[fill_id]
            record["total_fee"] = float(record.get("total_fee", 0.0) or 0.0) + fee_diffs[fill_id]
        fills.append(record)
        if fill_id in duplicate_ids:
            duplicate = dict(record)
            duplicate["external_fill_id"] = f"{record.get('external_fill_id')}_dup"
            fills.append(duplicate)
    orders = [_order_record(order.to_dict(), account_id, broker_name, trade_date, as_of_date) for order in store.load_orders(batch_id=broker_batch_id)]
    positions = [_position_record(position.to_dict(), account_id, broker_name, trade_date, as_of_date) for position in account.positions.values()]
    position_diffs = _parse_id_amounts(inject_position_diff or [])
    for position in positions:
        ts_code = str(position.get("ts_code") or "")
        if ts_code in position_diffs:
            delta = int(position_diffs[ts_code])
            position["position_shares"] = int(position.get("position_shares", 0) or 0) + delta
            position["available_shares"] = int(position.get("available_shares", 0) or 0) + delta
    cash = [
        {
            "account_id": account_id,
            "broker_name": broker_name,
            "trade_date": trade_date,
            "as_of_date": as_of_date or trade_date,
            "cash_balance": float(account.cash) + float(inject_cash_diff),
            "available_cash": float(account.available_cash if account.available_cash is not None else account.cash) + float(inject_cash_diff),
            "withdrawable_cash": float(account.withdrawable_cash if account.withdrawable_cash is not None else account.cash) + float(inject_cash_diff),
            "frozen_cash": float(account.frozen_cash),
            "unsettled_receivable": float(account.unsettled_receivable),
            "unsettled_payable": float(account.unsettled_payable),
        }
    ]
    settlements = [
        _settlement_record(event, account_id, broker_name, trade_date, as_of_date)
        for event in _statements_synthesizer_read_jsonl(Path(settlement_dir or "") / "settlement_events.jsonl")
    ]
    corporate_actions = [
        _corporate_action_record(entry, account_id, broker_name, trade_date, as_of_date)
        for entry in account.corporate_action_ledger
    ]
    paths = {
        "external_orders_path": _write_jsonl(output / "external_orders.jsonl", orders),
        "external_fills_path": _write_jsonl(output / "external_fills.jsonl", fills),
        "external_positions_path": _write_jsonl(output / "external_positions.jsonl", positions),
        "external_cash_path": _write_jsonl(output / "external_cash.jsonl", cash),
        "external_settlements_path": _write_jsonl(output / "external_settlements.jsonl", settlements),
        "external_corporate_actions_path": _write_jsonl(output / "external_corporate_actions.jsonl", corporate_actions),
    }
    manifest = {
        "synthetic": True,
        "created_at": _statements_synthesizer_utc_now(),
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": trade_date,
        "as_of_date": as_of_date or trade_date,
        "broker_batch_id": broker_batch_id,
        "record_counts": {
            "orders": len(orders),
            "fills": len(fills),
            "positions": len(positions),
            "cash": len(cash),
            "settlements": len(settlements),
            "corporate_actions": len(corporate_actions),
        },
        "injections": {
            "cash_diff": float(inject_cash_diff),
            "position_diff": list(inject_position_diff or []),
            "drop_fill": list(drop_fill or []),
            "duplicate_fill": list(duplicate_fill or []),
            "fee_diff": list(inject_fee_diff or []),
        },
        "paths": {key: str(value) for key, value in paths.items()},
    }
    (output / "synthetic_statement_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _fill_record(fill: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    total_fee = float(fill.get("cost", 0.0) or 0.0)
    if not total_fee:
        total_fee = sum(float(fill.get(field, 0.0) or 0.0) for field in ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee"])
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(fill.get("trade_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_fill_id": str(fill.get("broker_fill_id") or ""),
        "broker_fill_id": str(fill.get("broker_fill_id") or ""),
        "broker_order_id": str(fill.get("broker_order_id") or ""),
        "client_order_id": str(fill.get("client_order_id") or ""),
        "ts_code": str(fill.get("ts_code") or ""),
        "side": str(fill.get("side") or ""),
        "price": float(fill.get("price", 0.0) or 0.0),
        "shares": int(fill.get("shares", 0) or 0),
        "value": float(fill.get("value", 0.0) or 0.0),
        "commission": float(fill.get("commission", 0.0) or 0.0),
        "stamp_duty": float(fill.get("stamp_duty", 0.0) or 0.0),
        "transfer_fee": float(fill.get("transfer_fee", 0.0) or 0.0),
        "slippage": float(fill.get("slippage", 0.0) or 0.0),
        "market_impact": float(fill.get("market_impact", 0.0) or 0.0),
        "other_fee": float(fill.get("other_fee", 0.0) or 0.0),
        "total_fee": float(total_fee),
        "status": str(fill.get("status") or ""),
        "reason": str(fill.get("reason") or ""),
    }


def _order_record(order: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    request = order.get("request") if isinstance(order.get("request"), dict) else {}
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(request.get("trade_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_order_id": str(order.get("broker_order_id") or ""),
        "broker_order_id": str(order.get("broker_order_id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "ts_code": str(request.get("ts_code") or ""),
        "side": str(request.get("side") or ""),
        "price": float(request.get("price", 0.0) or 0.0),
        "shares": int(order.get("requested_shares", 0) or 0),
        "value": float(order.get("requested_value", 0.0) or 0.0),
        "status": str(order.get("status") or ""),
        "reason": str(order.get("reject_reason") or order.get("cancel_reason") or ""),
    }


def _position_record(position: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": trade_date,
        "as_of_date": as_of_date or trade_date,
        "ts_code": str(position.get("ts_code") or ""),
        "position_shares": int(position.get("shares", 0) or 0),
        "available_shares": int(position.get("available_shares", position.get("shares", 0)) or 0),
        "cost_basis": float(position.get("avg_cost", 0.0) or 0.0),
        "market_value": float(position.get("market_value", 0.0) or 0.0),
        "realized_pnl": float(position.get("realized_pnl", 0.0) or 0.0),
        "unrealized_pnl": float(position.get("unrealized_pnl", 0.0) or 0.0),
    }


def _settlement_record(event: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(event.get("trade_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_settlement_id": str(event.get("settlement_event_id") or ""),
        "source_id": str(event.get("source_id") or ""),
        "ts_code": str(event.get("ts_code") or ""),
        "event_type": str(event.get("event_type") or ""),
        "settlement_date": str(event.get("settle_date") or ""),
        "available_date": str(event.get("available_date") or ""),
        "cash_amount": float(event.get("cash_amount", 0.0) or 0.0),
        "shares": int(event.get("shares", 0) or 0),
        "status": str(event.get("status") or ""),
        "reason": str(event.get("reason") or ""),
    }


def _corporate_action_record(entry: Any, account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    payload = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(payload.get("apply_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_action_id": str(payload.get("action_id") or ""),
        "action_id": str(payload.get("action_id") or ""),
        "ts_code": str(payload.get("ts_code") or ""),
        "event_type": str(payload.get("event_type") or ""),
        "cash_amount": float(payload.get("cash_amount", 0.0) or 0.0),
        "shares": int(payload.get("shares_after", 0) or 0) - int(payload.get("shares_before", 0) or 0),
        "status": str(payload.get("status") or ""),
        "reason": str(payload.get("reason") or ""),
    }


def _parse_id_amounts(items: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in items:
        if ":" not in str(item):
            continue
        key, value = str(item).split(":", 1)
        try:
            result[key] = float(value)
        except ValueError:
            continue
    return result


def _statements_synthesizer_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return str(path)


def _statements_synthesizer_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import argparse
import json
from pathlib import Path



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import or synthesize generic broker statement files.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("import", "validate", "synthesize-from-internal", "show-summary"):
        command = sub.add_parser(name)
        command.add_argument("--source-dir")
        command.add_argument("--output-dir")
        command.add_argument("--schema-name", default="generic_broker_statement", choices=["generic_broker_statement", "qmt_statement_skeleton"])
        command.add_argument("--schema-config")
        command.add_argument("--account-id")
        command.add_argument("--broker-name")
        command.add_argument("--trade-date")
        command.add_argument("--as-of-date")
        command.add_argument("--broker-store-dir")
        command.add_argument("--broker-batch-id")
        command.add_argument("--paper-account-dir")
        command.add_argument("--settlement-dir")
        command.add_argument("--inject-cash-diff", type=float, default=0.0)
        command.add_argument("--inject-position-diff", action="append", default=[])
        command.add_argument("--drop-fill", action="append", default=[])
        command.add_argument("--duplicate-fill", action="append", default=[])
        command.add_argument("--inject-fee-diff", action="append", default=[])
        command.add_argument("--strict", action="store_true")
        command.add_argument("--fail-on-error", action="store_true")
        command.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "synthesize-from-internal":
        if not args.output_dir:
            raise ValueError("--output-dir is required")
        payload = synthesize_statement_from_internal(
            output_dir=args.output_dir,
            broker_store_dir=args.broker_store_dir,
            broker_batch_id=args.broker_batch_id,
            paper_account_dir=args.paper_account_dir,
            settlement_dir=args.settlement_dir,
            account_id=args.account_id or "paper_ashare",
            broker_name=args.broker_name or "synthetic_broker",
            trade_date=args.trade_date or "",
            as_of_date=args.as_of_date or args.trade_date or "",
            inject_cash_diff=args.inject_cash_diff,
            inject_position_diff=args.inject_position_diff,
            drop_fill=args.drop_fill,
            duplicate_fill=args.duplicate_fill,
            inject_fee_diff=args.inject_fee_diff,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "import":
        if not args.source_dir or not args.output_dir:
            raise ValueError("--source-dir and --output-dir are required")
        result = import_statement(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
            schema_config=args.schema_config,
            account_id=args.account_id,
            broker_name=args.broker_name,
            trade_date=args.trade_date,
            as_of_date=args.as_of_date,
            schema_name=args.schema_name,
            strict=args.strict,
        )
        payload = result.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1 if args.fail_on_error and result.validation.error_count else 0
    if args.command == "validate":
        source = args.source_dir or args.output_dir
        if not source:
            raise ValueError("--source-dir or --output-dir is required")
        report = validate_statement_dir(source, as_of_date=args.as_of_date or "", strict=args.strict)
        payload = report.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1 if args.fail_on_error and report.error_count else 0
    if args.command == "show-summary":
        source = Path(args.source_dir or args.output_dir or ".")
        payload = {
            "source_dir": str(source),
            "datasets": {dataset: len(rows) for dataset, rows in read_normalized_statement(source).items()},
        }
        manifest_path = source / "broker_statement_manifest.json"
        if manifest_path.exists():
            payload["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "BrokerStatementImportResult",
    "BrokerStatementManifest",
    "BrokerStatementParseIssue",
    "BrokerStatementSchema",
    "BrokerStatementValidationReport",
    "ExternalBrokerAccountSnapshot",
    "ExternalBrokerCashBalance",
    "ExternalBrokerCorporateActionItem",
    "ExternalBrokerFill",
    "ExternalBrokerOrder",
    "ExternalBrokerPosition",
    "ExternalBrokerSettlementItem",
    "ExternalBrokerTrade",
    "default_schema",
    "import_statement",
    "load_schema",
    "read_normalized_statement",
    "synthesize_statement_from_internal",
    "validate_statement",
    "validate_statement_dir",
]
