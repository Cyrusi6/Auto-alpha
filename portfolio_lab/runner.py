"""Run portfolio policy lab trials."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.io import select_factor_id
from backtest.run_backtest import main as run_backtest_main
from factor_store import LocalFactorStore
from portfolio_optimizer import PortfolioPolicy, write_portfolio_policy

from .metrics import failed_metrics, metrics_from_backtest
from .models import PortfolioLabConfig, PortfolioLabIssue, PortfolioLabResult, PortfolioPolicyTrial
from .policy_grid import generate_portfolio_policy_grid, load_policy_grid
from .report import write_portfolio_lab_artifacts
from .robustness import build_robustness_report
from .scenarios import default_portfolio_scenarios


def run_portfolio_lab(
    config: PortfolioLabConfig,
    policies: list[PortfolioPolicy] | None = None,
) -> PortfolioLabResult:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    store = LocalFactorStore(config.factor_store_dir)
    factor_id = select_factor_id(store, config.factor_id, latest_approved=config.latest_approved, factor_type=config.factor_type)
    policies = policies or generate_portfolio_policy_grid(
        factor_id=factor_id,
        index_code=config.index_code,
        max_trials=config.max_trials,
    )
    scenarios = default_portfolio_scenarios(config.scenario_profile)
    trials: list[PortfolioPolicyTrial] = []
    metrics = []
    issues: list[PortfolioLabIssue] = []
    max_trials = config.max_trials or len(policies) * len(scenarios)
    trial_counter = 0
    for policy in policies:
        for scenario in scenarios:
            if trial_counter >= max_trials:
                break
            trial_counter += 1
            trial_id = f"trial_{trial_counter:04d}_{policy.policy_id[-8:]}_{scenario.scenario_id}"
            backtest_root = Path(config.metadata.get("backtest_root_dir") or output_dir / "trials")
            trial_dir = backtest_root / trial_id
            policy_path, _policy_md = write_portfolio_policy(policy, trial_dir, filename="portfolio_policy.json")
            argv = [
                "--data-dir",
                config.data_dir,
                "--factor-store-dir",
                config.factor_store_dir,
                "--output-dir",
                str(trial_dir),
                "--factor-id",
                factor_id,
                "--portfolio-policy-path",
                str(policy_path),
                "--index-code",
                policy.index_code,
                "--top-n",
                str(policy.top_n),
                "--max-weight",
                str(policy.max_weight),
                "--portfolio-method",
                policy.portfolio_method,
                "--risk-aversion",
                str(policy.risk_aversion),
                "--turnover-penalty",
                str(policy.turnover_penalty),
                "--max-turnover",
                str(min(policy.max_turnover, scenario.max_turnover)),
                "--max-tracking-error",
                str(min(policy.max_tracking_error, scenario.max_tracking_error)),
                "--max-participation",
                str(scenario.max_participation),
            ]
            if config.metadata.get("data_freeze_dir"):
                argv.extend(["--data-freeze-dir", str(config.metadata["data_freeze_dir"])])
            if config.metadata.get("data_version_manifest_path"):
                argv.extend(["--data-version-manifest-path", str(config.metadata["data_version_manifest_path"])])
            if config.metadata.get("require_data_freeze"):
                argv.append("--require-data-freeze")
            if config.metadata.get("universe_name"):
                argv.extend(["--universe-name", str(config.metadata["universe_name"])])
            if config.metadata.get("capacity_aware"):
                argv.extend(["--capacity-aware", "--execution-plan-dir", str(trial_dir / "execution_plan")])
            if config.metadata.get("settlement_aware"):
                argv.extend(["--settlement-aware", "--settlement-dir", str(trial_dir / "settlement")])
            if config.metadata.get("corporate_action_aware"):
                argv.append("--corporate-action-aware")
            if config.metadata.get("point_in_time"):
                argv.extend(["--point-in-time", "--feature-cutoff-mode", "next_trade_day_open"])
            if config.metadata.get("risk_controls"):
                argv.extend(
                    [
                        "--risk-controls",
                        "--risk-control-dir",
                        str(trial_dir / "risk_state"),
                    ]
                )
            if policy.use_factor_risk_model:
                argv.append("--use-factor-risk-model")
            status = "success"
            error = None
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = run_backtest_main(argv)
                if exit_code != 0:
                    raise RuntimeError(f"backtest exit code {exit_code}")
                metric = metrics_from_backtest(trial_id, policy.policy_id, scenario.scenario_id, trial_dir / "backtest_result.json")
            except Exception as exc:
                status = "failed"
                error = str(exc)
                metric = failed_metrics(trial_id, policy.policy_id, scenario.scenario_id, error)
                issues.append(PortfolioLabIssue("error", "trial_failed", error, trial_id=trial_id))
            trials.append(
                PortfolioPolicyTrial(
                    trial_id=trial_id,
                    policy_id=policy.policy_id,
                    scenario_id=scenario.scenario_id,
                    factor_id=factor_id,
                    output_dir=str(trial_dir),
                    status=status,
                    error=error,
                    policy=policy.to_dict(),
                    scenario=scenario.to_dict(),
                )
            )
            metrics.append(metric)
    robustness = build_robustness_report(metrics, policies)
    selected_id = robustness.get("selected_policy_id")
    selected = next((policy for policy in policies if policy.policy_id == selected_id), None)
    selected_payload = selected.to_dict() if selected is not None else None
    lab_id = f"portfolio_lab_{factor_id[-8:]}_{_safe_time(_utc_now())}"
    result_paths = {"output_dir": str(output_dir)}
    result = PortfolioLabResult(
        lab_id=lab_id,
        created_at=_utc_now(),
        status="success" if selected is not None and not any(issue.severity == "error" for issue in issues) else "warning",
        factor_id=factor_id,
        config=config.to_dict(),
        trials=[trial.to_dict() for trial in trials],
        metrics=[row.to_dict() for row in metrics],
        robustness=robustness,
        selected_policy=selected_payload,
        issues=[issue.to_dict() for issue in issues],
        paths=result_paths,
        summary={
            "factor_id": factor_id,
            "policy_count": len(policies),
            "scenario_count": len(scenarios),
            "trial_count": len(trials),
            "selected_policy_id": selected_id,
            "error_count": sum(issue.severity == "error" for issue in issues),
        },
    )
    paths = write_portfolio_lab_artifacts(
        result,
        [policy.to_dict() for policy in policies],
        scenarios,
        trials,
        metrics,
    )
    object.__setattr__(result, "paths", {**paths, "output_dir": str(output_dir)})
    return result


def load_or_generate_grid(config: PortfolioLabConfig, grid_path: str | None, **kwargs: Any) -> list[PortfolioPolicy]:
    if grid_path:
        return load_policy_grid(grid_path)
    return generate_portfolio_policy_grid(factor_id=config.factor_id, index_code=config.metadata.get("index_code", "000300.SH"), max_trials=config.max_trials, **kwargs)


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return value.replace(":", "").replace("-", "").replace(".", "").replace("Z", "")
