"""Independent, profile-scoped A-share data admission."""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_generation,
    sha256_file,
    validate_generation,
)
from auto_alpha.platform.artifacts import storage as artifact_storage
from auto_alpha.platform.governance.network import signing as receipt_signing
from auto_alpha.platform.governance.network.signing import (
    ReceiptSigningError,
    verify_signature,
)


PROFILE_SCHEMA = "data_admission_profile_v1"
COVERAGE_PLAN_SCHEMA = "data_coverage_plan_v1"
COVERAGE_EVIDENCE_SCHEMA = "data_coverage_evidence_v1"
COVERAGE_ATTEMPT_SCHEMA = "data_coverage_attempt_started_v1"
COVERAGE_RECEIPT_SCHEMA = "data_coverage_receipt_v1"
COVERAGE_EVIDENCE_MANIFEST = "coverage_evidence_manifest.json"
ADMISSION_VERDICT_SCHEMA = "data_admission_verdict_v1"
ADMISSION_VERDICT_MANIFEST = "data_admission_verdict.json"
SOURCE_FREEZE_SCHEMA = "ashare_source_freeze_generation_v1"
LEGACY_SOURCE_FREEZE_SCHEMA = "canonical_ashare_research_freeze_v1"


_FIRST_PROFILE_ROLES: dict[str, tuple[str, ...]] = {
    "base-required": (
        "securities",
        "trade_calendar",
        "daily_bars",
        "daily_basic",
        "daily_limits",
        "adjustment_factors",
        "index_members",
        "corporate_actions",
        "index_daily_bars",
        "suspensions",
        "st_status_daily",
    ),
    "feature-family-conditional": (
        "financial_features",
        "index_daily_basic",
        "industry_members",
        "name_changes",
        "new_shares",
        "income_statements",
        "balance_sheets",
        "cashflow_statements",
        "earnings_forecasts",
        "earnings_express",
        "disclosure_calendar",
        "moneyflow",
        "margin_summary",
        "margin_detail",
        "top_list",
        "top_inst",
        "block_trades",
        "holder_number",
        "top10_holders",
        "top10_float_holders",
        "repurchases",
        "share_unlocks",
        "hk_holdings",
    ),
    "inactive": (
        "index_basic",
        "industry_classification",
        "financial_audit",
        "main_business",
        "holder_trades",
        "pledge_detail",
        "pledge_stat",
    ),
}

_FIRST_PROFILE_GRANULARITY: dict[str, str] = {
    "securities": "market_span",
    "trade_calendar": "exchange_span",
    "daily_bars": "security_day",
    "daily_basic": "security_day",
    "daily_limits": "security_day",
    "adjustment_factors": "security_day",
    "index_members": "index_day",
    "corporate_actions": "security_span",
    "index_daily_bars": "index_day",
    "suspensions": "security_day",
    "st_status_daily": "security_day",
    "financial_features": "security_day",
    "index_daily_basic": "index_day",
    "industry_members": "security_day",
    "name_changes": "security_span",
    "new_shares": "security_span",
    "income_statements": "security_span",
    "balance_sheets": "security_span",
    "cashflow_statements": "security_span",
    "earnings_forecasts": "security_span",
    "earnings_express": "security_span",
    "disclosure_calendar": "security_span",
    "moneyflow": "security_day",
    "margin_summary": "exchange_day",
    "margin_detail": "security_day",
    "top_list": "security_day",
    "top_inst": "security_day",
    "block_trades": "security_day",
    "holder_number": "security_span",
    "top10_holders": "security_span",
    "top10_float_holders": "security_span",
    "repurchases": "security_span",
    "share_unlocks": "security_span",
    "hk_holdings": "security_day",
    "index_basic": "index_span",
    "industry_classification": "security_span",
    "financial_audit": "security_span",
    "main_business": "security_span",
    "holder_trades": "security_span",
    "pledge_detail": "security_span",
    "pledge_stat": "security_span",
}

_FIRST_PROFILE_FAMILY: dict[str, str] = {
    "financial_features": "pit_financial",
    "index_daily_basic": "benchmark_valuation",
    "industry_members": "pit_industry",
    "name_changes": "security_events",
    "new_shares": "security_events",
    "income_statements": "pit_financial",
    "balance_sheets": "pit_financial",
    "cashflow_statements": "pit_financial",
    "earnings_forecasts": "earnings_events",
    "earnings_express": "earnings_events",
    "disclosure_calendar": "earnings_events",
    "moneyflow": "moneyflow",
    "margin_summary": "margin",
    "margin_detail": "margin",
    "top_list": "abnormal_trading",
    "top_inst": "abnormal_trading",
    "block_trades": "block_trading",
    "holder_number": "holder_structure",
    "top10_holders": "holder_structure",
    "top10_float_holders": "holder_structure",
    "repurchases": "shareholder_events",
    "share_unlocks": "shareholder_events",
    "hk_holdings": "northbound_holdings",
}

_FIRST_PROFILE_APPROVED_FIELDS: dict[str, tuple[str, ...]] = {
    "securities": (
        "ts_code",
        "symbol",
        "exchange",
        "board",
        "list_date",
        "delist_date",
        "list_status",
    ),
    "trade_calendar": ("exchange", "trade_date", "is_open", "prev_trade_date"),
    "daily_bars": (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume",
        "amount",
    ),
    "daily_basic": (
        "ts_code",
        "trade_date",
        "turnover_rate",
        "volume_ratio",
        "total_mv",
    ),
    "daily_limits": ("ts_code", "trade_date", "up_limit", "down_limit", "pre_close"),
    "adjustment_factors": ("ts_code", "trade_date", "adj_factor"),
    "index_members": ("index_code", "trade_date", "ts_code", "weight"),
    "corporate_actions": (
        "ts_code",
        "report_period",
        "ann_date",
        "implementation_date",
        "record_date",
        "ex_date",
        "pay_date",
        "list_date",
        "process_state",
        "base_shares",
        "cash_dividend",
        "stock_dividend_ratio",
        "stock_transfer_ratio",
    ),
    "index_daily_bars": (
        "index_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume",
        "amount",
    ),
    "suspensions": ("ts_code", "trade_date", "suspend_type", "suspend_timing"),
    "st_status_daily": ("ts_code", "trade_date", "type", "type_name"),
    "financial_features": (
        "security_id",
        "report_period",
        "announced_at",
        "roe",
        "revenue_yoy",
        "net_profit_yoy",
        "gross_margin",
        "debt_to_assets",
    ),
    "index_daily_basic": ("index_code", "trade_date", "pe", "pb", "turnover_rate"),
    "industry_members": ("security_id", "industry_code", "effective_from", "effective_to", "observed_at"),
    "name_changes": ("security_id", "name", "effective_from", "effective_to", "announced_at", "event_version"),
    "new_shares": ("security_id", "ipo_date", "issue_price", "observed_at"),
    "income_statements": (
        "security_id",
        "report_period",
        "announced_at",
        "revenue",
        "operating_profit",
        "net_profit",
        "event_version",
    ),
    "balance_sheets": (
        "security_id",
        "report_period",
        "announced_at",
        "total_assets",
        "total_liabilities",
        "equity",
        "event_version",
    ),
    "cashflow_statements": (
        "security_id",
        "report_period",
        "announced_at",
        "operating_cashflow",
        "investing_cashflow",
        "financing_cashflow",
        "event_version",
    ),
    "earnings_forecasts": (
        "security_id",
        "report_period",
        "announced_at",
        "forecast_type",
        "profit_change_low",
        "profit_change_high",
        "event_version",
    ),
    "earnings_express": ("security_id", "report_period", "announced_at", "revenue", "net_profit", "event_version"),
    "disclosure_calendar": (
        "security_id",
        "report_period",
        "scheduled_date",
        "actual_date",
        "observed_at",
        "event_version",
    ),
    "moneyflow": (
        "security_id",
        "trade_date",
        "small_net_inflow",
        "medium_net_inflow",
        "large_net_inflow",
        "extra_large_net_inflow",
    ),
    "margin_summary": ("exchange", "trade_date", "financing_balance", "securities_lending_balance"),
    "margin_detail": ("security_id", "trade_date", "financing_balance", "securities_lending_balance"),
    "top_list": ("security_id", "trade_date", "reason", "net_buy_amount"),
    "top_inst": ("security_id", "trade_date", "institution_type", "buy_amount", "sell_amount"),
    "block_trades": ("security_id", "trade_date", "price", "volume", "amount", "buyer", "seller"),
    "holder_number": ("security_id", "report_date", "announced_at", "holder_count", "event_version"),
    "top10_holders": ("security_id", "report_period", "announced_at", "holder_id", "holding_ratio", "event_version"),
    "top10_float_holders": (
        "security_id",
        "report_period",
        "announced_at",
        "holder_id",
        "holding_ratio",
        "event_version",
    ),
    "repurchases": ("security_id", "announced_at", "progress", "volume", "amount", "event_version"),
    "share_unlocks": ("security_id", "announced_at", "unlock_date", "unlock_shares", "event_version"),
    "hk_holdings": ("security_id", "trade_date", "holding_shares", "holding_ratio"),
}

_BASE_CONSUMER_ROLES: dict[str, tuple[str, ...]] = {
    "securities": ("identity", "lifecycle", "universe_control"),
    "trade_calendar": ("date_axis", "scheduling_control"),
    "daily_bars": ("formula_input", "target", "execution", "capacity"),
    "daily_basic": ("formula_input", "size_control", "turnover_capacity_control"),
    "daily_limits": ("tradability_control", "execution"),
    "adjustment_factors": ("causal_price_adjustment", "target"),
    "index_members": ("pit_universe_control",),
    "corporate_actions": ("target_control", "execution_control"),
    "index_daily_bars": ("benchmark_control",),
    "suspensions": ("tradability_control", "execution_control"),
    "st_status_daily": ("eligibility_control",),
}

_OBSERVED_EMPTY_ALLOWED = {
    "securities",
    "corporate_actions",
    "suspensions",
    "st_status_daily",
    "name_changes",
    "new_shares",
    "earnings_forecasts",
    "earnings_express",
    "disclosure_calendar",
    "top_list",
    "top_inst",
    "block_trades",
    "repurchases",
    "share_unlocks",
}


class AdmissionVerificationError(RuntimeError):
    """The verifier could not evaluate a malformed request or evidence set."""


@dataclass(frozen=True)
class DataAdmissionScope:
    access_view: str
    date_start: str
    date_end: str
    as_of_market_date: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityLifecycle:
    security_id: str
    list_date: str
    delist_date: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class CoveragePopulation:
    securities: tuple[SecurityLifecycle, ...]
    trading_dates: tuple[str, ...]
    exchanges: tuple[str, ...] = ()
    index_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "securities": [row.to_dict() for row in self.securities],
            "trading_dates": list(self.trading_dates),
            "exchanges": list(self.exchanges),
            "index_codes": list(self.index_codes),
        }


@dataclass(frozen=True)
class CoverageObligation:
    obligation_id: str
    dataset: str
    subject_kind: str
    subject: str
    date_start: str
    date_end: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _mapping_sequence(row: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = row.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return value


@dataclass(frozen=True)
class ProviderAcquisitionContract:
    provider: str
    provider_adapter: str
    endpoint: str
    provider_api_version: str
    adapter_schema_version: str
    permission_context_id: str
    capture_public_key_sha256: str
    pagination_mode: str
    row_cap: int
    allowed_retry_failure_kinds: tuple[str, ...]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ProviderAcquisitionContract":
        retry_kinds = row.get("allowed_retry_failure_kinds")
        if not isinstance(retry_kinds, Sequence) or isinstance(
            retry_kinds, (str, bytes)
        ):
            retry_kinds = ()
        return cls(
            provider=str(row.get("provider") or ""),
            provider_adapter=str(row.get("provider_adapter") or ""),
            endpoint=str(row.get("endpoint") or ""),
            provider_api_version=str(row.get("provider_api_version") or ""),
            adapter_schema_version=str(row.get("adapter_schema_version") or ""),
            permission_context_id=str(row.get("permission_context_id") or ""),
            capture_public_key_sha256=str(
                row.get("capture_public_key_sha256") or ""
            ),
            pagination_mode=str(row.get("pagination_mode") or ""),
            row_cap=(
                row["row_cap"]
                if isinstance(row.get("row_cap"), int)
                and not isinstance(row.get("row_cap"), bool)
                else -1
            ),
            allowed_retry_failure_kinds=tuple(
                str(item) for item in retry_kinds
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetAdmissionContract:
    dataset: str
    role: str
    feature_family: str
    coverage_granularity: str
    approved_fields: tuple[str, ...]
    consumer_roles: tuple[str, ...]
    evidence_grade: str
    empty_policy: str
    coverage_watermark: str
    requires_pre_span_state: bool
    record_subject_field: str
    record_date_field: str
    coverage_subjects: tuple[str, ...]
    read_only_required: bool
    max_retries: int
    max_split_leaves: int
    acquisition_contracts: tuple[ProviderAcquisitionContract, ...]
    not_applicable_authorities: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "DatasetAdmissionContract":
        approved_fields = _mapping_sequence(row, "approved_fields")
        consumer_roles = _mapping_sequence(row, "consumer_roles")
        coverage_subjects = _mapping_sequence(row, "coverage_subjects")
        acquisition_rows = _mapping_sequence(row, "acquisition_contracts")
        authority_rows = row.get("not_applicable_authorities")
        if not isinstance(authority_rows, Mapping):
            authority_rows = {}
        return cls(
            dataset=str(row.get("dataset") or ""),
            role=str(row.get("role") or ""),
            feature_family=str(row.get("feature_family") or ""),
            coverage_granularity=str(row.get("coverage_granularity") or ""),
            approved_fields=tuple(str(item) for item in approved_fields),
            consumer_roles=tuple(str(item) for item in consumer_roles),
            evidence_grade=str(row.get("evidence_grade") or ""),
            empty_policy=str(row.get("empty_policy") or ""),
            coverage_watermark=str(row.get("coverage_watermark") or ""),
            requires_pre_span_state=row.get("requires_pre_span_state") is True,
            record_subject_field=str(row.get("record_subject_field") or ""),
            record_date_field=str(row.get("record_date_field") or ""),
            coverage_subjects=tuple(str(item) for item in coverage_subjects),
            read_only_required=row.get("read_only_required") is True,
            max_retries=(
                row["max_retries"]
                if isinstance(row.get("max_retries"), int)
                and not isinstance(row.get("max_retries"), bool)
                else -1
            ),
            max_split_leaves=(
                row["max_split_leaves"]
                if isinstance(row.get("max_split_leaves"), int)
                and not isinstance(row.get("max_split_leaves"), bool)
                else -1
            ),
            acquisition_contracts=tuple(
                ProviderAcquisitionContract.from_mapping(item)
                for item in acquisition_rows
                if isinstance(item, Mapping)
            ),
            not_applicable_authorities=tuple(
                sorted(
                    (
                        str(reason),
                        tuple(sorted(str(dataset) for dataset in datasets)),
                    )
                    for reason, datasets in (
                        authority_rows.items()
                    )
                    if isinstance(datasets, Sequence)
                    and not isinstance(datasets, (str, bytes))
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "acquisition_contracts": [row.to_dict() for row in self.acquisition_contracts],
            "not_applicable_authorities": {
                reason: list(datasets)
                for reason, datasets in self.not_applicable_authorities
            },
        }


@dataclass(frozen=True)
class CoveragePlan:
    schema_version: str
    profile_id: str
    scope: DataAdmissionScope
    population: CoveragePopulation
    population_root: str
    dataset_contracts: tuple[DatasetAdmissionContract, ...]
    obligations: tuple[CoverageObligation, ...]
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "scope": self.scope.to_dict(),
            "population": self.population.to_dict(),
            "population_root": self.population_root,
            "dataset_contracts": [row.to_dict() for row in self.dataset_contracts],
            "obligations": [row.to_dict() for row in self.obligations],
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class CoverageVerification:
    """An independently recomputed exact-cover result."""

    outcome: str
    coverage_gap: int
    coverage_root: str
    blockers: tuple[str, ...]
    receipt_count: int
    satisfied_obligation_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataAdmissionBlocker:
    code: str
    dataset: str | None = None
    subject: str | None = None
    evidence_locator: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class DataAdmissionVerdict:
    verdict_id: str
    outcome: str
    profile_id: str
    source_generation_id: str
    scope: DataAdmissionScope
    coverage_plan_content_hash: str | None
    coverage_root: str | None
    data_scope_root: str | None
    blockers: tuple[DataAdmissionBlocker, ...]
    manifest_path: str
    content_hash: str

    @property
    def admitted(self) -> bool:
        return self.outcome == "admitted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict_id": self.verdict_id,
            "outcome": self.outcome,
            "profile_id": self.profile_id,
            "source_generation_id": self.source_generation_id,
            "scope": self.scope.to_dict(),
            "coverage_plan_content_hash": self.coverage_plan_content_hash,
            "coverage_root": self.coverage_root,
            "data_scope_root": self.data_scope_root,
            "blockers": [row.to_dict() for row in self.blockers],
            "manifest_path": self.manifest_path,
            "content_hash": self.content_hash,
        }


def _profile_record_subject_field(dataset: str) -> str:
    fields = _FIRST_PROFILE_APPROVED_FIELDS.get(dataset, ())
    if "index_code" in fields:
        return "index_code"
    if "exchange" in fields and not ({"ts_code", "security_id"} & set(fields)):
        return "exchange"
    if "ts_code" in fields:
        return "ts_code"
    if "security_id" in fields:
        return "security_id"
    return ""


def _profile_record_date_field(dataset: str) -> str:
    fields = _FIRST_PROFILE_APPROVED_FIELDS.get(dataset, ())
    for candidate in (
        "trade_date",
        "announced_at",
        "ann_date",
        "observed_at",
        "report_date",
        "scheduled_date",
        "ipo_date",
        "effective_from",
        "list_date",
        "report_period",
    ):
        if candidate in fields:
            return candidate
    return ""


def _profile_coverage_subjects(dataset: str) -> list[str]:
    granularity = _FIRST_PROFILE_GRANULARITY[dataset]
    if granularity == "market_span":
        return ["list_status:D", "list_status:L", "list_status:P"]
    if granularity.startswith("exchange_"):
        return ["SSE", "SZSE"]
    if granularity.startswith("index_"):
        return ["000300.SH"]
    return []


def _profile_not_applicable_authorities(dataset: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    if dataset in {
        "daily_bars",
        "daily_basic",
        "daily_limits",
        "adjustment_factors",
    }:
        rows["proven_suspension"] = ["suspensions"]
    if dataset == "daily_limits":
        rows["ipo_no_price_limit"] = ["securities"]
    return rows


def first_data_admission_profile() -> dict[str, Any]:
    """Return the content-addressed first-profile dataset-role declaration.

    This profile deliberately activates only the base price/volume and control
    closure. Its breadth thresholds and real provider evidence remain governed
    activation prerequisites rather than defaults chosen from observed targets.
    """

    datasets: list[dict[str, Any]] = []
    for role, names in _FIRST_PROFILE_ROLES.items():
        for dataset in names:
            row: dict[str, Any] = {
                "dataset": dataset,
                "role": role,
                "coverage_granularity": _FIRST_PROFILE_GRANULARITY[dataset],
                "approved_fields": list(_FIRST_PROFILE_APPROVED_FIELDS.get(dataset, ())),
                "consumer_roles": list(_BASE_CONSUMER_ROLES.get(dataset, ())),
                "evidence_grade": "inactive" if role == "inactive" else "governed_receipts",
                "empty_policy": (
                    "observed_empty_allowed"
                    if dataset in _OBSERVED_EMPTY_ALLOWED
                    else "nonempty_required"
                ),
                "coverage_watermark": (
                    "as_of_market_date"
                    if role == "base-required"
                    else "scope_end" if role == "feature-family-conditional" else "inactive"
                ),
                "record_subject_field": _profile_record_subject_field(dataset),
                "record_date_field": _profile_record_date_field(dataset),
                "coverage_subjects": (
                    [] if role == "inactive" else _profile_coverage_subjects(dataset)
                ),
                "read_only_required": role != "inactive",
                "max_retries": 2 if role != "inactive" else 0,
                "max_split_leaves": 4_096 if role != "inactive" else 0,
                # Provider-specific endpoint/schema/permission identities are
                # activated later without changing the canonical field names.
                # The first profile remains draft until Tushare evidence and
                # human approval populate these contracts.
                "acquisition_contracts": [],
                "not_applicable_authorities": _profile_not_applicable_authorities(
                    dataset
                ),
            }
            if dataset == "suspensions":
                row["requires_pre_span_state"] = True
            if role == "feature-family-conditional":
                row["feature_family"] = _FIRST_PROFILE_FAMILY[dataset]
                row["consumer_roles"] = [f"feature_family:{_FIRST_PROFILE_FAMILY[dataset]}"]
            datasets.append(row)
    semantic = _canonical_profile_semantic(
        {
            "schema_version": PROFILE_SCHEMA,
            "profile_name": "first_a_share_price_volume_research",
            "activation_status": "draft_evidence_blocked",
            "activated_feature_families": [],
            "admission_prerequisites": [
                "human_profile_activation_approval",
                "human_approved_validity_breadth_n",
                "human_approved_validity_breadth_x",
                "provider_acquisition_contract_approval",
                "capture_public_key_approval",
                "provider_coverage_receipts",
                "deterministic_freeze_replay",
            ],
            "datasets": datasets,
        }
    )
    content_hash = canonical_hash(semantic)
    return semantic | {
        "profile_id": f"dap_{content_hash[:24]}",
        "content_hash": content_hash,
    }


def compile_coverage_plan(
    profile: Mapping[str, Any],
    scope: DataAdmissionScope,
    lifecycle_population: CoveragePopulation,
) -> CoveragePlan:
    """Compile provider-neutral obligations for one immutable admission scope."""

    _validate_scope(scope)
    if profile.get("schema_version") != PROFILE_SCHEMA:
        raise AdmissionVerificationError("data_admission_profile_schema_invalid")
    profile_semantic = _canonical_profile_semantic(profile)
    profile_hash = canonical_hash(profile_semantic)
    declared_hash = str(profile.get("content_hash") or "")
    declared_id = str(profile.get("profile_id") or "")
    if declared_hash and declared_hash != profile_hash:
        raise AdmissionVerificationError("data_admission_profile_content_hash_invalid")
    profile_id = f"dap_{profile_hash[:24]}"
    if declared_id and declared_id != profile_id:
        raise AdmissionVerificationError("data_admission_profile_identity_invalid")

    population_semantic = _normalized_population(lifecycle_population)
    population_root = canonical_hash(population_semantic)
    active_families = {
        str(item) for item in profile_semantic.get("activated_feature_families") or ()
    }
    obligations: list[CoverageObligation] = []
    active_contracts: list[DatasetAdmissionContract] = []
    dataset_rows = profile_semantic.get("datasets") or ()
    if not isinstance(dataset_rows, Sequence) or isinstance(dataset_rows, (str, bytes)):
        raise AdmissionVerificationError("data_admission_profile_datasets_invalid")
    declared_families = {
        str(row.get("feature_family") or "")
        for row in dataset_rows
        if row.get("role") == "feature-family-conditional"
    }
    if "" in declared_families or not active_families <= declared_families:
        raise AdmissionVerificationError("data_admission_profile_feature_family_activation_invalid")
    for dataset_row in sorted(dataset_rows, key=lambda row: str(row.get("dataset") or "")):
        dataset = str(dataset_row.get("dataset") or "")
        role = str(dataset_row.get("role") or "")
        family = str(dataset_row.get("feature_family") or "")
        if role == "inactive" or (role == "feature-family-conditional" and family not in active_families):
            continue
        if role not in {"base-required", "feature-family-conditional"}:
            raise AdmissionVerificationError(f"data_admission_dataset_role_invalid:{dataset}")
        contract = DatasetAdmissionContract.from_mapping(dataset_row)
        active_contracts.append(contract)
        granularity = contract.coverage_granularity
        coverage_end_date = (
            scope.as_of_market_date
            if contract.coverage_watermark == "as_of_market_date"
            else scope.date_end
        )
        obligations.extend(
            _compile_dataset_obligations(
                dataset=dataset,
                granularity=granularity,
                scope=scope,
                coverage_end_date=coverage_end_date,
                population=lifecycle_population,
                coverage_subjects=contract.coverage_subjects,
            )
        )
        if contract.requires_pre_span_state:
            obligations.extend(
                _security_state_seed_obligations(
                    dataset=dataset,
                    scope=scope,
                    population=lifecycle_population,
                )
            )

    ordered = tuple(
        sorted(
            obligations,
            key=lambda row: (
                row.dataset,
                row.subject_kind,
                row.subject,
                row.date_start,
                row.date_end,
                row.obligation_id,
            ),
        )
    )
    semantic = {
        "schema_version": COVERAGE_PLAN_SCHEMA,
        "profile_id": profile_id,
        "scope": scope.to_dict(),
        "population": population_semantic,
        "population_root": population_root,
        "dataset_contracts": [row.to_dict() for row in active_contracts],
        "obligations": [row.to_dict() for row in ordered],
    }
    return CoveragePlan(
        schema_version=COVERAGE_PLAN_SCHEMA,
        profile_id=profile_id,
        scope=scope,
        population=CoveragePopulation(
            securities=tuple(
                SecurityLifecycle(**row) for row in population_semantic["securities"]
            ),
            trading_dates=tuple(population_semantic["trading_dates"]),
            exchanges=tuple(population_semantic["exchanges"]),
            index_codes=tuple(population_semantic["index_codes"]),
        ),
        population_root=population_root,
        dataset_contracts=tuple(active_contracts),
        obligations=ordered,
        content_hash=canonical_hash(semantic),
    )


def verify_coverage(
    plan: CoveragePlan,
    evidence_root: str | Path,
) -> CoverageVerification:
    """Recompute the durable attempt journal and atomic obligation exact cover."""

    root = Path(evidence_root)
    blockers: set[str] = set()
    blockers.update(_coverage_plan_blockers(plan))
    events = _load_coverage_events(root, plan, blockers)
    obligations = {row.obligation_id: row for row in plan.obligations}
    contracts_by_dataset = {row.dataset: row for row in plan.dataset_contracts}
    satisfying_pages: dict[str, list[dict[str, Any]]] = {
        obligation_id: [] for obligation_id in obligations
    }
    starts: dict[str, tuple[dict[str, Any], bool, CoverageObligation | None]] = {}
    terminals: dict[str, dict[str, Any]] = {}
    receipt_ids: set[str] = set()
    event_ids: set[str] = set()
    previous_event_hash = ""
    previous_occurred_at: datetime | None = None
    receipt_count = 0

    for ordinal, event in enumerate(events, start=1):
        event_hash = str(event.get("event_hash") or "")
        event_semantic = {key: value for key, value in event.items() if key != "event_hash"}
        common_valid = True
        if event_hash != canonical_hash(event_semantic):
            blockers.add("coverage_journal_event_hash_invalid")
            common_valid = False
        if (
            _positive_int(event.get("sequence")) != ordinal
            or str(event.get("previous_event_hash") or "") != previous_event_hash
        ):
            blockers.add("coverage_receipt_chain_invalid")
        previous_event_hash = event_hash
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in event_ids:
            blockers.add("coverage_journal_event_identity_invalid")
            common_valid = False
        event_ids.add(event_id)
        event_occurred_at = _valid_instant(str(event.get("occurred_at") or ""))
        if event_occurred_at is None or (
            previous_occurred_at is not None and event_occurred_at < previous_occurred_at
        ):
            blockers.add("coverage_journal_time_order_invalid")
            common_valid = False
        if event_occurred_at is not None:
            previous_occurred_at = event_occurred_at

        schema = str(event.get("schema_version") or "")
        if schema == COVERAGE_ATTEMPT_SCHEMA:
            attempt_id = str(event.get("attempt_id") or "")
            if (
                event.get("event_type") != "attempt_started"
                or event_id != f"attempt_started:{attempt_id}"
                or not attempt_id
                or attempt_id in starts
            ):
                blockers.add("coverage_attempt_identity_invalid")
                common_valid = False
            capture_public_key = _capture_public_key(event, blockers)
            if capture_public_key is None or not _verify_capture_signature(
                event,
                public_key_pem=capture_public_key,
                signature_field="attempt_start_signature",
            ):
                blockers.add("coverage_attempt_start_signature_invalid")
                common_valid = False
            obligation = _mapped_atomic_obligation(event, obligations, blockers)
            contract = contracts_by_dataset.get(obligation.dataset) if obligation else None
            request_valid = _validate_attempt_request(event, obligation, contract, blockers)
            retry_valid = _validate_retry_start(
                event,
                starts,
                terminals,
                contract,
                blockers,
            )
            occurred_at = _valid_instant(str(event.get("occurred_at") or ""))
            capture_started = _valid_instant(str(event.get("capture_started_at") or ""))
            if occurred_at is None or capture_started is None or occurred_at != capture_started:
                blockers.add("coverage_attempt_start_time_invalid")
                common_valid = False
            starts[attempt_id] = (
                event,
                common_valid and request_valid and retry_valid and obligation is not None,
                obligation,
            )
            continue

        if schema != COVERAGE_RECEIPT_SCHEMA:
            blockers.add("coverage_journal_event_schema_invalid")
            continue
        receipt_count += 1
        attempt_id = str(event.get("attempt_id") or "")
        receipt_id = str(event.get("receipt_id") or "")
        if (
            event.get("event_type") != "post_transport_receipt"
            or event_id != f"post_transport_receipt:{attempt_id}"
            or not receipt_id
            or receipt_id in receipt_ids
            or attempt_id in terminals
        ):
            blockers.add("coverage_receipt_identity_invalid")
            common_valid = False
        receipt_ids.add(receipt_id)
        terminals[attempt_id] = event
        start_record = starts.get(attempt_id)
        if start_record is None:
            blockers.add("coverage_receipt_attempt_start_missing")
            common_valid = False
            start_event: Mapping[str, Any] = {}
            obligation = None
            start_valid = False
        else:
            start_event, start_valid, obligation = start_record
        if (
            event.get("attempt_started_event_hash") != start_event.get("event_hash")
            or event.get("dataset") != start_event.get("dataset")
            or event.get("obligation_ids") != start_event.get("obligation_ids")
            or event.get("request") != start_event.get("request")
            or event.get("retry_of_attempt_id") != start_event.get("retry_of_attempt_id")
            or event.get("capture_started_at") != start_event.get("capture_started_at")
            or event.get("capture_public_key_pem_b64")
            != start_event.get("capture_public_key_pem_b64")
            or event.get("capture_public_key_sha256")
            != start_event.get("capture_public_key_sha256")
        ):
            blockers.add("coverage_receipt_attempt_binding_invalid")
            common_valid = False
        capture_started = _valid_instant(str(event.get("capture_started_at") or ""))
        capture_completed = _valid_instant(str(event.get("capture_completed_at") or ""))
        occurred_at = _valid_instant(str(event.get("occurred_at") or ""))
        if (
            capture_started is None
            or capture_completed is None
            or occurred_at is None
            or capture_completed < capture_started
            or occurred_at != capture_completed
        ):
            blockers.add("coverage_receipt_capture_time_invalid")
            common_valid = False
        capture_public_key = _capture_public_key(event, blockers)
        if capture_public_key is None or not _verify_capture_signature(
            event,
            public_key_pem=capture_public_key,
            signature_field="capture_signature",
        ):
            blockers.add("coverage_capture_signature_invalid")
            common_valid = False
        if not _receipt_matches_locked_page(event, start_event, blockers):
            common_valid = False

        disposition = str(event.get("terminal_disposition") or "")
        receipt_contract = contracts_by_dataset.get(str(event.get("dataset") or ""))
        disposition_valid = _validate_receipt_disposition(
            receipt=event,
            evidence_root=root,
            disposition=disposition,
            obligation=obligation,
            contract=receipt_contract,
            blockers=blockers,
        )
        if (
            common_valid
            and start_valid
            and disposition_valid
            and obligation is not None
            and disposition in {"satisfied_nonempty", "satisfied_empty", "not_applicable"}
        ):
            satisfying_pages[obligation.obligation_id].append(event)

    for attempt_id in starts:
        if attempt_id not in terminals:
            blockers.add("coverage_ambiguous_transport")

    satisfying_receipts: dict[str, list[str]] = {}
    for obligation_id, pages in satisfying_pages.items():
        receipt_ids_for_obligation = _verified_split_pages(
            obligations[obligation_id],
            pages,
            blockers,
        )
        satisfying_receipts[obligation_id] = receipt_ids_for_obligation
    blockers.update(
        _not_applicable_authority_blockers(
            plan,
            root,
            satisfying_pages,
            satisfying_receipts,
        )
    )
    blockers.update(
        _population_authority_blockers(
            plan,
            root,
            satisfying_pages,
            satisfying_receipts,
        )
    )
    coverage_gap = sum(not receipt_ids for receipt_ids in satisfying_receipts.values())
    if coverage_gap:
        blockers.add("coverage_obligation_unsatisfied")

    ordered_blockers = tuple(sorted(blockers))
    coverage_semantic = {
        "schema_version": "data_coverage_verification_v1",
        "coverage_plan_content_hash": plan.content_hash,
        "obligation_ids": sorted(obligations),
        "journal_events": events,
        "terminal_dispositions": {
            key: sorted(value) for key, value in sorted(satisfying_receipts.items())
        },
        "coverage_gap": coverage_gap,
        "evidence_grade": "governed_receipts" if not ordered_blockers else "blocked",
        "blockers": list(ordered_blockers),
    }
    satisfied_count = sum(1 for value in satisfying_receipts.values() if value)
    return CoverageVerification(
        outcome="admitted" if not ordered_blockers else "blocked",
        coverage_gap=coverage_gap,
        coverage_root=canonical_hash(coverage_semantic),
        blockers=ordered_blockers,
        receipt_count=receipt_count,
        satisfied_obligation_count=satisfied_count,
    )


def _verified_split_pages(
    obligation: CoverageObligation,
    pages: Sequence[Mapping[str, Any]],
    blockers: set[str],
) -> list[str]:
    if not pages:
        return []
    rows: list[tuple[int, int, str, str, str]] = []
    for receipt in pages:
        pagination = receipt.get("pagination")
        if not isinstance(pagination, Mapping):
            blockers.add("coverage_receipt_pagination_binding_invalid")
            return []
        ordinal = _positive_int(pagination.get("leaf_ordinal"))
        count = _positive_int(pagination.get("leaf_count"))
        if ordinal is None or count is None:
            blockers.add("coverage_receipt_pagination_binding_invalid")
            return []
        rows.append(
            (
                ordinal,
                count,
                str(pagination.get("leaf_start") or ""),
                str(pagination.get("leaf_end") or ""),
                str(receipt.get("receipt_id") or ""),
            )
        )
    counts = {row[1] for row in rows}
    ordinals = [row[0] for row in rows]
    if len(counts) != 1 or len(ordinals) != len(set(ordinals)):
        blockers.add("coverage_obligation_duplicate_satisfaction")
        return []
    leaf_count = next(iter(counts))
    ordered = sorted(rows)
    if len(ordered) != leaf_count or [row[0] for row in ordered] != list(
        range(1, leaf_count + 1)
    ):
        blockers.add("coverage_pagination_split_incomplete")
        return []
    if ordered[0][2] != obligation.date_start or ordered[-1][3] != obligation.date_end:
        blockers.add("coverage_pagination_split_geometry_invalid")
        return []
    for previous, current in zip(ordered, ordered[1:]):
        if _next_date(previous[3]) != current[2]:
            blockers.add("coverage_pagination_split_geometry_invalid")
            return []
    return [row[4] for row in ordered]


def _not_applicable_authority_blockers(
    plan: CoveragePlan,
    evidence_root: Path,
    satisfying_pages: Mapping[str, Sequence[Mapping[str, Any]]],
    satisfying_receipts: dict[str, list[str]],
) -> set[str]:
    blockers: set[str] = set()
    obligations = {row.obligation_id: row for row in plan.obligations}
    contracts = {row.dataset: row for row in plan.dataset_contracts}
    receipts_by_id = {
        str(receipt.get("receipt_id") or ""): receipt
        for pages in satisfying_pages.values()
        for receipt in pages
    }
    for obligation_id, receipt_ids in list(satisfying_receipts.items()):
        obligation = obligations[obligation_id]
        contract = contracts[obligation.dataset]
        allowed = dict(contract.not_applicable_authorities)
        valid = True
        for receipt_id in receipt_ids:
            receipt = receipts_by_id.get(receipt_id)
            if not isinstance(receipt, Mapping) or receipt.get(
                "terminal_disposition"
            ) != "not_applicable":
                continue
            evidence = receipt.get("applicability_evidence")
            reason = (
                str(evidence.get("reason") or "")
                if isinstance(evidence, Mapping)
                else ""
            )
            authority_ids = (
                evidence.get("authority_obligation_ids")
                if isinstance(evidence, Mapping)
                else ()
            )
            allowed_datasets = set(allowed.get(reason) or ())
            if (
                not isinstance(authority_ids, Sequence)
                or isinstance(authority_ids, (str, bytes))
                or not authority_ids
            ):
                valid = False
                continue
            for authority_id_value in authority_ids:
                authority_id = str(authority_id_value)
                authority = obligations.get(authority_id)
                authority_receipts = satisfying_receipts.get(authority_id) or []
                authority_positive = any(
                    receipts_by_id.get(value, {}).get("terminal_disposition")
                    == "satisfied_nonempty"
                    for value in authority_receipts
                )
                if (
                    authority is None
                    or authority.dataset not in allowed_datasets
                    or not authority_positive
                    or (
                        authority.subject_kind != "market"
                        and authority.subject != obligation.subject
                    )
                    or (
                        reason != "proven_suspension"
                        and (
                            authority.date_start > obligation.date_start
                            or authority.date_end < obligation.date_end
                        )
                    )
                    or not _applicability_reason_matches(
                        reason,
                        obligation=obligation,
                        authority=authority,
                        population=plan.population,
                        evidence_root=evidence_root,
                        obligations=obligations,
                        satisfying_pages=satisfying_pages,
                        satisfying_receipts=satisfying_receipts,
                    )
                ):
                    valid = False
        if not valid:
            satisfying_receipts[obligation_id] = []
            blockers.add("coverage_not_applicable_authority_invalid")
    return blockers


def _applicability_reason_matches(
    reason: str,
    *,
    obligation: CoverageObligation,
    authority: CoverageObligation,
    population: CoveragePopulation,
    evidence_root: Path,
    obligations: Mapping[str, CoverageObligation],
    satisfying_pages: Mapping[str, Sequence[Mapping[str, Any]]],
    satisfying_receipts: Mapping[str, Sequence[str]],
) -> bool:
    if reason == "proven_suspension":
        return (
            authority.dataset == "suspensions"
            and authority.subject == obligation.subject
            and _suspension_state_at(
                security_id=obligation.subject,
                target_date=obligation.date_start,
                evidence_root=evidence_root,
                obligations=obligations,
                satisfying_pages=satisfying_pages,
                satisfying_receipts=satisfying_receipts,
            )
            is True
        )
    if reason == "ipo_no_price_limit":
        lifecycle = next(
            (
                row
                for row in population.securities
                if row.security_id == obligation.subject
            ),
            None,
        )
        return (
            authority.dataset == "securities"
            and authority.subject_kind == "market"
            and lifecycle is not None
            and lifecycle.list_date == obligation.date_start == obligation.date_end
        )
    return False


def _suspension_state_at(
    *,
    security_id: str,
    target_date: str,
    evidence_root: Path,
    obligations: Mapping[str, CoverageObligation],
    satisfying_pages: Mapping[str, Sequence[Mapping[str, Any]]],
    satisfying_receipts: Mapping[str, Sequence[str]],
) -> bool | None:
    events: list[tuple[str, str, str]] = []
    for obligation_id, obligation in obligations.items():
        if (
            obligation.dataset != "suspensions"
            or obligation.subject != security_id
            or obligation.date_start > target_date
            or not satisfying_receipts.get(obligation_id)
        ):
            continue
        admitted_ids = set(satisfying_receipts[obligation_id])
        for receipt in satisfying_pages.get(obligation_id) or ():
            if (
                str(receipt.get("receipt_id") or "") not in admitted_ids
                or receipt.get("terminal_disposition") != "satisfied_nonempty"
            ):
                continue
            fields, items = _receipt_raw_records(receipt, evidence_root)
            if fields is None or items is None:
                return None
            try:
                subject_index = fields.index("ts_code")
                date_index = fields.index("trade_date")
                type_index = fields.index("suspend_type")
                timing_index = fields.index("suspend_timing")
            except ValueError:
                return None
            for item in items:
                event_date = str(item[date_index] or "")[:8]
                event_type = str(item[type_index] or "")
                timing = str(item[timing_index] or "")
                if (
                    str(item[subject_index] or "") != security_id
                    or _valid_date(event_date) is None
                    or event_date > target_date
                    or event_type not in {"S", "R"}
                    or timing
                    not in {"", "unknown", "before_open", "intraday", "after_close"}
                ):
                    return None
                events.append((event_date, event_type, timing))
    by_date: dict[str, list[tuple[str, str]]] = {}
    for event_date, event_type, timing in events:
        by_date.setdefault(event_date, []).append((event_type, timing))
    if any(len(rows) != 1 for rows in by_date.values()):
        return None
    suspended_before_day = False
    for event_date in sorted(by_date):
        event_type, timing = by_date[event_date][0]
        if event_date < target_date:
            suspended_before_day = event_type == "S"
            continue
        if event_type == "S":
            return suspended_before_day or timing == "before_open"
        # A resumption proves a full-day suspension only when it occurs after
        # the session. Unknown or intraday timing cannot excuse missing daily
        # market data; before-open resumption makes the security tradable.
        return suspended_before_day and timing == "after_close"
    return suspended_before_day


def _population_authority_blockers(
    plan: CoveragePlan,
    evidence_root: Path,
    satisfying_pages: Mapping[str, Sequence[Mapping[str, Any]]],
    satisfying_receipts: Mapping[str, Sequence[str]],
) -> set[str]:
    blockers: set[str] = set()
    contract_names = {row.dataset for row in plan.dataset_contracts}
    obligations = {row.obligation_id: row for row in plan.obligations}

    def admitted_receipts(dataset: str) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
        for obligation_id, pages in satisfying_pages.items():
            obligation = obligations[obligation_id]
            allowed = set(satisfying_receipts.get(obligation_id) or ())
            if obligation.dataset == dataset and allowed:
                rows.extend(
                    receipt
                    for receipt in pages
                    if str(receipt.get("receipt_id") or "") in allowed
                )
        return rows

    if "securities" in contract_names:
        security_rows: list[SecurityLifecycle] = []
        for receipt in admitted_receipts("securities"):
            fields, items = _receipt_raw_records(receipt, evidence_root)
            if fields is None or items is None:
                blockers.add("coverage_security_population_authority_invalid")
                continue
            try:
                ts_index = fields.index("ts_code")
                list_index = fields.index("list_date")
                delist_index = fields.index("delist_date")
            except ValueError:
                blockers.add("coverage_security_population_authority_invalid")
                continue
            for item in items:
                delist_value = item[delist_index]
                security_rows.append(
                    SecurityLifecycle(
                        security_id=str(item[ts_index] or ""),
                        list_date=str(item[list_index] or ""),
                        delist_date=(
                            str(delist_value) if delist_value not in {None, ""} else None
                        ),
                    )
                )
        try:
            observed = _normalized_population(
                CoveragePopulation(
                    securities=tuple(security_rows),
                    trading_dates=(),
                )
            )["securities"]
        except AdmissionVerificationError:
            observed = []
            blockers.add("coverage_security_population_authority_invalid")
        expected = _normalized_population(plan.population)["securities"]
        if not observed or observed != expected:
            blockers.add("coverage_security_population_mismatch")

    if "trade_calendar" in contract_names:
        calendar_by_exchange: dict[str, dict[str, bool]] = {}
        for receipt in admitted_receipts("trade_calendar"):
            fields, items = _receipt_raw_records(receipt, evidence_root)
            if fields is None or items is None:
                blockers.add("coverage_trade_calendar_authority_invalid")
                continue
            try:
                exchange_index = fields.index("exchange")
                date_index = fields.index("trade_date")
                open_index = fields.index("is_open")
            except ValueError:
                blockers.add("coverage_trade_calendar_authority_invalid")
                continue
            for item in items:
                exchange = str(item[exchange_index] or "")
                trade_date = str(item[date_index] or "")
                if _valid_date(trade_date) is None or trade_date in calendar_by_exchange.setdefault(
                    exchange, {}
                ):
                    blockers.add("coverage_trade_calendar_authority_invalid")
                    continue
                is_open = _calendar_open_value(item[open_index])
                if is_open is None:
                    blockers.add("coverage_trade_calendar_authority_invalid")
                    continue
                calendar_by_exchange[exchange][trade_date] = is_open
        required_exchanges = set(plan.population.exchanges)
        expected_calendar_dates = set(
            _calendar_dates(plan.scope.date_start, plan.scope.as_of_market_date)
        )
        if set(calendar_by_exchange) != required_exchanges or any(
            set(rows) != expected_calendar_dates for rows in calendar_by_exchange.values()
        ):
            blockers.add("coverage_trade_calendar_span_incomplete")
        observed_open_dates = sorted(
            {
                trade_date
                for rows in calendar_by_exchange.values()
                for trade_date, is_open in rows.items()
                if is_open
            }
        )
        if observed_open_dates != list(plan.population.trading_dates):
            blockers.add("coverage_trading_date_population_mismatch")
    return blockers


def _receipt_raw_records(
    receipt: Mapping[str, Any],
    evidence_root: Path,
) -> tuple[list[str] | None, list[list[Any]] | None]:
    response = receipt.get("response")
    if not isinstance(response, Mapping):
        return None, None
    path = _confined_path(
        evidence_root,
        str(response.get("raw_envelope_relative_path") or ""),
    )
    payload = _read_json_object(path) if path is not None else None
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return None, None
    fields = data.get("fields")
    items = data.get("items")
    if (
        not isinstance(fields, list)
        or not all(isinstance(field, str) for field in fields)
        or not isinstance(items, list)
        or not all(isinstance(item, list) for item in items)
    ):
        return None, None
    return fields, items


def _calendar_dates(date_start: str, date_end: str) -> tuple[str, ...]:
    start = datetime(int(date_start[:4]), int(date_start[4:6]), int(date_start[6:8]))
    end = datetime(int(date_end[:4]), int(date_end[4:6]), int(date_end[6:8]))
    rows: list[str] = []
    current = start
    while current <= end:
        rows.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return tuple(rows)


def _calendar_open_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value in {"0", "1", "false", "true", "False", "True"}:
        return value in {"1", "true", "True"}
    return None


def _coverage_plan_blockers(plan: CoveragePlan) -> set[str]:
    blockers: set[str] = set()
    if plan.schema_version != COVERAGE_PLAN_SCHEMA or not plan.profile_id.startswith("dap_"):
        blockers.add("coverage_plan_identity_invalid")
    try:
        population = _normalized_population(plan.population)
        _validate_scope(plan.scope)
    except AdmissionVerificationError:
        blockers.add("coverage_plan_population_invalid")
        return blockers
    if canonical_hash(population) != plan.population_root:
        blockers.add("coverage_plan_population_root_invalid")
    contracts = list(plan.dataset_contracts)
    datasets = [row.dataset for row in contracts]
    if not contracts or len(datasets) != len(set(datasets)) or datasets != sorted(datasets):
        blockers.add("coverage_plan_dataset_contracts_invalid")
    expected_obligations: list[CoverageObligation] = []
    for contract in contracts:
        try:
            _validate_profile_dataset_contract(contract.to_dict())
            coverage_end_date = (
                plan.scope.as_of_market_date
                if contract.coverage_watermark == "as_of_market_date"
                else plan.scope.date_end
            )
            expected_obligations.extend(
                _compile_dataset_obligations(
                    dataset=contract.dataset,
                    granularity=contract.coverage_granularity,
                    scope=plan.scope,
                    coverage_end_date=coverage_end_date,
                    population=plan.population,
                    coverage_subjects=contract.coverage_subjects,
                )
            )
            if contract.requires_pre_span_state:
                expected_obligations.extend(
                    _security_state_seed_obligations(
                        dataset=contract.dataset,
                        scope=plan.scope,
                        population=plan.population,
                    )
                )
        except AdmissionVerificationError:
            blockers.add("coverage_plan_dataset_contracts_invalid")
    required_exchanges = sorted(
        {
            subject
            for contract in contracts
            if contract.coverage_granularity.startswith("exchange_")
            for subject in contract.coverage_subjects
        }
    )
    required_indices = sorted(
        {
            subject
            for contract in contracts
            if contract.coverage_granularity.startswith("index_")
            for subject in contract.coverage_subjects
        }
    )
    if required_exchanges and list(population["exchanges"]) != required_exchanges:
        blockers.add("coverage_plan_exchange_population_invalid")
    if required_indices and list(population["index_codes"]) != required_indices:
        blockers.add("coverage_plan_index_population_invalid")
    expected = tuple(
        sorted(
            expected_obligations,
            key=lambda row: (
                row.dataset,
                row.subject_kind,
                row.subject,
                row.date_start,
                row.date_end,
                row.obligation_id,
            ),
        )
    )
    if not plan.obligations or plan.obligations != expected:
        blockers.add("coverage_plan_obligations_invalid")
    obligation_ids = [row.obligation_id for row in plan.obligations]
    if len(obligation_ids) != len(set(obligation_ids)):
        blockers.add("coverage_plan_obligations_invalid")
    semantic = {
        "schema_version": COVERAGE_PLAN_SCHEMA,
        "profile_id": plan.profile_id,
        "scope": plan.scope.to_dict(),
        "population": population,
        "population_root": plan.population_root,
        "dataset_contracts": [row.to_dict() for row in contracts],
        "obligations": [row.to_dict() for row in plan.obligations],
    }
    if canonical_hash(semantic) != plan.content_hash:
        blockers.add("coverage_plan_content_hash_invalid")
    return blockers


def _load_coverage_events(
    root: Path,
    plan: CoveragePlan,
    blockers: set[str],
) -> list[dict[str, Any]]:
    manifest = _read_json_object(root / COVERAGE_EVIDENCE_MANIFEST)
    if manifest is None:
        blockers.add("coverage_evidence_manifest_missing_or_invalid")
        return []
    if manifest.get("schema_version") != COVERAGE_EVIDENCE_SCHEMA:
        blockers.add("coverage_evidence_schema_invalid")
    semantic = {key: value for key, value in manifest.items() if key != "content_hash"}
    if manifest.get("content_hash") != canonical_hash(semantic):
        blockers.add("coverage_evidence_manifest_hash_invalid")
    if manifest.get("coverage_plan_content_hash") != plan.content_hash:
        blockers.add("coverage_plan_identity_mismatch")
    journal = manifest.get("attempt_journal")
    if not isinstance(journal, Mapping):
        blockers.add("coverage_receipt_journal_invalid")
        return []
    journal_path = _confined_path(root, str(journal.get("relative_path") or ""))
    if journal_path is None or not journal_path.is_file():
        blockers.add("coverage_receipt_journal_missing")
        return []
    if journal.get("sha256") != sha256_file(journal_path):
        blockers.add("coverage_receipt_journal_hash_invalid")
    events = _read_json_lines(journal_path)
    if events is None:
        blockers.add("coverage_receipt_journal_invalid")
        return []
    if _nonnegative_int(journal.get("event_count")) != len(events):
        blockers.add("coverage_receipt_journal_count_invalid")
    return events


def _mapped_atomic_obligation(
    event: Mapping[str, Any],
    obligations: Mapping[str, CoverageObligation],
    blockers: set[str],
) -> CoverageObligation | None:
    mapped = event.get("obligation_ids")
    if (
        not isinstance(mapped, Sequence)
        or isinstance(mapped, (str, bytes))
        or len(mapped) != 1
        or str(mapped[0]) not in obligations
    ):
        blockers.add("coverage_receipt_orphaned")
        return None
    obligation = obligations[str(mapped[0])]
    if event.get("dataset") != obligation.dataset:
        blockers.add("coverage_receipt_dataset_mismatch")
        return None
    return obligation


def _validate_attempt_request(
    event: Mapping[str, Any],
    obligation: CoverageObligation | None,
    contract: DatasetAdmissionContract | None,
    blockers: set[str],
) -> bool:
    request = event.get("request")
    if not isinstance(request, Mapping) or obligation is None or contract is None:
        blockers.add("coverage_attempt_request_invalid")
        return False
    required = (
        "provider",
        "provider_adapter",
        "endpoint",
        "provider_api_version",
        "adapter_schema_version",
        "permission_context_id",
        "capture_public_key_sha256",
        "pagination_mode",
        "row_cap",
        "allowed_retry_failure_kinds",
        "evidence_use_identity",
    )
    if any(not str(request.get(key) or "") for key in required):
        blockers.add("coverage_attempt_request_invalid")
        return False
    params = request.get("normalized_params")
    fields = request.get("fields")
    pagination_plan = request.get("pagination_plan")
    record_projection = request.get("obligation_record_projection")
    if (
        not isinstance(params, Mapping)
        or not isinstance(fields, Sequence)
        or isinstance(fields, (str, bytes))
        or not fields
        or any(not isinstance(field, str) or not field for field in fields)
        or len(set(fields)) != len(fields)
        or not isinstance(pagination_plan, Mapping)
        or not isinstance(record_projection, Mapping)
    ):
        blockers.add("coverage_attempt_request_invalid")
        return False
    if request.get("request_fingerprint") != _expected_request_fingerprint(request):
        blockers.add("coverage_attempt_request_fingerprint_invalid")
        return False
    if request.get("canonical_dataset") != contract.dataset:
        blockers.add("coverage_attempt_dataset_contract_invalid")
        return False
    acquisition = ProviderAcquisitionContract.from_mapping(request)
    if acquisition not in contract.acquisition_contracts:
        blockers.add("coverage_attempt_acquisition_contract_not_activated")
        return False
    if (
        acquisition.pagination_mode != "deterministic_split"
        or _positive_int(acquisition.row_cap) is None
        or not acquisition.allowed_retry_failure_kinds
    ):
        blockers.add("coverage_attempt_acquisition_policy_invalid")
        return False
    if event.get("capture_public_key_sha256") != acquisition.capture_public_key_sha256:
        blockers.add("coverage_attempt_capture_authority_mismatch")
        return False
    if (
        request.get("read_only") is not contract.read_only_required
        or _nonnegative_int(request.get("max_retries")) != contract.max_retries
        or _nonnegative_int(request.get("retry_ordinal")) is None
    ):
        blockers.add("coverage_attempt_retry_policy_invalid")
        return False
    if list(fields) != list(contract.approved_fields):
        blockers.add("coverage_attempt_approved_fields_mismatch")
        return False
    if request.get("evidence_use_identity") != _expected_evidence_use_identity(
        contract,
        acquisition,
    ):
        blockers.add("coverage_attempt_evidence_use_identity_invalid")
        return False
    if set(params) != {"subject", "date_start", "date_end"}:
        blockers.add("coverage_attempt_narrowing_params_forbidden")
        return False
    leaf_count = _positive_int(pagination_plan.get("leaf_count"))
    leaf_ordinal = _positive_int(pagination_plan.get("leaf_ordinal"))
    split_leaves = pagination_plan.get("split_leaves")
    split_plan_valid = _split_plan_valid(
        split_leaves,
        obligation=obligation,
        max_split_leaves=contract.max_split_leaves,
    )
    selected_leaf = _selected_split_leaf(
        split_leaves,
        leaf_ordinal=leaf_ordinal,
        leaf_count=leaf_count,
    )
    if (
        params.get("subject") != obligation.subject
        or params.get("date_start") != pagination_plan.get("leaf_start")
        or params.get("date_end") != pagination_plan.get("leaf_end")
        or pagination_plan.get("obligation_id") != obligation.obligation_id
        or pagination_plan.get("root_start") != obligation.date_start
        or pagination_plan.get("root_end") != obligation.date_end
        or leaf_ordinal is None
        or leaf_count is None
        or leaf_ordinal > leaf_count
        or leaf_count > contract.max_split_leaves
        or not split_plan_valid
        or pagination_plan.get("split_plan_root") != canonical_hash(split_leaves)
        or selected_leaf is None
        or selected_leaf.get("leaf_start")
        != pagination_plan.get("leaf_start")
        or selected_leaf.get("leaf_end")
        != pagination_plan.get("leaf_end")
        or _valid_date(str(pagination_plan.get("leaf_start") or "")) is None
        or _valid_date(str(pagination_plan.get("leaf_end") or "")) is None
        or not obligation.date_start
        <= str(pagination_plan.get("leaf_start"))
        <= str(pagination_plan.get("leaf_end"))
        <= obligation.date_end
        or _positive_int(pagination_plan.get("row_cap")) != acquisition.row_cap
        or record_projection.get("subject_field") != contract.record_subject_field
        or record_projection.get("date_field") != contract.record_date_field
    ):
        blockers.add("coverage_attempt_obligation_geometry_invalid")
        return False
    return True


def _validate_retry_start(
    event: Mapping[str, Any],
    starts: Mapping[str, tuple[dict[str, Any], bool, CoverageObligation | None]],
    terminals: Mapping[str, dict[str, Any]],
    contract: DatasetAdmissionContract | None,
    blockers: set[str],
) -> bool:
    retry_of = event.get("retry_of_attempt_id")
    request = event.get("request") if isinstance(event.get("request"), Mapping) else {}
    retry_ordinal = _nonnegative_int(request.get("retry_ordinal"))
    lineage_key = _attempt_lineage_key(event)
    obligation_key = _attempt_obligation_key(event)
    split_plan_root = _attempt_split_plan_root(event)
    if retry_of is None:
        duplicate_root = any(
            _attempt_lineage_key(start[0]) == lineage_key for start in starts.values()
        )
        split_plan_changed = any(
            _attempt_obligation_key(start[0]) == obligation_key
            and _attempt_split_plan_root(start[0]) != split_plan_root
            for start in starts.values()
        )
        if retry_ordinal != 0 or duplicate_root or split_plan_changed:
            blockers.add("coverage_receipt_retry_lineage_invalid")
            return False
        return True
    predecessor_id = str(retry_of)
    predecessor = starts.get(predecessor_id)
    terminal = terminals.get(predecessor_id)
    if predecessor is None or terminal is None:
        blockers.add("coverage_receipt_retry_lineage_invalid")
        return False
    predecessor_start = predecessor[0]
    predecessor_request = predecessor_start.get("request")
    previous_ordinal = (
        _nonnegative_int(predecessor_request.get("retry_ordinal"))
        if isinstance(predecessor_request, Mapping)
        else None
    )
    max_retries = _nonnegative_int(request.get("max_retries"))
    started_at = _valid_instant(str(event.get("capture_started_at") or ""))
    predecessor_completed = _valid_instant(str(terminal.get("capture_completed_at") or ""))
    forked = any(
        start[0].get("retry_of_attempt_id") == predecessor_id for start in starts.values()
    )
    if (
        contract is None
        or
        request.get("read_only") is not True
        or terminal.get("terminal_disposition") != "failed"
        or predecessor_start.get("dataset") != event.get("dataset")
        or predecessor_start.get("obligation_ids") != event.get("obligation_ids")
        or not isinstance(predecessor_request, Mapping)
        or predecessor_request.get("request_fingerprint") != request.get("request_fingerprint")
        or previous_ordinal is None
        or retry_ordinal != previous_ordinal + 1
        or max_retries is None
        or max_retries != contract.max_retries
        or retry_ordinal > max_retries
        or forked
        or started_at is None
        or predecessor_completed is None
        or started_at <= predecessor_completed
    ):
        blockers.add("coverage_receipt_retry_lineage_invalid")
        return False
    return True


def _receipt_matches_locked_page(
    receipt: Mapping[str, Any],
    start: Mapping[str, Any],
    blockers: set[str],
) -> bool:
    request = start.get("request")
    pagination = receipt.get("pagination")
    if not isinstance(request, Mapping) or not isinstance(pagination, Mapping):
        blockers.add("coverage_receipt_pagination_binding_invalid")
        return False
    plan = request.get("pagination_plan")
    if not isinstance(plan, Mapping) or (
        pagination.get("row_cap") != plan.get("row_cap")
        or pagination.get("leaf_ordinal") != plan.get("leaf_ordinal")
        or pagination.get("leaf_count") != plan.get("leaf_count")
        or pagination.get("leaf_start") != plan.get("leaf_start")
        or pagination.get("leaf_end") != plan.get("leaf_end")
        or pagination.get("root_start") != plan.get("root_start")
        or pagination.get("root_end") != plan.get("root_end")
        or pagination.get("terminal") is not True
        or pagination.get("end_marker") is not True
        or pagination.get("cursor") not in {None, ""}
        or pagination.get("next_cursor") not in {None, ""}
    ):
        blockers.add("coverage_receipt_pagination_binding_invalid")
        return False
    return True


def verify_data_admission(
    profile_manifest: str | Path,
    source_generation_manifest: str | Path,
    scope: DataAdmissionScope,
    verdict_root: str | Path,
) -> DataAdmissionVerdict:
    """Issue a content-addressed admit-or-block verdict from underlying evidence.

    Producer readiness fields are intentionally excluded from every decision. A
    legacy generation can therefore be adjudicated and archived, but cannot turn
    itself into canonical research data.
    """

    _validate_scope(scope)
    profile = _load_profile_manifest(Path(profile_manifest))
    active_dataset_closure = _active_dataset_closure(profile)
    source_path = Path(source_generation_manifest).resolve()
    source = _read_json_object(source_path)
    if source is None:
        raise AdmissionVerificationError("source_generation_manifest_invalid")
    source_generation_id = str(source.get("generation_id") or "")
    source_content_hash = str(source.get("content_hash") or "")
    expected_source_generation_id = (
        f"ashare_source_freeze_{source_content_hash[:24]}"
        if source.get("schema_version") == SOURCE_FREEZE_SCHEMA
        else None
    )
    if (
        source.get("schema_version") not in {SOURCE_FREEZE_SCHEMA, LEGACY_SOURCE_FREEZE_SCHEMA}
        or not source_generation_id
        or not _sha256_hex(source_content_hash)
        or not source_generation_id.endswith(source_content_hash[:24])
        or (
            expected_source_generation_id is not None
            and source_generation_id != expected_source_generation_id
        )
        or not _source_generation_hash_valid(source)
    ):
        raise AdmissionVerificationError("source_generation_identity_invalid")

    blocker_rows: list[DataAdmissionBlocker] = []
    if source.get("schema_version") == SOURCE_FREEZE_SCHEMA:
        try:
            from .source_freeze import SourceFreezeError, validate_source_freeze_generation

            validate_source_freeze_generation(source_path)
        except (
            SourceFreezeError,
            OSError,
            KeyError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            blocker_rows.append(
                DataAdmissionBlocker(
                    "source_generation_structural_validation_failed",
                    subject=type(exc).__name__,
                )
            )
    if profile.get("activation_status") != "active":
        blocker_rows.append(DataAdmissionBlocker("data_admission_profile_not_activated"))
    else:
        # V1 has no trusted human-signature root. Treating a mutable `active`
        # string or a self-hashed reviewer record as approval would let the
        # autonomous loop relax its own admission profile.
        blocker_rows.append(DataAdmissionBlocker("data_admission_profile_human_approval_required"))

    admission_evidence = source.get("admission_evidence")
    source_artifact_root = str(source.get("source_artifact_root") or "")
    if source.get("schema_version") == LEGACY_SOURCE_FREEZE_SCHEMA:
        blocker_rows.append(DataAdmissionBlocker("legacy_source_generation_evidence_unbound"))
        admission_evidence = {}
    elif not isinstance(admission_evidence, Mapping):
        admission_evidence = {}
    if source.get("schema_version") == SOURCE_FREEZE_SCHEMA and source.get(
        "admission_evidence_root"
    ) != canonical_hash(dict(admission_evidence)):
        blocker_rows.append(DataAdmissionBlocker("source_admission_evidence_root_invalid"))
    source_root = source_path.parent
    source_dataset_roots = _source_dataset_content_roots(source)
    if source.get("schema_version") == SOURCE_FREEZE_SCHEMA:
        if not _sha256_hex(source_artifact_root):
            blocker_rows.append(DataAdmissionBlocker("source_artifact_root_missing_or_invalid"))
        blocker_rows.extend(
            _verify_source_evidence_references(source_root, admission_evidence)
        )
    population = _load_coverage_population(
        source_root,
        str(admission_evidence.get("lifecycle_population_relative_path") or ""),
    )
    coverage_plan: CoveragePlan | None = None
    coverage_verification: CoverageVerification | None = None
    if population is None:
        blocker_rows.append(DataAdmissionBlocker("coverage_population_evidence_missing"))
    else:
        try:
            coverage_plan = compile_coverage_plan(profile, scope, population)
        except AdmissionVerificationError as exc:
            blocker_rows.append(
                DataAdmissionBlocker(
                    "coverage_population_evidence_invalid",
                    subject=str(exc),
                )
            )

    coverage_relative = str(admission_evidence.get("coverage_evidence_relative_path") or "")
    coverage_path = _confined_path(source_root, coverage_relative)
    if coverage_plan is None or coverage_path is None or not coverage_path.is_dir():
        blocker_rows.append(DataAdmissionBlocker("coverage_receipts_missing"))
    else:
        coverage_verification = verify_coverage(coverage_plan, coverage_path)
        blocker_rows.extend(
            DataAdmissionBlocker(code, evidence_locator="coverage_evidence")
            for code in coverage_verification.blockers
        )

    data_scope_relative = str(admission_evidence.get("data_scope_evidence_relative_path") or "")
    data_scope_path = _confined_path(source_root, data_scope_relative)
    data_scope_root: str | None = None
    if data_scope_path is None or not data_scope_path.is_file():
        artifact_roles = _source_artifact_roles(source)
        required_roles = {
            "stock_axis": "stock_axis_missing",
            "date_axis": "date_axis_missing",
            "feature_axis": "feature_axis_missing",
            "feature_values": "feature_values_missing",
            "feature_validity": "feature_validity_missing",
            "target_values": "target_values_missing",
            "target_availability": "target_availability_missing",
        }
        for role, blocker in required_roles.items():
            if role not in artifact_roles:
                blocker_rows.append(DataAdmissionBlocker(blocker))
        blocker_rows.extend(
            (
                DataAdmissionBlocker("data_scope_evidence_missing"),
                DataAdmissionBlocker("pit_verification_evidence_missing"),
                DataAdmissionBlocker("source_to_derived_lineage_evidence_missing"),
                DataAdmissionBlocker("validity_breadth_evidence_missing"),
            )
        )
    else:
        data_scope_root, scope_blockers = _verify_data_scope_manifest(
            data_scope_path,
            source_root=source_root,
            profile_id=str(profile["profile_id"]),
            active_dataset_closure=active_dataset_closure,
            source_dataset_roots=source_dataset_roots,
            source_artifact_root=source_artifact_root,
            scope=scope,
            coverage_root=coverage_verification.coverage_root if coverage_verification else None,
        )
        blocker_rows.extend(scope_blockers)

    replay_relative = str(admission_evidence.get("deterministic_replay_relative_path") or "")
    replay_path = _confined_path(source_root, replay_relative)
    if replay_path is None or not replay_path.is_file():
        blocker_rows.append(DataAdmissionBlocker("deterministic_freeze_replay_evidence_missing"))
    else:
        blocker_rows.extend(
            _verify_replay_manifest(
                replay_path,
                source_artifact_root=source_artifact_root,
                profile_id=str(profile["profile_id"]),
                scope=scope,
                data_scope_root=data_scope_root,
            )
        )

    blockers = _ordered_blockers(blocker_rows)
    coverage_gap = (
        coverage_verification.coverage_gap
        if coverage_verification is not None
        else len(coverage_plan.obligations) if coverage_plan is not None else None
    )
    producer_claims_ignored = sorted(
        key
        for key in ("alpha_search_authorized", "complete", "coverage_root")
        if key in source
    )
    semantic = {
        "schema_version": ADMISSION_VERDICT_SCHEMA,
        "outcome": "admitted" if not blockers else "blocked",
        "profile_id": profile["profile_id"],
        "profile_content_hash": profile["content_hash"],
        "source_generation_id": source_generation_id,
        "source_content_hash": source_content_hash,
        "scope": scope.to_dict(),
        "active_dataset_closure": active_dataset_closure,
        "coverage_plan_content_hash": coverage_plan.content_hash if coverage_plan else None,
        "coverage_root": coverage_verification.coverage_root if coverage_verification else None,
        "data_scope_root": data_scope_root,
        "metrics": {
            "coverage_gap": coverage_gap,
            "coverage_receipt_count": (
                coverage_verification.receipt_count if coverage_verification else 0
            ),
        },
        "deterministic_replay_verified": not any(
            row.code.startswith("deterministic_freeze_replay") for row in blockers
        ),
        "blockers": [row.to_dict() for row in blockers],
        "producer_claims_ignored": producer_claims_ignored,
        "verifier_identity": _verifier_identity(),
    }
    extra_files: dict[str, bytes] = {}
    if coverage_plan is not None:
        extra_files["coverage_plan.json"] = (
            json.dumps(coverage_plan.to_dict(), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    published = publish_generation(
        verdict_root,
        prefix="data_admission_verdict",
        manifest_name=ADMISSION_VERDICT_MANIFEST,
        semantic=semantic,
        extra_files=extra_files,
    )
    return _verdict_from_manifest(published)


def validate_data_admission_verdict(
    path: str | Path,
    *,
    expected_source_generation_id: str | None = None,
    expected_scope: DataAdmissionScope | None = None,
    require_admitted: bool = False,
) -> DataAdmissionVerdict:
    """Validate a verifier generation before a governed consumer uses it."""

    try:
        payload = validate_generation(
            path,
            schema=ADMISSION_VERDICT_SCHEMA,
            manifest_name=ADMISSION_VERDICT_MANIFEST,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AdmissionVerificationError("data_admission_verdict_invalid") from exc
    verdict = _verdict_from_manifest(payload)
    if (
        verdict.outcome not in {"admitted", "blocked"}
        or verdict.outcome == "admitted" and verdict.blockers
        or verdict.outcome == "blocked" and not verdict.blockers
        or not verdict.verdict_id.startswith("data_admission_verdict_")
        or not verdict.profile_id.startswith("dap_")
        or payload.get("verifier_identity") != _verifier_identity()
    ):
        raise AdmissionVerificationError("data_admission_verdict_semantics_invalid")
    for value in (verdict.coverage_plan_content_hash, verdict.coverage_root, verdict.data_scope_root):
        if value is not None and not _sha256_hex(value):
            raise AdmissionVerificationError("data_admission_verdict_root_invalid")
    if verdict.coverage_plan_content_hash is not None:
        plan_path = Path(verdict.manifest_path).parent / "coverage_plan.json"
        plan = _read_json_object(plan_path)
        if plan is None:
            raise AdmissionVerificationError("data_admission_coverage_plan_missing")
        plan_semantic = {key: value for key, value in plan.items() if key != "content_hash"}
        if (
            plan.get("content_hash") != verdict.coverage_plan_content_hash
            or canonical_hash(plan_semantic) != verdict.coverage_plan_content_hash
            or plan.get("profile_id") != verdict.profile_id
            or plan.get("scope") != verdict.scope.to_dict()
        ):
            raise AdmissionVerificationError("data_admission_coverage_plan_invalid")
    if expected_source_generation_id and verdict.source_generation_id != expected_source_generation_id:
        raise AdmissionVerificationError("data_admission_verdict_source_mismatch")
    if expected_scope and verdict.scope != expected_scope:
        raise AdmissionVerificationError("data_admission_verdict_scope_mismatch")
    if require_admitted and not verdict.admitted:
        raise AdmissionVerificationError("data_admission_verdict_blocked")
    if verdict.admitted and payload.get("schema_version") == ADMISSION_VERDICT_SCHEMA:
        raise AdmissionVerificationError("data_admission_v1_bundle_resolution_unavailable")
    if verdict.admitted and (
        verdict.blockers
        or not verdict.coverage_root
        or not verdict.data_scope_root
        or not bool(payload.get("deterministic_replay_verified"))
    ):
        raise AdmissionVerificationError("data_admission_admitted_verdict_incomplete")
    return verdict


def _load_profile_manifest(path: Path) -> dict[str, Any]:
    profile = _read_json_object(path)
    if profile is None or profile.get("schema_version") != PROFILE_SCHEMA:
        raise AdmissionVerificationError("data_admission_profile_manifest_invalid")
    semantic = _canonical_profile_semantic(profile)
    content_hash = canonical_hash(semantic)
    profile_id = f"dap_{content_hash[:24]}"
    if profile.get("content_hash") != content_hash or profile.get("profile_id") != profile_id:
        raise AdmissionVerificationError("data_admission_profile_manifest_identity_invalid")
    return semantic | {"content_hash": content_hash, "profile_id": profile_id}


def _load_coverage_population(root: Path, relative_path: str) -> CoveragePopulation | None:
    path = _confined_path(root, relative_path)
    if path is None or not path.is_file():
        return None
    payload = _read_json_object(path)
    if payload is None or payload.get("schema_version") != "data_coverage_population_v1":
        return None
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("content_hash") != canonical_hash(semantic):
        return None
    securities = payload.get("securities")
    trading_dates = payload.get("trading_dates")
    exchanges = payload.get("exchanges") or ()
    index_codes = payload.get("index_codes") or ()
    if (
        not isinstance(securities, Sequence)
        or isinstance(securities, (str, bytes))
        or not isinstance(trading_dates, Sequence)
        or isinstance(trading_dates, (str, bytes))
        or not isinstance(exchanges, Sequence)
        or isinstance(exchanges, (str, bytes))
        or not isinstance(index_codes, Sequence)
        or isinstance(index_codes, (str, bytes))
    ):
        return None
    rows: list[SecurityLifecycle] = []
    for item in securities:
        if not isinstance(item, Mapping):
            return None
        rows.append(
            SecurityLifecycle(
                security_id=str(item.get("security_id") or ""),
                list_date=str(item.get("list_date") or ""),
                delist_date=(
                    str(item["delist_date"])
                    if item.get("delist_date") not in {None, ""}
                    else None
                ),
            )
        )
    return CoveragePopulation(
        securities=tuple(rows),
        trading_dates=tuple(str(item) for item in trading_dates),
        exchanges=tuple(str(item) for item in exchanges),
        index_codes=tuple(str(item) for item in index_codes),
    )


def _source_artifact_roles(source: Mapping[str, Any]) -> set[str]:
    derived = source.get("strict_derived_bundle")
    if not isinstance(derived, Mapping):
        return set()
    artifacts = derived.get("frozen_artifacts") or derived.get("artifacts") or ()
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        return set()
    return {
        str(row.get("role") or "")
        for row in artifacts
        if isinstance(row, Mapping) and row.get("role")
    }


def _source_dataset_content_roots(source: Mapping[str, Any]) -> dict[str, str]:
    partitions = source.get("partitions")
    if not isinstance(partitions, Sequence) or isinstance(partitions, (str, bytes)):
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for value in partitions:
        if not isinstance(value, Mapping):
            continue
        dataset = str(value.get("dataset") or "")
        relative_path = str(value.get("relative_path") or "")
        sha256 = str(value.get("sha256") or "")
        records = _nonnegative_int(value.get("record_count"))
        if not dataset or not relative_path or not _sha256_hex(sha256) or records is None:
            continue
        grouped.setdefault(dataset, []).append(
            {
                "path": relative_path,
                "sha256": sha256,
                "records": records,
            }
        )
    return {
        dataset: canonical_hash(sorted(rows, key=lambda row: row["path"]))
        for dataset, rows in sorted(grouped.items())
    }


def _source_generation_hash_valid(source: Mapping[str, Any]) -> bool:
    declared = str(source.get("content_hash") or "")
    legacy_core_keys = (
        "schema_version",
        "source_catalog_hash",
        "period_policy",
        "partition_root",
        "search_partition_root",
        "period_coverage",
        "dataset_quality_root",
        "cross_source_reconciliation_hash",
        "source_semantic_hash",
        "strict_derived_bundle",
        "blockers",
        "warnings",
    )
    schema_version = source.get("schema_version")
    core_keys = (
        (
            "schema_version",
            "source_artifact_root",
            *legacy_core_keys[1:-2],
            "admission_evidence",
            "admission_evidence_root",
            *legacy_core_keys[-2:],
        )
        if schema_version == SOURCE_FREEZE_SCHEMA
        else legacy_core_keys
    )
    if schema_version in {SOURCE_FREEZE_SCHEMA, LEGACY_SOURCE_FREEZE_SCHEMA} and all(
        key in source for key in core_keys
    ):
        return canonical_hash({key: source[key] for key in core_keys}) == declared
    semantic = {
        key: value for key, value in source.items() if key not in {"content_hash", "generation_id"}
    }
    return canonical_hash(semantic) == declared


def _verify_source_evidence_references(
    source_root: Path,
    admission_evidence: Mapping[str, Any],
) -> list[DataAdmissionBlocker]:
    blockers: list[DataAdmissionBlocker] = []
    references = (
        (
            "lifecycle_population_relative_path",
            "lifecycle_population_sha256",
            None,
        ),
        (
            "coverage_evidence_relative_path",
            "coverage_evidence_manifest_sha256",
            COVERAGE_EVIDENCE_MANIFEST,
        ),
        (
            "data_scope_evidence_relative_path",
            "data_scope_evidence_sha256",
            None,
        ),
        (
            "deterministic_replay_relative_path",
            "deterministic_replay_evidence_sha256",
            None,
        ),
    )
    for path_key, hash_key, child_name in references:
        relative_path = str(admission_evidence.get(path_key) or "")
        if not relative_path:
            continue
        target = _confined_path(source_root, relative_path)
        if target is not None and child_name is not None:
            target = _confined_path(target, child_name)
        declared_hash = str(admission_evidence.get(hash_key) or "")
        if (
            target is None
            or not target.is_file()
            or not _sha256_hex(declared_hash)
            or sha256_file(target) != declared_hash
        ):
            blockers.append(
                DataAdmissionBlocker(
                    "source_evidence_reference_integrity_invalid",
                    subject=path_key,
                )
            )
    return blockers


def _verify_data_scope_manifest(
    path: Path,
    *,
    source_root: Path,
    profile_id: str,
    active_dataset_closure: Sequence[Mapping[str, Any]],
    source_dataset_roots: Mapping[str, str],
    source_artifact_root: str,
    scope: DataAdmissionScope,
    coverage_root: str | None,
) -> tuple[str | None, list[DataAdmissionBlocker]]:
    blockers: list[DataAdmissionBlocker] = []
    payload = _read_json_object(path)
    if payload is None or payload.get("schema_version") != "data_scope_evidence_v1":
        return None, [DataAdmissionBlocker("data_scope_evidence_invalid")]
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("content_hash") != canonical_hash(semantic):
        blockers.append(DataAdmissionBlocker("data_scope_evidence_hash_invalid"))
    if payload.get("profile_id") != profile_id:
        blockers.append(DataAdmissionBlocker("data_scope_profile_mismatch"))
    if payload.get("source_artifact_root") != source_artifact_root:
        blockers.append(DataAdmissionBlocker("data_scope_source_artifact_root_mismatch"))
    if payload.get("scope") != scope.to_dict():
        blockers.append(DataAdmissionBlocker("data_scope_scope_mismatch"))
    if coverage_root is None or payload.get("coverage_root") != coverage_root:
        blockers.append(DataAdmissionBlocker("data_scope_coverage_root_mismatch"))

    active_sources = payload.get("active_source_artifacts")
    expected_datasets = {
        str(row.get("dataset") or "") for row in active_dataset_closure
    }
    declared_active_source_rows: list[dict[str, str]] = []
    if not isinstance(active_sources, Sequence) or isinstance(active_sources, (str, bytes)):
        blockers.append(DataAdmissionBlocker("active_source_artifacts_invalid"))
    else:
        seen_datasets: set[str] = set()
        for value in active_sources:
            if not isinstance(value, Mapping):
                blockers.append(DataAdmissionBlocker("active_source_artifact_invalid"))
                continue
            dataset = str(value.get("dataset") or "")
            content_root = str(value.get("content_root") or "")
            if (
                dataset not in expected_datasets
                or dataset in seen_datasets
                or not _sha256_hex(content_root)
            ):
                blockers.append(
                    DataAdmissionBlocker(
                        "active_source_artifact_invalid",
                        dataset=dataset or None,
                    )
                )
                continue
            seen_datasets.add(dataset)
            declared_active_source_rows.append(
                {"dataset": dataset, "content_root": content_root}
            )
        if seen_datasets != expected_datasets:
            blockers.append(DataAdmissionBlocker("active_source_artifact_closure_incomplete"))
    declared_active_source_rows.sort(key=lambda row: row["dataset"])
    active_source_rows = [
        {
            "dataset": dataset,
            "content_root": str(source_dataset_roots.get(dataset) or ""),
        }
        for dataset in sorted(expected_datasets)
    ]
    if any(not _sha256_hex(row["content_root"]) for row in active_source_rows) or (
        declared_active_source_rows != active_source_rows
    ):
        blockers.append(
            DataAdmissionBlocker("active_source_artifact_source_lineage_mismatch")
        )
    active_source_root = canonical_hash(active_source_rows)
    if payload.get("active_source_root") != active_source_root:
        blockers.append(DataAdmissionBlocker("active_source_root_invalid"))
    active_parent_roots = {row["content_root"] for row in active_source_rows}

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        return None, blockers + [DataAdmissionBlocker("data_scope_artifacts_invalid")]
    artifact_rows: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for value in artifacts:
        if not isinstance(value, Mapping):
            blockers.append(DataAdmissionBlocker("data_scope_artifact_invalid"))
            continue
        row = dict(value)
        role = str(row.get("role") or "")
        relative_path = str(row.get("relative_path") or "")
        artifact_path = _confined_path(source_root, relative_path)
        if not role or role in seen_roles:
            blockers.append(DataAdmissionBlocker("data_scope_artifact_role_invalid", subject=role or None))
            continue
        seen_roles.add(role)
        if artifact_path is None or not artifact_path.is_file():
            blockers.append(DataAdmissionBlocker("data_scope_artifact_missing", subject=role))
            continue
        if (
            str(row.get("sha256") or "") != sha256_file(artifact_path)
            or _nonnegative_int(row.get("size_bytes")) != artifact_path.stat().st_size
        ):
            blockers.append(DataAdmissionBlocker("data_scope_artifact_integrity_invalid", subject=role))
            continue
        parent_roots = row.get("parent_roots")
        if (
            not isinstance(parent_roots, Sequence)
            or isinstance(parent_roots, (str, bytes))
            or not parent_roots
            or any(not _sha256_hex(str(item)) for item in parent_roots)
            or not {str(item) for item in parent_roots} <= active_parent_roots
        ):
            blockers.append(DataAdmissionBlocker("data_scope_artifact_lineage_invalid", subject=role))
            continue
        artifact_rows.append(
            {
                "role": role,
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "parent_roots": sorted(str(item) for item in parent_roots),
            }
        )

    required_roles = {
        "stock_axis",
        "date_axis",
        "feature_axis",
        "feature_values",
        "feature_validity",
        "target_values",
        "target_availability",
        "target_contract",
        "pit_universe_membership",
        "source_to_derived_lineage",
        "pit_audit",
        "quality_report",
        "reconciliation_report",
    }
    for role in sorted(required_roles - seen_roles):
        blockers.append(DataAdmissionBlocker(f"{role}_missing"))

    identities = payload.get("transform_identities")
    required_identities = {
        "provider_adapter",
        "normalization",
        "pit_transform",
        "target_formula",
        "producer_code",
        "toolchain",
    }
    if not isinstance(identities, Mapping) or any(
        not _sha256_hex(str(identities.get(key) or "")) for key in required_identities
    ):
        blockers.append(DataAdmissionBlocker("data_scope_transform_identity_invalid"))

    metrics = payload.get("metrics")
    zero_tolerance = {
        "unexplained_unknown",
        "conflicting_primary_key",
        "unexplained_duplicate",
        "parse_error",
        "pit_availability_gap",
        "coverage_gap",
        "lineage_gap",
        "unexplained_target_unknown",
    }
    if not isinstance(metrics, Mapping):
        blockers.append(DataAdmissionBlocker("data_scope_metrics_invalid"))
    else:
        for metric in sorted(zero_tolerance):
            if _nonnegative_int(metrics.get(metric)) != 0:
                blockers.append(DataAdmissionBlocker("data_scope_zero_tolerance_metric_failed", subject=metric))

    breadth = payload.get("validity_breadth")
    if (
        not isinstance(breadth, Mapping)
        or breadth.get("human_approved") is not True
        or _positive_int(breadth.get("minimum_valid_security_count")) is None
        or not isinstance(breadth.get("minimum_pit_universe_fraction"), (int, float))
        or isinstance(breadth.get("minimum_pit_universe_fraction"), bool)
        or not 0 < float(breadth["minimum_pit_universe_fraction"]) <= 1
        or not _sha256_hex(str(breadth.get("approval_identity") or ""))
    ):
        blockers.append(DataAdmissionBlocker("validity_breadth_evidence_invalid"))

    data_scope_semantic = {
        "schema_version": "data_scope_root_v1",
        "profile_id": profile_id,
        "active_source_artifacts": active_source_rows,
        "active_source_root": active_source_root,
        "scope": scope.to_dict(),
        "coverage_root": coverage_root,
        "artifacts": sorted(artifact_rows, key=lambda row: row["role"]),
        "transform_identities": dict(identities) if isinstance(identities, Mapping) else {},
        "metrics": dict(metrics) if isinstance(metrics, Mapping) else {},
        "validity_breadth": dict(breadth) if isinstance(breadth, Mapping) else {},
    }
    # V1 verifies the evidence envelope and computes a provisional scope root,
    # but it does not yet resolve and replay the self-contained canonical matrix
    # bundle. An envelope alone must not authorize governed target access.
    blockers.append(DataAdmissionBlocker("canonical_bundle_contract_unresolved"))
    return canonical_hash(data_scope_semantic), blockers


def _verify_replay_manifest(
    path: Path,
    *,
    source_artifact_root: str,
    profile_id: str,
    scope: DataAdmissionScope,
    data_scope_root: str | None,
) -> list[DataAdmissionBlocker]:
    payload = _read_json_object(path)
    if payload is None or payload.get("schema_version") != "deterministic_freeze_replay_evidence_v1":
        return [DataAdmissionBlocker("deterministic_freeze_replay_evidence_invalid")]
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    blockers: list[DataAdmissionBlocker] = []
    if payload.get("content_hash") != canonical_hash(semantic):
        blockers.append(DataAdmissionBlocker("deterministic_freeze_replay_hash_invalid"))
    if (
        payload.get("source_artifact_root") != source_artifact_root
        or payload.get("profile_id") != profile_id
        or payload.get("scope") != scope.to_dict()
        or data_scope_root is None
    ):
        blockers.append(DataAdmissionBlocker("deterministic_freeze_replay_binding_invalid"))
    rebuilds = payload.get("rebuilds")
    if (
        not isinstance(rebuilds, Sequence)
        or isinstance(rebuilds, (str, bytes))
        or len(rebuilds) < 2
        or any(not isinstance(row, Mapping) for row in rebuilds)
    ):
        return blockers + [DataAdmissionBlocker("deterministic_freeze_replay_runs_invalid")]
    roots = {str(row.get("data_scope_root") or "") for row in rebuilds if isinstance(row, Mapping)}
    worker_counts = {
        _positive_int(row.get("worker_count")) for row in rebuilds if isinstance(row, Mapping)
    }
    output_identities = {
        str(row.get("output_identity") or "") for row in rebuilds if isinstance(row, Mapping)
    }
    artifact_roots = {
        str(row.get("artifact_byte_root") or "") for row in rebuilds if isinstance(row, Mapping)
    }
    if roots != {data_scope_root} or len(artifact_roots) != 1 or not all(
        _sha256_hex(value) for value in artifact_roots
    ):
        blockers.append(DataAdmissionBlocker("deterministic_freeze_replay_mismatch"))
    if None in worker_counts or len(worker_counts) < 2:
        blockers.append(DataAdmissionBlocker("deterministic_freeze_replay_worker_variation_missing"))
    if len(output_identities) < 2 or not all(_sha256_hex(value) for value in output_identities):
        blockers.append(DataAdmissionBlocker("deterministic_freeze_replay_location_variation_missing"))
    if not _sha256_hex(str(payload.get("verifier_execution_identity") or "")):
        blockers.append(DataAdmissionBlocker("deterministic_freeze_replay_verifier_identity_invalid"))
    return blockers


def _active_dataset_closure(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    active_families = {
        str(item) for item in _mapping_sequence(profile, "activated_feature_families")
    }
    rows: list[dict[str, Any]] = []
    for row in _mapping_sequence(profile, "datasets"):
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "")
        family = str(row.get("feature_family") or "")
        if role == "base-required" or (
            role == "feature-family-conditional" and family in active_families
        ):
            rows.append(
                {
                    "dataset": str(row.get("dataset") or ""),
                    "role": role,
                    "feature_family": family,
                    "approved_fields": list(_mapping_sequence(row, "approved_fields")),
                    "consumer_roles": list(_mapping_sequence(row, "consumer_roles")),
                    "coverage_granularity": str(row.get("coverage_granularity") or ""),
                    "evidence_grade": str(row.get("evidence_grade") or ""),
                }
            )
    return sorted(rows, key=lambda row: row["dataset"])


def _ordered_blockers(rows: Sequence[DataAdmissionBlocker]) -> tuple[DataAdmissionBlocker, ...]:
    unique = {
        (row.code, row.dataset, row.subject, row.evidence_locator): row for row in rows
    }
    return tuple(
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: tuple("" if value is None else value for value in item),
        )
    )


def _verdict_from_manifest(payload: Mapping[str, Any]) -> DataAdmissionVerdict:
    raw_scope = payload.get("scope")
    if not isinstance(raw_scope, Mapping):
        raise AdmissionVerificationError("data_admission_verdict_scope_invalid")
    raw_blockers = payload.get("blockers")
    if not isinstance(raw_blockers, Sequence) or isinstance(raw_blockers, (str, bytes)):
        raise AdmissionVerificationError("data_admission_verdict_blockers_invalid")
    blockers: list[DataAdmissionBlocker] = []
    for row in raw_blockers:
        if not isinstance(row, Mapping) or not row.get("code"):
            raise AdmissionVerificationError("data_admission_verdict_blocker_invalid")
        blockers.append(
            DataAdmissionBlocker(
                code=str(row["code"]),
                dataset=str(row["dataset"]) if row.get("dataset") is not None else None,
                subject=str(row["subject"]) if row.get("subject") is not None else None,
                evidence_locator=(
                    str(row["evidence_locator"])
                    if row.get("evidence_locator") is not None
                    else None
                ),
            )
        )
    scope = DataAdmissionScope(
        access_view=str(raw_scope.get("access_view") or ""),
        date_start=str(raw_scope.get("date_start") or ""),
        date_end=str(raw_scope.get("date_end") or ""),
        as_of_market_date=str(raw_scope.get("as_of_market_date") or ""),
    )
    _validate_scope(scope)
    return DataAdmissionVerdict(
        verdict_id=str(payload.get("generation_id") or ""),
        outcome=str(payload.get("outcome") or ""),
        profile_id=str(payload.get("profile_id") or ""),
        source_generation_id=str(payload.get("source_generation_id") or ""),
        scope=scope,
        coverage_plan_content_hash=(
            str(payload["coverage_plan_content_hash"])
            if payload.get("coverage_plan_content_hash") is not None
            else None
        ),
        coverage_root=(
            str(payload["coverage_root"]) if payload.get("coverage_root") is not None else None
        ),
        data_scope_root=(
            str(payload["data_scope_root"]) if payload.get("data_scope_root") is not None else None
        ),
        blockers=tuple(blockers),
        manifest_path=str(payload.get("manifest_path") or ""),
        content_hash=str(payload.get("content_hash") or ""),
    )


def _canonical_profile_semantic(profile: Mapping[str, Any]) -> dict[str, Any]:
    raw = {
        str(key): value
        for key, value in dict(profile).items()
        if key not in {"profile_id", "content_hash"}
    }
    datasets = raw.get("datasets")
    if not isinstance(datasets, Sequence) or isinstance(datasets, (str, bytes)):
        raise AdmissionVerificationError("data_admission_profile_datasets_invalid")
    normalized_rows: list[dict[str, Any]] = []
    names: set[str] = set()
    for value in datasets:
        if not isinstance(value, Mapping):
            raise AdmissionVerificationError("data_admission_profile_dataset_invalid")
        row = {str(key): item for key, item in value.items()}
        dataset = str(row.get("dataset") or "")
        if not dataset or dataset in names:
            raise AdmissionVerificationError("data_admission_profile_dataset_identity_invalid")
        names.add(dataset)
        for key in ("approved_fields", "consumer_roles"):
            items = row.get(key)
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                row[key] = sorted({str(item) for item in items})
        subjects = row.get("coverage_subjects")
        if isinstance(subjects, Sequence) and not isinstance(subjects, (str, bytes)):
            row["coverage_subjects"] = sorted({str(item) for item in subjects})
        acquisitions = row.get("acquisition_contracts")
        if isinstance(acquisitions, Sequence) and not isinstance(acquisitions, (str, bytes)):
            normalized_acquisitions = [
                {str(key): item for key, item in value.items()}
                for value in acquisitions
                if isinstance(value, Mapping)
            ]
            if len(normalized_acquisitions) != len(acquisitions):
                raise AdmissionVerificationError(
                    f"data_admission_acquisition_contract_invalid:{dataset}"
                )
            for acquisition in normalized_acquisitions:
                failure_kinds = acquisition.get("allowed_retry_failure_kinds")
                if isinstance(failure_kinds, Sequence) and not isinstance(
                    failure_kinds, (str, bytes)
                ):
                    acquisition["allowed_retry_failure_kinds"] = sorted(
                        {str(item) for item in failure_kinds}
                    )
            row["acquisition_contracts"] = sorted(
                normalized_acquisitions,
                key=lambda value: canonical_hash(value),
            )
        authorities = row.get("not_applicable_authorities")
        if isinstance(authorities, Mapping):
            normalized_authorities = {
                str(reason): sorted({str(item) for item in datasets})
                for reason, datasets in sorted(authorities.items())
                if isinstance(datasets, Sequence)
                and not isinstance(datasets, (str, bytes))
            }
            if len(normalized_authorities) != len(authorities):
                raise AdmissionVerificationError(
                    f"data_admission_applicability_authority_invalid:{dataset}"
                )
            row["not_applicable_authorities"] = normalized_authorities
        _validate_profile_dataset_contract(row)
        normalized_rows.append(row)
    raw["datasets"] = sorted(normalized_rows, key=lambda row: str(row["dataset"]))
    dataset_names = {str(row["dataset"]) for row in normalized_rows}
    for row in normalized_rows:
        authorities = row.get("not_applicable_authorities") or {}
        if any(
            authority not in dataset_names
            for datasets in authorities.values()
            for authority in datasets
        ):
            raise AdmissionVerificationError(
                f"data_admission_applicability_authority_invalid:{row['dataset']}"
            )
    active = raw.get("activated_feature_families") or ()
    if not isinstance(active, Sequence) or isinstance(active, (str, bytes)):
        raise AdmissionVerificationError("data_admission_profile_feature_families_invalid")
    raw["activated_feature_families"] = sorted({str(item) for item in active})
    prerequisites = raw.get("admission_prerequisites")
    if isinstance(prerequisites, Sequence) and not isinstance(prerequisites, (str, bytes)):
        raw["admission_prerequisites"] = sorted({str(item) for item in prerequisites})
    return raw


def _validate_profile_dataset_contract(row: Mapping[str, Any]) -> None:
    dataset = str(row.get("dataset") or "")
    role = str(row.get("role") or "")
    granularity = str(row.get("coverage_granularity") or "")
    fields = row.get("approved_fields")
    consumers = row.get("consumer_roles")
    subjects = row.get("coverage_subjects")
    acquisitions = row.get("acquisition_contracts")
    authorities = row.get("not_applicable_authorities")
    valid_granularities = {
        "market_span",
        "security_day",
        "security_span",
        "security_lifecycle",
        "exchange_day",
        "exchange_span",
        "index_day",
        "index_span",
    }
    if role not in {"base-required", "feature-family-conditional", "inactive"}:
        raise AdmissionVerificationError(f"data_admission_dataset_role_invalid:{dataset}")
    if granularity not in valid_granularities:
        raise AdmissionVerificationError(
            f"data_admission_coverage_granularity_unsupported:{dataset}"
        )
    if (
        not isinstance(fields, Sequence)
        or isinstance(fields, (str, bytes))
        or not isinstance(consumers, Sequence)
        or isinstance(consumers, (str, bytes))
        or not isinstance(subjects, Sequence)
        or isinstance(subjects, (str, bytes))
        or not isinstance(acquisitions, Sequence)
        or isinstance(acquisitions, (str, bytes))
        or not isinstance(authorities, Mapping)
    ):
        raise AdmissionVerificationError(f"data_admission_dataset_contract_invalid:{dataset}")
    if role == "inactive":
        if (
            fields
            or consumers
            or subjects
            or acquisitions
            or authorities
            or row.get("evidence_grade") != "inactive"
            or row.get("coverage_watermark") != "inactive"
            or row.get("read_only_required") is not False
            or _nonnegative_int(row.get("max_retries")) != 0
            or _nonnegative_int(row.get("max_split_leaves")) != 0
        ):
            raise AdmissionVerificationError(
                f"data_admission_inactive_dataset_contract_invalid:{dataset}"
            )
        return
    subject_field = str(row.get("record_subject_field") or "")
    date_field = str(row.get("record_date_field") or "")
    if (
        not fields
        or not consumers
        or row.get("evidence_grade") != "governed_receipts"
        or row.get("empty_policy") not in {"nonempty_required", "observed_empty_allowed"}
        or row.get("coverage_watermark") not in {"scope_end", "as_of_market_date"}
        or subject_field not in fields
        or date_field not in fields
        or row.get("read_only_required") is not True
        or _nonnegative_int(row.get("max_retries")) is None
        or _positive_int(row.get("max_split_leaves")) is None
    ):
        raise AdmissionVerificationError(f"data_admission_dataset_contract_invalid:{dataset}")
    if granularity.startswith(("exchange_", "index_")) or granularity == "market_span":
        if not subjects:
            raise AdmissionVerificationError(
                f"data_admission_coverage_subjects_missing:{dataset}"
            )
    elif subjects:
        raise AdmissionVerificationError(
            f"data_admission_coverage_subjects_unexpected:{dataset}"
        )
    if role == "feature-family-conditional" and not str(row.get("feature_family") or ""):
        raise AdmissionVerificationError(
            f"data_admission_feature_family_missing:{dataset}"
        )
    acquisition_rows = [ProviderAcquisitionContract.from_mapping(value) for value in acquisitions]
    if any(
        not all(contract.to_dict().values())
        or contract.pagination_mode != "deterministic_split"
        or _positive_int(contract.row_cap) is None
        or not _sha256_hex(contract.capture_public_key_sha256)
        or not contract.allowed_retry_failure_kinds
        or not set(contract.allowed_retry_failure_kinds)
        <= {"network_error", "rate_limited", "timeout"}
        for contract in acquisition_rows
    ) or len({canonical_hash(contract.to_dict()) for contract in acquisition_rows}) != len(
        acquisition_rows
    ):
        raise AdmissionVerificationError(
            f"data_admission_acquisition_contract_invalid:{dataset}"
        )
    if any(
        not str(reason)
        or not isinstance(datasets, Sequence)
        or isinstance(datasets, (str, bytes))
        or not datasets
        or any(not str(authority) for authority in datasets)
        for reason, datasets in authorities.items()
    ):
        raise AdmissionVerificationError(
            f"data_admission_applicability_authority_invalid:{dataset}"
        )


def _compile_dataset_obligations(
    *,
    dataset: str,
    granularity: str,
    scope: DataAdmissionScope,
    coverage_end_date: str,
    population: CoveragePopulation,
    coverage_subjects: Sequence[str],
) -> list[CoverageObligation]:
    if granularity.startswith("security_") and not population.securities:
        raise AdmissionVerificationError("data_admission_security_population_empty")
    if granularity.endswith("_day") and not population.trading_dates:
        raise AdmissionVerificationError("data_admission_trading_date_population_empty")
    if granularity.startswith("exchange_") and not coverage_subjects:
        raise AdmissionVerificationError("data_admission_exchange_population_empty")
    if granularity.startswith("index_") and not coverage_subjects:
        raise AdmissionVerificationError("data_admission_index_population_empty")
    if granularity == "market_span":
        if not coverage_subjects:
            raise AdmissionVerificationError("data_admission_market_subject_invalid")
        return _partition_span_obligations(
            dataset=dataset,
            subject_kind="market",
            subjects=coverage_subjects,
            scope=scope,
            coverage_end_date=coverage_end_date,
        )
    if granularity == "security_day":
        return _security_day_obligations(
            dataset=dataset,
            scope=scope,
            coverage_end_date=coverage_end_date,
            population=population,
        )
    if granularity in {"security_span", "security_lifecycle"}:
        return _security_span_obligations(
            dataset=dataset,
            granularity=granularity,
            scope=scope,
            coverage_end_date=coverage_end_date,
            population=population,
        )
    if granularity in {"exchange_day", "index_day"}:
        return _partition_day_obligations(
            dataset=dataset,
            subject_kind="exchange" if granularity == "exchange_day" else "index",
            subjects=coverage_subjects,
            scope=scope,
            coverage_end_date=coverage_end_date,
            population=population,
        )
    if granularity in {"exchange_span", "index_span"}:
        return _partition_span_obligations(
            dataset=dataset,
            subject_kind="exchange" if granularity == "exchange_span" else "index",
            subjects=coverage_subjects,
            scope=scope,
            coverage_end_date=coverage_end_date,
        )
    raise AdmissionVerificationError(f"data_admission_coverage_granularity_unsupported:{dataset}")


def _security_day_obligations(
    *,
    dataset: str,
    scope: DataAdmissionScope,
    coverage_end_date: str,
    population: CoveragePopulation,
) -> list[CoverageObligation]:
    rows: list[CoverageObligation] = []
    trading_dates = sorted(set(population.trading_dates))
    for security in sorted(population.securities, key=lambda row: row.security_id):
        if not security.security_id or _valid_date(security.list_date) is None:
            raise AdmissionVerificationError("data_admission_security_lifecycle_invalid")
        delist_date = security.delist_date
        if delist_date is not None and _valid_date(delist_date) is None:
            raise AdmissionVerificationError("data_admission_security_lifecycle_invalid")
        for trade_date in trading_dates:
            if _valid_date(trade_date) is None:
                raise AdmissionVerificationError("data_admission_trading_date_invalid")
            if not (scope.date_start <= trade_date <= coverage_end_date):
                continue
            if trade_date < security.list_date or (delist_date is not None and trade_date > delist_date):
                continue
            semantic = {
                "dataset": dataset,
                "subject_kind": "security",
                "subject": security.security_id,
                "date_start": trade_date,
                "date_end": trade_date,
            }
            rows.append(
                CoverageObligation(
                    obligation_id=f"obl_{canonical_hash(semantic)[:24]}",
                    **semantic,
                )
            )
    return rows


def _security_span_obligations(
    *,
    dataset: str,
    granularity: str,
    scope: DataAdmissionScope,
    coverage_end_date: str,
    population: CoveragePopulation,
) -> list[CoverageObligation]:
    rows: list[CoverageObligation] = []
    for security in sorted(population.securities, key=lambda row: row.security_id):
        if not security.security_id or _valid_date(security.list_date) is None:
            raise AdmissionVerificationError("data_admission_security_lifecycle_invalid")
        if security.delist_date is not None and _valid_date(security.delist_date) is None:
            raise AdmissionVerificationError("data_admission_security_lifecycle_invalid")
        date_start = max(scope.date_start, security.list_date)
        date_end = coverage_end_date
        if security.delist_date is not None:
            date_end = min(date_end, _previous_date(security.delist_date))
        if date_start > date_end:
            continue
        subject_kind = "lifecycle" if granularity == "security_lifecycle" else "security"
        _append_obligation(
            rows,
            dataset=dataset,
            subject_kind=subject_kind,
            subject=security.security_id,
            date_start=date_start,
            date_end=date_end,
        )
    return rows


def _security_state_seed_obligations(
    *,
    dataset: str,
    scope: DataAdmissionScope,
    population: CoveragePopulation,
) -> list[CoverageObligation]:
    seed_end = _previous_date(scope.date_start)
    rows: list[CoverageObligation] = []
    for security in sorted(population.securities, key=lambda row: row.security_id):
        if security.list_date >= scope.date_start:
            continue
        _append_obligation(
            rows,
            dataset=dataset,
            subject_kind="security_state_seed",
            subject=security.security_id,
            date_start=security.list_date,
            date_end=seed_end,
        )
    return rows


def _partition_day_obligations(
    *,
    dataset: str,
    subject_kind: str,
    subjects: Sequence[str],
    scope: DataAdmissionScope,
    coverage_end_date: str,
    population: CoveragePopulation,
) -> list[CoverageObligation]:
    rows: list[CoverageObligation] = []
    for subject in sorted({str(item) for item in subjects if str(item)}):
        for trade_date in sorted(set(population.trading_dates)):
            if _valid_date(trade_date) is None:
                raise AdmissionVerificationError("data_admission_trading_date_invalid")
            if scope.date_start <= trade_date <= coverage_end_date:
                _append_obligation(
                    rows,
                    dataset=dataset,
                    subject_kind=subject_kind,
                    subject=subject,
                    date_start=trade_date,
                    date_end=trade_date,
                )
    return rows


def _partition_span_obligations(
    *,
    dataset: str,
    subject_kind: str,
    subjects: Sequence[str],
    scope: DataAdmissionScope,
    coverage_end_date: str,
) -> list[CoverageObligation]:
    rows: list[CoverageObligation] = []
    for subject in sorted({str(item) for item in subjects if str(item)}):
        _append_obligation(
            rows,
            dataset=dataset,
            subject_kind=subject_kind,
            subject=subject,
            date_start=scope.date_start,
            date_end=coverage_end_date,
        )
    return rows


def _append_obligation(
    rows: list[CoverageObligation],
    *,
    dataset: str,
    subject_kind: str,
    subject: str,
    date_start: str,
    date_end: str,
) -> None:
    semantic = {
        "dataset": dataset,
        "subject_kind": subject_kind,
        "subject": subject,
        "date_start": date_start,
        "date_end": date_end,
    }
    rows.append(
        CoverageObligation(
            obligation_id=f"obl_{canonical_hash(semantic)[:24]}",
            **semantic,
        )
    )


def _validate_receipt_disposition(
    *,
    receipt: Mapping[str, Any],
    evidence_root: Path,
    disposition: str,
    obligation: CoverageObligation | None,
    contract: DatasetAdmissionContract | None,
    blockers: set[str],
) -> bool:
    if disposition not in {
        "satisfied_nonempty",
        "satisfied_empty",
        "not_applicable",
        "failed",
        "ambiguous_transport",
        "permission_denied",
        "schema_mismatch",
        "cap_suspected",
    }:
        blockers.add("coverage_receipt_disposition_invalid")
        return False
    if disposition in {
        "failed",
        "ambiguous_transport",
        "permission_denied",
        "schema_mismatch",
        "cap_suspected",
    }:
        if disposition == "failed":
            request = receipt.get("request")
            response = receipt.get("response")
            failure_kind = str(receipt.get("failure_kind") or "")
            acquisition = (
                ProviderAcquisitionContract.from_mapping(request)
                if isinstance(request, Mapping)
                else None
            )
            if (
                contract is None
                or acquisition not in contract.acquisition_contracts
                or failure_kind not in acquisition.allowed_retry_failure_kinds
                or not isinstance(response, Mapping)
                or response.get("transport_status") != "failed"
                or response.get("failure_kind") != failure_kind
                or _nonnegative_int(response.get("item_count")) != 0
            ):
                blockers.add("coverage_retryable_failure_evidence_invalid")
            return False
        if disposition in {
            "ambiguous_transport",
            "permission_denied",
            "schema_mismatch",
            "cap_suspected",
        }:
            blockers.add(f"coverage_{disposition}")
        return False
    if disposition == "not_applicable":
        applicability = receipt.get("applicability_evidence")
        authorities = dict(contract.not_applicable_authorities) if contract else {}
        reason = (
            str(applicability.get("reason") or "")
            if isinstance(applicability, Mapping)
            else ""
        )
        authority_ids = (
            applicability.get("authority_obligation_ids")
            if isinstance(applicability, Mapping)
            else None
        )
        if (
            not isinstance(applicability, Mapping)
            or not isinstance(authority_ids, Sequence)
            or isinstance(authority_ids, (str, bytes))
            or not authority_ids
        ):
            blockers.add("coverage_not_applicable_evidence_invalid")
            return False
        semantic = {
            "reason": reason,
            "authority_obligation_ids": list(authority_ids),
        }
        if (
            reason not in authorities
            or applicability.get("content_hash") != canonical_hash(semantic)
        ):
            blockers.add("coverage_not_applicable_evidence_invalid")
            return False
        return True

    request = receipt.get("request")
    response = receipt.get("response")
    pagination = receipt.get("pagination")
    if not isinstance(request, Mapping) or not isinstance(response, Mapping) or not isinstance(pagination, Mapping):
        blockers.add("coverage_receipt_shape_invalid")
        return False
    required_request_values = (
        request.get("provider"),
        request.get("provider_adapter"),
        request.get("endpoint"),
        request.get("provider_api_version"),
        request.get("adapter_schema_version"),
        request.get("permission_context_id"),
    )
    if any(not str(value or "") for value in required_request_values):
        blockers.add("coverage_receipt_request_identity_invalid")
        return False
    if not isinstance(request.get("normalized_params"), Mapping):
        blockers.add("coverage_receipt_request_identity_invalid")
        return False
    if str(request.get("request_fingerprint") or "") != _expected_request_fingerprint(
        request
    ) or not _sha256_hex(
        str(request.get("evidence_use_identity") or "")
    ):
        blockers.add("coverage_receipt_request_identity_invalid")
        return False
    fields = request.get("fields")
    response_fields = response.get("response_fields")
    if (
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(field, str) or not field for field in fields)
        or len(set(fields)) != len(fields)
        or not isinstance(response_fields, list)
        or fields != response_fields
    ):
        blockers.add("coverage_receipt_schema_invalid")
        return False
    if (
        str(response.get("transport_status") or "") != "completed"
        or response.get("provider_code") != 0
        or pagination.get("terminal") is not True
        or pagination.get("cap_suspected") is not False
    ):
        blockers.add("coverage_receipt_terminal_evidence_invalid")
        return False
    row_cap = _positive_int(pagination.get("row_cap"))
    returned_count = _nonnegative_int(pagination.get("returned_count"))
    item_count = _nonnegative_int(response.get("item_count"))
    if row_cap is None or returned_count is None or item_count is None or returned_count != item_count:
        blockers.add("coverage_receipt_count_invalid")
        return False
    if returned_count >= row_cap and pagination.get("end_marker") is not True:
        blockers.add("coverage_cap_suspected")
        return False

    relative_path = str(response.get("raw_envelope_relative_path") or "")
    raw_path = _confined_path(evidence_root, relative_path)
    if raw_path is None or not raw_path.is_file():
        blockers.add("coverage_raw_envelope_missing")
        return False
    if str(response.get("raw_envelope_sha256") or "") != sha256_file(raw_path):
        blockers.add("coverage_raw_envelope_hash_invalid")
        return False
    raw_payload = _read_json_object(raw_path)
    if raw_payload is None or str(response.get("response_payload_hash") or "") != canonical_hash(raw_payload):
        blockers.add("coverage_response_payload_hash_invalid")
        return False
    data = raw_payload.get("data")
    raw_fields = data.get("fields") if isinstance(data, Mapping) else None
    raw_items = data.get("items") if isinstance(data, Mapping) else None
    if (
        raw_payload.get("code") != 0
        or not isinstance(data, Mapping)
        or not isinstance(raw_fields, list)
        or any(not isinstance(field, str) for field in raw_fields)
        or raw_fields != fields
        or not isinstance(raw_items, list)
    ):
        blockers.add("coverage_raw_envelope_schema_invalid")
        return False
    items = raw_items
    if (
        len(items) != returned_count
        or any(
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes))
            or len(item) != len(fields)
            for item in items
        )
        or str(response.get("records_hash") or "") != canonical_hash(items)
    ):
        blockers.add("coverage_records_hash_invalid")
        return False
    projection = request.get("obligation_record_projection")
    if not isinstance(projection, Mapping) or obligation is None:
        blockers.add("coverage_response_obligation_mapping_invalid")
        return False
    subject_field = str(projection.get("subject_field") or "")
    date_field = str(projection.get("date_field") or "")
    if subject_field not in fields or date_field not in fields:
        blockers.add("coverage_response_obligation_mapping_invalid")
        return False
    subject_index = list(fields).index(subject_field)
    date_index = list(fields).index(date_field)
    if obligation.subject_kind == "market" and obligation.dataset == "securities":
        try:
            status_index = list(fields).index("list_status")
        except ValueError:
            blockers.add("coverage_response_obligation_mapping_invalid")
            return False
        expected_status = obligation.subject.removeprefix("list_status:")
        mapped_invalid = expected_status not in {"L", "D", "P"} or any(
            not str(item[subject_index] or "")
            or _valid_date(str(item[date_index] or "")) is None
            or str(item[status_index] or "") != expected_status
            for item in items
        )
    else:
        mapped_invalid = any(
            str(item[subject_index]) != obligation.subject
            or not obligation.date_start
            <= str(item[date_index] or "")[:8]
            <= obligation.date_end
            for item in items
        )
    if mapped_invalid:
        blockers.add("coverage_response_obligation_mapping_invalid")
        return False
    if disposition == "satisfied_empty" and returned_count != 0:
        blockers.add("coverage_observed_empty_invalid")
        return False
    if (
        disposition == "satisfied_empty"
        and (contract is None or contract.empty_policy != "observed_empty_allowed")
    ):
        blockers.add("coverage_observed_empty_for_dense_dataset")
        return False
    if disposition == "satisfied_nonempty" and returned_count == 0:
        blockers.add("coverage_nonempty_receipt_invalid")
        return False
    return True


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _verifier_identity() -> str:
    return canonical_hash(
        {
            "schema_version": ADMISSION_VERDICT_SCHEMA,
            "coverage_plan_schema_version": COVERAGE_PLAN_SCHEMA,
            "coverage_attempt_schema_version": COVERAGE_ATTEMPT_SCHEMA,
            "coverage_receipt_schema_version": COVERAGE_RECEIPT_SCHEMA,
            "capture_signature_contract": "approved_rsa_sha256_capture_key_v1",
            "authority": "independent_admission_verifier",
            "admission_code_sha256": sha256_file(Path(__file__)),
            "artifact_storage_code_sha256": sha256_file(
                Path(str(artifact_storage.__file__))
            ),
            "receipt_signing_code_sha256": sha256_file(
                Path(str(receipt_signing.__file__))
            ),
            "python_implementation": sys.implementation.name,
            "python_version": list(sys.version_info[:3]),
        }
    )


def _capture_public_key(
    payload: Mapping[str, Any],
    blockers: set[str],
) -> bytes | None:
    if payload.get("capture_key_isolated") is not True:
        blockers.add("coverage_capture_key_isolation_invalid")
        return None
    try:
        public_key = base64.b64decode(
            str(payload.get("capture_public_key_pem_b64") or ""),
            validate=True,
        )
        public_key_text = public_key.decode("ascii")
    except (ValueError, UnicodeDecodeError):
        blockers.add("coverage_capture_public_key_invalid")
        return None
    if canonical_hash(public_key_text) != payload.get("capture_public_key_sha256"):
        blockers.add("coverage_capture_public_key_invalid")
        return None
    return public_key


def _verify_capture_signature(
    payload: Mapping[str, Any],
    *,
    public_key_pem: bytes,
    signature_field: str,
) -> bool:
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"event_hash", signature_field}
    }
    try:
        verify_signature(
            public_key_pem=public_key_pem,
            payload=_canonical_bytes(semantic),
            signature_b64=str(payload.get(signature_field) or ""),
        )
    except (ReceiptSigningError, OSError):
        return False
    return True


def _expected_request_fingerprint(request: Mapping[str, Any]) -> str:
    return canonical_hash(
        {
            "canonical_dataset": request.get("canonical_dataset"),
            "provider": request.get("provider"),
            "provider_adapter": request.get("provider_adapter"),
            "endpoint": request.get("endpoint"),
            "provider_api_version": request.get("provider_api_version"),
            "adapter_schema_version": request.get("adapter_schema_version"),
            "permission_context_id": request.get("permission_context_id"),
            "capture_public_key_sha256": request.get("capture_public_key_sha256"),
            "pagination_mode": request.get("pagination_mode"),
            "row_cap": request.get("row_cap"),
            "allowed_retry_failure_kinds": request.get(
                "allowed_retry_failure_kinds"
            ),
            "normalized_params": request.get("normalized_params"),
            "fields": request.get("fields"),
            "read_only": request.get("read_only"),
            "max_retries": request.get("max_retries"),
            "pagination_plan": request.get("pagination_plan"),
            "obligation_record_projection": request.get("obligation_record_projection"),
        }
    )


def _expected_evidence_use_identity(
    contract: DatasetAdmissionContract,
    acquisition: ProviderAcquisitionContract,
) -> str:
    return canonical_hash(
        {
            "schema_version": "coverage_evidence_use_v1",
            "dataset": contract.dataset,
            "approved_fields": list(contract.approved_fields),
            "record_subject_field": contract.record_subject_field,
            "record_date_field": contract.record_date_field,
            "read_only_required": contract.read_only_required,
            "max_retries": contract.max_retries,
            "max_split_leaves": contract.max_split_leaves,
            "acquisition_contract": acquisition.to_dict(),
        }
    )


def _split_plan_valid(
    leaves: Any,
    *,
    obligation: CoverageObligation,
    max_split_leaves: int,
) -> bool:
    if (
        not isinstance(leaves, Sequence)
        or isinstance(leaves, (str, bytes))
        or not leaves
        or len(leaves) > max_split_leaves
        or any(not isinstance(row, Mapping) for row in leaves)
    ):
        return False
    normalized = [dict(row) for row in leaves]
    if [row.get("leaf_ordinal") for row in normalized] != list(
        range(1, len(normalized) + 1)
    ):
        return False
    if (
        str(normalized[0].get("leaf_start") or "") != obligation.date_start
        or str(normalized[-1].get("leaf_end") or "") != obligation.date_end
    ):
        return False
    for row in normalized:
        start = str(row.get("leaf_start") or "")
        end = str(row.get("leaf_end") or "")
        if _valid_date(start) is None or _valid_date(end) is None or start > end:
            return False
    return all(
        _next_date(str(previous.get("leaf_end")))
        == str(current.get("leaf_start"))
        for previous, current in zip(normalized, normalized[1:])
    )


def _selected_split_leaf(
    leaves: Any,
    *,
    leaf_ordinal: int | None,
    leaf_count: int | None,
) -> dict[str, Any] | None:
    if (
        leaf_ordinal is None
        or leaf_count is None
        or not isinstance(leaves, Sequence)
        or isinstance(leaves, (str, bytes))
        or len(leaves) != leaf_count
        or not 1 <= leaf_ordinal <= leaf_count
        or not isinstance(leaves[leaf_ordinal - 1], Mapping)
    ):
        return None
    return dict(leaves[leaf_ordinal - 1])


def _attempt_lineage_key(event: Mapping[str, Any]) -> tuple[str, ...]:
    request = event.get("request") if isinstance(event.get("request"), Mapping) else {}
    pagination = (
        request.get("pagination_plan")
        if isinstance(request.get("pagination_plan"), Mapping)
        else {}
    )
    obligation_ids = event.get("obligation_ids")
    mapped = (
        tuple(str(item) for item in obligation_ids)
        if isinstance(obligation_ids, Sequence) and not isinstance(obligation_ids, (str, bytes))
        else ()
    )
    return (
        str(event.get("dataset") or ""),
        *mapped,
        str(pagination.get("leaf_ordinal") or ""),
        str(pagination.get("leaf_count") or ""),
        str(pagination.get("leaf_start") or ""),
        str(pagination.get("leaf_end") or ""),
    )


def _attempt_obligation_key(event: Mapping[str, Any]) -> tuple[str, ...]:
    obligation_ids = event.get("obligation_ids")
    mapped = (
        tuple(str(item) for item in obligation_ids)
        if isinstance(obligation_ids, Sequence) and not isinstance(obligation_ids, (str, bytes))
        else ()
    )
    return (str(event.get("dataset") or ""), *mapped)


def _attempt_split_plan_root(event: Mapping[str, Any]) -> str:
    request = event.get("request") if isinstance(event.get("request"), Mapping) else {}
    pagination = (
        request.get("pagination_plan")
        if isinstance(request.get("pagination_plan"), Mapping)
        else {}
    )
    return str(pagination.get("split_plan_root") or "")


def _read_json_lines(path: Path) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                return None
            rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return rows


def _confined_path(root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    lexical_candidate = root / relative
    if lexical_candidate.is_symlink():
        return None
    try:
        root_resolved = root.resolve(strict=False)
        candidate = lexical_candidate.resolve(strict=False)
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return candidate


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _normalized_population(population: CoveragePopulation) -> dict[str, Any]:
    security_ids = [row.security_id for row in population.securities]
    if len(security_ids) != len(set(security_ids)):
        raise AdmissionVerificationError("data_admission_security_population_duplicate")
    for row in population.securities:
        if (
            not row.security_id
            or _valid_date(row.list_date) is None
            or row.delist_date is not None
            and (
                _valid_date(row.delist_date) is None
                or row.delist_date <= row.list_date
            )
        ):
            raise AdmissionVerificationError("data_admission_security_lifecycle_invalid")
    if any(_valid_date(value) is None for value in population.trading_dates):
        raise AdmissionVerificationError("data_admission_trading_date_invalid")
    if any(not str(value) for value in (*population.exchanges, *population.index_codes)):
        raise AdmissionVerificationError("data_admission_partition_subject_invalid")
    return {
        "securities": [
            row.to_dict()
            for row in sorted(population.securities, key=lambda item: item.security_id)
        ],
        "trading_dates": sorted(set(population.trading_dates)),
        "exchanges": sorted(set(population.exchanges)),
        "index_codes": sorted(set(population.index_codes)),
    }


def _validate_scope(scope: DataAdmissionScope) -> None:
    if (
        scope.access_view not in {"bootstrap", "research", "validation", "retrospective_test", "sealed_holdout"}
        or _valid_date(scope.date_start) is None
        or _valid_date(scope.date_end) is None
        or _valid_date(scope.as_of_market_date) is None
        or scope.date_start > scope.date_end
        or scope.as_of_market_date < scope.date_end
    ):
        raise AdmissionVerificationError("data_admission_scope_invalid")


def _valid_date(value: str) -> str | None:
    if len(value) != 8 or not value.isdigit():
        return None
    try:
        datetime(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
    return value


def _valid_instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _previous_date(value: str) -> str:
    parsed = datetime(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return (parsed - timedelta(days=1)).strftime("%Y%m%d")


def _next_date(value: str) -> str:
    parsed = datetime(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return (parsed + timedelta(days=1)).strftime("%Y%m%d")
