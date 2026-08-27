import base64
import hashlib
import json

import pytest

from auto_alpha.data.pit.corporate_actions.normalizer import (
    bind_cninfo_documents_to_security_identity_intervals,
    build_cninfo_text_extractor_contract,
    extract_cninfo_document_text,
    normalize_corporate_action_records,
    parse_cninfo_corporate_action_documents,
    project_cninfo_corporate_action_event_versions,
    publish_cninfo_corporate_action_semantics,
    validate_cninfo_corporate_action_semantics,
)
from auto_alpha.data.pit.corporate_actions.reconciliation import (
    derive_causal_adjustment_factor_vintages,
)
from auto_alpha.data.pit.corporate_actions.run_actions import main as actions_main
from auto_alpha.data.pit.corporate_actions.report import write_corporate_action_report
from auto_alpha.data.ingestion.pipeline.ashare import AShareDataConfig, AShareDataManager
from auto_alpha.research.formulas.data_loader import AShareDataLoader
from auto_alpha.execution.trading.paper import LocalPaperAccount
from auto_alpha.execution.trading.paper import PaperPosition
from auto_alpha.platform.artifacts.storage import canonical_hash


def _prepare_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    return data_dir


def test_normalize_corporate_actions_from_sample(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]

    events = normalize_corporate_action_records(records)

    assert len(events) == 4
    assert any(event.action_type == "cash_dividend" for event in events)
    assert any(event.stock_transfer_ratio > 0 for event in events)
    assert any(event.stock_distribution_ratio > 0 for event in events)
    assert any(event.action_type == "combined_distribution" for event in events)
    assert any(event.action_type == "proposal_only" for event in events)
    assert len({event.action_id for event in events}) == len(events)


def test_write_corporate_action_report_outputs_artifacts(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]
    events = normalize_corporate_action_records(records)

    paths = write_corporate_action_report(data_dir, events, tmp_path / "actions", "20240102", "20240104", reconcile_adjustment=True)

    assert (tmp_path / "actions" / "corporate_actions_report.json").exists()
    assert (tmp_path / "actions" / "total_return_series.jsonl").exists()
    assert (tmp_path / "actions" / "adjustment_factor_reconciliation.json").exists()
    report = json.loads((tmp_path / "actions" / "corporate_actions_report.json").read_text())
    assert report["event_count"] == 4
    assert paths["total_return_report_path"].endswith("total_return_report.json")


def test_paper_account_applies_corporate_actions_idempotently(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]
    events = normalize_corporate_action_records(records)
    account = LocalPaperAccount(tmp_path / "account")
    state = account.reset(1000.0)
    state.positions["000001.SZ"] = PaperPosition(ts_code="000001.SZ", shares=1000, avg_cost=10.0)
    account.save_state(state)

    first_state, first_apps = account.apply_corporate_actions(events, trade_date="20240104", mode="pay_date")
    second_state, second_apps = account.apply_corporate_actions(events, trade_date="20240104", mode="pay_date")

    assert sum(app.status == "APPLIED" for app in first_apps) == 1
    assert sum(app.status == "APPLIED" for app in second_apps) == 1
    assert first_state.cash == second_state.cash
    assert len(second_state.corporate_action_ledger) == 1
    assert (tmp_path / "account" / "corporate_action_ledger.jsonl").exists()


def test_loader_corporate_action_total_return_mode(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]
    events = normalize_corporate_action_records(records)
    write_corporate_action_report(data_dir, events, tmp_path / "actions", "20240102", "20240104")

    loader = AShareDataLoader(
        data_dir,
        corporate_action_aware=True,
        corporate_action_dir=tmp_path / "actions",
        target_return_mode="corporate_action_total_return",
    ).load_data()

    assert len(loader.corporate_action_events) == 4
    assert "total_return_close" in loader.raw_data_cache
    assert "corporate_action_flag" in loader.raw_data_cache
    assert loader.target_ret.shape == loader.raw_data_cache["close"].shape


def test_causal_adjustment_vintage_uses_only_known_effective_events():
    bars = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240103", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240104", "pre_close": 9},
    ]
    events = [
        {
            "event_id": "cash-dividend-2024",
            "event_version_id": "event-v1",
            "ts_code": "000001.SZ",
            "known_at": "20240102",
            "effective_at": "20240103",
            "cash_div_per_share": "1",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "a" * 64,
            "pit_evidence_eligible": True,
        },
        {
            "event_id": "late-cash-dividend-2024",
            "event_version_id": "late-event-v1",
            "ts_code": "000001.SZ",
            "known_at": "20240105",
            "effective_at": "20240104",
            "cash_div_per_share": "1",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "b" * 64,
            "pit_evidence_eligible": True,
        },
    ]

    result = derive_causal_adjustment_factor_vintages(bars, events)

    assert result["derivation_complete"] is False
    assert result["data_admission_eligible"] is False
    assert [row["causal_adj_factor"] for row in result["rows"]] == [
        "1.000000000000",
        "1.111111111111",
        "1.111111111111",
    ]
    assert result["rows"][1]["event_version_ids"] == ["event-v1"]
    assert result["rows"][2]["event_version_ids"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "corporate_action_known_after_effective"
    }


def test_future_corporate_action_version_cannot_rewrite_past_factors():
    bars = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240103", "pre_close": 10},
    ]
    baseline = derive_causal_adjustment_factor_vintages(bars, [])
    with_future = derive_causal_adjustment_factor_vintages(
        bars,
        [
            {
                "event_id": "future-cash-dividend-2024",
                "event_version_id": "future-v1",
                "ts_code": "000001.SZ",
                "known_at": "20240201",
                "effective_at": "20240202",
                "cash_div_per_share": "1",
                "stock_distribution_ratio": "0",
                "source_document_sha256": "c" * 64,
                "pit_evidence_eligible": True,
            }
        ],
    )

    assert with_future["rows"] == baseline["rows"]


def test_later_corporate_action_revision_cannot_rewrite_applied_factor():
    bars = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240103", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240104", "pre_close": 9},
    ]
    events = [
        {
            "event_id": "cash-dividend-2024",
            "event_version_id": "event-v1",
            "ts_code": "000001.SZ",
            "known_at": "20240102",
            "effective_at": "20240103",
            "cash_div_per_share": "1",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "a" * 64,
            "pit_evidence_eligible": True,
        },
        {
            "event_id": "cash-dividend-2024",
            "event_version_id": "event-v2",
            "ts_code": "000001.SZ",
            "known_at": "20240104",
            "effective_at": "20240103",
            "cash_div_per_share": "2",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "b" * 64,
            "pit_evidence_eligible": True,
        },
    ]

    result = derive_causal_adjustment_factor_vintages(bars, events)

    assert result["derivation_complete"] is True
    assert [row["causal_adj_factor"] for row in result["rows"]] == [
        "1.000000000000",
        "1.111111111111",
        "1.111111111111",
    ]
    assert result["rows"][1]["event_version_ids"] == ["event-v1"]
    assert result["rows"][2]["event_version_ids"] == []


def test_causal_adjustment_vintage_cli_publishes_immutable_blocked_evidence(
    tmp_path,
):
    bars = tmp_path / "bars.jsonl"
    events = tmp_path / "events.jsonl"
    bars.write_text(
        json.dumps(
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240102",
                "pre_close": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events.write_text("", encoding="utf-8")
    output = tmp_path / "vintages"

    first = actions_main(
        [
            "derive-adjustment-vintage",
            "--daily-bars",
            str(bars),
            "--event-versions",
            str(events),
            "--output-dir",
            str(output),
        ]
    )
    second = actions_main(
        [
            "derive-adjustment-vintage",
            "--daily-bars",
            str(bars),
            "--event-versions",
            str(events),
            "--output-dir",
            str(output),
        ]
    )

    pointer = json.loads((output / "current.json").read_text())
    generation = output / "generations" / pointer["manifest"].split("/")[1]
    assert first == 0
    assert second == 0
    assert (generation / "causal_adjustment_vintage_manifest.json").is_file()
    assert (generation / "causal_adjustment_factors.jsonl").is_file()
    manifest = json.loads(
        (generation / "causal_adjustment_vintage_manifest.json").read_text()
    )
    assert manifest["data_admission_eligible"] is False
    assert all(value is False for value in manifest["safety"].values())


def _cninfo_document(
    announcement_id: str,
    title: str,
    body: bytes,
    *,
    announcement_time: int,
    announcement_time_precision_proven: bool = True,
) -> dict[str, object]:
    document_sha256 = hashlib.sha256(body).hexdigest()
    inventory_records = [
        {
            "demand_identity": "1" * 64,
            "inventory_content_hash": "2" * 64,
            "leaf_profile": "base",
            "sec_code": "000001",
            "sec_name": "平安银行",
            "org_id": "gssz0000001",
            "announcement_title": title,
            "announcement_type": "corporate_action",
            "column_id": "corporate_action",
            "matched_leaves": ["corporate_actions_202101"],
        }
    ]
    source_scope_roles = ["corporate_actions"]
    semantic: dict[str, object] = {
        "schema_version": "cninfo_document_postprocess_record_v1",
        "ordinal": int(announcement_id),
        "announcement_id": announcement_id,
        "announcement_time": announcement_time,
        "announcement_time_precision_proven": (
            announcement_time_precision_proven
        ),
        "announcement_title": title,
        "announcement_type": "corporate_action",
        "column_id": "corporate_action",
        "matched_leaves": ["corporate_actions_202101"],
        "source_scope_roles": source_scope_roles,
        "sec_code": "000001",
        "sec_name": "平安银行",
        "org_id": "gssz0000001",
        "security_id": "",
        "adjunct_url": f"finalpage/2021-01-01/{announcement_id}.html",
        "declared_adjunct_size_kb": max(1, len(body) // 1024),
        "document_format": "html",
        "document_sha256": document_sha256,
        "document_size_bytes": len(body),
        "source_request_id": f"cninfo_document_{announcement_id}",
        "source_request_semantic_hash": "3" * 64,
        "source_raw_envelope_sha256": "a" * 64,
        "source_raw_payload_sha256": document_sha256,
        "source_parent_generation_id": "free_provider_backfill_test",
        "source_parent_content_hash": "4" * 64,
        "source_parent_terminal_signature": base64.b64encode(
            b"t" * 256
        ).decode("ascii"),
        "source_parent_publication_signature": base64.b64encode(
            b"p" * 256
        ).decode("ascii"),
        "source_inventory_records": inventory_records,
        "source_inventory_content_hash": canonical_hash(["2" * 64]),
        "source_inventory_scope_root": canonical_hash(
            {
                "source_inventory_records": inventory_records,
                "source_scope_roles": source_scope_roles,
            }
        ),
        "source_document_closure_root": "d" * 64,
        "source_lineage_complete": True,
        "source_governed_evidence_eligible": False,
        "closure_complete": True,
        "closure_downstream_eligible": True,
        "closure_blockers": [],
        "governance_blockers": [
            "independent_data_admission_verdict_required"
        ],
        "data_admission_eligible": False,
        "pit_evidence_eligible": False,
        "independent_data_admission_verdict_required": True,
    }
    record_id = canonical_hash(semantic)
    return semantic | {
        "document_record_id": record_id,
        "body": body,
        "document_body_replay_verified": True,
        "security_id": "ashare_entity_000001",
    }


def _rebind_cninfo_document_record(row: dict[str, object]) -> None:
    semantic = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "body",
            "document_body_replay_verified",
            "document_record_id",
        }
    }
    semantic["security_id"] = ""
    row["document_record_id"] = canonical_hash(semantic)


def test_cninfo_announcement_parser_builds_causal_distribution_terms():
    body = """<!doctype html><html><body>
    <h1>2020年度权益分派实施公告</h1>
    <p>每10股派1.50元（含税），送2股，转增3股。</p>
    <p>股权登记日：2021年06月17日；除权除息日：2021年06月18日；
    现金红利发放日：2021年06月18日；新增股份上市日：2021年06月18日。</p>
    </body></html>""".encode()
    result = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "1001",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1623715200000,
            )
        ]
    )

    assert result["technical_replay_complete"] is True
    assert result["data_admission_eligible"] is False
    assert result["parsed_document_count"] == 1
    event = result["event_versions"][0]
    assert event["stage"] == "implementation"
    assert event["fiscal_period_end"] == "20201231"
    assert event["cash_div_per_share"] == "0.150000000000"
    assert event["stock_bonus_ratio"] == "0.200000000000"
    assert event["stock_transfer_ratio"] == "0.300000000000"
    assert event["record_date"] == "20210617"
    assert event["effective_at"] == "20210618"
    assert event["parser_semantic_complete"] is True
    assert event["semantic_candidate_eligible"] is False
    assert event["pit_evidence_eligible"] is False
    assert event["independent_event_coverage_verdict_required"] is True
    assert result["event_chains"][0]["chain_complete"] is False


def test_cninfo_announcement_parser_links_versions_by_known_time():
    rows = []
    fixtures = (
        (
            "2001",
            "2020年度利润分配预案公告",
            "2020年度利润分配预案：每10股派1元（含税）。",
            1612137600000,
        ),
        (
            "2002",
            "2020年度股东大会决议公告",
            "股东大会审议通过2020年度利润分配方案，每10股派1元（含税）。",
            1619827200000,
        ),
        (
            "2003",
            "2020年度权益分派实施公告",
            "每10股派1元（含税）、送0股、转增0股。"
            "股权登记日：2021年5月19日，除权除息日：2021年5月20日，"
            "现金红利发放日：2021年5月20日。",
            1621209600000,
        ),
        (
            "2004",
            "关于2020年度权益分派实施公告的更正公告",
            "更正后每10股派1.2元（含税）、送0股、转增0股。"
            "股权登记日：2021年5月19日，除权除息日：2021年5月20日，"
            "现金红利发放日：2021年5月20日。",
            1621296000000,
        ),
    )
    for announcement_id, title, text, known_at in fixtures:
        body = f"<html><body>{text}</body></html>".encode()
        rows.append(
            _cninfo_document(
                announcement_id,
                title,
                body,
                announcement_time=known_at,
            )
        )

    first = parse_cninfo_corporate_action_documents(rows)
    second = parse_cninfo_corporate_action_documents(list(reversed(rows)))

    assert first == second
    chain = first["event_chains"][0]
    assert chain["stages_observed"] == [
        "proposal",
        "shareholder_approval",
        "implementation",
        "correction",
    ]
    assert chain["chain_complete"] is True
    assert len(chain["ordered_event_version_ids"]) == 4
    correction = next(
        row for row in first["event_versions"] if row["stage"] == "correction"
    )
    assert correction["cash_div_per_share"] == "0.120000000000"
    assert correction["parser_semantic_complete"] is True
    assert correction["supersedes_event_version_id"] == chain[
        "ordered_event_version_ids"
    ][2]
    assert chain["terminal_event_version_id"] == correction[
        "event_version_id"
    ]
    implementation = next(
        row
        for row in first["event_versions"]
        if row["stage"] == "implementation"
    )
    alone = parse_cninfo_corporate_action_documents([rows[2]])
    assert alone["event_versions"][0]["supersedes_event_version_id"] is None
    assert (
        alone["event_versions"][0]["event_version_id"]
        != implementation["event_version_id"]
    )


def test_cninfo_announcement_parser_fails_closed_on_identity_or_bytes():
    body = "<html><body>2020年度每10股派1元</body></html>".encode()
    unresolved = _cninfo_document(
        "3001",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    unresolved.pop("security_id")
    tampered = _cninfo_document(
        "3002",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    tampered["document_sha256"] = "0" * 64

    result = parse_cninfo_corporate_action_documents([unresolved, tampered])

    assert result["technical_replay_complete"] is False
    assert result["parsed_document_count"] == 0
    assert result["blocked_document_count"] == 2
    assert {row["code"] for row in result["blockers"]} == {
        "document_sha256_mismatch",
        "security_identity_unresolved",
    }
    assert result["event_versions"] == []


def test_cninfo_html_text_adapter_is_bounded_and_ignores_scripts():
    body = (
        b"<!doctype html><html><head><script>hidden target</script></head>"
        b"<body><p>visible &amp; causal</p></body></html>"
    )

    text = extract_cninfo_document_text(body, "html", max_text_chars=100)

    assert "visible & causal" in text
    assert "hidden target" not in text


def test_cninfo_parser_rejects_unbound_text_extractor():
    contract = build_cninfo_text_extractor_contract()
    assert contract["python_runtime"]["implementation"] == "CPython"
    assert contract["python_runtime"]["stdlib_html_parser_sha256"]
    assert contract["pdf_dynamic_libraries"]
    assert contract["fixed_process_environment"] == {
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    contract["pdf_binary_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="cninfo_text_extractor_contract_invalid"):
        parse_cninfo_corporate_action_documents(
            [],
            text_extractor_contract=contract,
        )

    body = "<html><body>2020年度利润分配预案：每10股派1元。</body></html>".encode()
    with pytest.raises(ValueError, match="cninfo_semantic_document_budget_exceeded"):
        parse_cninfo_corporate_action_documents(
            [
                _cninfo_document(
                    "4101",
                    "2020年度利润分配预案公告",
                    body,
                    announcement_time=1612137600000,
                ),
                _cninfo_document(
                    "4102",
                    "2020年度利润分配预案公告",
                    body,
                    announcement_time=1612137600000,
                ),
            ],
            max_documents=1,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_documents": 60_001}, "cninfo_semantic_resource_budget_invalid"),
        (
            {"max_text_chars": 16 * 1024 * 1024 + 1},
            "cninfo_semantic_resource_budget_invalid",
        ),
    ],
)
def test_cninfo_parser_resource_budgets_cannot_exceed_hard_caps(
    kwargs,
    message,
):
    with pytest.raises(ValueError, match=message):
        parse_cninfo_corporate_action_documents([], **kwargs)

    with pytest.raises(ValueError, match="cninfo_document_body_limit_exceeded"):
        extract_cninfo_document_text(
            b"<p>small</p>",
            "html",
            max_document_bytes=132 * 1024 * 1024 + 1,
        )
    with pytest.raises(ValueError, match="cninfo_document_text_limit_invalid"):
        extract_cninfo_document_text(
            b"<p>small</p>",
            "html",
            max_text_chars=16 * 1024 * 1024 + 1,
        )


def test_cninfo_parser_rejects_size_replay_and_parent_signature_forgery():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送0股、转增0股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    wrong_size = _cninfo_document(
        "4201",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    wrong_size["document_size_bytes"] = len(body) + 1
    caller_asserted = _cninfo_document(
        "4202",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    caller_asserted["source_governed_evidence_eligible"] = True
    _rebind_cninfo_document_record(caller_asserted)
    bad_signature = _cninfo_document(
        "4203",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    bad_signature["source_parent_terminal_signature"] = "caller-says-valid"
    _rebind_cninfo_document_record(bad_signature)
    wrong_payload = _cninfo_document(
        "4204",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    wrong_payload["source_raw_payload_sha256"] = "f" * 64
    _rebind_cninfo_document_record(wrong_payload)

    result = parse_cninfo_corporate_action_documents(
        [wrong_size, caller_asserted, bad_signature, wrong_payload]
    )

    assert result["event_versions"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "document_size_bytes_mismatch",
        "source_raw_payload_sha256_mismatch",
        "source_lineage_incomplete",
    }


def test_cninfo_parser_cannot_self_assert_governed_source_lineage():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送1股、转增1股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日；新增股份上市日：2021年5月20日。"
        "</body></html>"
    ).encode()
    row = _cninfo_document(
        "4001",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621209600000,
    )
    row.pop("source_document_closure_root")

    result = parse_cninfo_corporate_action_documents([row])

    assert result["technical_replay_complete"] is False
    assert result["event_versions"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "source_lineage_incomplete"
    }
    assert result["governance_blockers"] == [
        {
            "announcement_id": "4001",
            "code": "independent_source_admission_pending",
        }
    ]


def test_cninfo_parser_preserves_unknown_terms_and_conservative_same_day_time():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日；新增股份上市日：2021年5月20日。"
        "</body></html>"
    ).encode()
    row = _cninfo_document(
        "4501",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621468800000,
        announcement_time_precision_proven=False,
    )

    result = parse_cninfo_corporate_action_documents([row])

    event = result["event_versions"][0]
    assert event["stock_bonus_ratio"] is None
    assert event["stock_transfer_ratio"] is None
    assert event["stock_distribution_ratio"] is None
    assert event["economic_terms_complete"] is False
    assert event["known_timing"] == "after_close"
    assert event["semantic_candidate_eligible"] is False
    assert {row["code"] for row in result["blockers"]} == {
        "corporate_action_economic_terms_incomplete",
        "corporate_action_known_after_effective",
    }


def test_cninfo_parser_separates_adjustment_from_event_ledger_completeness():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送1股、转增1股。"
        "除权除息日：2021年5月20日。</body></html>"
    ).encode()
    result = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "4601",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )

    event = result["event_versions"][0]
    assert event["adjustment_semantic_complete"] is True
    assert event["event_ledger_semantic_complete"] is False
    assert event["semantic_candidate_eligible"] is False
    assert "corporate_action_final_semantics_incomplete" in result[
        "event_chains"
    ][0]["blockers"]
    assert {row["code"] for row in result["blockers"]} == {
        "corporate_action_cash_pay_date_unresolved",
        "corporate_action_record_date_unresolved",
        "corporate_action_stock_listing_date_unresolved",
    }


def test_cninfo_parser_enforces_causal_event_ledger_date_order():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送1股、转增1股。"
        "股权登记日：2021年5月20日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月19日；新增股份上市日：2021年5月19日。"
        "</body></html>"
    ).encode()
    result = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "4701",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )

    event = result["event_versions"][0]
    assert event["event_ledger_semantic_complete"] is False
    assert event["parser_semantic_complete"] is False
    assert {row["code"] for row in result["blockers"]} == {
        "corporate_action_record_effective_date_order_invalid",
        "corporate_action_effective_pay_date_order_invalid",
        "corporate_action_effective_listing_date_order_invalid",
    }


def test_cninfo_event_chain_rejects_duplicate_stage_and_nonmonotonic_time():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送0股、转增0股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    documents = [
        _cninfo_document(
            "4801",
            "2020年度权益分派实施公告",
            body,
            announcement_time=1621209600000,
        ),
        _cninfo_document(
            "4802",
            "2020年度权益分派实施公告",
            body,
            announcement_time=1621296000000,
        ),
    ]

    result = parse_cninfo_corporate_action_documents(documents)
    chain = result["event_chains"][0]

    assert chain["chain_complete"] is False
    assert "corporate_action_stage_duplicate" in chain["blockers"]
    assert (
        "corporate_action_chain_stage_not_strictly_monotonic"
        in chain["blockers"]
    )


def test_cninfo_event_chain_requires_correction_target_and_live_final_stage():
    correction_body = (
        "<html><body>更正后每10股派1元、送0股、转增0股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    correction_only = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "4901",
                "关于2020年度权益分派实施公告的更正公告",
                correction_body,
                announcement_time=1621296000000,
            )
        ]
    )["event_chains"][0]
    withdrawal_body = (
        "<html><body>2020年度利润分配方案取消。</body></html>"
    ).encode()
    withdrawn = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "4902",
                "关于取消2020年度利润分配方案的公告",
                withdrawal_body,
                announcement_time=1621382400000,
            )
        ]
    )["event_chains"][0]

    assert "corporate_action_correction_target_missing" in correction_only[
        "blockers"
    ]
    assert "corporate_action_chain_withdrawn" in withdrawn["blockers"]
    assert "corporate_action_final_implementation_missing" in withdrawn[
        "blockers"
    ]
    assert correction_only["chain_complete"] is False
    assert withdrawn["chain_complete"] is False


def test_cninfo_semantics_publish_and_validate_complete_file_closure(tmp_path):
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元。"
        "除权除息日：2021年5月20日。</body></html>"
    ).encode()
    evidence = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "5001",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )

    first = publish_cninfo_corporate_action_semantics(evidence, tmp_path)
    second = publish_cninfo_corporate_action_semantics(evidence, tmp_path)
    validated = validate_cninfo_corporate_action_semantics(tmp_path)

    assert first["generation_id"] == second["generation_id"]
    assert validated["generation_id"] == first["generation_id"]
    assert validated["derivation_content_hash"] == evidence["content_hash"]
    assert validated["data_admission_eligible"] is False

    generation = tmp_path / "generations" / first["generation_id"]
    (generation / "event_versions.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(
        ValueError,
        match="cninfo_corporate_action_semantic_evidence_invalid",
    ):
        validate_cninfo_corporate_action_semantics(
            generation / "cninfo_corporate_action_semantic_manifest.json"
        )


def _resign_cninfo_semantic_derivation(
    evidence: dict[str, object],
) -> dict[str, object]:
    resigned = json.loads(json.dumps(evidence))
    resigned.pop("content_hash", None)
    resigned.pop("generation_id", None)
    content_hash = canonical_hash(resigned)
    resigned["content_hash"] = content_hash
    resigned["generation_id"] = (
        "cninfo_corporate_action_semantics_" + content_hash[:24]
    )
    return resigned


def test_cninfo_semantics_publisher_rejects_self_signed_parser_and_extractor(
    tmp_path,
):
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送0股、转增0股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    evidence = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "5101",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )
    forged_payloads = []
    evil_parser = json.loads(json.dumps(evidence))
    evil_parser["parser_identity"] = "attacker_parser"
    forged_payloads.append(evil_parser)
    evil_root = json.loads(json.dumps(evidence))
    evil_root["parser_implementation_root"] = "0" * 64
    forged_payloads.append(evil_root)
    empty_contract = json.loads(json.dumps(evidence))
    empty_contract["text_extractor_contract"] = {}
    forged_payloads.append(empty_contract)

    for ordinal, forged in enumerate(forged_payloads):
        with pytest.raises(
            ValueError,
            match="cninfo_corporate_action_semantics_invalid",
        ):
            publish_cninfo_corporate_action_semantics(
                _resign_cninfo_semantic_derivation(forged),
                tmp_path / str(ordinal),
            )


def test_cninfo_semantics_publisher_rejects_forged_chain_and_counts(tmp_path):
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送0股、转增0股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    evidence = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "5102",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )
    forged_chain = json.loads(json.dumps(evidence))
    forged_chain["event_chains"][0]["chain_complete"] = True
    forged_chain["event_chains"][0]["blockers"] = []
    forged_count = json.loads(json.dumps(evidence))
    forged_count["source_document_count"] += 1

    for ordinal, forged in enumerate((forged_chain, forged_count)):
        with pytest.raises(
            ValueError,
            match="cninfo_corporate_action_semantics_invalid",
        ):
            publish_cninfo_corporate_action_semantics(
                _resign_cninfo_semantic_derivation(forged),
                tmp_path / str(ordinal),
            )


def test_cninfo_semantics_validator_rejects_reformatted_jsonl_after_resigning(
    tmp_path,
):
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送0股、转增0股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    evidence = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "5103",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )
    published = publish_cninfo_corporate_action_semantics(evidence, tmp_path)
    generation = tmp_path / "generations" / published["generation_id"]
    jsonl_path = generation / "event_versions.jsonl"
    row = json.loads(jsonl_path.read_text(encoding="utf-8"))
    noncanonical_bytes = (
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(", ", ": "),
        )
        + "\n"
    ).encode()
    jsonl_path.write_bytes(noncanonical_bytes)

    manifest_path = generation / "cninfo_corporate_action_semantic_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["event_versions"]["sha256"] = hashlib.sha256(
        noncanonical_bytes
    ).hexdigest()
    manifest["artifacts"]["event_versions"]["size_bytes"] = len(
        noncanonical_bytes
    )
    publication_semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    publication_hash = canonical_hash(publication_semantic)
    resigned_generation_id = (
        "cninfo_corporate_action_semantics_" + publication_hash[:24]
    )
    manifest["content_hash"] = publication_hash
    manifest["generation_id"] = resigned_generation_id
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    resigned_generation = generation.with_name(resigned_generation_id)
    generation.rename(resigned_generation)

    with pytest.raises(
        ValueError,
        match="cninfo_corporate_action_semantic_evidence_invalid",
    ):
        validate_cninfo_corporate_action_semantics(
            resigned_generation
            / "cninfo_corporate_action_semantic_manifest.json"
        )


def test_cninfo_event_projection_uses_pit_identity_and_remains_unadmitted():
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元、送2股、转增3股。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日；新增股份上市日：2021年5月20日。"
        "</body></html>"
    ).encode()
    parsed = parse_cninfo_corporate_action_documents(
        [
            _cninfo_document(
                "6001",
                "2020年度权益分派实施公告",
                body,
                announcement_time=1621209600000,
            )
        ]
    )
    identity = [
        {
            "security_id": "ashare_entity_000001",
            "trade_date_start": "20210101",
            "trade_date_end": "20211231",
            "security_code": "000001.SZ",
            "identity_resolved": True,
            "identity_unique": True,
            "active_on_trade_date": True,
        }
    ]

    projected = project_cninfo_corporate_action_event_versions(
        parsed["event_versions"], identity
    )

    assert projected["projection_complete"] is False
    event = projected["events"][0]
    assert event["ts_code"] == "000001.SZ"
    assert event["stock_distribution_ratio"] == "0.500000000000"
    assert event["source_semantic_candidate_eligible"] is False
    assert event["downstream_semantic_complete"] is False
    assert event["pit_evidence_eligible"] is False
    assert {row["code"] for row in projected["blockers"]} == {
        "corporate_action_source_semantic_candidate_ineligible"
    }
    assert projected["data_admission_eligible"] is False


def test_cninfo_parser_input_binds_only_validated_identity_intervals(
    tmp_path,
):
    from auto_alpha.data.pit.engine import (
        derive_security_identity_lifecycle_timeline,
        publish_security_identity_lifecycle_intervals,
    )

    timeline = derive_security_identity_lifecycle_timeline(
        security_ids=["ashare_entity_000001"],
        trade_dates=["20210519", "20210520", "20210521"],
        pre_span_seeds=[
            {
                "seed_version_id": "seed-v1",
                "security_id": "ashare_entity_000001",
                "as_of_date": "20210518",
                "security_code": "000001.SZ",
                "security_name": "平安银行",
                "lifecycle_state": "listed",
                "list_date": "19910403",
                "delist_date": None,
                "stable_identity_evidence_hash": "a" * 64,
                "source_evidence_hash": "b" * 64,
                "pit_evidence_eligible": True,
            }
        ],
        event_versions=[],
        materialize_daily_rows=False,
    )
    published = publish_security_identity_lifecycle_intervals(
        timeline, tmp_path / "identity"
    )
    body = (
        "<html><body>2020年度权益分派实施：每10股派1元。"
        "股权登记日：2021年5月19日；除权除息日：2021年5月20日；"
        "现金红利发放日：2021年5月20日。</body></html>"
    ).encode()
    document = _cninfo_document(
        "7001",
        "2020年度权益分派实施公告",
        body,
        announcement_time=1621382400000,
    )
    document["security_id"] = ""
    evidence_path = tmp_path / "identity"

    bound = list(
        bind_cninfo_documents_to_security_identity_intervals(
            [document], evidence_path
        )
    )
    parsed = parse_cninfo_corporate_action_documents(bound)

    assert bound[0]["security_id"] == "ashare_entity_000001"
    assert bound[0]["identity_projection_complete"] is True
    assert bound[0]["source_governed_evidence_eligible"] is False
    assert parsed["event_versions"][0]["security_id"] == (
        "ashare_entity_000001"
    )
    assert parsed["event_versions"][0]["semantic_candidate_eligible"] is False
    assert parsed["data_admission_eligible"] is False
    projection = project_cninfo_corporate_action_event_versions(
        parsed["event_versions"], timeline["intervals"]
    )
    assert "corporate_action_identity_projection_unresolved" not in {
        row["code"] for row in projection["blockers"]
    }


def test_cninfo_parser_exactly_classifies_verified_non_action_scope():
    body = b"<html><body>unrelated offering notice</body></html>"
    document = _cninfo_document(
        "8001",
        "首次公开发行公告",
        body,
        announcement_time=1621382400000,
    )
    records = [
        {
            "demand_identity": "d" * 64,
            "inventory_content_hash": "e" * 64,
            "leaf_profile": "base",
            "sec_code": "000001",
            "sec_name": "平安银行",
            "org_id": "gssz0000001",
            "announcement_title": "首次公开发行公告",
            "announcement_type": "type",
            "column_id": "column",
            "matched_leaves": ["initial_offerings_202105"],
        }
    ]
    roles = ["initial_offerings"]
    document["source_inventory_records"] = records
    document["source_inventory_content_hash"] = canonical_hash(["e" * 64])
    document["matched_leaves"] = ["initial_offerings_202105"]
    document["source_scope_roles"] = roles
    document["source_inventory_scope_root"] = canonical_hash(
        {
            "source_inventory_records": records,
            "source_scope_roles": roles,
        }
    )
    document["security_id"] = ""
    _rebind_cninfo_document_record(document)

    result = parse_cninfo_corporate_action_documents([document])

    assert result["technical_replay_complete"] is True
    assert result["out_of_scope_document_count"] == 1
    assert result["blocked_document_count"] == 0
    assert result["event_versions"] == []
    assert result["document_results"][0]["status"] == "out_of_scope"
