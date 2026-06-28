"""Local offline CI runner for repeatable release checks."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from release_manager.inventory import PLATFORM_MODULES

from .commands import CiCommandResult, run_command


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local offline CI smoke suite.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true")
    mode.add_argument("--full", action="store_true")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path.cwd()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = "full" if args.full else "quick"
    results: list[CiCommandResult] = []
    import_result = _import_smoke()
    if import_result is not None:
        results.append(import_result)
    if args.fail_fast and results and not results[-1].success:
        return _finish(args, output_dir, mode, results)
    quick_dir = output_dir / "quick_artifacts"
    smoke_dir = quick_dir / "sample_smoke"
    sample_data_dir = quick_dir / "sample_data"
    commands = [
        (
            "data_source_sample_smoke",
            [
                sys.executable,
                "-m",
                "data_source_validation.run_smoke",
                "--provider",
                "sample",
                "--data-dir",
                str(sample_data_dir),
                "--output-dir",
                str(smoke_dir),
                "--start-date",
                "20240102",
                "--end-date",
                "20240104",
                "--datasets",
                "securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions",
                "--index-codes",
                "000300.SH",
                "--validate",
                "--stats",
                "--pretty",
            ],
        ),
        (
            "corporate_actions_smoke",
            [
                sys.executable,
                "-m",
                "corporate_actions.run_actions",
                "report",
                "--data-dir",
                str(sample_data_dir),
                "--output-dir",
                str(quick_dir / "corporate_actions"),
                "--start-date",
                "20240102",
                "--end-date",
                "20240104",
                "--reconcile-adjustment",
                "--pretty",
            ],
        ),
        (
            "paper_account_reset_smoke",
            [
                sys.executable,
                "-m",
                "paper_account.run_account",
                "--account-dir",
                str(quick_dir / "account"),
                "reset",
                "--initial-cash",
                "1000000",
                "--pretty",
            ],
        ),
        (
            "settlement_engine_smoke",
            [
                sys.executable,
                "-m",
                "settlement_engine.run_settlement",
                "smoke",
                "--data-dir",
                str(sample_data_dir),
                "--account-dir",
                str(quick_dir / "account"),
                "--settlement-dir",
                str(quick_dir / "settlement"),
                "--as-of-date",
                "20240104",
                "--profile",
                "cn_ashare_paper_default",
                "--pretty",
            ],
        ),
        (
            "risk_controls_smoke",
            [
                sys.executable,
                "-m",
                "risk_controls.run_controls",
                "smoke",
                "--output-dir",
                str(quick_dir / "risk_controls"),
                "--state-dir",
                str(quick_dir / "risk_state"),
                "--pretty",
            ],
        ),
        (
            "compute_cluster_probe",
            [
                sys.executable,
                "-m",
                "compute_cluster.run_compute",
                "probe",
                "--state-dir",
                str(quick_dir / "compute_state"),
                "--output-dir",
                str(quick_dir / "compute_probe"),
                "--pretty",
            ],
        ),
        (
            "compute_cluster_smoke",
            [
                sys.executable,
                "-m",
                "compute_cluster.run_compute",
                "smoke",
                "--state-dir",
                str(quick_dir / "compute_state"),
                "--output-dir",
                str(quick_dir / "compute_smoke"),
                "--pretty",
            ],
        ),
        (
            "point_in_time_validate_smoke",
            [
                sys.executable,
                "-m",
                "point_in_time.run_pit",
                "validate",
                "--data-dir",
                str(sample_data_dir),
                "--output-dir",
                str(quick_dir / "pit"),
                "--start-date",
                "20240102",
                "--end-date",
                "20240104",
                "--as-of-date",
                "20240104",
                "--min-listing-days",
                "0",
                "--pretty",
            ],
        ),
        (
            "model_core_register_smoke",
            [
                sys.executable,
                "-m",
                "model_core.engine",
                "--dry-run",
                "--register",
                "--data-dir",
                str(sample_data_dir),
                "--output-dir",
                str(quick_dir / "model_out"),
                "--factor-store-dir",
                str(quick_dir / "factor_store"),
                "--report-dir",
                str(quick_dir / "reports"),
                "--pretty",
            ],
        ),
        (
            "feature_factory_v2_smoke",
            [
                sys.executable,
                "-m",
                "feature_factory.run_features",
                "build",
                "--data-dir",
                str(sample_data_dir),
                "--output-dir",
                str(quick_dir / "features_v2"),
                "--feature-set-name",
                "ashare_features_v2",
                "--pretty",
            ],
        ),
        (
            "alpha_factory_smoke",
            [
                sys.executable,
                "-m",
                "alpha_factory.run_factory",
                "run",
                "--campaign-name",
                "local_ci_alpha",
                "--data-dir",
                str(sample_data_dir),
                "--factor-store-dir",
                str(quick_dir / "alpha_store"),
                "--output-dir",
                str(quick_dir / "alpha_factory"),
                "--report-dir",
                str(quick_dir / "alpha_reports"),
                "--feature-set-name",
                "ashare_features_v2",
                "--feature-set-manifest-path",
                str(quick_dir / "features_v2" / "feature_set_manifest.json"),
                "--candidate-budget",
                "12",
                "--template-budget",
                "4",
                "--random-budget",
                "4",
                "--mutation-budget",
                "2",
                "--crossover-budget",
                "1",
                "--corpus-budget",
                "0",
                "--proxy-max-candidates",
                "12",
                "--top-k",
                "4",
                "--pretty",
            ],
        ),
        (
            "leakage_audit_smoke",
            [
                sys.executable,
                "-m",
                "leakage_audit.run_audit",
                "--data-dir",
                str(sample_data_dir),
                "--factor-store-dir",
                str(quick_dir / "factor_store"),
                "--output-dir",
                str(quick_dir / "leakage"),
                "--as-of-date",
                "20240104",
                "--cutoff-date",
                "20240104",
                "--run-static-scan",
                "--run-truncation-test",
                "--max-formulas",
                "3",
                "--point-in-time",
                "--pretty",
            ],
        ),
        (
            "validation_lab_smoke",
            [
                sys.executable,
                "-m",
                "validation_lab.run_validation",
                "run-suite",
                "--data-dir",
                str(sample_data_dir),
                "--factor-store-dir",
                str(quick_dir / "factor_store"),
                "--latest-approved",
                "--output-dir",
                str(quick_dir / "validation_lab"),
                "--run-placebo",
                "--placebo-trials",
                "2",
                "--run-regime",
                "--run-sensitivity",
                "--run-stress-backtest",
                "--pretty",
            ],
        ),
        (
            "factor_certification_smoke",
            [
                sys.executable,
                "-m",
                "factor_certification.run_certify",
                "run",
                "--factor-store-dir",
                str(quick_dir / "factor_store"),
                "--latest-approved",
                "--output-dir",
                str(quick_dir / "factor_certification"),
                "--validation-lab-report-path",
                str(quick_dir / "validation_lab" / "validation_lab_report.json"),
                "--factor-validation-summary-path",
                str(quick_dir / "validation_lab" / "factor_validation_summary.json"),
                "--multiple-testing-report-path",
                str(quick_dir / "validation_lab" / "multiple_testing_report.json"),
                "--overfit-risk-report-path",
                str(quick_dir / "validation_lab" / "overfit_risk_report.json"),
                "--placebo-test-report-path",
                str(quick_dir / "validation_lab" / "placebo_test_report.json"),
                "--stress-backtest-report-path",
                str(quick_dir / "validation_lab" / "stress_backtest_report.json"),
                "--pretty",
            ],
        ),
        (
            "model_lifecycle_smoke",
            [
                sys.executable,
                "-m",
                "factor_lifecycle.run_lifecycle",
                "propose-activation",
                "--data-dir",
                str(sample_data_dir),
                "--factor-store-dir",
                str(quick_dir / "factor_store"),
                "--registry-dir",
                str(quick_dir / "model_registry"),
                "--approval-store-dir",
                str(quick_dir / "approvals"),
                "--output-dir",
                str(quick_dir / "model_lifecycle"),
                "--as-of-date",
                "20240104",
                "--create-review-package",
                "--require-approval",
                "--validation-lab-report-path",
                str(quick_dir / "validation_lab" / "validation_lab_report.json"),
                "--factor-validation-summary-path",
                str(quick_dir / "validation_lab" / "factor_validation_summary.json"),
                "--overfit-risk-report-path",
                str(quick_dir / "validation_lab" / "overfit_risk_report.json"),
                "--placebo-test-report-path",
                str(quick_dir / "validation_lab" / "placebo_test_report.json"),
                "--factor-certification-decision-path",
                str(quick_dir / "factor_certification" / "factor_certification_decision.json"),
                "--pretty",
            ],
        ),
        (
            "broker_statement_synthesize_smoke",
            [
                sys.executable,
                "-m",
                "broker_statement.run_statement",
                "synthesize-from-internal",
                "--output-dir",
                str(quick_dir / "statement_source"),
                "--paper-account-dir",
                str(quick_dir / "account"),
                "--account-id",
                "paper_ashare",
                "--broker-name",
                "synthetic_broker",
                "--trade-date",
                "20240104",
                "--as-of-date",
                "20240104",
                "--pretty",
            ],
        ),
        (
            "broker_statement_import_smoke",
            [
                sys.executable,
                "-m",
                "broker_statement.run_statement",
                "import",
                "--source-dir",
                str(quick_dir / "statement_source"),
                "--output-dir",
                str(quick_dir / "statement_import"),
                "--account-id",
                "paper_ashare",
                "--broker-name",
                "synthetic_broker",
                "--trade-date",
                "20240104",
                "--as-of-date",
                "20240104",
                "--pretty",
            ],
        ),
        (
            "eod_reconciliation_smoke",
            [
                sys.executable,
                "-m",
                "reconciliation_center.run_reconcile",
                "eod",
                "--statement-dir",
                str(quick_dir / "statement_import"),
                "--paper-account-dir",
                str(quick_dir / "account"),
                "--output-dir",
                str(quick_dir / "eod_reconciliation"),
                "--account-id",
                "paper_ashare",
                "--trade-date",
                "20240104",
                "--as-of-date",
                "20240104",
                "--pretty",
            ],
        ),
        (
            "artifact_schema_validate",
            [
                sys.executable,
                "-m",
                "artifact_schema.run_validate",
                "--artifact-dir",
                str(smoke_dir),
                "--artifact-dir",
                str(quick_dir / "corporate_actions"),
                "--artifact-dir",
                str(quick_dir / "settlement"),
                "--artifact-dir",
                str(quick_dir / "compute_probe"),
                "--artifact-dir",
                str(quick_dir / "compute_state"),
                "--artifact-dir",
                str(quick_dir / "compute_smoke"),
                "--artifact-dir",
                str(quick_dir / "account"),
                "--artifact-dir",
                str(quick_dir / "pit"),
                "--artifact-dir",
                str(quick_dir / "leakage"),
                "--artifact-dir",
                str(quick_dir / "validation_lab"),
                "--artifact-dir",
                str(quick_dir / "factor_certification"),
                "--artifact-dir",
                str(quick_dir / "model_registry"),
                "--artifact-dir",
                str(quick_dir / "model_lifecycle"),
                "--artifact-dir",
                str(quick_dir / "features_v2"),
                "--artifact-dir",
                str(quick_dir / "alpha_factory"),
                "--artifact-dir",
                str(quick_dir / "statement_import"),
                "--artifact-dir",
                str(quick_dir / "eod_reconciliation"),
                "--output-dir",
                str(output_dir / "schema"),
                "--write-manifest",
                "--pretty",
            ],
        ),
        (
            "formula_corpus_smoke",
            [
                sys.executable,
                "-m",
                "formula_corpus.run_corpus",
                "--output-dir",
                str(quick_dir / "formula_corpus"),
                "--pretty",
            ],
        ),
        (
            "alphagpt_pretrain_smoke",
            [
                sys.executable,
                "-m",
                "neural_search.run_pretrain",
                "--sequence-path",
                str(quick_dir / "formula_corpus" / "formula_sequences.jsonl"),
                "--output-dir",
                str(quick_dir / "pretrain"),
                "--epochs",
                "1",
                "--batch-size",
                "4",
                "--max-sequences",
                "8",
                "--device",
                "cpu",
                "--pretty",
            ],
        ),
        (
            "release_manager_dry_run",
            [
                sys.executable,
                "-m",
                "release_manager.run_release",
                "--release-name",
                f"local_ci_{mode}",
                "--output-dir",
                str(output_dir / "release"),
                "--artifact-dir",
                str(smoke_dir),
                "--run-import-smoke",
                "--run-dashboard-import",
                "--run-schema-validation",
                "--pretty",
            ],
        ),
    ]
    if mode == "full":
        commands.append(
            (
                "research_suite_sample",
                [
                    sys.executable,
                    "-m",
                    "research_suite.run_suite",
                    "--suite-name",
                    "local_ci_suite",
                    "--provider",
                    "sample",
                    "--data-dir",
                    str(output_dir / "suite_data"),
                    "--universe-name",
                    "csi300_sample",
                    "--index-code",
                    "000300.SH",
                    "--factor-store-dir",
                    str(output_dir / "suite_store"),
                    "--report-dir",
                    str(output_dir / "suite_reports"),
                    "--output-dir",
                    str(output_dir / "suite"),
                    "--backtest-dir",
                    str(output_dir / "suite_backtest"),
                    "--orders-dir",
                    str(output_dir / "suite_orders"),
                    "--as-of-date",
                    "20240104",
                    "--search-mode",
                    "random",
                    "--search-population-size",
                    "6",
                    "--search-generations",
                    "1",
                    "--search-max-candidates",
                    "4",
                    "--top-k",
                    "3",
                    "--composite-method",
                    "rank_average",
                    "--promote-latest-composite",
                    "--walk-forward-train-size",
                    "1",
                    "--walk-forward-test-size",
                    "1",
                    "--walk-forward-step-size",
                    "1",
                    "--pretty",
                ],
            )
        )
    if mode == "full" and not args.skip_build:
        commands.append(("package_build", ["uv", "build"]))
    if mode == "full" and not args.skip_pytest:
        commands.append(("pytest", ["uv", "run", "pytest"]))
    for name, command in commands:
        result = run_command(name, command, root)
        results.append(result)
        if args.fail_fast and not result.success:
            break
    return _finish(args, output_dir, mode, results)


def _finish(args: argparse.Namespace, output_dir: Path, mode: str, results: list[CiCommandResult]) -> int:
    payload = {
        "created_at": _utc_now(),
        "mode": mode,
        "status": "passed" if all(result.success for result in results) else "failed",
        "commands": [result.to_dict() for result in results],
    }
    write_json_artifact(output_dir / "ci_report.json", payload, artifact_type="ci_report", producer="ci")
    write_jsonl_artifact(output_dir / "ci_command_results.jsonl", [result.to_dict() for result in results], artifact_type="ci_command_results", producer="ci")
    (output_dir / "ci_report.md").write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if payload["status"] == "passed" else 1


def _import_smoke() -> CiCommandResult:
    started = _utc_now()
    failures = []
    for module in [*PLATFORM_MODULES, "dashboard.app"]:
        try:
            importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append({"module": module, "error": str(exc)})
    payload = json.dumps({"failures": failures}, ensure_ascii=False)
    return CiCommandResult(
        name="import_smoke",
        command=["python", "-c", "import platform modules"],
        returncode=1 if failures else 0,
        duration_seconds=0.0,
        started_at=started,
        finished_at=_utc_now(),
        stdout_tail=payload,
        stderr_tail="",
    )


def _markdown(payload: dict) -> str:
    lines = [
        "# Local CI Report",
        "",
        f"- mode: `{payload.get('mode')}`",
        f"- status: `{payload.get('status')}`",
        "",
        "| command | status | duration | returncode |",
        "| --- | --- | ---: | ---: |",
    ]
    for result in payload.get("commands", []):
        lines.append(
            f"| {result.get('name')} | {result.get('success')} | {float(result.get('duration_seconds', 0.0)):.3f} | {result.get('returncode')} |"
        )
    return "\n".join(lines) + "\n"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    raise SystemExit(main())
