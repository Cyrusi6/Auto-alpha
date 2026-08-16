from __future__ import annotations

import pytest

from auto_alpha.data.lake.store.admission import (
    AdmissionVerificationError,
    CoveragePopulation,
    DataAdmissionScope,
    SecurityLifecycle,
    compile_coverage_plan,
)
from tests.data.admission_evidence import controlled_dataset_row, inactive_dataset_row


def test_coverage_plan_respects_lifecycle_and_inactive_dataset_roles() -> None:
    profile = {
        "schema_version": "data_admission_profile_v1",
        "profile_name": "lifecycle_fixture",
        "activated_feature_families": [],
        "datasets": [
            controlled_dataset_row("st_status_daily"),
            inactive_dataset_row("name_changes"),
        ],
    }
    scope = DataAdmissionScope(
        access_view="research",
        date_start="20190102",
        date_end="20190105",
        as_of_market_date="20190105",
    )
    population = CoveragePopulation(
        securities=(
            SecurityLifecycle(
                security_id="000001.SZ",
                list_date="20180101",
                delist_date="20190104",
            ),
        ),
        trading_dates=("20190102", "20190103", "20190104"),
    )

    plan = compile_coverage_plan(profile, scope, population)

    assert [
        (row.dataset, row.subject_kind, row.subject, row.date_start, row.date_end)
        for row in plan.obligations
    ] == [
        ("st_status_daily", "security", "000001.SZ", "20190102", "20190102"),
        ("st_status_daily", "security", "000001.SZ", "20190103", "20190103"),
        ("st_status_daily", "security", "000001.SZ", "20190104", "20190104"),
    ]


def test_coverage_plan_reaches_as_of_watermark_and_seeds_suspension_state() -> None:
    profile = {
        "schema_version": "data_admission_profile_v1",
        "activated_feature_families": [],
        "datasets": [
            controlled_dataset_row(
                dataset,
                requires_pre_span_state=dataset == "suspensions",
            )
            for dataset in ("st_status_daily", "suspensions")
        ],
    }
    scope = DataAdmissionScope("research", "20190102", "20190102", "20190104")
    population = CoveragePopulation(
        securities=(SecurityLifecycle("000001.SZ", "20180101"),),
        trading_dates=("20190102", "20190103", "20190104"),
    )

    plan = compile_coverage_plan(profile, scope, population)

    assert [
        (row.subject_kind, row.date_start, row.date_end)
        for row in plan.obligations
        if row.dataset == "st_status_daily"
    ] == [
        ("security", "20190102", "20190102"),
        ("security", "20190103", "20190103"),
        ("security", "20190104", "20190104"),
    ]
    assert [
        (row.subject_kind, row.date_start, row.date_end)
        for row in plan.obligations
        if row.dataset == "suspensions"
    ] == [
        ("security", "20190102", "20190102"),
        ("security", "20190103", "20190103"),
        ("security", "20190104", "20190104"),
        ("security_state_seed", "20180101", "20190101"),
    ]


def test_coverage_plan_rejects_unknown_watermark_instead_of_narrowing_scope() -> None:
    row = controlled_dataset_row("st_status_daily")
    row["coverage_watermark"] = "as_of_market_data"
    profile = {
        "schema_version": "data_admission_profile_v1",
        "activated_feature_families": [],
        "datasets": [row],
    }

    with pytest.raises(
        AdmissionVerificationError,
        match="data_admission_dataset_contract_invalid",
    ):
        compile_coverage_plan(
            profile,
            DataAdmissionScope("research", "20190102", "20190102", "20190104"),
            CoveragePopulation(
                securities=(SecurityLifecycle("000001.SZ", "20180101"),),
                trading_dates=("20190102", "20190103", "20190104"),
            ),
        )
