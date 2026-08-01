"""Run or plan factor certification campaigns."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
from typing import Any

from factor_certification.run_certify import main as run_factor_certify_main

from .registry import LocalFactorCertificationCampaignStore


def run_factor_certification_campaign(
    store_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    max_items: int | None = None,
    resume: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    store = LocalFactorCertificationCampaignStore(store_dir)
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
            item = {**item, "status": "planned", "output_dir": str(output_root / str(item.get("item_id")))}
            updated.append(item)
            continue
        result = _run_item(item, output_root)
        updated.append(result)
        if result.get("status") == "success":
            success += 1
        else:
            failed += 1
    store.write_items(updated)
    status = "planned" if dry_run else ("partial" if failed else "success")
    return {
        "status": status,
        "item_count": len(items),
        "success_count": success,
        "failed_count": failed,
        "skipped_count": skipped,
        "paths": store.paths(),
    }


def _run_item(item: dict[str, Any], output_root: Path) -> dict[str, Any]:
    queue_item = (item.get("metadata") or {}).get("queue_item", {}) if isinstance(item.get("metadata"), dict) else {}
    metadata = queue_item.get("metadata", {}) if isinstance(queue_item.get("metadata"), dict) else {}
    artifacts = metadata.get("validation_artifacts", {}) if isinstance(metadata.get("validation_artifacts"), dict) else {}
    output_dir = output_root / str(item.get("item_id"))
    argv = [
        "run",
        "--factor-store-dir",
        str(queue_item.get("factor_store_dir") or item.get("factor_store_dir") or ""),
        "--factor-id",
        str(item.get("factor_id")),
        "--output-dir",
        str(output_dir),
        "--policy-profile",
        str(item.get("certification_policy_profile") or queue_item.get("certification_policy_profile") or "sample_lenient_certification"),
    ]
    for attr in [
        "validation_lab_report_path",
        "multiple_testing_report_path",
        "overfit_risk_report_path",
        "placebo_test_report_path",
        "regime_validation_report_path",
        "sensitivity_report_path",
        "stress_backtest_report_path",
        "factor_validation_summary_path",
    ]:
        value = artifacts.get(attr)
        if value:
            argv.extend([f"--{attr.replace('_', '-')}", str(value)])
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exit_code = run_factor_certify_main(argv)
        if exit_code != 0:
            raise RuntimeError(buffer.getvalue().strip() or f"factor certification exit code {exit_code}")
        payload = json.loads(buffer.getvalue() or "{}")
        paths = payload.get("paths", {}) if isinstance(payload.get("paths"), dict) else {}
        return {
            **item,
            "status": "success",
            "output_dir": str(output_dir),
            "decision_path": paths.get("factor_certification_decision_path"),
            "scorecard_path": paths.get("factor_certification_scorecard_path"),
            "package_path": paths.get("factor_certification_package_path"),
            "error": None,
            "metadata": {**(item.get("metadata") or {}), "certification_result": payload},
        }
    except Exception as exc:
        return {**item, "status": "failed", "output_dir": str(output_dir), "error": str(exc)}
