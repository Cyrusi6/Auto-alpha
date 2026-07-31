"""Prevent sealed-holdout evidence from becoming Alpha Factory input."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_PATH_FIELDS = {
    "data_dir",
    "output_dir",
    "factor_store_dir",
    "report_dir",
    "candidates_json",
    "universe_file",
}
_SENTINEL = "holdout_feedback_forbidden.json"


def assert_no_holdout_feedback_paths(config: Any) -> None:
    """Fail before output creation when any configured path descends from holdout evidence."""

    values = config.to_dict() if hasattr(config, "to_dict") else dict(config)
    for field, raw_value in values.items():
        if field not in _PATH_FIELDS and not field.endswith(("_path", "_dir", "_dirs")):
            continue
        candidates = raw_value if isinstance(raw_value, list) else [raw_value]
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            path = Path(candidate).expanduser().resolve(strict=False)
            for ancestor in (path, *path.parents):
                sentinel = ancestor / _SENTINEL
                if not sentinel.exists():
                    continue
                if sentinel.is_symlink() or not sentinel.is_file():
                    raise RuntimeError(f"sealed_holdout_feedback_sentinel_invalid:{field}")
                try:
                    payload = json.loads(sentinel.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f"sealed_holdout_feedback_sentinel_invalid:{field}") from exc
                if (
                    payload.get("artifact_type") != "holdout_feedback_firewall"
                    or payload.get("feedback_to_search_forbidden") is not True
                    or payload.get("search_agent_readable") is not False
                ):
                    raise RuntimeError(f"sealed_holdout_feedback_sentinel_invalid:{field}")
                raise RuntimeError(f"sealed_holdout_feedback_path_forbidden:{field}")
