import json

from alpha_factory import AlphaCampaignConfig, AlphaFactoryRunner
from alpha_factory.models import AlphaCandidateRecord
from alpha_factory.static_checks import run_static_checks
from data_lake.run_lake import main as lake_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from feature_factory import FEATURE_SET_V3, build_feature_tensor_artifacts, load_feature_manifest, make_formula_vocab_from_manifest
from feature_promotion.decision import build_allow_deny_lists, default_decisions_from_review_package
from feature_promotion.evidence import build_feature_promotion_evidence, build_promotion_candidates
from feature_promotion.policy import (
    apply_promotion_to_manifest,
    create_default_policy,
    feature_default_status,
    load_promotion_gate,
    policy_hash,
)
from feature_promotion.review import make_review_package
from factor_certification.models import CertificationPolicy
from factor_certification.scorecard import build_factor_certification_scorecard
from matrix_refresh.planner import build_matrix_refresh_plan
from matrix_store import build_matrix_cache
from model_core.data_loader import AShareDataLoader


def _prepare_v3_promotion(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    result = build_feature_tensor_artifacts(
        loader,
        output_dir=tmp_path / "features_v3",
        feature_set_name=FEATURE_SET_V3,
    )
    manifest = json.loads((tmp_path / "features_v3" / "feature_set_manifest.json").read_text(encoding="utf-8"))
    policy = create_default_policy(manifest).to_dict()
    policy["policy_hash"] = policy_hash(policy)
    policy_path = tmp_path / "feature_promotion_policy.json"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence, evidence_summary = build_feature_promotion_evidence(
        manifest=manifest,
        policy=create_default_policy(manifest),
        feature_family_readiness_path=tmp_path / "features_v3" / "feature_family_readiness.json",
        feature_pit_alignment_report_path=tmp_path / "features_v3" / "feature_pit_alignment_report.json",
        feature_build_warnings_path=tmp_path / "features_v3" / "feature_build_warnings.jsonl",
        feature_coverage_report_path=tmp_path / "features_v3" / "feature_coverage_report.json",
    )
    candidates = build_promotion_candidates(manifest, create_default_policy(manifest))
    review = make_review_package(create_default_policy(manifest), candidates, evidence).to_dict()
    decisions = default_decisions_from_review_package(review, reviewer="unit_reviewer")
    allowlist, denylist, application = build_allow_deny_lists(policy=policy, decisions=decisions, review_package=review)
    allowlist_path = tmp_path / "feature_promotion_allowlist.json"
    denylist_path = tmp_path / "feature_promotion_denylist.json"
    allowlist_path.write_text(json.dumps(allowlist, ensure_ascii=False, indent=2), encoding="utf-8")
    denylist_path.write_text(json.dumps(denylist, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "data_dir": data_dir,
        "features_dir": tmp_path / "features_v3",
        "manifest": manifest,
        "manifest_path": tmp_path / "features_v3" / "feature_set_manifest.json",
        "policy": policy,
        "policy_path": policy_path,
        "evidence_summary": evidence_summary,
        "review": review,
        "allowlist": allowlist,
        "denylist": denylist,
        "application": application,
        "allowlist_path": allowlist_path,
        "denylist_path": denylist_path,
        "tensor_result": result,
    }


def test_feature_promotion_policy_evidence_and_lists(tmp_path):
    ctx = _prepare_v3_promotion(tmp_path)
    manifest = ctx["manifest"]
    policy = create_default_policy(manifest)
    weak = [item for item in manifest["feature_definitions"] if item.get("pit_safety") != "pit_safe"]
    unsafe_feature = {
        "feature_name": "UNSAFE_FAKE_FINANCIAL",
        "family": "financial_statement",
        "default_enabled": True,
        "used_for_alpha": True,
        "pit_safety": "pit_safe",
        "availability_field": None,
    }
    unsafe_status, unsafe_reason = feature_default_status(unsafe_feature, policy)

    assert ctx["policy"]["policy_hash"] == policy_hash(ctx["policy"])
    assert ctx["evidence_summary"]["evidence_count"] == manifest["feature_count"]
    assert ctx["evidence_summary"]["weak_pit_feature_count"] == len(weak)
    assert ctx["review"]["summary"]["needs_review_count"] > 0
    assert ctx["allowlist"]["alpha_eligible_features"]
    assert ctx["denylist"]["blocked_features"]
    assert unsafe_status == "blocked"
    assert unsafe_reason == "missing_required_availability_field"


def test_alpha_factory_requires_feature_promotion_allowlist(tmp_path):
    ctx = _prepare_v3_promotion(tmp_path)
    result = AlphaFactoryRunner(
        AlphaCampaignConfig(
            campaign_name="unit_promotion_gate",
            data_dir=str(ctx["data_dir"]),
            output_dir=str(tmp_path / "alpha"),
            factor_store_dir=str(tmp_path / "store"),
            report_dir=str(tmp_path / "reports"),
            feature_set_name=FEATURE_SET_V3,
            feature_set_manifest_path=str(ctx["manifest_path"]),
            feature_promotion_policy_path=str(ctx["policy_path"]),
            feature_promotion_allowlist_path=str(ctx["allowlist_path"]),
            feature_promotion_denylist_path=str(ctx["denylist_path"]),
            require_feature_promotion=True,
            candidate_budget=16,
            template_budget=8,
            random_budget=4,
            mutation_budget=2,
            crossover_budget=1,
            corpus_budget=0,
            proxy_max_candidates=8,
            top_k=4,
            seed=23,
        )
    ).run()
    feature_names = {item["feature_name"] for item in ctx["manifest"]["feature_definitions"]}
    allowed = set(ctx["allowlist"]["alpha_eligible_features"])
    rows = [
        json.loads(line)
        for line in (tmp_path / "alpha" / "alpha_candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result.status == "success"
    assert result.summary["promotion_policy_hash"] == ctx["policy"]["policy_hash"]
    assert result.summary["alpha_eligible_feature_count"] == len(allowed)
    assert rows
    for row in rows:
        used = set(row["formula_names"]) & feature_names
        assert used <= allowed


def test_static_checks_reject_blocked_feature(tmp_path):
    ctx = _prepare_v3_promotion(tmp_path)
    manifest = load_feature_manifest(ctx["manifest_path"])
    vocab = make_formula_vocab_from_manifest(manifest)
    feature_meta = {item["feature_name"]: item for item in ctx["manifest"]["feature_definitions"]}
    blocked = ctx["denylist"]["blocked_features"][0]
    gate = load_promotion_gate(
        policy_path=ctx["policy_path"],
        allowlist_path=ctx["allowlist_path"],
        denylist_path=ctx["denylist_path"],
        require_promotion=True,
    )
    candidate = AlphaCandidateRecord(
        alpha_candidate_id="alpha_blocked",
        formula_hash="blocked_hash",
        formula_tokens=[vocab.encode_name(blocked)],
        formula_names=[blocked],
        source="unit",
        source_refs=[],
        feature_set_name=FEATURE_SET_V3,
        feature_version=FEATURE_SET_V3,
        operator_version="ashare_ops_v1",
        complexity=1,
        lookback=1,
        family_tags=["unit"],
    )
    checked, rows = run_static_checks(
        [candidate],
        max_complexity=10,
        max_lookback=10,
        vocab=vocab,
        promotion_gate=gate,
        feature_meta=feature_meta,
    )

    assert checked[0].status == "rejected"
    assert rows[0]["status"] == "failed"
    assert any("blocked_feature_used" in error for error in rows[0]["errors"])


def test_matrix_refresh_detects_feature_promotion_policy_hash_drift(tmp_path, capsys):
    ctx = _prepare_v3_promotion(tmp_path)
    promoted_manifest, _summary = apply_promotion_to_manifest(
        ctx["manifest"],
        policy_path=ctx["policy_path"],
        allowlist_path=ctx["allowlist_path"],
        denylist_path=ctx["denylist_path"],
    )
    promoted_manifest_path = tmp_path / "promoted_feature_set_manifest.json"
    promoted_manifest_path.write_text(json.dumps(promoted_manifest, ensure_ascii=False), encoding="utf-8")
    assert (
        lake_main(
            [
                "create-version",
                "--data-dir",
                str(ctx["data_dir"]),
                "--registry-dir",
                str(tmp_path / "registry"),
                "--output-dir",
                str(tmp_path / "version"),
                "--provider",
                "sample",
                "--profile-name",
                "sample_feature_promotion",
                "--start-date",
                "20240102",
                "--end-date",
                "20240104",
                "--datasets",
                "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions",
            ]
        )
        == 0
    )
    capsys.readouterr()
    cache_dir = tmp_path / "matrix_cache"
    build_matrix_cache(
        ctx["data_dir"],
        output_dir=cache_dir,
        data_version_manifest_path=tmp_path / "version" / "dataset_version_manifest.json",
        feature_set_name=FEATURE_SET_V3,
        feature_set_manifest_path=promoted_manifest_path,
    )
    changed_policy = dict(ctx["policy"]) | {"policy_hash": "changed_policy_hash_for_unit_test"}
    changed_policy_path = tmp_path / "changed_policy.json"
    changed_policy_path.write_text(json.dumps(changed_policy, ensure_ascii=False), encoding="utf-8")

    plan = build_matrix_refresh_plan(
        data_dir=ctx["data_dir"],
        matrix_cache_dir=cache_dir,
        data_version_manifest_path=tmp_path / "version" / "dataset_version_manifest.json",
        feature_set_name=FEATURE_SET_V3,
        feature_set_manifest_path=promoted_manifest_path,
        feature_promotion_policy_path=changed_policy_path,
    )

    assert plan.recommendation == "full_rebuild"
    assert "feature_promotion_policy_hash_drift" in plan.reasons


def test_factor_certification_blocks_unapproved_feature_usage(tmp_path):
    validation_report_path = tmp_path / "validation_lab_report.json"
    validation_report_path.write_text(
        json.dumps(
            {
                "target": {
                    "metadata": {
                        "feature_promotion_policy_hash": "policy_hash",
                        "unapproved_feature_used": True,
                        "blocked_feature_used": False,
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    policy = CertificationPolicy(
        policy_id="unit_policy",
        profile_name="unit",
        require_validation_lab=False,
        require_multiple_testing=False,
        require_overfit_risk=False,
        require_placebo=False,
        require_stress_backtest=False,
    )

    scorecard = build_factor_certification_scorecard(
        "factor_unit",
        policy,
        {"validation_lab_report_path": str(validation_report_path)},
    )
    checks = {check.name: check for check in scorecard.checks}

    assert checks["feature_promotion_check"].status == "failed"
    assert checks["feature_promotion_check"].severity == "blocker"
    assert scorecard.summary["blocker_count"] >= 1
