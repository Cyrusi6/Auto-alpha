"""Replay plan construction."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .models import ProductionReplayConfig, ProductionReplayPlan


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def make_replay_id(replay_name: str, start_date: str, end_date: str, replay_mode: str) -> str:
    digest = hashlib.sha256(f"{replay_name}|{start_date}|{end_date}|{replay_mode}".encode("utf-8")).hexdigest()[:12]
    return f"replay_{start_date}_{end_date}_{digest}"


def build_replay_plan(config: ProductionReplayConfig) -> ProductionReplayPlan:
    return ProductionReplayPlan(
        replay_id=config.replay_id,
        replay_name=config.replay_name,
        replay_mode=config.replay_mode,
        created_at=utc_now(),
        start_date=config.start_date,
        end_date=config.end_date,
        trade_dates=list(config.trade_dates),
        day_count=len(config.trade_dates),
        config=config.to_dict(),
    )
