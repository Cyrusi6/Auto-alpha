from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifact_schema.validator import validate_artifact
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_g.fee_cli import main as fee_cli_main
from task_055_g.fees import (
    FeeScheduleCalculator,
    FeeWorkflowError,
    acquire_fee_documents,
    build_fee_plan,
    canonical_hash,
    extract_fee_rules,
    independent_verify_fee_schedule,
    official_fee_workflow_spec,
    publish_fee_document_verification,
    publish_fee_schedule_v2,
    run_fee_dag,
    validate_fee_document_acquisition,
    validate_fee_rule_extraction,
    validate_fee_schedule_v2,
)


def _policy_seal(tmp_path: Path) -> Path:
    semantic = {
        "schema_version": "task055a_portfolio_diagnostic_policy_seal_v1",
        "observation_boundary_hash": "a" * 64,
        "simulation_bundle_hash": "b" * 64,
        "exact20_ids": [f"factor_{index:02d}" for index in range(20)],
        "candidate_identity_root": canonical_hash([f"factor_{index:02d}" for index in range(20)]),
        "signal_cutoff": "20240528",
        "execution_endpoint": "20240530",
        "evidence_level": "retrospective_modeled_daily_bar_proxy",
        "selection_data_reused": True,
        "untouched_holdout": False,
        "portfolio_construction": {
            "independent_factor_runs": True,
            "long_only": True,
            "rebalance": "daily",
            "top_n": 20,
            "weighting": "equal_weight",
            "tie_break": "stable_ts_code",
            "combination_or_selection": False,
        },
        "scenarios": {name: policy.to_dict() for name, policy in PREREGISTERED_SCENARIOS.items()},
        "physical_state_evidence": {},
        "code_semantic_hash": "c" * 64,
        "immutable": True,
    }
    payload = semantic | {
        "content_hash": canonical_hash(semantic),
        "generation_id": "policy_seal_fixture",
    }
    path = tmp_path / "policy_seal.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _official_body() -> bytes:
    clauses = [
        "自2008年9月19日起，沪深市场证券交易印花税按成交金额0.1%向卖方单边征收，买方不征收，按分四舍五入，不设最低收费。",
        "自2023年8月28日起，沪深市场证券交易印花税按成交金额0.05%向卖方单边征收，买方不征收，按分四舍五入，不设最低收费。",
        "自2015年8月1日起，沪深市场股票交易过户费按成交金额0.002%向买卖双方收取，按分四舍五入，不设最低收费。",
        "自2022年4月29日起，沪深市场股票交易过户费按成交金额0.001%向买卖双方收取，按分四舍五入，不设最低收费。",
        "自2015年8月1日起，沪深市场证券交易经手费按成交金额0.00487%向买卖双方收取，按分四舍五入，不设最低收费。",
        "自2023年8月28日起，沪深市场证券交易经手费按成交金额0.00341%向买卖双方收取，按分四舍五入，不设最低收费。",
        "自2012年7月20日起，沪深市场证券交易监管费按成交金额0.002%向买卖双方收取，按分四舍五入，不设最低收费。",
    ]
    return ("<html><body><h1>官方证券收费规则</h1><p>" + "</p><p>".join(clauses) + "</p></body></html>").encode("utf-8")


def _documents() -> list[dict]:
    return [
        {
            "document_id": "official_fee_history",
            "publisher": "中国证券监督管理委员会",
            "request_url": "https://www.csrc.gov.cn/official-fee-history.html",
        }
    ]


def _extractors() -> list[dict]:
    return [
        {"extractor_id": "stamp_2008", "document_id": "official_fee_history", "parser_id": "cn_stamp_duty_rate_v1", "occurrence": 0},
        {"extractor_id": "stamp_2023", "document_id": "official_fee_history", "parser_id": "cn_stamp_duty_rate_v1", "occurrence": 1},
        {"extractor_id": "transfer_2015", "document_id": "official_fee_history", "parser_id": "cn_transfer_fee_rate_v1", "occurrence": 0},
        {"extractor_id": "transfer_2022", "document_id": "official_fee_history", "parser_id": "cn_transfer_fee_rate_v1", "occurrence": 1},
        {"extractor_id": "handling_2015", "document_id": "official_fee_history", "parser_id": "cn_handling_fee_rate_v1", "occurrence": 0},
        {"extractor_id": "handling_2023", "document_id": "official_fee_history", "parser_id": "cn_handling_fee_rate_v1", "occurrence": 1},
        {"extractor_id": "management_2012", "document_id": "official_fee_history", "parser_id": "cn_securities_management_fee_rate_v1", "occurrence": 0},
    ]


def _fetcher(url: str) -> dict:
    return {
        "body": _official_body(),
        "final_url": url,
        "redirect_chain": [url],
        "http_status": 200,
        "tls_verified": True,
        "hostname_verified": True,
        "peer_certificate_sha256": "a" * 64,
        "retrieved_at": "2026-07-16T12:00:00+08:00",
        "response_headers": {"content-type": "text/html; charset=utf-8"},
    }


def _dag(tmp_path: Path) -> dict:
    return run_fee_dag(
        output_root=tmp_path / "dag",
        policy_seal=_policy_seal(tmp_path),
        simulation_start="20160104",
        simulation_end="20240530",
        documents=_documents(),
        extractors=_extractors(),
        allow_network=True,
        fetcher=_fetcher,
        allow_synthetic_test_fixture=True,
    )


def _production_bodies() -> dict[str, bytes]:
    spec = official_fee_workflow_spec()
    urls = {row["document_id"]: row["request_url"] for row in spec["documents"]}
    text_by_id = {
        "stamp_history_context": (
            "2008年4月24日，证券交易印花税税率下调为1‰。"
            "2008年9月19日，证券交易印花税改为单边征收。"
        ),
        "stamp_tax_law": (
            "证券交易印花税对证券交易的出让方征收，不对受让方征收。"
            "证券交易印花税以证券交易成交金额为计税依据。"
        ),
        "stamp_half_2023": "自2023年8月28日起，证券交易印花税实施减半征收。",
        "fee_reform_2015": (
            "自2015年8月1日起，沪深市场股票交易过户费调整为按成交金额0.002%，向买卖双方双向收取。"
            "自2015年8月1日起，沪深市场证券交易经手费调整为按成交金额0.00487%，向买卖双方双向收取。"
        ),
        "transfer_fee_2022": (
            "自2022年4月29日起，沪深市场股票交易过户费统一下调为按成交金额0.001%，向买卖双方双向收取。"
        ),
        "handling_fee_2023": (
            "自2023年8月28日起，沪深市场证券交易经手费下调为按成交金额0.00341%，向买卖双方双向收取。"
        ),
        "management_fee_2012": (
            "本通知发布于2012年7月13日。"
            "上海、深圳证券交易所按股票年交易额收取证券交易监管费0.02‰。"
            "从今年开始，监管费按照上述标准执行。"
        ),
    }
    return {
        urls[document_id]: (
            f"<html><body>本文件为收费规则历史依据，用于明确适用日期、市场、方向和计费基数。{text}"
            "本文件所列条款应结合原始发布机关和生效日期核验。</body></html>"
        ).encode("utf-8")
        for document_id, text in text_by_id.items()
    }


def _production_fetcher(url: str) -> dict:
    return _fetcher(url) | {"body": _production_bodies()[url]}


def _production_dag(tmp_path: Path) -> dict:
    spec = official_fee_workflow_spec()
    return run_fee_dag(
        output_root=tmp_path / "task_055_g_production_fee_dag",
        policy_seal=_policy_seal(tmp_path),
        simulation_start="20160104",
        simulation_end="20240530",
        documents=spec["documents"],
        extractors=spec["extractors"],
        allow_network=True,
        fetcher=_production_fetcher,
        allow_synthetic_test_fixture=True,
    )


def test_production_fee_parser_native_artifacts_are_strict_schema_registered(tmp_path):
    result = _production_dag(tmp_path)
    artifacts = [
        (result["plan"]["manifest_path"], "task055g_fee_plan"),
        (result["acquisition"]["manifest_path"], "task055g_fee_document_acquisition"),
        (
            Path(result["acquisition"]["manifest_path"]).parent / result["acquisition"]["transport_ledger_relative_path"],
            "task055g_fee_transport_ledger",
        ),
        (result["document_verification"]["manifest_path"], "task055g_fee_document_verification"),
        (result["extraction"]["manifest_path"], "task055g_fee_rule_extraction"),
        (result["schedule"]["manifest_path"], "task055g_fee_schedule_v2"),
        (result["independent_verification"]["manifest_path"], "task055g_fee_independent_verification"),
    ]
    for path, artifact_type in artifacts:
        validation = validate_artifact(path, strict=True)
        assert validation.valid is True
        assert validation.artifact_type == artifact_type
        assert not any(issue.code == "unknown_artifact" for issue in validation.issues)
    assert len(result["schedule"]["rules"]) == 40
    assert result["independent_verification"]["status"] == "passed"


def test_production_fee_parser_fails_closed_when_official_rate_clause_is_missing(tmp_path):
    spec = official_fee_workflow_spec()

    def bad_fetcher(url: str) -> dict:
        result = _production_fetcher(url)
        if "7426794" in url:
            result["body"] = (
                "<html><body>本文件为收费规则历史依据，用于明确适用日期、市场、方向和计费基数。"
                "自2023年8月28日起，沪深市场证券交易经手费按成交金额向买卖双方双向收取。"
                "本文件所列条款应结合原始发布机关和生效日期核验。</body></html>"
            ).encode("utf-8")
        return result

    with pytest.raises(FeeWorkflowError, match="production_fee_rate_missing"):
        run_fee_dag(
            output_root=tmp_path / "task_055_g_bad_production_fee_dag",
            policy_seal=_policy_seal(tmp_path),
            simulation_start="20160104",
            simulation_end="20240530",
            documents=spec["documents"],
            extractors=spec["extractors"],
            allow_network=True,
            fetcher=bad_fetcher,
            allow_synthetic_test_fixture=True,
        )


def test_fee_dag_extracts_rules_from_document_bytes_and_recomputes_them(tmp_path):
    result = _dag(tmp_path)
    schedule = result["schedule"]
    assert schedule["policy_seal_hash"] == json.loads(_policy_seal(tmp_path).read_text())["content_hash"]
    assert schedule["document_acquisition_content_hash"] == result["acquisition"]["content_hash"]
    assert schedule["transport_ledger_root"] == result["acquisition"]["transport_ledger_root"]
    assert schedule["builder_semantic_hash"] == canonical_hash(schedule["semantic_source_hashes"])
    assert len(schedule["rules"]) == 40
    assertion = next(row for row in result["extraction"]["assertions"] if row["assertion_id"] == "handling_2023")
    assert assertion["parsed"]["effective_start"] == "20230828"
    assert assertion["parsed"]["side_rates"]["SELL"]["rate"] == "0.0000341"
    assert assertion["parsed"]["markets"] == ["SSE", "SZSE"]
    assert assertion["parsed"]["basis"] == "notional"
    assert assertion["parsed"]["rounding"] == "cent_half_up"
    assert assertion["locator"]["text_end"] > assertion["locator"]["text_start"]
    assert result["independent_verification"]["status"] == "passed"

    with pytest.raises(FeeWorkflowError, match="synthetic_fee_schedule_forbidden"):
        validate_fee_schedule_v2(schedule["manifest_path"])
    verified = validate_fee_schedule_v2(schedule["manifest_path"], allow_synthetic_test_fixture=True)
    assert verified["rules_root"] == schedule["rules_root"]


def test_fee_calculator_keeps_statutory_costs_outside_modeled_multiplier(tmp_path):
    schedule = _dag(tmp_path)["schedule"]
    calculator = FeeScheduleCalculator(schedule["manifest_path"], allow_synthetic_test_fixture=True)
    baseline = calculator.calculate(
        date="20240102",
        market="SSE",
        side="SELL",
        notional=100_000.0,
        shares=10_000,
        zero_all_costs=False,
        modeled_multiplier=1.0,
    )
    doubled = calculator.calculate(
        date="20240102",
        market="SSE",
        side="SELL",
        notional=100_000.0,
        shares=10_000,
        zero_all_costs=False,
        modeled_multiplier=2.0,
    )
    for component in ("stamp_duty", "transfer_fee", "handling_fee", "securities_management_fee"):
        assert doubled[component] == baseline[component]
    for component in ("commission", "slippage", "impact"):
        assert doubled[component] == 2 * baseline[component]


def test_fee_plan_rejects_free_rate_mapping_and_wrong_policy_seal(tmp_path):
    extractors = _extractors()
    extractors[0]["rate"] = 0.5
    with pytest.raises(FeeWorkflowError, match="free_mapping_forbidden"):
        build_fee_plan(
            output_root=tmp_path / "plan",
            policy_seal=_policy_seal(tmp_path),
            simulation_start="20160104",
            simulation_end="20240530",
            documents=_documents(),
            extractors=extractors,
        )
    policy = _policy_seal(tmp_path)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["scenarios"]["baseline"]["commission_rate"] = 0.9
    payload["content_hash"] = canonical_hash({key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}})
    policy.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FeeWorkflowError, match="scenarios_mismatch"):
        build_fee_plan(
            output_root=tmp_path / "bad_policy",
            policy_seal=policy,
            simulation_start="20160104",
            simulation_end="20240530",
            documents=_documents(),
            extractors=_extractors(),
        )


def test_acquisition_rejects_cross_host_redirect_and_document_tampering(tmp_path):
    plan = build_fee_plan(
        output_root=tmp_path / "plan",
        policy_seal=_policy_seal(tmp_path),
        simulation_start="20160104",
        simulation_end="20240530",
        documents=_documents(),
        extractors=_extractors(),
    )
    with pytest.raises(FeeWorkflowError, match="cross_host_redirect"):
        acquire_fee_documents(
            plan=plan["manifest_path"],
            output_root=tmp_path / "bad_redirect",
            allow_network=True,
            fetcher=lambda url: _fetcher(url) | {
                "final_url": "https://www.sse.com.cn/other.html",
                "redirect_chain": [url, "https://www.sse.com.cn/other.html"],
            },
            allow_synthetic_test_fixture=True,
        )
    acquisition = acquire_fee_documents(
        plan=plan["manifest_path"],
        output_root=tmp_path / "acquisition",
        allow_network=True,
        fetcher=_fetcher,
        allow_synthetic_test_fixture=True,
    )
    manifest_path = Path(acquisition["manifest_path"])
    document_path = manifest_path.parent / acquisition["documents"][0]["artifact_relative_path"]
    document_path.write_bytes(document_path.read_bytes() + b"tampered")
    with pytest.raises(FeeWorkflowError, match="document_sha_mismatch"):
        validate_fee_document_acquisition(
            manifest_path,
            plan=plan["manifest_path"],
            allow_synthetic_test_fixture=True,
        )


def test_extraction_rejects_forged_offsets_and_rate_even_with_rehashed_manifest(tmp_path):
    plan = build_fee_plan(
        output_root=tmp_path / "plan",
        policy_seal=_policy_seal(tmp_path),
        simulation_start="20160104",
        simulation_end="20240530",
        documents=_documents(),
        extractors=_extractors(),
    )
    acquisition = acquire_fee_documents(
        plan=plan["manifest_path"],
        output_root=tmp_path / "acquisition",
        allow_network=True,
        fetcher=_fetcher,
        allow_synthetic_test_fixture=True,
    )
    verification = publish_fee_document_verification(
        output_root=tmp_path / "verification",
        plan=plan["manifest_path"],
        acquisition=acquisition["manifest_path"],
        allow_synthetic_test_fixture=True,
    )
    extraction = extract_fee_rules(
        output_root=tmp_path / "extraction",
        plan=plan["manifest_path"],
        acquisition=acquisition["manifest_path"],
        document_verification=verification["manifest_path"],
        allow_synthetic_test_fixture=True,
    )
    path = Path(extraction["manifest_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["assertions"][0]["locator"]["text_start"] += 1
    payload["assertions"][0]["parsed"]["side_rates"]["SELL"]["rate"] = "0.9"
    payload["assertion_root"] = canonical_hash(payload["assertions"])
    payload["content_hash"] = canonical_hash({key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}})
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FeeWorkflowError, match="assertion_reparse_mismatch"):
        validate_fee_rule_extraction(
            path,
            plan=plan["manifest_path"],
            acquisition=acquisition["manifest_path"],
            document_verification=verification["manifest_path"],
            allow_synthetic_test_fixture=True,
        )


def test_fee_cli_runs_native_stage_sequence_with_test_transport(tmp_path):
    policy = _policy_seal(tmp_path)
    config = tmp_path / "fee_config.json"
    config.write_text(
        json.dumps(
            {
                "policy_seal": str(policy),
                "simulation_start": "20160104",
                "simulation_end": "20240530",
                "documents": _documents(),
                "extractors": _extractors(),
            }
        ),
        encoding="utf-8",
    )
    roots = {name: tmp_path / name for name in ("plan", "acquisition", "verification", "extraction", "schedule", "independent")}
    assert fee_cli_main(["fee-plan", "--config", str(config), "--output-root", str(roots["plan"])]) == 0
    plan = roots["plan"] / json.loads((roots["plan"] / "current.json").read_text())["manifest"]
    assert fee_cli_main(
        ["fee-document-acquire", "--plan", str(plan), "--output-root", str(roots["acquisition"]), "--allow-network"],
        _test_fetcher=_fetcher,
    ) == 0
    acquisition = roots["acquisition"] / json.loads((roots["acquisition"] / "current.json").read_text())["manifest"]
    assert fee_cli_main(
        ["fee-document-verify", "--plan", str(plan), "--acquisition", str(acquisition), "--output-root", str(roots["verification"])],
        _test_fetcher=_fetcher,
    ) == 0
    verification = roots["verification"] / json.loads((roots["verification"] / "current.json").read_text())["manifest"]
    assert fee_cli_main(
        ["fee-rule-extract", "--plan", str(plan), "--acquisition", str(acquisition), "--document-verification", str(verification), "--output-root", str(roots["extraction"])],
        _test_fetcher=_fetcher,
    ) == 0
    extraction = roots["extraction"] / json.loads((roots["extraction"] / "current.json").read_text())["manifest"]
    assert fee_cli_main(
        ["fee-publish", "--plan", str(plan), "--acquisition", str(acquisition), "--document-verification", str(verification), "--extraction", str(extraction), "--output-root", str(roots["schedule"])],
        _test_fetcher=_fetcher,
    ) == 0
    schedule = roots["schedule"] / json.loads((roots["schedule"] / "current.json").read_text())["manifest"]
    assert fee_cli_main(
        ["fee-independent-verify", "--schedule", str(schedule), "--output-root", str(roots["independent"])],
        _test_fetcher=_fetcher,
    ) == 0
    attestation = roots["independent"] / json.loads((roots["independent"] / "current.json").read_text())["manifest"]
    assert json.loads(attestation.read_text())["status"] == "passed"


def test_independent_verifier_detects_cloned_document_tampering(tmp_path):
    result = _dag(tmp_path)
    schedule_path = Path(result["schedule"]["manifest_path"])
    document = next((schedule_path.parent / "native_acquisition" / "documents").iterdir())
    document.write_bytes(document.read_bytes().replace(b"0.00341%", b"0.90341%"))
    with pytest.raises(FeeWorkflowError):
        independent_verify_fee_schedule(
            schedule=schedule_path,
            allow_synthetic_test_fixture=True,
        )
