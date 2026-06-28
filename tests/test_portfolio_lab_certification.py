import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry
from portfolio_certification.run_portfolio_certify import main as certify_main
from portfolio_lab.run_portfolio_lab import main as lab_main
from portfolio_optimizer import build_portfolio_policy, from_portfolio_policy


def _prepare_factor(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    formula_tokens = [0]
    formula_names = ["RET_1D"]
    formula_hash = stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1")
    factor_id = f"factor_{formula_hash[:16]}"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=formula_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-28T00:00:00Z",
            status="approved",
            metrics={"score": 0.5},
            factor_type="composite",
            parent_factor_ids=["factor_component"],
        )
    )
    values = [
        [float(stock_idx + date_idx + 1) for date_idx, _date in enumerate(loader.trade_dates)]
        for stock_idx, _code in enumerate(loader.ts_codes)
    ]
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    return data_dir, tmp_path / "store", factor_id


def test_portfolio_policy_is_deterministic_and_registry_can_activate(tmp_path):
    policy = build_portfolio_policy(policy_name="test", top_n=2, max_names=2, max_weight=0.1, source_factor_id="factor_x")
    same = build_portfolio_policy(policy_name="test", top_n=2, max_names=2, max_weight=0.1, source_factor_id="factor_x")
    config = from_portfolio_policy(policy)

    assert policy.policy_id == same.policy_id
    assert config.max_names == 2

    registry = LocalModelRegistry(tmp_path / "registry")
    model = registry.register_portfolio_policy({"portfolio_policy": policy.to_dict(), "certification_status": "certified"})
    activated, _deployment = registry.activate(model.model_version_id, explicit_override=True)

    assert activated.model_kind == "optimizer_policy"
    assert registry.latest_active_optimizer_policy().model_version_id == model.model_version_id


def test_portfolio_lab_and_certification_cli(tmp_path, capsys):
    data_dir, store_dir, factor_id = _prepare_factor(tmp_path)
    capsys.readouterr()

    assert (
        lab_main(
            [
                "run",
                "--data-dir",
                str(data_dir),
                "--factor-store-dir",
                str(store_dir),
                "--factor-id",
                factor_id,
                "--output-dir",
                str(tmp_path / "lab"),
                "--portfolio-methods",
                "equal_weight,risk_aware",
                "--risk-aversions",
                "1.0",
                "--turnover-penalties",
                "0.1",
                "--max-names-values",
                "2",
                "--top-n-values",
                "2",
                "--max-trials",
                "2",
                "--pretty",
            ]
        )
        == 0
    )
    lab_payload = json.loads(capsys.readouterr().out)
    assert lab_payload["trial_count"] == 2
    assert (tmp_path / "lab" / "selected_portfolio_policy.json").exists()
    assert (tmp_path / "lab" / "portfolio_trial_metrics.jsonl.schema.json").exists()

    assert (
        certify_main(
            [
                "run",
                "--factor-store-dir",
                str(store_dir),
                "--factor-id",
                factor_id,
                "--portfolio-policy-path",
                str(tmp_path / "lab" / "selected_portfolio_policy.json"),
                "--portfolio-lab-report-path",
                str(tmp_path / "lab" / "portfolio_lab_report.json"),
                "--portfolio-robustness-report-path",
                str(tmp_path / "lab" / "portfolio_robustness_report.json"),
                "--output-dir",
                str(tmp_path / "cert"),
                "--policy-profile",
                "sample_lenient_portfolio",
                "--register-policy",
                "--model-registry-dir",
                str(tmp_path / "registry"),
                "--pretty",
            ]
        )
        == 0
    )
    cert_payload = json.loads(capsys.readouterr().out)

    assert cert_payload["certification_status"] in {"certified", "conditional"}
    assert cert_payload["model_version_id"]
    assert (tmp_path / "cert" / "certified_portfolio_policy.json").exists()
    assert (tmp_path / "cert" / "portfolio_policy_activation_request.json").exists()
