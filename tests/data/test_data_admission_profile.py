from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path

from auto_alpha.data.lake.store.admission import (
    CoveragePopulation,
    DataAdmissionScope,
    SecurityLifecycle,
    compile_coverage_plan,
    first_data_admission_profile,
)
from auto_alpha.platform.artifacts.schema.validator import validate_artifact


BASE_REQUIRED = {
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
}

FEATURE_FAMILY_CONDITIONAL = {
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
}

INACTIVE = {
    "index_basic",
    "industry_classification",
    "financial_audit",
    "main_business",
    "holder_trades",
    "pledge_detail",
    "pledge_stat",
}


def test_first_data_admission_profile_classifies_every_dataset_exactly_once() -> None:
    profile = first_data_admission_profile()
    rows = list(profile["datasets"])
    names = [str(row["dataset"]) for row in rows]

    assert len(rows) == 41
    assert len(names) == len(set(names))
    assert _datasets_with_role(rows, "base-required") == BASE_REQUIRED
    assert _datasets_with_role(rows, "feature-family-conditional") == FEATURE_FAMILY_CONDITIONAL
    assert _datasets_with_role(rows, "inactive") == INACTIVE
    assert BASE_REQUIRED | FEATURE_FAMILY_CONDITIONAL | INACTIVE == set(names)
    for row in rows:
        assert row["coverage_granularity"]
        assert row["evidence_grade"]
        if row["role"] == "inactive":
            assert row["approved_fields"] == []
            assert row["consumer_roles"] == []
        else:
            assert row["approved_fields"]
            assert row["consumer_roles"]
            assert row["empty_policy"] in {
                "nonempty_required",
                "observed_empty_allowed",
            }
    by_name = {str(row["dataset"]): row for row in rows}
    assert set(by_name["daily_basic"]["approved_fields"]) == {
        "ts_code",
        "trade_date",
        "turnover_rate",
        "volume_ratio",
        "total_mv",
    }
    assert set(by_name["daily_limits"]["approved_fields"]) == {
        "ts_code",
        "trade_date",
        "up_limit",
        "down_limit",
        "pre_close",
    }
    assert {
        "implementation_date",
        "record_date",
        "process_state",
        "base_shares",
    } <= set(by_name["corporate_actions"]["approved_fields"])


def test_first_data_admission_profile_has_a_stable_content_address(
    tmp_path: Path,
) -> None:
    first = first_data_admission_profile()
    second = first_data_admission_profile()

    assert first == second
    assert first["profile_id"] == second["profile_id"]
    assert first["content_hash"] == second["content_hash"]
    assert first["profile_id"] == f"dap_{first['content_hash'][:24]}"
    assert len(first["content_hash"]) == 64
    assert set(first["content_hash"]) <= set("0123456789abcdef")

    reordered = deepcopy(dict(first))
    reordered["datasets"] = list(reversed(list(first["datasets"])))
    reordered.pop("profile_id")
    reordered.pop("content_hash")

    assert compile_coverage_plan(first, _scope(), _population()).profile_id == compile_coverage_plan(
        reordered,
        _scope(),
        _population(),
    ).profile_id
    profile_path = tmp_path / "data_admission_profile.json"
    profile_path.write_text(json.dumps(first, sort_keys=True), encoding="utf-8")
    assert validate_artifact(profile_path, strict=True).valid is True


def test_conditional_datasets_create_obligations_only_after_family_activation() -> None:
    profile = first_data_admission_profile()
    conditional_rows = [
        row
        for row in profile["datasets"]
        if row["role"] == "feature-family-conditional"
    ]
    conditional_names = {str(row["dataset"]) for row in conditional_rows}
    inactive_names = _datasets_with_role(profile["datasets"], "inactive")

    without_activation = compile_coverage_plan(profile, _scope(), _population())
    without_activation_names = {row.dataset for row in without_activation.obligations}

    assert conditional_names.isdisjoint(without_activation_names)
    assert inactive_names.isdisjoint(without_activation_names)

    selected = next(
        row
        for row in conditional_rows
        if row["coverage_granularity"] == "security_day"
    )
    activated_family = str(selected["feature_family"])
    activated = deepcopy(dict(profile))
    activated["activated_feature_families"] = [activated_family]
    activated.pop("profile_id")
    activated.pop("content_hash")

    with_activation = compile_coverage_plan(activated, _scope(), _population())
    with_activation_names = {row.dataset for row in with_activation.obligations}
    expected_activated_names = {
        str(row["dataset"])
        for row in conditional_rows
        if row["feature_family"] == activated_family
    }

    assert expected_activated_names
    assert expected_activated_names <= with_activation_names
    assert (conditional_names - expected_activated_names).isdisjoint(with_activation_names)
    assert inactive_names.isdisjoint(with_activation_names)


def _datasets_with_role(rows: Iterable[Mapping[str, object]], role: str) -> set[str]:
    return {
        str(row["dataset"])
        for row in rows
        if row["role"] == role
    }


def _scope() -> DataAdmissionScope:
    return DataAdmissionScope(
        access_view="research",
        date_start="20190102",
        date_end="20190102",
        as_of_market_date="20190102",
    )


def _population() -> CoveragePopulation:
    return CoveragePopulation(
        securities=(
            SecurityLifecycle(
                security_id="000001.SZ",
                list_date="20100101",
            ),
        ),
        trading_dates=("20190102",),
        exchanges=("SSE", "SZSE"),
        index_codes=("000300.SH",),
    )
