"""Policy profiles for local A-share pre-trade controls."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import (
    RiskBreachAction,
    RiskControlPolicy,
    RiskControlPolicyManifest,
    RiskControlSeverity,
    RiskControlScope,
    RiskLimitDefinition,
)


DEFAULT_PROFILE = "cn_ashare_paper_default"


def default_policy(profile: str = DEFAULT_PROFILE) -> RiskControlPolicy:
    now = _utc_now()
    if profile == "emergency_block_all":
        limits = [
            RiskLimitDefinition(
                "emergency_block_all",
                "Block all new orders",
                RiskControlScope.kill_switch,
                "block_all",
                True,
                action=RiskBreachAction.block,
                severity=RiskControlSeverity.blocker,
            )
        ]
        return RiskControlPolicy(f"policy_{profile}", profile, now, limits, notes="Emergency policy blocks all orders.")
    if profile == "strict_paper_gate":
        limits = [
            RiskLimitDefinition("single_order_value", "Single order notional", RiskControlScope.order, "order_value", 1_000_000.0),
            RiskLimitDefinition("single_order_shares", "Single order shares", RiskControlScope.order, "shares", 100_000),
            RiskLimitDefinition("gross_buy_value", "Gross buy value", RiskControlScope.portfolio, "gross_buy_value", 5_000_000.0),
            RiskLimitDefinition("order_count", "Order count", RiskControlScope.portfolio, "order_count", 20),
            RiskLimitDefinition("restricted_symbol", "Restricted symbol", RiskControlScope.symbol, "restricted_symbol", True),
        ]
        return RiskControlPolicy(
            "policy_strict_paper_gate",
            profile,
            now,
            limits,
            restricted_symbols=["688999.SH"],
            notes="Strict local smoke policy for paper gate validation.",
        )
    limits = [
        RiskLimitDefinition(
            "single_order_value",
            "Single order notional",
            RiskControlScope.order,
            "order_value",
            50_000_000.0,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "single_order_shares",
            "Single order shares",
            RiskControlScope.order,
            "shares",
            5_000_000,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "gross_buy_value",
            "Gross buy value",
            RiskControlScope.portfolio,
            "gross_buy_value",
            100_000_000.0,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "order_count",
            "Order count",
            RiskControlScope.portfolio,
            "order_count",
            500,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "restricted_symbol",
            "Restricted symbol",
            RiskControlScope.symbol,
            "restricted_symbol",
            True,
            action=RiskBreachAction.reject,
            severity=RiskControlSeverity.error,
        ),
    ]
    return RiskControlPolicy(
        "policy_cn_ashare_paper_default",
        profile,
        now,
        limits,
        restricted_symbols=[],
        notes="Default paper trading limits for local A-share research operations.",
    )


def load_policy(path: str | Path | None = None, profile: str = DEFAULT_PROFILE) -> RiskControlPolicy:
    if path is None:
        return default_policy(profile)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("artifact_type"):
        payload = {key: value for key, value in payload.items() if key not in {"artifact_type", "schema_version", "producer", "artifact_metadata"}}
    limits = [RiskLimitDefinition(**item) for item in payload.get("limits", [])]
    return RiskControlPolicy(
        policy_id=str(payload.get("policy_id") or f"policy_{profile}"),
        profile=str(payload.get("profile") or profile),
        created_at=str(payload.get("created_at") or _utc_now()),
        limits=limits,
        restricted_symbols=[str(item) for item in payload.get("restricted_symbols", [])],
        notes=payload.get("notes"),
        metadata=dict(payload.get("metadata") or {}),
    )


def write_policy(policy: RiskControlPolicy, path: str | Path) -> Path:
    return write_json_artifact(path, policy.to_dict(), artifact_type="risk_control_policy_manifest", producer="risk_controls")


def validate_policy(policy: RiskControlPolicy, policy_path: str | Path = "") -> RiskControlPolicyManifest:
    issues: list[dict[str, Any]] = []
    seen = set()
    for limit in policy.limits:
        if not limit.limit_id:
            issues.append({"severity": "error", "message": "limit_id is required"})
        if limit.limit_id in seen:
            issues.append({"severity": "error", "message": f"duplicate limit_id: {limit.limit_id}"})
        seen.add(limit.limit_id)
        if limit.action not in {"allow", "warn", "clip", "reject", "block", "require_approval"}:
            issues.append({"severity": "error", "message": f"unsupported action: {limit.action}"})
    return RiskControlPolicyManifest(
        policy_id=policy.policy_id,
        profile=policy.profile,
        created_at=_utc_now(),
        policy_path=str(policy_path),
        limit_count=len(policy.limits),
        restricted_symbol_count=len(policy.restricted_symbols),
        status="error" if any(item.get("severity") == "error" for item in issues) else "valid",
        issues=issues,
    )


def write_policy_manifest(policy: RiskControlPolicy, path: str | Path, policy_path: str | Path = "") -> Path:
    manifest = validate_policy(policy, policy_path=policy_path)
    return write_json_artifact(path, manifest.to_dict(), artifact_type="risk_control_policy_manifest", producer="risk_controls")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
