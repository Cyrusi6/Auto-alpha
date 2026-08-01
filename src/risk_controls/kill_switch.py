"""Kill switch helpers."""

from __future__ import annotations

from pathlib import Path

from .models import KillSwitchState
from .state import LocalRiskControlState


def activate_kill_switch(state_dir: str | Path, reason: str, actor: str = "local_user") -> KillSwitchState:
    return LocalRiskControlState(state_dir).activate_kill_switch(reason=reason, actor=actor)


def deactivate_kill_switch(state_dir: str | Path, reason: str, actor: str = "local_user", approval_id: str | None = None) -> KillSwitchState:
    return LocalRiskControlState(state_dir).deactivate_kill_switch(reason=reason, actor=actor, approval_id=approval_id)


def load_kill_switch(state_dir: str | Path) -> KillSwitchState:
    return LocalRiskControlState(state_dir).load_kill_switch()
