"""Task 055-A prospective-holdout and event-ledger simulation baseline."""

__all__ = ["BLOCKED_STATUS", "SUCCESS_STATUS", "run_task055a"]


def __getattr__(name):
    if name in __all__:
        from .run import BLOCKED_STATUS, SUCCESS_STATUS, run_task055a

        return {"BLOCKED_STATUS": BLOCKED_STATUS, "SUCCESS_STATUS": SUCCESS_STATUS, "run_task055a": run_task055a}[name]
    raise AttributeError(name)
