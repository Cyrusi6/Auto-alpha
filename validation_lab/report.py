"""Writers for validation lab artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    FactorValidationSummary,
    FactorValidationWindowResult,
    MultipleTestingSummary,
    OverfitRiskSummary,
    PlaceboTestResult,
    RegimeValidationResult,
    SensitivityTestResult,
    StressBacktestResult,
    ValidationIssue,
    ValidationLabReport,
    ValidationSplit,
)


def write_validation_lab_artifacts(
    output_dir: str | Path,
    report: ValidationLabReport,
    splits: list[ValidationSplit],
    window_results: list[FactorValidationWindowResult],
    summary: FactorValidationSummary,
    multiple_testing: MultipleTestingSummary,
    overfit: OverfitRiskSummary,
    placebo: PlaceboTestResult | None,
    placebo_trials: list[dict[str, Any]],
    regimes: list[RegimeValidationResult],
    sensitivity: list[SensitivityTestResult],
    sensitivity_surface: dict[str, Any],
    stress: list[StressBacktestResult],
    issues: list[ValidationIssue],
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "validation_lab_report_path": root / "validation_lab_report.json",
        "validation_lab_report_md_path": root / "validation_lab_report.md",
        "validation_splits_path": root / "validation_splits.jsonl",
        "factor_validation_results_path": root / "factor_validation_results.jsonl",
        "factor_validation_summary_path": root / "factor_validation_summary.json",
        "multiple_testing_report_path": root / "multiple_testing_report.json",
        "overfit_risk_report_path": root / "overfit_risk_report.json",
        "placebo_test_report_path": root / "placebo_test_report.json",
        "placebo_trials_path": root / "placebo_trials.jsonl",
        "regime_validation_report_path": root / "regime_validation_report.json",
        "regime_results_path": root / "regime_results.jsonl",
        "sensitivity_report_path": root / "sensitivity_report.json",
        "sensitivity_results_path": root / "sensitivity_results.jsonl",
        "robustness_surface_path": root / "robustness_surface.json",
        "stress_backtest_report_path": root / "stress_backtest_report.json",
        "stress_backtest_results_path": root / "stress_backtest_results.jsonl",
        "validation_issues_path": root / "validation_issues.jsonl",
    }
    write_json_artifact(paths["validation_lab_report_path"], report.to_dict(), "validation_lab_report", "validation_lab")
    paths["validation_lab_report_md_path"].write_text(_markdown(report), encoding="utf-8")
    write_jsonl_artifact(paths["validation_splits_path"], [item.to_dict() for item in splits], "validation_splits", "validation_lab")
    write_jsonl_artifact(
        paths["factor_validation_results_path"],
        [item.to_dict() for item in window_results],
        "factor_validation_results",
        "validation_lab",
    )
    write_json_artifact(
        paths["factor_validation_summary_path"],
        summary.to_dict(),
        "factor_validation_summary",
        "validation_lab",
    )
    write_json_artifact(
        paths["multiple_testing_report_path"],
        multiple_testing.to_dict(),
        "multiple_testing_report",
        "validation_lab",
    )
    write_json_artifact(paths["overfit_risk_report_path"], overfit.to_dict(), "overfit_risk_report", "validation_lab")
    write_json_artifact(
        paths["placebo_test_report_path"],
        placebo.to_dict() if placebo is not None else {"enabled": False},
        "placebo_test_report",
        "validation_lab",
    )
    write_jsonl_artifact(paths["placebo_trials_path"], placebo_trials, "placebo_trials", "validation_lab")
    write_json_artifact(
        paths["regime_validation_report_path"],
        _aggregate_regime(regimes),
        "regime_validation_report",
        "validation_lab",
    )
    write_jsonl_artifact(paths["regime_results_path"], [item.to_dict() for item in regimes], "regime_results", "validation_lab")
    write_json_artifact(
        paths["sensitivity_report_path"],
        _aggregate_sensitivity(sensitivity, sensitivity_surface),
        "sensitivity_report",
        "validation_lab",
    )
    write_jsonl_artifact(
        paths["sensitivity_results_path"],
        [item.to_dict() for item in sensitivity],
        "sensitivity_results",
        "validation_lab",
    )
    write_json_artifact(paths["robustness_surface_path"], sensitivity_surface, "robustness_surface", "validation_lab")
    write_json_artifact(
        paths["stress_backtest_report_path"],
        _aggregate_stress(stress),
        "stress_backtest_report",
        "validation_lab",
    )
    write_jsonl_artifact(
        paths["stress_backtest_results_path"],
        [item.to_dict() for item in stress],
        "stress_backtest_results",
        "validation_lab",
    )
    write_jsonl_artifact(paths["validation_issues_path"], [item.to_dict() for item in issues], "validation_issues", "validation_lab")
    return {key: str(value) for key, value in paths.items()}


def write_stress_backtest_artifacts(
    output_dir: str | Path,
    stress_results: list[StressBacktestResult],
    summary: dict[str, Any],
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "stress_backtest_report.json"
    md_path = root / "stress_backtest_report.md"
    results_path = root / "stress_backtest_results.jsonl"
    payload = {"summary": summary, **summary, "results": [item.to_dict() for item in stress_results]}
    write_json_artifact(report_path, payload, "stress_backtest_report", "validation_lab")
    write_jsonl_artifact(results_path, [item.to_dict() for item in stress_results], "stress_backtest_results", "validation_lab")
    md_path.write_text(_stress_markdown(payload), encoding="utf-8")
    return {
        "stress_backtest_report_path": str(report_path),
        "stress_backtest_report_md_path": str(md_path),
        "stress_backtest_results_path": str(results_path),
    }


def _aggregate_regime(items: list[RegimeValidationResult]) -> dict[str, Any]:
    return {
        "regime_count": len(items),
        "regime_pass_ratio": sum(item.passed for item in items) / len(items) if items else 0.0,
        "results": [item.to_dict() for item in items],
    }


def _aggregate_sensitivity(items: list[SensitivityTestResult], surface: dict[str, Any]) -> dict[str, Any]:
    return {
        **surface,
        "scenario_count": len(items),
        "sensitivity_pass_ratio": sum(item.passed for item in items) / len(items) if items else 0.0,
        "results": [item.to_dict() for item in items],
    }


def _aggregate_stress(items: list[StressBacktestResult]) -> dict[str, Any]:
    return {
        "stress_scenario_count": len(items),
        "stress_backtest_pass_ratio": sum(item.passed for item in items) / len(items) if items else 0.0,
        "results": [item.to_dict() for item in items],
    }


def _markdown(report: ValidationLabReport) -> str:
    payload = report.to_dict()
    lines = [
        "# Validation Lab Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- factor_id: `{(payload.get('target') or {}).get('factor_id', '')}`",
        f"- split_method: `{payload.get('split_method')}`",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(payload.get("validation_summary", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Overfit",
        "",
        "```json",
        json.dumps(payload.get("overfit_risk_summary", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Issues",
        "",
        "| severity | code | message |",
        "| --- | --- | --- |",
    ]
    for issue in payload.get("issues", []):
        lines.append(f"| {issue.get('severity')} | {issue.get('code')} | {issue.get('message')} |")
    return "\n".join(lines) + "\n"


def _stress_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Stress Backtest Report",
        "",
        "```json",
        json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "| scenario | passed | score | reason |",
        "| --- | --- | --- | --- |",
    ]
    for item in payload.get("results", []):
        lines.append(
            f"| {item.get('scenario_id')} | {item.get('passed')} | {(item.get('metrics') or {}).get('score', '')} | {item.get('reason', '')} |"
        )
    return "\n".join(lines) + "\n"
