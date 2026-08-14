from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from auto_alpha.data.lake.store.admission import (
    CoveragePlan,
    CoveragePopulation,
    DataAdmissionScope,
    SecurityLifecycle,
    compile_coverage_plan,
    verify_coverage,
)
from auto_alpha.platform.artifacts.storage import canonical_hash, sha256_file
from tests.data.admission_evidence import (
    build_attempt_pair,
    controlled_dataset_row,
    reseal_pair,
    reseal_receipt,
    request_fingerprint,
    write_coverage_evidence,
    write_json,
)


def test_verify_coverage_rejects_self_attested_complete_without_receipts(
    tmp_path: Path,
) -> None:
    plan = _one_obligation_plan()
    evidence_root = write_coverage_evidence(
        tmp_path / "coverage",
        plan=plan,
        events=[],
        producer_complete=True,
        producer_coverage_root="f" * 64,
    )

    verification = verify_coverage(plan, evidence_root)

    assert verification.outcome == "blocked"
    assert verification.coverage_gap == 1
    assert verification.blockers == ("coverage_obligation_unsatisfied",)
    assert verification.coverage_root != "f" * 64


def test_verify_coverage_admits_one_observed_empty_exact_cover(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "admitted"
    assert verification.coverage_gap == 0
    assert verification.blockers == ()
    assert len(verification.coverage_root) == 64


@pytest.mark.parametrize(
    ("corruption", "expected_blocker"),
    [
        ("broken_chain", "coverage_receipt_chain_invalid"),
        ("duplicate_terminal", "coverage_receipt_retry_lineage_invalid"),
    ],
)
def test_verify_coverage_blocks_broken_chain_or_duplicate_satisfaction(
    tmp_path: Path,
    corruption: str,
    expected_blocker: str,
) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    first_start, first_receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="9" * 64 if corruption == "broken_chain" else "",
        empty=True,
    )
    events = [first_start, first_receipt]
    if corruption == "duplicate_terminal":
        second_start, second_receipt = build_attempt_pair(
            root,
            plan.obligations[0],
            attempt_ordinal=2,
            sequence_start=3,
            previous_event_hash=first_receipt["event_hash"],
            empty=True,
        )
        events.extend((second_start, second_receipt))
    write_coverage_evidence(root, plan=plan, events=events)

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert verification.blockers == (expected_blocker,)


def test_failed_attempt_is_retained_when_a_governed_retry_satisfies_obligation(
    tmp_path: Path,
) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    failed_start, failed_receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
        disposition="failed",
    )
    retry_start, retry_receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        attempt_ordinal=2,
        sequence_start=3,
        previous_event_hash=failed_receipt["event_hash"],
        empty=True,
        retry_of_attempt_id=failed_receipt["attempt_id"],
        retry_ordinal=1,
    )
    write_coverage_evidence(
        root,
        plan=plan,
        events=[failed_start, failed_receipt, retry_start, retry_receipt],
    )

    verification = verify_coverage(plan, root)

    assert verification.outcome == "admitted"
    assert verification.receipt_count == 2
    assert verification.satisfied_obligation_count == 1


@pytest.mark.parametrize(
    ("corruption", "expected_blocker"),
    [
        ("cap", "coverage_cap_suspected"),
        ("permission", "coverage_permission_denied"),
        ("orphan", "coverage_receipt_orphaned"),
        ("fake_empty", "coverage_receipt_terminal_evidence_invalid"),
        ("split_gap", "coverage_receipt_pagination_binding_invalid"),
    ],
)
def test_ambiguous_or_unmapped_attempts_never_satisfy_coverage(
    tmp_path: Path,
    corruption: str,
    expected_blocker: str,
) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    if corruption == "cap":
        receipt["terminal_disposition"] = "cap_suspected"
        receipt["pagination"]["cap_suspected"] = True
        reseal_receipt(receipt)
    elif corruption == "permission":
        receipt["terminal_disposition"] = "permission_denied"
        reseal_receipt(receipt)
    elif corruption == "orphan":
        start["obligation_ids"] = ["obl_unknown"]
        receipt["obligation_ids"] = ["obl_unknown"]
        reseal_pair(start, receipt)
    elif corruption == "fake_empty":
        receipt["response"]["provider_code"] = 40203
        reseal_receipt(receipt)
    else:
        receipt["pagination"]["leaf_count"] = 2
        reseal_receipt(receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert verification.coverage_gap == 1
    assert expected_blocker in verification.blockers


def test_attempt_without_post_transport_receipt_is_ambiguous(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, _receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    write_coverage_evidence(root, plan=plan, events=[start])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert verification.coverage_gap == 1
    assert "coverage_ambiguous_transport" in verification.blockers


def test_verify_coverage_rejects_a_self_consistent_empty_plan(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    forged = replace(plan, obligations=(), content_hash="0" * 64)
    root = write_coverage_evidence(tmp_path / "coverage", plan=forged, events=[])

    verification = verify_coverage(forged, root)

    assert verification.outcome == "blocked"
    assert "coverage_plan_obligations_invalid" in verification.blockers
    assert "coverage_plan_content_hash_invalid" in verification.blockers


def test_receipt_must_use_the_profile_approved_fields(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    weak_contract = plan.dataset_contracts[0].__class__.from_mapping(
        controlled_dataset_row(
            "st_status_daily",
            approved_fields=("ts_code", "trade_date"),
        )
    )
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=weak_contract,
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_attempt_approved_fields_mismatch" in verification.blockers


def test_receipt_with_unconsumed_cursor_is_not_terminal(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    receipt["pagination"]["cursor"] = "NEXT_PAGE_EXISTS"
    reseal_receipt(receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_receipt_pagination_binding_invalid" in verification.blockers


def test_malformed_split_plan_blocks_instead_of_crashing(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    pagination = start["request"]["pagination_plan"]
    pagination["leaf_count"] = 2
    pagination["leaf_ordinal"] = 2
    start["request"]["request_fingerprint"] = request_fingerprint(start["request"])
    reseal_pair(start, receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_attempt_obligation_geometry_invalid" in verification.blockers


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_cap", "not-an-integer"),
        ("allowed_retry_failure_kinds", 7),
    ],
)
def test_malformed_acquisition_contract_blocks_instead_of_crashing(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    start["request"][field] = value
    start["request"]["request_fingerprint"] = request_fingerprint(start["request"])
    reseal_pair(start, receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_attempt_acquisition_contract_not_activated" in verification.blockers


def test_malformed_response_fields_blocks_instead_of_crashing(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    receipt["response"]["response_fields"] = 7
    reseal_receipt(receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_receipt_schema_invalid" in verification.blockers


def test_malformed_applicability_authorities_blocks_instead_of_crashing(
    tmp_path: Path,
) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
        disposition="not_applicable",
        applicability_evidence={
            "reason": "proven_suspension",
            "authority_obligation_ids": ["authority"],
        },
    )
    receipt["applicability_evidence"]["authority_obligation_ids"] = 7
    reseal_receipt(receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_not_applicable_evidence_invalid" in verification.blockers


def test_raw_envelope_fields_must_be_a_json_list(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    raw_path = root / receipt["response"]["raw_envelope_relative_path"]
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    payload["data"]["fields"] = {
        field: ordinal for ordinal, field in enumerate(start["request"]["fields"])
    }
    write_json(raw_path, payload)
    receipt["response"]["response_payload_hash"] = canonical_hash(payload)
    receipt["response"]["raw_envelope_sha256"] = sha256_file(raw_path)
    reseal_receipt(receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_raw_envelope_schema_invalid" in verification.blockers


def test_failed_attempt_cannot_be_bypassed_by_a_new_root_attempt(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    first_start, first_receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
        disposition="failed",
    )
    second_start, second_receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=2,
        sequence_start=3,
        previous_event_hash=first_receipt["event_hash"],
        empty=True,
    )
    write_coverage_evidence(
        root,
        plan=plan,
        events=[first_start, first_receipt, second_start, second_receipt],
    )

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_receipt_retry_lineage_invalid" in verification.blockers


def test_split_pages_must_exactly_cover_the_atomic_obligation(tmp_path: Path) -> None:
    plan = _span_obligation_plan()
    obligation = plan.obligations[0]
    contract = plan.dataset_contracts[0]

    for gap, expected in ((False, "admitted"), (True, "blocked")):
        root = tmp_path / ("gap" if gap else "exact")
        split_leaves = [
            {
                "leaf_ordinal": 1,
                "leaf_start": "20190102",
                "leaf_end": "20190102" if gap else "20190103",
            },
            {
                "leaf_ordinal": 2,
                "leaf_start": "20190104",
                "leaf_end": "20190104",
            },
        ]
        first_start, first_receipt = build_attempt_pair(
            root,
            obligation,
            contract=contract,
            population=plan.population,
            attempt_ordinal=1,
            sequence_start=1,
            previous_event_hash="",
            empty=True,
            leaf_ordinal=1,
            leaf_count=2,
            leaf_start="20190102",
            leaf_end="20190102" if gap else "20190103",
            split_leaves=split_leaves,
        )
        second_start, second_receipt = build_attempt_pair(
            root,
            obligation,
            contract=contract,
            population=plan.population,
            attempt_ordinal=2,
            sequence_start=3,
            previous_event_hash=first_receipt["event_hash"],
            empty=True,
            leaf_ordinal=2,
            leaf_count=2,
            leaf_start="20190104",
            leaf_end="20190104",
            split_leaves=split_leaves,
        )
        write_coverage_evidence(
            root,
            plan=plan,
            events=[first_start, first_receipt, second_start, second_receipt],
        )

        verification = verify_coverage(plan, root)

        assert verification.outcome == expected
        if gap:
            assert "coverage_attempt_obligation_geometry_invalid" in verification.blockers


def test_attempt_signature_cannot_be_replaced_with_a_plain_hash(tmp_path: Path) -> None:
    plan = _one_obligation_plan()
    root = tmp_path / "coverage"
    start, receipt = build_attempt_pair(
        root,
        plan.obligations[0],
        contract=plan.dataset_contracts[0],
        population=plan.population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
    )
    start["attempt_start_signature"] = "0" * 64
    start["event_hash"] = canonical_hash(
        {key: value for key, value in start.items() if key != "event_hash"}
    )
    receipt["attempt_started_event_hash"] = start["event_hash"]
    receipt["previous_event_hash"] = start["event_hash"]
    reseal_receipt(receipt)
    write_coverage_evidence(root, plan=plan, events=[start, receipt])

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_attempt_start_signature_invalid" in verification.blockers


def test_security_master_receipt_must_match_the_full_plan_population(tmp_path: Path) -> None:
    profile = {
        "schema_version": "data_admission_profile_v1",
        "activated_feature_families": [],
        "datasets": [
            controlled_dataset_row(
                "securities",
                coverage_granularity="market_span",
                approved_fields=(
                    "ts_code",
                    "symbol",
                    "exchange",
                    "board",
                    "list_date",
                    "delist_date",
                    "list_status",
                ),
                empty_policy="nonempty_required",
                record_subject_field="ts_code",
                record_date_field="list_date",
                coverage_subjects=("CN_A_SHARE",),
            ),
            controlled_dataset_row(
                "trade_calendar",
                coverage_granularity="exchange_span",
                approved_fields=("exchange", "trade_date", "is_open", "prev_trade_date"),
                empty_policy="nonempty_required",
                record_subject_field="exchange",
                record_date_field="trade_date",
                coverage_subjects=("SSE", "SZSE"),
            ),
        ],
    }
    population = CoveragePopulation(
        securities=(
            SecurityLifecycle("000001.SZ", "20100104"),
            SecurityLifecycle("600000.SH", "20000104"),
        ),
        trading_dates=("20190102",),
        exchanges=("SSE", "SZSE"),
    )
    plan = compile_coverage_plan(
        profile,
        DataAdmissionScope("research", "20190102", "20190102", "20190102"),
        population,
    )
    incomplete_population = CoveragePopulation(
        securities=(population.securities[0],),
        trading_dates=population.trading_dates,
        exchanges=population.exchanges,
    )
    contracts = {row.dataset: row for row in plan.dataset_contracts}
    events = []
    previous_hash = ""
    for ordinal, obligation in enumerate(plan.obligations, start=1):
        start, receipt = build_attempt_pair(
            tmp_path / "coverage",
            obligation,
            contract=contracts[obligation.dataset],
            population=(
                incomplete_population
                if obligation.dataset == "securities"
                else population
            ),
            attempt_ordinal=ordinal,
            sequence_start=2 * ordinal - 1,
            previous_event_hash=previous_hash,
            empty=False,
        )
        events.extend((start, receipt))
        previous_hash = receipt["event_hash"]
    root = write_coverage_evidence(
        tmp_path / "coverage",
        plan=plan,
        events=events,
    )

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_security_population_mismatch" in verification.blockers


def test_security_master_record_must_match_its_status_partition(
    tmp_path: Path,
) -> None:
    profile = {
        "schema_version": "data_admission_profile_v1",
        "activated_feature_families": [],
        "datasets": [
            controlled_dataset_row(
                "securities",
                coverage_granularity="market_span",
                approved_fields=(
                    "ts_code",
                    "symbol",
                    "exchange",
                    "board",
                    "list_date",
                    "delist_date",
                    "list_status",
                ),
                coverage_subjects=("CN_A_SHARE",),
                record_subject_field="ts_code",
                record_date_field="list_date",
            )
        ],
    }
    population = CoveragePopulation(
        securities=(SecurityLifecycle("000001.SZ", "20100104"),),
        trading_dates=("20190102",),
    )
    plan = compile_coverage_plan(
        profile,
        DataAdmissionScope("research", "20190102", "20190102", "20190102"),
        population,
    )
    contract = plan.dataset_contracts[0]
    events = []
    previous_hash = ""
    for ordinal, obligation in enumerate(plan.obligations, start=1):
        is_listed_partition = obligation.subject == "list_status:L"
        start, receipt = build_attempt_pair(
            tmp_path / "coverage",
            obligation,
            contract=contract,
            population=population,
            attempt_ordinal=ordinal,
            sequence_start=2 * ordinal - 1,
            previous_event_hash=previous_hash,
            empty=not is_listed_partition,
            record_overrides=(
                {"list_status": "D"} if is_listed_partition else None
            ),
        )
        events.extend((start, receipt))
        previous_hash = receipt["event_hash"]
    root = write_coverage_evidence(
        tmp_path / "coverage",
        plan=plan,
        events=events,
    )

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_response_obligation_mapping_invalid" in verification.blockers


def test_not_applicable_requires_a_positive_independent_authority(
    tmp_path: Path,
) -> None:
    daily = controlled_dataset_row(
        "daily_bars",
        approved_fields=("ts_code", "trade_date", "close"),
        empty_policy="nonempty_required",
    )
    daily["not_applicable_authorities"] = {
        "proven_suspension": ["suspensions"]
    }
    suspension = controlled_dataset_row(
        "suspensions",
        approved_fields=("ts_code", "trade_date", "suspend_type", "suspend_timing"),
    )
    profile = {
        "schema_version": "data_admission_profile_v1",
        "activated_feature_families": [],
        "datasets": [daily, suspension],
    }
    population = CoveragePopulation(
        securities=(SecurityLifecycle("000001.SZ", "20100104"),),
        trading_dates=("20190102",),
    )
    plan = compile_coverage_plan(
        profile,
        DataAdmissionScope("research", "20190102", "20190102", "20190102"),
        population,
    )
    obligations = {row.dataset: row for row in plan.obligations}
    contracts = {row.dataset: row for row in plan.dataset_contracts}
    root = tmp_path / "coverage"
    daily_start, daily_receipt = build_attempt_pair(
        root,
        obligations["daily_bars"],
        contract=contracts["daily_bars"],
        population=population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
        disposition="not_applicable",
        applicability_evidence={
            "reason": "proven_suspension",
            "authority_obligation_ids": [obligations["suspensions"].obligation_id],
        },
    )
    suspension_start, suspension_receipt = build_attempt_pair(
        root,
        obligations["suspensions"],
        contract=contracts["suspensions"],
        population=population,
        attempt_ordinal=2,
        sequence_start=3,
        previous_event_hash=daily_receipt["event_hash"],
        empty=False,
    )
    write_coverage_evidence(
        root,
        plan=plan,
        events=[
            daily_start,
            daily_receipt,
            suspension_start,
            suspension_receipt,
        ],
    )

    verification = verify_coverage(plan, root)

    assert verification.outcome == "admitted"
    assert verification.coverage_gap == 0


@pytest.mark.parametrize(
    "record_overrides",
    [
        {"suspend_type": "R", "suspend_timing": "before_open"},
        {"suspend_type": "S", "suspend_timing": "after_close"},
    ],
    ids=("resumed-before-open", "suspended-after-close"),
)
def test_partial_day_event_does_not_prove_full_day_suspension(
    tmp_path: Path,
    record_overrides: dict[str, str],
) -> None:
    daily = controlled_dataset_row(
        "daily_bars",
        approved_fields=("ts_code", "trade_date", "close"),
        empty_policy="nonempty_required",
    )
    daily["not_applicable_authorities"] = {
        "proven_suspension": ["suspensions"]
    }
    suspension = controlled_dataset_row(
        "suspensions",
        approved_fields=("ts_code", "trade_date", "suspend_type", "suspend_timing"),
    )
    profile = {
        "schema_version": "data_admission_profile_v1",
        "activated_feature_families": [],
        "datasets": [daily, suspension],
    }
    population = CoveragePopulation(
        securities=(SecurityLifecycle("000001.SZ", "20100104"),),
        trading_dates=("20190102",),
    )
    plan = compile_coverage_plan(
        profile,
        DataAdmissionScope("research", "20190102", "20190102", "20190102"),
        population,
    )
    obligations = {row.dataset: row for row in plan.obligations}
    contracts = {row.dataset: row for row in plan.dataset_contracts}
    root = tmp_path / "coverage"
    daily_start, daily_receipt = build_attempt_pair(
        root,
        obligations["daily_bars"],
        contract=contracts["daily_bars"],
        population=population,
        attempt_ordinal=1,
        sequence_start=1,
        previous_event_hash="",
        empty=True,
        disposition="not_applicable",
        applicability_evidence={
            "reason": "proven_suspension",
            "authority_obligation_ids": [obligations["suspensions"].obligation_id],
        },
    )
    suspension_start, suspension_receipt = build_attempt_pair(
        root,
        obligations["suspensions"],
        contract=contracts["suspensions"],
        population=population,
        attempt_ordinal=2,
        sequence_start=3,
        previous_event_hash=daily_receipt["event_hash"],
        empty=False,
        record_overrides=record_overrides,
    )
    write_coverage_evidence(
        root,
        plan=plan,
        events=[
            daily_start,
            daily_receipt,
            suspension_start,
            suspension_receipt,
        ],
    )

    verification = verify_coverage(plan, root)

    assert verification.outcome == "blocked"
    assert "coverage_not_applicable_authority_invalid" in verification.blockers


def _one_obligation_plan() -> CoveragePlan:
    profile = {
        "schema_version": "data_admission_profile_v1",
        "profile_name": "single_st_day_fixture",
        "activated_feature_families": [],
        "datasets": [controlled_dataset_row("st_status_daily")],
    }
    return compile_coverage_plan(
        profile,
        DataAdmissionScope("research", "20190102", "20190102", "20190102"),
        CoveragePopulation(
            securities=(SecurityLifecycle("000001.SZ", "20100104"),),
            trading_dates=("20190102",),
        ),
    )


def _span_obligation_plan() -> CoveragePlan:
    profile = {
        "schema_version": "data_admission_profile_v1",
        "profile_name": "span_fixture",
        "activated_feature_families": [],
        "datasets": [
            controlled_dataset_row(
                "corporate_actions",
                coverage_granularity="security_span",
                approved_fields=("ts_code", "ann_date", "event_type"),
                record_subject_field="ts_code",
                record_date_field="ann_date",
            )
        ],
    }
    return compile_coverage_plan(
        profile,
        DataAdmissionScope("research", "20190102", "20190104", "20190104"),
        CoveragePopulation(
            securities=(SecurityLifecycle("000001.SZ", "20100104"),),
            trading_dates=("20190102", "20190103", "20190104"),
        ),
    )
