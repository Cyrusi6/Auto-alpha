"""Lifecycle policy loading helpers."""

from __future__ import annotations

import json
from pathlib import Path

from .models import LifecyclePolicy


def load_lifecycle_policy(path: str | Path | None = None) -> LifecyclePolicy:
    if not path:
        return LifecyclePolicy()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return LifecyclePolicy(**payload)
