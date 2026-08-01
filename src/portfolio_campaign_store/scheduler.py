"""Run or plan portfolio certification campaigns."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

from portfolio_certification.run_portfolio_certify import main as run_portfolio_certify_main
from portfolio_lab.run_portfolio_lab import main as run_portfolio_lab_main

from .registry import LocalPortfolioCampaignStore


def run_portfolio_campaign(
    store_dir: str | Path,
    *,
    data_dir: str | Path | None = None,
    factor_store_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    max_items: int | None = None,
    resume: bool = False,
    dry_run: bool = False,
    scenario_profile: str = "sample",
    portfolio_policy_profile: str = "sample_lenient_portfolio",
    index_code: str = "000300.SH",
    as_of_date: str = "20240104",
    max_trials: int = 1,
) -> dict[str, Any]:
    store = LocalPortfolioCampaignStore(store_dir)
    output_root = Path(output_dir or store.root_dir / "items")
    output_root.mkdir(parents=True, exist_ok=True)
    items = store.load_items()
    if max_items and max_items > 0:
        items = items[:max_items]
    updated = []
    success = failed = skipped = 0
    for item in items:
        if resume and item.get("status") == "success":
            skipped += 1
            updated.append(item)
            continue
        if dry_run:
            updated.append({**item, "status": "planned", "portfolio_lab_output_dir": str(output_root / str(item.get("item_id")) / "lab")})
            continue
        result = _run_item(
            item,
            output_root,
            data_dir=str(data_dir or ""),
            factor_store_dir=str(factor_store_dir or item.get("factor_store_dir") or ""),
            scenario_profile=scenario_profile,
            portfolio_policy_profile=portfolio_policy_profile,
            index_code=index_code,
            as_of_date=as_of_date,
            max_trials=max_trials,
        )
        updated.append(result)
        if result.get("status") == "success":
            success += 1
        else:
            failed += 1
    store.write_items(updated)
    return {
        "status": "planned" if dry_run else ("partial" if failed else "success"),
        "item_count": len(items),
        "success_count": success,
        "failed_count": failed,
        "skipped_count": skipped,
        "paths": store.paths(),
    }


def _run_item(
    item: dict[str, Any],
    output_root: Path,
    *,
    data_dir: str,
    factor_store_dir: str,
    scenario_profile: str,
    portfolio_policy_profile: str,
    index_code: str,
    as_of_date: str,
    max_trials: int,
) -> dict[str, Any]:
    item_root = output_root / str(item.get("item_id"))
    lab_dir = item_root / "portfolio_lab"
    cert_dir = item_root / "portfolio_certification"
    pool_record = (item.get("metadata") or {}).get("certified_factor_pool_record", {}) if isinstance(item.get("metadata"), dict) else {}
    factor_certifacts = pool_record.get("certification_artifacts", {}) if isinstance(pool_record.get("certification_artifacts"), dict) else {}
    validation_artifacts = pool_record.get("validation_artifacts", {}) if isinstance(pool_record.get("validation_artifacts"), dict) else {}
    try:
        lab_argv = [
            "run",
            "--data-dir",
            data_dir,
            "--factor-store-dir",
            factor_store_dir,
            "--output-dir",
            str(lab_dir),
            "--factor-id",
            str(item.get("factor_id")),
            "--factor-type",
            "any",
            "--index-code",
            index_code,
            "--as-of-date",
            as_of_date,
            "--scenario-profile",
            scenario_profile,
            "--max-trials",
            str(max_trials),
        ]
        with contextlib.redirect_stdout(io.StringIO()) as lab_stdout:
            lab_exit = run_portfolio_lab_main(lab_argv)
        if lab_exit != 0:
            raise RuntimeError(lab_stdout.getvalue().strip() or f"portfolio lab exit code {lab_exit}")
        lab_payload = json.loads(lab_stdout.getvalue() or "{}")
        paths = lab_payload.get("paths", {}) if isinstance(lab_payload.get("paths"), dict) else {}
        selected_policy = paths.get("selected_portfolio_policy_path") or str(lab_dir / "selected_portfolio_policy.json")
        cert_argv = [
            "run",
            "--factor-store-dir",
            factor_store_dir,
            "--factor-id",
            str(item.get("factor_id")),
            "--factor-type",
            "any",
            "--selected-portfolio-policy-path",
            str(selected_policy),
            "--portfolio-lab-report-path",
            str(paths.get("portfolio_lab_report_path") or lab_dir / "portfolio_lab_report.json"),
            "--portfolio-robustness-report-path",
            str(paths.get("portfolio_robustness_report_path") or lab_dir / "portfolio_robustness_report.json"),
            "--output-dir",
            str(cert_dir),
            "--policy-profile",
            portfolio_policy_profile,
        ]
        if factor_certifacts.get("decision_path"):
            cert_argv.extend(["--factor-certification-decision-path", str(factor_certifacts["decision_path"])])
        if validation_artifacts.get("validation_lab_report_path"):
            cert_argv.extend(["--validation-lab-report-path", str(validation_artifacts["validation_lab_report_path"])])
        with contextlib.redirect_stdout(io.StringIO()) as cert_stdout:
            cert_exit = run_portfolio_certify_main(cert_argv)
        if cert_exit != 0:
            raise RuntimeError(cert_stdout.getvalue().strip() or f"portfolio certification exit code {cert_exit}")
        cert_payload = json.loads(cert_stdout.getvalue() or "{}")
        cert_paths = cert_payload.get("paths", {}) if isinstance(cert_payload.get("paths"), dict) else {}
        return {
            **item,
            "status": "success",
            "portfolio_lab_output_dir": str(lab_dir),
            "portfolio_lab_report_path": paths.get("portfolio_lab_report_path"),
            "selected_portfolio_policy_path": selected_policy,
            "portfolio_certification_decision_path": cert_paths.get("portfolio_certification_decision_path"),
            "certified_portfolio_policy_path": cert_paths.get("certified_portfolio_policy_path"),
            "error": None,
            "metadata": {**(item.get("metadata") or {}), "portfolio_lab_result": lab_payload, "portfolio_certification_result": cert_payload},
        }
    except Exception as exc:
        return {**item, "status": "failed", "portfolio_lab_output_dir": str(lab_dir), "error": str(exc)}
