"""Report writers for live readiness."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import LiveReadinessDecision, LiveReadinessPolicy, LiveReadinessScorecard


def write_live_readiness_artifacts(
    policy: LiveReadinessPolicy,
    scorecard: LiveReadinessScorecard,
    decision: LiveReadinessDecision,
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    policy_path = write_json_artifact(root / "live_readiness_policy.json", policy.to_dict(), "live_readiness_policy", "live_readiness")
    scorecard_path = write_json_artifact(root / "live_readiness_scorecard.json", scorecard.to_dict(), "live_readiness_scorecard", "live_readiness")
    decision_path = write_json_artifact(root / "live_readiness_decision.json", decision.to_dict(), "live_readiness_decision", "live_readiness")
    checks_path = write_jsonl_artifact(root / "live_readiness_checks.jsonl", [item.to_dict() for item in scorecard.checks], "live_readiness_checks", "live_readiness")
    package = {"status": decision.status, "passed": decision.passed, "score": decision.score, "paths": {}}
    package_path = write_json_artifact(root / "live_readiness_package.json", package, "live_readiness_package", "live_readiness")
    md_path = root / "live_readiness_report.md"
    md_path.write_text(_render_markdown(decision.to_dict()), encoding="utf-8")
    return {
        "live_readiness_policy_path": str(policy_path),
        "live_readiness_scorecard_path": str(scorecard_path),
        "live_readiness_decision_path": str(decision_path),
        "live_readiness_checks_path": str(checks_path),
        "live_readiness_package_path": str(package_path),
        "live_readiness_report_md_path": str(md_path),
    }


def _render_markdown(payload: dict) -> str:
    return "\n".join(
        [
            "# Live Readiness Decision",
            "",
            f"- status: `{payload.get('status')}`",
            f"- passed: `{payload.get('passed')}`",
            f"- new_status: `{payload.get('new_status')}`",
            f"- score: `{payload.get('score')}`",
            "",
            "## Required Remediation",
            "",
            "```json",
            json.dumps(payload.get("required_remediation", []), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    ) + "\n"
