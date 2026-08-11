"""Portfolio robustness lab policies, scenarios, metrics, and workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PortfolioPolicyScenario:
    scenario_id: str
    name: str
    cost_multiplier: float = 1.0
    max_participation: float = 0.10
    max_turnover: float = 1.0
    max_tracking_error: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioPolicyTrial:
    trial_id: str
    policy_id: str
    scenario_id: str
    factor_id: str
    output_dir: str
    status: str
    error: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    scenario: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioTrialMetrics:
    trial_id: str
    policy_id: str
    scenario_id: str
    status: str
    score: float
    total_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    avg_turnover: float = 0.0
    tracking_error: float = 0.0
    fill_rate: float = 0.0
    constraint_reject_rate: float = 0.0
    capacity_warning_count: float = 0.0
    risk_constraint_violations: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioLabIssue:
    severity: str
    code: str
    message: str
    trial_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioLabConfig:
    data_dir: str
    factor_store_dir: str
    output_dir: str
    factor_id: str | None = None
    factor_type: str = "composite"
    latest_approved: bool = True
    index_code: str = "000300.SH"
    scenario_profile: str = "sample"
    max_trials: int | None = None
    pretty: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioLabResult:
    lab_id: str
    created_at: str
    status: str
    factor_id: str
    config: dict[str, Any]
    trials: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    robustness: dict[str, Any]
    selected_policy: dict[str, Any] | None
    issues: list[dict[str, Any]]
    paths: dict[str, str]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import itertools
import json
from pathlib import Path
from typing import Iterable

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact
from auto_alpha.portfolio.construction.optimizer import PortfolioPolicy, build_portfolio_policy


def generate_portfolio_policy_grid(
    factor_id: str | None = None,
    methods: Iterable[str] = ("equal_weight", "risk_aware"),
    risk_aversions: Iterable[float] = (0.5, 1.0),
    turnover_penalties: Iterable[float] = (0.0, 0.1),
    benchmark_weights: Iterable[float] = (1.0,),
    max_weight_values: Iterable[float] = (0.10,),
    max_names_values: Iterable[int] = (2, 20),
    max_turnover_values: Iterable[float] = (1.0,),
    max_tracking_error_values: Iterable[float] = (1.0,),
    top_n_values: Iterable[int] = (2, 20),
    index_code: str = "000300.SH",
    use_factor_risk_model: bool = False,
    max_trials: int | None = None,
) -> list[PortfolioPolicy]:
    policies: list[PortfolioPolicy] = []
    for values in itertools.product(
        list(methods),
        list(risk_aversions),
        list(turnover_penalties),
        list(benchmark_weights),
        list(max_weight_values),
        list(max_names_values),
        list(max_turnover_values),
        list(max_tracking_error_values),
        list(top_n_values),
    ):
        method, risk_aversion, turnover_penalty, benchmark_weight, max_weight, max_names, max_turnover, max_tracking_error, top_n = values
        if method == "equal_weight" and len([item for item in policies if item.portfolio_method == "equal_weight"]) >= 1:
            continue
        policy = build_portfolio_policy(
            policy_name=f"{method}_top{int(top_n)}_w{float(max_weight):.3f}_ra{float(risk_aversion):.2f}",
            portfolio_method=str(method),
            index_code=index_code,
            top_n=int(top_n),
            max_weight=float(max_weight),
            max_names=int(max_names),
            risk_aversion=float(risk_aversion),
            turnover_penalty=float(turnover_penalty),
            benchmark_weight=float(benchmark_weight),
            max_turnover=float(max_turnover),
            max_tracking_error=float(max_tracking_error),
            use_factor_risk_model=bool(use_factor_risk_model),
            source_factor_id=factor_id,
        )
        policies.append(policy)
        if max_trials is not None and len(policies) >= max_trials:
            break
    return policies


def load_policy_grid(path: str | Path) -> list[PortfolioPolicy]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("policies") if isinstance(payload, dict) else payload
    policies = []
    for record in records or []:
        if isinstance(record, dict):
            allowed = {key: value for key, value in record.items() if key in PortfolioPolicy.__dataclass_fields__}
            policies.append(PortfolioPolicy(**allowed))
    return policies


def write_policy_grid(policies: list[PortfolioPolicy], output_dir: str | Path) -> Path:
    path = Path(output_dir) / "portfolio_policy_grid.json"
    write_json_artifact(
        path,
        {"policies": [policy.to_dict() for policy in policies], "policy_count": len(policies)},
        artifact_type="portfolio_policy_grid",
        producer="portfolio_lab",
    )
    return path

import json
import math
from pathlib import Path
from typing import Any



def metrics_from_backtest(trial_id: str, policy_id: str, scenario_id: str, backtest_result_path: str | Path, status: str = "success") -> PortfolioTrialMetrics:
    payload = _read_json(backtest_result_path)
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    total_return = _finite(metrics.get("total_return"))
    sharpe = _finite(metrics.get("sharpe"))
    max_drawdown = _finite(metrics.get("max_drawdown"))
    avg_turnover = _finite(metrics.get("avg_turnover"))
    tracking_error = _finite(metrics.get("tracking_error"))
    fill_rate = _finite(metrics.get("fill_rate"))
    reject_rate = _finite(metrics.get("constraint_reject_rate"))
    capacity_warning_count = _finite(metrics.get("capacity_warning_count"))
    violations = _finite(metrics.get("risk_constraint_violations"))
    score = sharpe + total_return - max_drawdown - 0.25 * avg_turnover - 0.25 * reject_rate - 0.1 * violations
    return PortfolioTrialMetrics(
        trial_id=trial_id,
        policy_id=policy_id,
        scenario_id=scenario_id,
        status=status,
        score=float(score),
        total_return=total_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        avg_turnover=avg_turnover,
        tracking_error=tracking_error,
        fill_rate=fill_rate,
        constraint_reject_rate=reject_rate,
        capacity_warning_count=capacity_warning_count,
        risk_constraint_violations=violations,
        diagnostics=metrics,
    )


def failed_metrics(trial_id: str, policy_id: str, scenario_id: str, error: str) -> PortfolioTrialMetrics:
    return PortfolioTrialMetrics(trial_id, policy_id, scenario_id, "failed", -999.0, diagnostics={"error": error})


def _read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _finite(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0

import json
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_portfolio_lab_artifacts(
    result: PortfolioLabResult,
    policies: list[dict[str, Any]],
    scenarios: list[PortfolioPolicyScenario],
    trials: list[PortfolioPolicyTrial],
    metrics: list[PortfolioTrialMetrics],
) -> dict[str, str]:
    root = Path(result.paths["output_dir"])
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "portfolio_lab_report_path": root / "portfolio_lab_report.json",
        "portfolio_lab_report_md_path": root / "portfolio_lab_report.md",
        "portfolio_policy_grid_path": root / "portfolio_policy_grid.json",
        "portfolio_scenarios_path": root / "portfolio_scenarios.json",
        "portfolio_policy_trials_path": root / "portfolio_policy_trials.jsonl",
        "portfolio_trial_metrics_path": root / "portfolio_trial_metrics.jsonl",
        "portfolio_robustness_report_path": root / "portfolio_robustness_report.json",
        "portfolio_robustness_report_md_path": root / "portfolio_robustness_report.md",
        "selected_portfolio_policy_path": root / "selected_portfolio_policy.json",
        "portfolio_lab_issues_path": root / "portfolio_lab_issues.jsonl",
        "portfolio_lab_artifact_catalog_path": root / "portfolio_lab_artifact_catalog.json",
    }
    write_json_artifact(paths["portfolio_lab_report_path"], result.to_dict(), "portfolio_lab_report", "portfolio_lab")
    write_json_artifact(paths["portfolio_policy_grid_path"], {"policies": policies, "policy_count": len(policies)}, "portfolio_policy_grid", "portfolio_lab")
    write_json_artifact(paths["portfolio_scenarios_path"], {"scenarios": [item.to_dict() for item in scenarios]}, "portfolio_scenarios", "portfolio_lab")
    write_jsonl_artifact(paths["portfolio_policy_trials_path"], [item.to_dict() for item in trials], "portfolio_policy_trials", "portfolio_lab")
    write_jsonl_artifact(paths["portfolio_trial_metrics_path"], [item.to_dict() for item in metrics], "portfolio_trial_metrics", "portfolio_lab")
    write_json_artifact(paths["portfolio_robustness_report_path"], result.robustness, "portfolio_robustness_report", "portfolio_lab")
    if result.selected_policy:
        write_json_artifact(paths["selected_portfolio_policy_path"], result.selected_policy, "selected_portfolio_policy", "portfolio_lab")
    else:
        write_json_artifact(paths["selected_portfolio_policy_path"], {"policy": None}, "selected_portfolio_policy", "portfolio_lab")
    write_jsonl_artifact(paths["portfolio_lab_issues_path"], result.issues, "portfolio_lab_issues", "portfolio_lab")
    catalog = {
        "entries": [
            {"name": key.replace("_path", ""), "path": str(path), "kind": "jsonl" if str(path).endswith(".jsonl") else "json"}
            for key, path in paths.items()
            if not str(path).endswith(".md")
        ]
    }
    write_json_artifact(paths["portfolio_lab_artifact_catalog_path"], catalog, "portfolio_lab_artifact_catalog", "portfolio_lab")
    result_payload = result.to_dict()
    result_payload["paths"] = {key: str(path) for key, path in paths.items()}
    write_json_artifact(paths["portfolio_lab_report_path"], result_payload, "portfolio_lab_report", "portfolio_lab")
    paths["portfolio_lab_report_md_path"].write_text(_markdown(result), encoding="utf-8")
    paths["portfolio_robustness_report_md_path"].write_text(_robustness_markdown(result.robustness), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def _markdown(result: PortfolioLabResult) -> str:
    lines = [
        "# Portfolio Lab Report",
        "",
        f"- lab_id: `{result.lab_id}`",
        f"- status: `{result.status}`",
        f"- factor_id: `{result.factor_id}`",
        f"- selected_policy_id: `{(result.selected_policy or {}).get('policy_id')}`",
        "",
        "## Robustness",
        "",
        "```json",
        json.dumps(result.robustness, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def _robustness_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio Robustness Report",
        "",
        "| policy_id | method | selection_score | pass_ratio |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in payload.get("ranked_policies", []):
        lines.append(
            f"| {row.get('policy_id')} | {row.get('portfolio_method')} | {row.get('selection_score')} | {row.get('scenario_pass_ratio')} |"
        )
    lines.append("")
    return "\n".join(lines)

from collections import defaultdict
from typing import Any

from auto_alpha.portfolio.construction.optimizer import PortfolioPolicy



def build_robustness_report(metrics: list[PortfolioTrialMetrics], policies: list[PortfolioPolicy]) -> dict[str, Any]:
    by_policy: dict[str, list[PortfolioTrialMetrics]] = defaultdict(list)
    for row in metrics:
        by_policy[row.policy_id].append(row)
    rows = []
    for policy in policies:
        policy_rows = by_policy.get(policy.policy_id, [])
        successful = [row for row in policy_rows if row.status == "success"]
        scores = [row.score for row in successful]
        scenario_pass_ratio = len(successful) / len(policy_rows) if policy_rows else 0.0
        mean_score = sum(scores) / len(scores) if scores else -999.0
        worst_score = min(scores) if scores else -999.0
        avg_turnover = sum(row.avg_turnover for row in successful) / len(successful) if successful else 0.0
        avg_reject_rate = sum(row.constraint_reject_rate for row in successful) / len(successful) if successful else 0.0
        rows.append(
            {
                "policy_id": policy.policy_id,
                "policy_name": policy.policy_name,
                "portfolio_method": policy.portfolio_method,
                "trial_count": len(policy_rows),
                "successful_trials": len(successful),
                "scenario_pass_ratio": float(scenario_pass_ratio),
                "mean_score": float(mean_score),
                "worst_score": float(worst_score),
                "avg_turnover": float(avg_turnover),
                "avg_reject_rate": float(avg_reject_rate),
                "selection_score": float(mean_score + 0.25 * worst_score + scenario_pass_ratio - 0.1 * avg_turnover - 0.2 * avg_reject_rate),
            }
        )
    rows.sort(key=lambda item: (float(item["selection_score"]), float(item["mean_score"])), reverse=True)
    return {
        "policy_count": len(policies),
        "trial_count": len(metrics),
        "successful_trial_count": sum(row.status == "success" for row in metrics),
        "ranked_policies": rows,
        "selected_policy_id": rows[0]["policy_id"] if rows else None,
        "selected_score": rows[0]["selection_score"] if rows else -999.0,
    }

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def default_portfolio_scenarios(profile: str = "sample") -> list[PortfolioPolicyScenario]:
    if profile == "production":
        return [
            PortfolioPolicyScenario("base", "Base", 1.0, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("high_cost", "High Cost", 2.0, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("low_capacity", "Low Capacity", 1.0, 0.05, 0.8, 1.0),
            PortfolioPolicyScenario("tight_risk", "Tight Risk", 1.0, 0.10, 0.6, 0.7),
        ]
    if profile == "research":
        return [
            PortfolioPolicyScenario("base", "Base", 1.0, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("high_cost", "High Cost", 1.5, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("low_capacity", "Low Capacity", 1.0, 0.05, 0.8, 1.0),
        ]
    return [PortfolioPolicyScenario("base", "Base", 1.0, 0.10, 1.0, 1.0)]


def write_scenarios(scenarios: list[PortfolioPolicyScenario], output_dir) -> str:
    from pathlib import Path

    path = Path(output_dir) / "portfolio_scenarios.json"
    write_json_artifact(path, {"scenarios": [item.to_dict() for item in scenarios]}, "portfolio_scenarios", "portfolio_lab")
    return str(path)

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.portfolio.simulator.backtest import select_factor_id
from auto_alpha.portfolio.simulator.backtest import main as run_backtest_main
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.portfolio.construction.optimizer import PortfolioPolicy, write_portfolio_policy



def run_portfolio_lab(
    config: PortfolioLabConfig,
    policies: list[PortfolioPolicy] | None = None,
) -> PortfolioLabResult:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    store = LocalFactorStore(config.factor_store_dir)
    factor_id = select_factor_id(store, config.factor_id, latest_approved=config.latest_approved, factor_type=config.factor_type)
    record = next((item for item in store.load_factors() if item.factor_id == factor_id), None)
    if record is None or record.status != "factor_certified":
        raise RuntimeError("portfolio_lab_requires_factor_certified; use portfolio_research for combinations")
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

import argparse
import json
from pathlib import Path



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run portfolio policy lab trials.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-grid", "run", "resume", "aggregate", "select-policy", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_common(cmd)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backtest-root-dir")
    parser.add_argument("--universe-name")
    parser.add_argument("--factor-id")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="composite")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--latest-production-candidate", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--model-version-id")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--validation-lab-report-path")
    parser.add_argument("--alpha-factory-report-path")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--scenario-profile", default="sample")
    parser.add_argument("--scenario-config-path")
    parser.add_argument("--policy-grid-path")
    parser.add_argument("--portfolio-methods", default="equal_weight,risk_aware")
    parser.add_argument("--risk-aversions", default="0.5,1.0")
    parser.add_argument("--turnover-penalties", default="0.0,0.1")
    parser.add_argument("--benchmark-weights", default="1.0")
    parser.add_argument("--max-weight-values", default="0.10")
    parser.add_argument("--max-names-values", default="2,20")
    parser.add_argument("--max-turnover-values", default="1.0")
    parser.add_argument("--max-tracking-error-values", default="1.0")
    parser.add_argument("--top-n-values", default="2,20")
    parser.add_argument("--max-trials", type=int)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--use-compute-scheduler", action="store_true")
    parser.add_argument("--compute-state-dir")
    parser.add_argument("--max-parallel-cpu-jobs", type=int, default=1)
    parser.add_argument("--max-parallel-gpu-jobs", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = PortfolioLabConfig(
        data_dir=args.data_dir,
        factor_store_dir=args.factor_store_dir,
        output_dir=args.output_dir,
        factor_id=args.factor_id,
        factor_type=args.factor_type,
        latest_approved=args.latest_approved or not bool(args.factor_id),
        index_code=args.index_code,
        scenario_profile=args.scenario_profile,
        max_trials=args.max_trials,
        pretty=args.pretty,
        metadata={
            "index_code": args.index_code,
            "data_freeze_dir": args.data_freeze_dir,
            "data_version_manifest_path": args.data_version_manifest_path,
            "require_data_freeze": bool(args.require_data_freeze),
            "factor_certification_decision_path": args.factor_certification_decision_path,
            "validation_lab_report_path": args.validation_lab_report_path,
            "alpha_factory_report_path": args.alpha_factory_report_path,
            "feature_set_manifest_path": args.feature_set_manifest_path,
            "capacity_aware": bool(args.capacity_aware),
            "settlement_aware": bool(args.settlement_aware),
            "corporate_action_aware": bool(args.corporate_action_aware),
            "point_in_time": bool(args.point_in_time),
            "risk_controls": bool(args.risk_controls),
            "use_compute_scheduler": bool(args.use_compute_scheduler),
            "compute_state_dir": args.compute_state_dir,
            "backtest_root_dir": args.backtest_root_dir,
            "universe_name": args.universe_name,
            "as_of_date": args.as_of_date,
        },
    )
    try:
        if args.command == "init-grid":
            policies = _policies_from_args(args)
            path = write_policy_grid(policies, args.output_dir)
            payload = {"policy_grid_path": str(path), "policy_count": len(policies)}
        else:
            policies = load_policy_grid(args.policy_grid_path) if args.policy_grid_path else _policies_from_args(args)
            result = run_portfolio_lab(config, policies=policies)
            payload = {"status": result.status, **result.summary, "paths": result.paths}
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _policies_from_args(args) -> list:
    return generate_portfolio_policy_grid(
        factor_id=args.factor_id,
        methods=_strs(args.portfolio_methods),
        risk_aversions=_floats(args.risk_aversions),
        turnover_penalties=_floats(args.turnover_penalties),
        benchmark_weights=_floats(args.benchmark_weights),
        max_weight_values=_floats(args.max_weight_values),
        max_names_values=_ints(args.max_names_values),
        max_turnover_values=_floats(args.max_turnover_values),
        max_tracking_error_values=_floats(args.max_tracking_error_values),
        top_n_values=_ints(args.top_n_values),
        index_code=args.index_code,
        use_factor_risk_model=args.use_factor_risk_model,
        max_trials=args.max_trials,
    )


def _strs(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _floats(value: str) -> list[float]:
    return [float(item) for item in _strs(value)]


def _ints(value: str) -> list[int]:
    return [int(float(item)) for item in _strs(value)]


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "PortfolioLabConfig",
    "PortfolioLabIssue",
    "PortfolioLabResult",
    "PortfolioPolicyScenario",
    "PortfolioPolicyTrial",
    "PortfolioTrialMetrics",
    "generate_portfolio_policy_grid",
    "load_policy_grid",
    "run_portfolio_lab",
    "write_policy_grid",
]
