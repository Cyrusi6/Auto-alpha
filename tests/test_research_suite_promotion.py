from factor_store import FactorRecord, LocalFactorStore
from research_suite.models import PromotionConfig, WalkForwardResult
from research_suite.promotion import promote_factor_if_eligible


def _save_factor(store, factor_id, factor_type="composite", status="approved"):
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["COMPOSITE"],
            formula_tokens=[],
            formula_hash=f"hash_{factor_id}",
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status=status,
            factor_type=factor_type,
        )
    )


def test_promotion_passes_for_composite_and_updates_store(tmp_path):
    store = LocalFactorStore(tmp_path)
    _save_factor(store, "factor_comp", "composite")
    wf = WalkForwardResult(
        factor_id="factor_comp",
        windows=[],
        summary={"mean_test_score": 0.1, "positive_test_score_ratio": 1.0},
    )

    decision = promote_factor_if_eligible(
        store,
        "factor_comp",
        wf,
        {"fill_rate": 0.5, "constraint_reject_rate": 0.2},
        PromotionConfig(),
    )
    updated = store.load_latest_factor(status="production_candidate", factor_type="composite")

    assert decision.passed is True
    assert updated.factor_id == "factor_comp"
    assert updated.metadata["promotion_decision"]["passed"] is True


def test_promotion_rejects_non_composite_when_required(tmp_path):
    store = LocalFactorStore(tmp_path)
    _save_factor(store, "factor_single", "single")
    wf = WalkForwardResult(
        factor_id="factor_single",
        windows=[],
        summary={"mean_test_score": 0.1, "positive_test_score_ratio": 1.0},
    )

    decision = promote_factor_if_eligible(
        store,
        "factor_single",
        wf,
        {"fill_rate": 1.0, "constraint_reject_rate": 0.0},
        PromotionConfig(require_composite=True),
    )

    assert decision.passed is False
    assert "factor_is_not_composite" in decision.reasons
    assert store.load_factors()[0].status == "approved"
