"""Go-live gate artifact writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import GoLiveGateDecision, GoLiveGatePolicy, GoLiveGateScorecard, GoLiveReviewPackage


def write_go_live_artifacts(
    *,
    output_dir: str | Path,
    policy: GoLiveGatePolicy,
    scorecard: GoLiveGateScorecard,
    decision: GoLiveGateDecision,
    review_package: GoLiveReviewPackage | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "go_live_gate_policy_path": root / "go_live_gate_policy.json",
        "go_live_gate_scorecard_path": root / "go_live_gate_scorecard.json",
        "go_live_gate_decision_path": root / "go_live_gate_decision.json",
        "go_live_gate_report_md_path": root / "go_live_gate_report.md",
        "go_live_gate_checks_path": root / "go_live_gate_checks.jsonl",
        "go_live_review_package_path": root / "go_live_review_package.json",
        "go_live_review_package_md_path": root / "go_live_review_package.md",
    }
    write_json_artifact(paths["go_live_gate_policy_path"], policy.to_dict(), "go_live_gate_policy", "go_live_gate")
    write_json_artifact(paths["go_live_gate_scorecard_path"], scorecard.to_dict(), "go_live_gate_scorecard", "go_live_gate")
    write_json_artifact(paths["go_live_gate_decision_path"], decision.to_dict(), "go_live_gate_decision", "go_live_gate")
    write_jsonl_artifact(paths["go_live_gate_checks_path"], [check.to_dict() for check in scorecard.checks], "go_live_gate_checks", "go_live_gate")
    review = review_package or GoLiveReviewPackage(
        review_id=f"go_live_review_{_utc_id()}",
        created_at=_utc_now(),
        go_live_gate_decision_path=str(paths["go_live_gate_decision_path"]),
        go_live_status=decision.status,
        status="pending",
        reviewer=None,
        comment=None,
        summary={"required_remediation_count": len(decision.required_remediation), "score": decision.score},
    )
    write_json_artifact(paths["go_live_review_package_path"], review.to_dict(), "go_live_review_package", "go_live_gate")
    paths["go_live_gate_report_md_path"].write_text(_render_report(decision.to_dict()), encoding="utf-8")
    paths["go_live_review_package_md_path"].write_text(_render_review(review.to_dict()), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Go/No-Go Gate Report",
        "",
        f"- status: `{payload.get('status')}`",
        f"- passed: `{payload.get('passed')}`",
        f"- score: `{payload.get('score')}`",
        f"- required_remediation_count: `{len(payload.get('required_remediation', []))}`",
        "",
        "> This is a local pre-live review gate. It is not a trading permission, broker authorization, regulatory filing, or legal opinion.",
        "",
        "| check | status | severity | reason |",
        "| --- | --- | --- | --- |",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| {check.get('check_id')} | {check.get('status')} | {check.get('severity')} | {check.get('reason')} |")
    return "\n".join(lines) + "\n"


def _render_review(payload: dict[str, Any]) -> str:
    return "\n".join(["# Go-Live Review Package", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")
