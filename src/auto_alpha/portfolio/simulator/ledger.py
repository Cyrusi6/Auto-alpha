"""Strict event-ledger simulation contracts."""

__all__ = ["BLOCKED_STATUS", "SUCCESS_STATUS", "run_task055a"]


def __getattr__(name):
    if name in __all__:
        from auto_alpha.portfolio.simulator.ledger_run import BLOCKED_STATUS
        from auto_alpha.portfolio.simulator.ledger_run import SUCCESS_STATUS
        from auto_alpha.portfolio.simulator.ledger_run import run_task055a

        return {"BLOCKED_STATUS": BLOCKED_STATUS, "SUCCESS_STATUS": SUCCESS_STATUS, "run_task055a": run_task055a}[name]
    raise AttributeError(name)
