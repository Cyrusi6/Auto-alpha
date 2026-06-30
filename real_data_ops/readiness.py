"""Readiness reports for gated real-data jobs."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from artifact_schema.writer import write_json_artifact
from data_backfill.planner import build_backfill_plan
from data_pipeline.ashare.config import AShareDataConfig

from .env_file import redacted_token_metadata
from .models import RealDataProfile, RealDataReadinessReport


def build_readiness_report(config: AShareDataConfig, profile: RealDataProfile) -> RealDataReadinessReport:
    plan = build_backfill_plan(
        config,
        datasets=profile.datasets,
        chunk_days=_plan_chunk_days(profile),
        chunk_strategy=profile.chunk_strategy,
        dataset_chunk_days=profile.dataset_chunk_days,
        max_requests=profile.max_requests,
    )
    diagnostics: list[dict] = []
    token = redacted_token_metadata(config.tushare_token)
    status = "ok"
    if profile.provider == "tushare" and not profile.allow_network:
        status = "blocked"
        diagnostics.append({"code": "network_disabled", "severity": "warning", "message": "--allow-network is required for real Tushare"})
    if profile.provider == "tushare" and profile.require_token and not config.tushare_token:
        status = "blocked"
        diagnostics.append({"code": "missing_token", "severity": "error", "message": "TUSHARE_TOKEN is required"})
    runtime = plan.estimated_request_count / max(float(profile.rate_limit_per_minute), 1.0)
    return RealDataReadinessReport(
        status=status,
        provider=profile.provider,
        profile_name=profile.profile_name,
        allow_network=profile.allow_network,
        require_token=profile.require_token,
        token=token,
        api_url_host=urlparse(config.tushare_api_url).netloc,
        estimated_requests=plan.estimated_request_count,
        estimated_min_runtime_minutes=float(runtime),
        diagnostics=diagnostics,
    )


def write_readiness_report(report: RealDataReadinessReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(root / "real_data_readiness_report.json", report.to_dict(), "real_data_readiness_report", "real_data_ops")
    md_path = root / "real_data_readiness_report.md"
    payload = report.to_dict()
    lines = [
        "# Real Data Readiness",
        "",
        f"- status: `{payload['status']}`",
        f"- provider: `{payload['provider']}`",
        f"- profile: `{payload['profile_name']}`",
        f"- api_url_host: `{payload['api_url_host']}`",
        f"- token_present: `{payload['token']['token_present']}`",
        f"- estimated_requests: `{payload['estimated_requests']}`",
        f"- estimated_min_runtime_minutes: `{payload['estimated_min_runtime_minutes']:.2f}`",
        "",
        "## Diagnostics",
    ]
    for item in payload.get("diagnostics", []):
        lines.append(f"- `{item.get('severity')}` `{item.get('code')}`: {item.get('message')}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _plan_chunk_days(profile: RealDataProfile) -> int:
    if profile.chunk_strategy == "production_daily":
        return max(1, min(profile.dataset_chunk_days.values() or [1]))
    return max(1, min(profile.dataset_chunk_days.values() or [30]))
