"""Serializable A-share signal, decision, execution, and PnL clock."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BacktestTimeContract:
    contract_id: str = "next_trade_day_open_v1"
    signal_time: str = "close_t"
    decision_time: str = "after_close_t"
    order_time: str = "before_open_t_plus_1"
    execution_time: str = "open_t_plus_1"
    price_field: str = "open"
    pnl_interval: str = "open_to_open"
    signal_lag_days: int = 1

    def validate(self) -> None:
        if self.signal_lag_days < 1:
            raise ValueError("next_trade_day_open requires signal_lag_days >= 1")
        if self.price_field != "open" or self.pnl_interval != "open_to_open":
            raise ValueError("formal daily next-open contract requires open fills and open-to-open PnL")

    def to_dict(self):
        self.validate()
        return asdict(self)


def normalize_execution_mode(value: str) -> tuple[str, list[str]]:
    mode = str(value)
    if mode == "next_trade_day_open":
        return mode, []
    if mode == "next_open":
        return "next_trade_day_open", ["next_open is a compatibility alias for next_trade_day_open"]
    raise ValueError(f"unsupported execution timing mode: {value}")
