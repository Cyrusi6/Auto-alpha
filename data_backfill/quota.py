"""Request budget and online guard helpers for backfill runs."""

from __future__ import annotations

import hashlib
from data_pipeline.ashare.config import AShareDataConfig

from .models import BackfillPlan, BackfillQuotaSummary


def evaluate_backfill_quota(
    plan: BackfillPlan,
    config: AShareDataConfig,
    allow_network: bool = False,
    require_token: bool = False,
    max_requests: int | None = None,
) -> BackfillQuotaSummary:
    token = config.tushare_token or ""
    token_present = bool(token)
    estimated = int(plan.estimated_request_count)
    status = "ok"
    reason: str | None = None
    if config.provider == "tushare" and not allow_network:
        status = "blocked"
        reason = "network_disabled"
    elif require_token and not token_present:
        status = "blocked"
        reason = "missing_token"
    elif max_requests is not None and estimated > max_requests:
        status = "blocked"
        reason = "max_requests_exceeded"
    return BackfillQuotaSummary(
        provider=config.provider,
        allow_network=bool(allow_network),
        token_present=token_present,
        token_hash_prefix=hashlib.sha256(token.encode("utf-8")).hexdigest()[:8] if token else None,
        token_suffix=token[-4:] if token else None,
        max_requests=max_requests,
        estimated_requests=estimated,
        status=status,
        reason=reason,
    )
