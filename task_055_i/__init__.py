"""Task 055-I single-canary network authority and native response application."""

from .authority import publish_task055i_authority, validate_runtime_authority
from .executor import verify_and_accept_canary


def run_task055i(*args, **kwargs):
    from .run import run_task055i as implementation

    return implementation(*args, **kwargs)


def verify_task055i_report(*args, **kwargs):
    from .run import verify_task055i_report as implementation

    return implementation(*args, **kwargs)

__all__ = [
    "publish_task055i_authority",
    "run_task055i",
    "validate_runtime_authority",
    "verify_and_accept_canary",
    "verify_task055i_report",
]
