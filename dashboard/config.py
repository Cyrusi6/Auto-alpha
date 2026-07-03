"""Dashboard configuration for local A-share artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DashboardConfig:
    data_dir: Path = Path("data/ashare")
    factor_store_dir: Path = Path("artifacts/factor_store")
    report_dir: Path = Path("artifacts/reports")
    backtest_dir: Path = Path("artifacts/backtest")
    orders_dir: Path = Path("artifacts/orders")
    approval_store_dir: Path = Path("artifacts/approvals")
    paper_account_dir: Path = Path("artifacts/account")
    production_dir: Path = Path("artifacts/production")
    production_orchestrator_dir: Path = Path("artifacts/production_orchestrator")
    production_replay_dir: Path = Path("artifacts/production_replay")
    broker_file_gateway_dir: Path = Path("artifacts/broker_file_gateway")
    operator_handoff_dir: Path = Path("artifacts/operator_handoff")
    broker_mapping_certification_dir: Path = Path("artifacts/broker_mapping_certification")
    shadow_trading_dir: Path = Path("artifacts/shadow_trading")
    shadow_lab_dir: Path = Path("artifacts/shadow_lab")
    live_readiness_dir: Path = Path("artifacts/live_readiness")
    program_trading_compliance_dir: Path = Path("artifacts/program_trading_compliance")
    broker_connectivity_dir: Path = Path("artifacts/broker_connectivity")
    broker_readonly_mirror_dir: Path = Path("artifacts/broker_readonly_mirror")
    broker_uat_dir: Path = Path("artifacts/broker_uat")
    go_live_gate_dir: Path = Path("artifacts/go_live_gate")
    incident_dir: Path = Path("artifacts/incidents")
    monitoring_dir: Path = Path("artifacts/monitoring")
    matrix_cache_dir: Path = Path("data/ashare/matrix_cache")
    benchmark_dir: Path = Path("artifacts/benchmark")
    cross_source_dir: Path = Path("artifacts/cross_source")
    data_source_smoke_dir: Path = Path("artifacts/data_source_smoke")
    backfill_dir: Path = Path("artifacts/backfill")
    data_lake_dir: Path = Path("artifacts/data_lake")
    real_data_dir: Path = Path("artifacts/real_data")
    matrix_refresh_dir: Path = Path("artifacts/matrix_refresh")
    compute_dir: Path = Path("artifacts/compute")
    experiment_dir: Path = Path("artifacts/experiment")
    schema_validation_dir: Path = Path("artifacts/schema_validation")
    release_dir: Path = Path("artifacts/release")
    ci_dir: Path = Path(".ci_artifacts")
    formula_corpus_dir: Path = Path("artifacts/formula_corpus")
    formula_batch_eval_dir: Path = Path("artifacts/formula_batch_eval")
    feature_factory_dir: Path = Path("artifacts/features")
    alpha_factory_dir: Path = Path("artifacts/alpha_factory")
    alpha_experiment_store_dir: Path = Path("artifacts/alpha_experiment_store")
    validation_lab_dir: Path = Path("artifacts/validation_lab")
    validation_campaign_store_dir: Path = Path("artifacts/validation_campaign_store")
    factor_certification_dir: Path = Path("artifacts/factor_certification")
    factor_certification_campaign_dir: Path = Path("artifacts/factor_certification_campaign")
    portfolio_lab_dir: Path = Path("artifacts/portfolio_lab")
    portfolio_certification_dir: Path = Path("artifacts/portfolio_certification")
    portfolio_campaign_dir: Path = Path("artifacts/portfolio_campaign")
    pretrain_dir: Path = Path("artifacts/alphagpt_pretrain")
    model_registry_dir: Path = Path("artifacts/model_registry")
    model_lifecycle_dir: Path = Path("artifacts/model_lifecycle")
    pit_dir: Path = Path("artifacts/point_in_time")
    leakage_dir: Path = Path("artifacts/leakage_audit")

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        return cls(
            data_dir=Path(os.getenv("ASHARE_DASHBOARD_DATA_DIR") or os.getenv("ASHARE_DATA_DIR") or "data/ashare"),
            factor_store_dir=Path(
                os.getenv("ASHARE_DASHBOARD_FACTOR_STORE_DIR")
                or os.getenv("ASHARE_FACTOR_STORE_DIR")
                or "artifacts/factor_store"
            ),
            report_dir=Path(os.getenv("ASHARE_DASHBOARD_REPORT_DIR") or "artifacts/reports"),
            backtest_dir=Path(os.getenv("ASHARE_DASHBOARD_BACKTEST_DIR") or "artifacts/backtest"),
            orders_dir=Path(os.getenv("ASHARE_DASHBOARD_ORDERS_DIR") or os.getenv("ASHARE_ORDER_OUTPUT_DIR") or "artifacts/orders"),
            approval_store_dir=Path(os.getenv("ASHARE_DASHBOARD_APPROVAL_STORE_DIR") or "artifacts/approvals"),
            paper_account_dir=Path(os.getenv("ASHARE_DASHBOARD_PAPER_ACCOUNT_DIR") or "artifacts/account"),
            production_dir=Path(os.getenv("ASHARE_DASHBOARD_PRODUCTION_DIR") or "artifacts/production"),
            production_orchestrator_dir=Path(
                os.getenv("ASHARE_DASHBOARD_PRODUCTION_ORCHESTRATOR_DIR") or "artifacts/production_orchestrator"
            ),
            production_replay_dir=Path(os.getenv("ASHARE_DASHBOARD_PRODUCTION_REPLAY_DIR") or "artifacts/production_replay"),
            broker_file_gateway_dir=Path(os.getenv("ASHARE_DASHBOARD_BROKER_FILE_GATEWAY_DIR") or "artifacts/broker_file_gateway"),
            operator_handoff_dir=Path(os.getenv("ASHARE_DASHBOARD_OPERATOR_HANDOFF_DIR") or "artifacts/operator_handoff"),
            broker_mapping_certification_dir=Path(
                os.getenv("ASHARE_DASHBOARD_BROKER_MAPPING_CERTIFICATION_DIR") or "artifacts/broker_mapping_certification"
            ),
            shadow_trading_dir=Path(os.getenv("ASHARE_DASHBOARD_SHADOW_TRADING_DIR") or "artifacts/shadow_trading"),
            shadow_lab_dir=Path(os.getenv("ASHARE_DASHBOARD_SHADOW_LAB_DIR") or "artifacts/shadow_lab"),
            live_readiness_dir=Path(os.getenv("ASHARE_DASHBOARD_LIVE_READINESS_DIR") or "artifacts/live_readiness"),
            program_trading_compliance_dir=Path(
                os.getenv("ASHARE_DASHBOARD_PROGRAM_TRADING_COMPLIANCE_DIR") or "artifacts/program_trading_compliance"
            ),
            broker_connectivity_dir=Path(os.getenv("ASHARE_DASHBOARD_BROKER_CONNECTIVITY_DIR") or "artifacts/broker_connectivity"),
            broker_readonly_mirror_dir=Path(os.getenv("ASHARE_DASHBOARD_BROKER_READONLY_MIRROR_DIR") or "artifacts/broker_readonly_mirror"),
            broker_uat_dir=Path(os.getenv("ASHARE_DASHBOARD_BROKER_UAT_DIR") or "artifacts/broker_uat"),
            go_live_gate_dir=Path(os.getenv("ASHARE_DASHBOARD_GO_LIVE_GATE_DIR") or "artifacts/go_live_gate"),
            incident_dir=Path(os.getenv("ASHARE_DASHBOARD_INCIDENT_DIR") or "artifacts/incidents"),
            monitoring_dir=Path(os.getenv("ASHARE_DASHBOARD_MONITORING_DIR") or "artifacts/monitoring"),
            matrix_cache_dir=Path(os.getenv("ASHARE_DASHBOARD_MATRIX_CACHE_DIR") or "data/ashare/matrix_cache"),
            benchmark_dir=Path(os.getenv("ASHARE_DASHBOARD_BENCHMARK_DIR") or "artifacts/benchmark"),
            cross_source_dir=Path(os.getenv("ASHARE_DASHBOARD_CROSS_SOURCE_DIR") or "artifacts/cross_source"),
            data_source_smoke_dir=Path(os.getenv("ASHARE_DASHBOARD_DATA_SOURCE_SMOKE_DIR") or "artifacts/data_source_smoke"),
            backfill_dir=Path(os.getenv("ASHARE_DASHBOARD_BACKFILL_DIR") or "artifacts/backfill"),
            data_lake_dir=Path(os.getenv("ASHARE_DASHBOARD_DATA_LAKE_DIR") or "artifacts/data_lake"),
            real_data_dir=Path(os.getenv("ASHARE_DASHBOARD_REAL_DATA_DIR") or os.getenv("ASHARE_REAL_DATA_OUTPUT_DIR") or "artifacts/real_data"),
            matrix_refresh_dir=Path(os.getenv("ASHARE_DASHBOARD_MATRIX_REFRESH_DIR") or "artifacts/matrix_refresh"),
            compute_dir=Path(os.getenv("ASHARE_DASHBOARD_COMPUTE_DIR") or "artifacts/compute"),
            experiment_dir=Path(os.getenv("ASHARE_DASHBOARD_EXPERIMENT_DIR") or "artifacts/experiment"),
            schema_validation_dir=Path(os.getenv("ASHARE_DASHBOARD_SCHEMA_VALIDATION_DIR") or "artifacts/schema_validation"),
            release_dir=Path(os.getenv("ASHARE_DASHBOARD_RELEASE_DIR") or "artifacts/release"),
            ci_dir=Path(os.getenv("ASHARE_DASHBOARD_CI_DIR") or ".ci_artifacts"),
            formula_corpus_dir=Path(os.getenv("ASHARE_DASHBOARD_FORMULA_CORPUS_DIR") or "artifacts/formula_corpus"),
            formula_batch_eval_dir=Path(os.getenv("ASHARE_DASHBOARD_FORMULA_BATCH_EVAL_DIR") or "artifacts/formula_batch_eval"),
            feature_factory_dir=Path(os.getenv("ASHARE_DASHBOARD_FEATURE_FACTORY_DIR") or "artifacts/features"),
            alpha_factory_dir=Path(os.getenv("ASHARE_DASHBOARD_ALPHA_FACTORY_DIR") or "artifacts/alpha_factory"),
            alpha_experiment_store_dir=Path(
                os.getenv("ASHARE_DASHBOARD_ALPHA_EXPERIMENT_STORE_DIR") or "artifacts/alpha_experiment_store"
            ),
            validation_lab_dir=Path(os.getenv("ASHARE_DASHBOARD_VALIDATION_LAB_DIR") or "artifacts/validation_lab"),
            validation_campaign_store_dir=Path(
                os.getenv("ASHARE_DASHBOARD_VALIDATION_CAMPAIGN_STORE_DIR") or "artifacts/validation_campaign_store"
            ),
            factor_certification_dir=Path(os.getenv("ASHARE_DASHBOARD_FACTOR_CERTIFICATION_DIR") or "artifacts/factor_certification"),
            factor_certification_campaign_dir=Path(
                os.getenv("ASHARE_DASHBOARD_FACTOR_CERTIFICATION_CAMPAIGN_DIR") or "artifacts/factor_certification_campaign"
            ),
            portfolio_lab_dir=Path(os.getenv("ASHARE_DASHBOARD_PORTFOLIO_LAB_DIR") or "artifacts/portfolio_lab"),
            portfolio_certification_dir=Path(os.getenv("ASHARE_DASHBOARD_PORTFOLIO_CERTIFICATION_DIR") or "artifacts/portfolio_certification"),
            portfolio_campaign_dir=Path(os.getenv("ASHARE_DASHBOARD_PORTFOLIO_CAMPAIGN_DIR") or "artifacts/portfolio_campaign"),
            pretrain_dir=Path(os.getenv("ASHARE_DASHBOARD_PRETRAIN_DIR") or "artifacts/alphagpt_pretrain"),
            model_registry_dir=Path(os.getenv("ASHARE_DASHBOARD_MODEL_REGISTRY_DIR") or "artifacts/model_registry"),
            model_lifecycle_dir=Path(os.getenv("ASHARE_DASHBOARD_MODEL_LIFECYCLE_DIR") or "artifacts/model_lifecycle"),
            pit_dir=Path(os.getenv("ASHARE_DASHBOARD_PIT_DIR") or "artifacts/point_in_time"),
            leakage_dir=Path(os.getenv("ASHARE_DASHBOARD_LEAKAGE_DIR") or "artifacts/leakage_audit"),
        )
