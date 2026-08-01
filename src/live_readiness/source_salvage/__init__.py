"""Task 055-E offline source salvage and causal valuation-domain audit."""

from .contracts import OFFLINE_BLOCKED_STATUS, OFFLINE_STAGE_STATUS


def run_offline_source_salvage(*args, **kwargs):
    from .run import run_offline_source_salvage as implementation

    return implementation(*args, **kwargs)

__all__ = [
    "OFFLINE_BLOCKED_STATUS",
    "OFFLINE_STAGE_STATUS",
    "run_offline_source_salvage",
]
