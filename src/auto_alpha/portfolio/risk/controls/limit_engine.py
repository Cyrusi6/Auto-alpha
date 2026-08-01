"""Risk limit evaluation for local orders."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from .exposure import normalize_order, order_exposure
from .models import (
    KillSwitchState,
    RiskBreachAction,
    RiskControlBreach,
    RiskControlDecision,
    RiskControlPolicy,
    RiskControlReport,
    RiskControlSeverity,
    RiskControlStatus,
    RiskLimitDefinition,
    RiskLimitUsageSnapshot,
)


class RiskControlLimitEngine:
    def __init__(
        self,
        policy: RiskControlPolicy,
        *,
        allow_clipping: bool = False,
        available_cash: float | None = None,
        available_shares: dict[str, float] | None = None,
        kill_switch: KillSwitchState | None = None,
        batch_id: str = "",
        scope: str = "order",
    ):
        self.policy = policy
        self.allow_clipping = bool(allow_clipping)
        self.available_cash = available_cash
        self.available_shares = available_shares or {}
        self.kill_switch = kill_switch or KillSwitchState(active=False)
        self.batch_id = batch_id
        self.scope = scope

    def evaluate(self, orders: list[Any], trade_date: str | None = None) -> RiskControlReport:
        normalized = [normalize_order(order, idx) for idx, order in enumerate(orders)]
        trade_date = trade_date or _first_trade_date(normalized)
        created_at = _utc_now()
        breaches: list[RiskControlBreach] = []
        decisions: list[RiskControlDecision] = []
        usage: list[RiskLimitUsageSnapshot] = []
        if self.kill_switch.active:
            for order in normalized:
                breach = self._breach(
                    "kill_switch_active",
                    "kill_switch",
                    True,
                    True,
                    RiskControlSeverity.blocker,
                    RiskBreachAction.block,
                    "blocked",
                    "risk kill switch is active",
                    order,
                )
                breaches.append(breach)
                decisions.append(self._decision(order, RiskControlStatus.blocked, RiskBreachAction.block, [breach], order))
        elif self._has_block_all_policy():
            for order in normalized:
                breach = self._breach(
                    "emergency_block_all",
                    "block_all",
                    True,
                    True,
                    RiskControlSeverity.blocker,
                    RiskBreachAction.block,
                    "blocked",
                    "emergency risk policy blocks all orders",
                    order,
                )
                breaches.append(breach)
                decisions.append(self._decision(order, RiskControlStatus.blocked, RiskBreachAction.block, [breach], order))
        else:
            portfolio = order_exposure(normalized)
            usage.extend(self._portfolio_usage(portfolio, trade_date))
            portfolio_breaches = self._portfolio_breaches(portfolio)
            for order in normalized:
                order_breaches = self._order_breaches(order)
                related = [*portfolio_breaches, *order_breaches]
                breaches.extend(order_breaches)
                decisions.append(self._decision_for_order(order, related))
            breaches.extend(portfolio_breaches)
        counts = _count_decisions(decisions)
        warning_count = sum(1 for breach in breaches if breach.severity == RiskControlSeverity.warning)
        error_count = sum(1 for breach in breaches if breach.severity == RiskControlSeverity.error)
        blocker_count = sum(1 for breach in breaches if breach.severity == RiskControlSeverity.blocker)
        status = RiskControlStatus.passed
        if counts["rejected"] or blocker_count:
            status = RiskControlStatus.blocked if blocker_count else RiskControlStatus.rejected
        elif counts["clipped"]:
            status = RiskControlStatus.clipped
        elif warning_count:
            status = RiskControlStatus.warning
        report_id = f"rcr_{_safe_hash(self.policy.policy_id, trade_date or '', self.batch_id or '', created_at)}"
        return RiskControlReport(
            report_id=report_id,
            created_at=created_at,
            policy_id=self.policy.policy_id,
            profile=self.policy.profile,
            trade_date=trade_date or "",
            batch_id=self.batch_id,
            scope=self.scope,
            status=status,
            accepted_orders=counts["accepted"],
            rejected_orders=counts["rejected"],
            clipped_orders=counts["clipped"],
            warning_count=warning_count,
            error_count=error_count,
            blocker_count=blocker_count,
            breaches=breaches,
            decisions=decisions,
            usage=usage,
            kill_switch=self.kill_switch.to_dict(),
            summary={
                **order_exposure(normalized),
                "decision_count": len(decisions),
                "breach_count": len(breaches),
                "allow_clipping": self.allow_clipping,
            },
        )

    def _has_block_all_policy(self) -> bool:
        return any(limit.enabled and limit.metric == "block_all" and bool(limit.threshold) for limit in self.policy.limits)

    def _portfolio_usage(self, portfolio: dict[str, float], trade_date: str) -> list[RiskLimitUsageSnapshot]:
        records = []
        for limit in self.policy.limits:
            if not limit.enabled or limit.metric not in portfolio:
                continue
            value = float(portfolio.get(limit.metric, 0.0) or 0.0)
            records.append(
                RiskLimitUsageSnapshot(
                    usage_id=f"rlu_{_safe_hash(self.batch_id, limit.limit_id, trade_date, str(value))}",
                    created_at=_utc_now(),
                    trade_date=trade_date,
                    scope=limit.scope,
                    batch_id=self.batch_id,
                    metric=limit.metric,
                    value=value,
                    threshold=limit.threshold,
                    status="breached" if _over(value, limit.threshold) else "ok",
                    limit_id=limit.limit_id,
                )
            )
        return records

    def _portfolio_breaches(self, portfolio: dict[str, float]) -> list[RiskControlBreach]:
        breaches = []
        for limit in self.policy.limits:
            if not limit.enabled or limit.metric not in portfolio:
                continue
            value = float(portfolio.get(limit.metric, 0.0) or 0.0)
            if _over(value, limit.threshold):
                breaches.append(
                    self._breach(
                        limit.limit_id,
                        limit.metric,
                        value,
                        limit.threshold,
                        limit.severity,
                        limit.action,
                        "breached",
                        f"{limit.name} exceeded: {value:.2f} > {limit.threshold}",
                    )
                )
        if self.available_cash is not None and portfolio.get("gross_buy_value", 0.0) > self.available_cash + 1e-9:
            breaches.append(
                self._breach(
                    "available_cash",
                    "gross_buy_value",
                    portfolio["gross_buy_value"],
                    self.available_cash,
                    RiskControlSeverity.error,
                    RiskBreachAction.reject,
                    "breached",
                    "gross buy value exceeds available cash",
                )
            )
        return breaches

    def _order_breaches(self, order: dict[str, Any]) -> list[RiskControlBreach]:
        breaches = []
        for limit in self.policy.limits:
            if not limit.enabled:
                continue
            if limit.metric == "restricted_symbol" and order.get("ts_code") in set(self.policy.restricted_symbols):
                breaches.append(
                    self._breach(
                        limit.limit_id,
                        limit.metric,
                        True,
                        True,
                        limit.severity,
                        limit.action,
                        "breached",
                        f"symbol is restricted: {order.get('ts_code')}",
                        order,
                    )
                )
            elif limit.metric in {"order_value", "shares", "target_weight"}:
                value = abs(float(order.get(limit.metric, 0.0) or 0.0))
                if _over(value, limit.threshold):
                    breaches.append(
                        self._breach(
                            limit.limit_id,
                            limit.metric,
                            value,
                            limit.threshold,
                            limit.severity,
                            limit.action,
                            "breached",
                            f"{limit.name} exceeded: {value:.2f} > {limit.threshold}",
                            order,
                        )
                    )
        if order.get("side") == "SELL" and self.available_shares:
            available = float(self.available_shares.get(str(order.get("ts_code")), 0.0) or 0.0)
            shares = float(order.get("shares", 0.0) or 0.0)
            if shares > available + 1e-9:
                breaches.append(
                    self._breach(
                        "available_shares",
                        "shares",
                        shares,
                        available,
                        RiskControlSeverity.error,
                        RiskBreachAction.reject,
                        "breached",
                        "sell shares exceed available shares",
                        order,
                    )
                )
        return breaches

    def _decision_for_order(self, order: dict[str, Any], breaches: list[RiskControlBreach]) -> RiskControlDecision:
        relevant = [breach for breach in breaches if breach.order_id in {None, order.get("order_id")}]
        if any(breach.action == RiskBreachAction.block or breach.severity == RiskControlSeverity.blocker for breach in relevant):
            return self._decision(order, RiskControlStatus.blocked, RiskBreachAction.block, relevant, order)
        if any(breach.action == RiskBreachAction.reject for breach in relevant):
            return self._decision(order, RiskControlStatus.rejected, RiskBreachAction.reject, relevant, order)
        clip_breaches = [breach for breach in relevant if breach.action == RiskBreachAction.clip]
        if clip_breaches and self.allow_clipping:
            clipped = self._clip_order(order, clip_breaches)
            return self._decision(order, RiskControlStatus.clipped, RiskBreachAction.clip, relevant, clipped)
        if any(breach.action == RiskBreachAction.require_approval for breach in relevant):
            return self._decision(order, RiskControlStatus.override_required, RiskBreachAction.require_approval, relevant, order)
        return self._decision(order, RiskControlStatus.passed, RiskBreachAction.allow, relevant, order)

    def _clip_order(self, order: dict[str, Any], breaches: list[RiskControlBreach]) -> dict[str, Any]:
        clipped = dict(order)
        for breach in breaches:
            if breach.metric == "order_value":
                threshold = float(breach.threshold or 0.0)
                clipped["order_value"] = min(float(clipped.get("order_value", 0.0) or 0.0), threshold)
            elif breach.metric == "shares":
                threshold = int(float(breach.threshold or 0.0))
                clipped["shares"] = min(int(clipped.get("shares", 0) or 0), threshold)
        return clipped

    def _breach(
        self,
        limit_id: str,
        metric: str,
        value: Any,
        threshold: Any,
        severity: str,
        action: str,
        status: str,
        message: str,
        order: dict[str, Any] | None = None,
    ) -> RiskControlBreach:
        order_id = str(order.get("order_id")) if order else None
        ts_code = str(order.get("ts_code")) if order else None
        return RiskControlBreach(
            breach_id=f"rcb_{_safe_hash(self.batch_id, limit_id, metric, str(value), order_id or '')}",
            created_at=_utc_now(),
            limit_id=limit_id,
            scope=self.scope,
            metric=metric,
            value=value,
            threshold=threshold,
            severity=severity,
            action=action,
            status=status,
            message=message,
            order_id=order_id,
            ts_code=ts_code,
        )

    def _decision(
        self,
        order: dict[str, Any],
        status: str,
        action: str,
        breaches: list[RiskControlBreach],
        final_order: dict[str, Any],
    ) -> RiskControlDecision:
        return RiskControlDecision(
            decision_id=f"rcd_{_safe_hash(self.batch_id, str(order.get('order_id')), status)}",
            created_at=_utc_now(),
            order_id=str(order.get("order_id") or ""),
            trade_date=str(order.get("trade_date") or ""),
            ts_code=str(order.get("ts_code") or ""),
            side=str(order.get("side") or ""),
            status=status,
            action=action,
            original_order_value=float(order.get("order_value", 0.0) or 0.0),
            final_order_value=float(final_order.get("order_value", 0.0) or 0.0),
            original_shares=int(order.get("shares", 0) or 0),
            final_shares=int(final_order.get("shares", 0) or 0),
            breach_ids=[breach.breach_id for breach in breaches],
            reasons=[breach.message for breach in breaches],
            metadata={"final_order": final_order},
        )


def _count_decisions(decisions: list[RiskControlDecision]) -> dict[str, int]:
    return {
        "accepted": sum(1 for decision in decisions if decision.status in {RiskControlStatus.passed, RiskControlStatus.warning}),
        "rejected": sum(1 for decision in decisions if decision.status in {RiskControlStatus.rejected, RiskControlStatus.blocked, RiskControlStatus.override_required}),
        "clipped": sum(1 for decision in decisions if decision.status == RiskControlStatus.clipped),
    }


def _over(value: float, threshold: Any) -> bool:
    try:
        return float(value) > float(threshold)
    except (TypeError, ValueError):
        return False


def _first_trade_date(orders: list[dict[str, Any]]) -> str:
    for order in orders:
        if order.get("trade_date"):
            return str(order["trade_date"])
    return ""


def _safe_hash(*items: str) -> str:
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()[:20]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
