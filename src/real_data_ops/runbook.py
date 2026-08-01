"""Runbook generation for long real-data backfill jobs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .models import RealDataProfile, RealDataRunbook


def build_runbook(
    *,
    profile: RealDataProfile,
    data_dir: str,
    output_dir: str,
    staging_dir: str | None,
    plan_path: str | None,
    state_path: str | None,
    completed_jobs: int,
    failed_jobs: int,
    quarantined_jobs: int,
    estimated_requests: int,
    request_budget_used: int,
    token_expiry: str | None,
    resume_command: list[str],
) -> RealDataRunbook:
    expiry_hours, risk = _expiry_status(token_expiry)
    estimated_runtime = estimated_requests / max(float(profile.rate_limit_per_minute), 1.0)
    return RealDataRunbook(
        profile_name=profile.profile_name,
        plan_path=plan_path,
        state_path=state_path,
        staging_dir=staging_dir,
        data_dir=data_dir,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        quarantined_jobs=quarantined_jobs,
        estimated_requests=estimated_requests,
        request_budget_used=request_budget_used,
        rate_limit_per_minute=profile.rate_limit_per_minute,
        estimated_min_runtime_minutes=float(estimated_runtime),
        token_expiry=token_expiry,
        time_to_expiry_hours=expiry_hours,
        expiry_risk=risk,
        resume_command=resume_command,
    )


def write_runbook(runbook: RealDataRunbook, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(root / "real_data_runbook.json", runbook.to_dict(), "real_data_runbook", "real_data_ops")
    md_path = root / "real_data_runbook.md"
    payload = runbook.to_dict()
    lines = [
        "# Real Data Runbook",
        "",
        f"- profile: `{payload['profile_name']}`",
        f"- data_dir: `{payload['data_dir']}`",
        f"- completed_jobs: `{payload['completed_jobs']}`",
        f"- failed_jobs: `{payload['failed_jobs']}`",
        f"- estimated_requests: `{payload['estimated_requests']}`",
        f"- rate_limit_per_minute: `{payload['rate_limit_per_minute']}`",
        f"- estimated_min_runtime_minutes: `{payload['estimated_min_runtime_minutes']:.2f}`",
        f"- token_expiry: `{payload.get('token_expiry') or ''}`",
        f"- expiry_risk: `{payload['expiry_risk']}`",
        "",
        "## Resume Command",
        "",
        "```bash",
        " ".join(payload["resume_command"]),
        "```",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _expiry_status(token_expiry: str | None) -> tuple[float | None, str]:
    if not token_expiry:
        return None, "unknown"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y%m%d"):
        try:
            expiry = datetime.strptime(token_expiry, fmt)
            hours = (expiry - datetime.now()).total_seconds() / 3600.0
            if hours < 0:
                return float(hours), "expired"
            if hours < 24:
                return float(hours), "high"
            if hours < 72:
                return float(hours), "medium"
            return float(hours), "low"
        except ValueError:
            continue
    return None, "unparseable"
