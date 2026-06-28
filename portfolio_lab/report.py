"""Portfolio lab report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import PortfolioLabResult, PortfolioPolicyScenario, PortfolioPolicyTrial, PortfolioTrialMetrics


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
