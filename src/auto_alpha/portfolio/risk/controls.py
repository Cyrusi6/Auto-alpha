"""Portfolio risk-control limits, state, order gates, overrides, and reporting."""

from __future__ import annotations

from typing import Any


def normalize_order(payload: Any, index: int = 0) -> dict[str, Any]:
    if hasattr(payload, "to_dict"):
        row = dict(payload.to_dict())
    elif hasattr(payload, "__dataclass_fields__"):
        row = {field: getattr(payload, field) for field in payload.__dataclass_fields__}
    else:
        row = dict(payload)
    order_id = (
        row.get("order_id")
        or row.get("child_order_id")
        or row.get("parent_order_id")
        or row.get("client_order_id")
        or f"order_{index + 1}"
    )
    row["order_id"] = str(order_id)
    row["trade_date"] = str(row.get("trade_date") or "")
    row["ts_code"] = str(row.get("ts_code") or "")
    row["side"] = str(row.get("side") or "").upper()
    row["order_value"] = _float(row.get("order_value") or row.get("requested_value") or row.get("value"))
    row["shares"] = int(_float(row.get("shares") or row.get("requested_shares")))
    row["price"] = _float(row.get("price"))
    row["target_weight"] = _float(row.get("target_weight"))
    return row


def order_exposure(orders: list[dict[str, Any]]) -> dict[str, float]:
    gross = sum(abs(float(order.get("order_value", 0.0) or 0.0)) for order in orders)
    buys = sum(float(order.get("order_value", 0.0) or 0.0) for order in orders if str(order.get("side") or "").upper() == "BUY")
    sells = sum(float(order.get("order_value", 0.0) or 0.0) for order in orders if str(order.get("side") or "").upper() == "SELL")
    return {
        "order_count": float(len(orders)),
        "gross_order_value": float(gross),
        "gross_buy_value": float(max(buys, 0.0)),
        "gross_sell_value": float(max(sells, 0.0)),
        "net_order_value": float(buys - sells),
        "max_order_value": max((abs(float(order.get("order_value", 0.0) or 0.0)) for order in orders), default=0.0),
    }


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

from dataclasses import asdict, dataclass, field
from typing import Any


class RiskControlScope:
    order = "order"
    child_order = "child_order"
    broker_request = "broker_request"
    portfolio = "portfolio"
    account = "account"
    symbol = "symbol"
    kill_switch = "kill_switch"


class RiskControlSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class RiskBreachAction:
    allow = "allow"
    warn = "warn"
    clip = "clip"
    reject = "reject"
    block = "block"
    require_approval = "require_approval"


class RiskControlStatus:
    passed = "passed"
    warning = "warning"
    rejected = "rejected"
    clipped = "clipped"
    blocked = "blocked"
    override_required = "override_required"


@dataclass(frozen=True)
class RiskLimitDefinition:
    limit_id: str
    name: str
    scope: str
    metric: str
    threshold: float | str | bool | None
    action: str = RiskBreachAction.reject
    severity: str = RiskControlSeverity.error
    enabled: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlPolicy:
    policy_id: str
    profile: str
    created_at: str
    limits: list[RiskLimitDefinition]
    restricted_symbols: list[str] = field(default_factory=list)
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "limits": [limit.to_dict() for limit in self.limits],
        }


@dataclass(frozen=True)
class RiskControlPolicyManifest:
    policy_id: str
    profile: str
    created_at: str
    policy_path: str
    limit_count: int
    restricted_symbol_count: int
    status: str = "valid"
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskLimitUsageSnapshot:
    usage_id: str
    created_at: str
    trade_date: str
    scope: str
    batch_id: str
    metric: str
    value: float
    threshold: float | str | bool | None
    status: str
    limit_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlBreach:
    breach_id: str
    created_at: str
    limit_id: str
    scope: str
    metric: str
    value: float | str | bool | None
    threshold: float | str | bool | None
    severity: str
    action: str
    status: str
    message: str
    order_id: str | None = None
    ts_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlDecision:
    decision_id: str
    created_at: str
    order_id: str
    trade_date: str
    ts_code: str
    side: str
    status: str
    action: str
    original_order_value: float
    final_order_value: float
    original_shares: int
    final_shares: int
    breach_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KillSwitchState:
    active: bool
    activated_at: str | None = None
    activated_by: str | None = None
    reason: str = ""
    deactivated_at: str | None = None
    deactivated_by: str | None = None
    deactivation_reason: str = ""
    approval_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskOverrideRequest:
    override_id: str
    created_at: str
    scope: str
    reason: str
    requested_by: str
    expires_at: str | None = None
    max_usage_count: int | None = None
    approval_id: str | None = None
    status: str = "pending_approval"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskOverrideApprovalSummary:
    override_id: str
    approval_id: str
    status: str
    scope: str
    expires_at: str | None = None
    max_usage_count: int | None = None
    applied_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlAuditEvent:
    event_id: str
    created_at: str
    event_type: str
    status: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskControlReport:
    report_id: str
    created_at: str
    policy_id: str
    profile: str
    trade_date: str
    batch_id: str
    scope: str
    status: str
    accepted_orders: int
    rejected_orders: int
    clipped_orders: int
    warning_count: int
    error_count: int
    blocker_count: int
    breaches: list[RiskControlBreach] = field(default_factory=list)
    decisions: list[RiskControlDecision] = field(default_factory=list)
    usage: list[RiskLimitUsageSnapshot] = field(default_factory=list)
    kill_switch: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "breaches": [breach.to_dict() for breach in self.breaches],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "usage": [item.to_dict() for item in self.usage],
        }

import hashlib
from datetime import datetime
from typing import Any



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
        created_at = _controls_limit_engine_utc_now()
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
                    created_at=_controls_limit_engine_utc_now(),
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
            created_at=_controls_limit_engine_utc_now(),
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
            created_at=_controls_limit_engine_utc_now(),
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


def _controls_limit_engine_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



DEFAULT_PROFILE = "cn_ashare_paper_default"


def default_policy(profile: str = DEFAULT_PROFILE) -> RiskControlPolicy:
    now = _controls_policy_utc_now()
    if profile == "emergency_block_all":
        limits = [
            RiskLimitDefinition(
                "emergency_block_all",
                "Block all new orders",
                RiskControlScope.kill_switch,
                "block_all",
                True,
                action=RiskBreachAction.block,
                severity=RiskControlSeverity.blocker,
            )
        ]
        return RiskControlPolicy(f"policy_{profile}", profile, now, limits, notes="Emergency policy blocks all orders.")
    if profile == "strict_paper_gate":
        limits = [
            RiskLimitDefinition("single_order_value", "Single order notional", RiskControlScope.order, "order_value", 1_000_000.0),
            RiskLimitDefinition("single_order_shares", "Single order shares", RiskControlScope.order, "shares", 100_000),
            RiskLimitDefinition("gross_buy_value", "Gross buy value", RiskControlScope.portfolio, "gross_buy_value", 5_000_000.0),
            RiskLimitDefinition("order_count", "Order count", RiskControlScope.portfolio, "order_count", 20),
            RiskLimitDefinition("restricted_symbol", "Restricted symbol", RiskControlScope.symbol, "restricted_symbol", True),
        ]
        return RiskControlPolicy(
            "policy_strict_paper_gate",
            profile,
            now,
            limits,
            restricted_symbols=["688999.SH"],
            notes="Strict local smoke policy for paper gate validation.",
        )
    limits = [
        RiskLimitDefinition(
            "single_order_value",
            "Single order notional",
            RiskControlScope.order,
            "order_value",
            50_000_000.0,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "single_order_shares",
            "Single order shares",
            RiskControlScope.order,
            "shares",
            5_000_000,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "gross_buy_value",
            "Gross buy value",
            RiskControlScope.portfolio,
            "gross_buy_value",
            100_000_000.0,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "order_count",
            "Order count",
            RiskControlScope.portfolio,
            "order_count",
            500,
            action=RiskBreachAction.warn,
            severity=RiskControlSeverity.warning,
        ),
        RiskLimitDefinition(
            "restricted_symbol",
            "Restricted symbol",
            RiskControlScope.symbol,
            "restricted_symbol",
            True,
            action=RiskBreachAction.reject,
            severity=RiskControlSeverity.error,
        ),
    ]
    return RiskControlPolicy(
        "policy_cn_ashare_paper_default",
        profile,
        now,
        limits,
        restricted_symbols=[],
        notes="Default paper trading limits for local A-share research auto_alpha.execution.trading.daily.",
    )


def load_policy(path: str | Path | None = None, profile: str = DEFAULT_PROFILE) -> RiskControlPolicy:
    if path is None:
        return default_policy(profile)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("artifact_type"):
        payload = {key: value for key, value in payload.items() if key not in {"artifact_type", "schema_version", "producer", "artifact_metadata"}}
    limits = [RiskLimitDefinition(**item) for item in payload.get("limits", [])]
    return RiskControlPolicy(
        policy_id=str(payload.get("policy_id") or f"policy_{profile}"),
        profile=str(payload.get("profile") or profile),
        created_at=str(payload.get("created_at") or _controls_policy_utc_now()),
        limits=limits,
        restricted_symbols=[str(item) for item in payload.get("restricted_symbols", [])],
        notes=payload.get("notes"),
        metadata=dict(payload.get("metadata") or {}),
    )


def write_policy(policy: RiskControlPolicy, path: str | Path) -> Path:
    return write_json_artifact(path, policy.to_dict(), artifact_type="risk_control_policy_manifest", producer="risk_controls")


def validate_policy(policy: RiskControlPolicy, policy_path: str | Path = "") -> RiskControlPolicyManifest:
    issues: list[dict[str, Any]] = []
    seen = set()
    for limit in policy.limits:
        if not limit.limit_id:
            issues.append({"severity": "error", "message": "limit_id is required"})
        if limit.limit_id in seen:
            issues.append({"severity": "error", "message": f"duplicate limit_id: {limit.limit_id}"})
        seen.add(limit.limit_id)
        if limit.action not in {"allow", "warn", "clip", "reject", "block", "require_approval"}:
            issues.append({"severity": "error", "message": f"unsupported action: {limit.action}"})
    return RiskControlPolicyManifest(
        policy_id=policy.policy_id,
        profile=policy.profile,
        created_at=_controls_policy_utc_now(),
        policy_path=str(policy_path),
        limit_count=len(policy.limits),
        restricted_symbol_count=len(policy.restricted_symbols),
        status="error" if any(item.get("severity") == "error" for item in issues) else "valid",
        issues=issues,
    )


def write_policy_manifest(policy: RiskControlPolicy, path: str | Path, policy_path: str | Path = "") -> Path:
    manifest = validate_policy(policy, policy_path=policy_path)
    return write_json_artifact(path, manifest.to_dict(), artifact_type="risk_control_policy_manifest", producer="risk_controls")


def _controls_policy_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_risk_control_report(
    report: RiskControlReport,
    output_dir: str | Path,
    accepted_orders: list[dict[str, Any]] | None = None,
    rejected_orders: list[dict[str, Any]] | None = None,
    clipped_orders: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "risk_control_report_path": root / "risk_control_report.json",
        "risk_control_report_md_path": root / "risk_control_report.md",
        "risk_control_breaches_path": root / "risk_control_breaches.jsonl",
        "risk_limit_usage_path": root / "risk_limit_usage.jsonl",
        "risk_control_decisions_path": root / "risk_control_decisions.jsonl",
        "accepted_orders_path": root / "accepted_orders.jsonl",
        "rejected_orders_path": root / "rejected_orders.jsonl",
        "clipped_orders_path": root / "clipped_orders.jsonl",
        "kill_switch_state_path": root / "kill_switch_state.json",
    }
    payload = report.to_dict()
    payload["paths"] = {key: str(path) for key, path in paths.items()}
    write_json_artifact(paths["risk_control_report_path"], payload, artifact_type="risk_control_report", producer="risk_controls")
    write_jsonl_artifact(paths["risk_control_breaches_path"], [item.to_dict() for item in report.breaches], artifact_type="risk_control_breaches", producer="risk_controls")
    write_jsonl_artifact(paths["risk_limit_usage_path"], [item.to_dict() for item in report.usage], artifact_type="risk_limit_usage", producer="risk_controls")
    write_jsonl_artifact(paths["risk_control_decisions_path"], [item.to_dict() for item in report.decisions], artifact_type="risk_control_decisions", producer="risk_controls")
    write_jsonl_artifact(paths["accepted_orders_path"], accepted_orders or [], artifact_type="risk_accepted_orders", producer="risk_controls")
    write_jsonl_artifact(paths["rejected_orders_path"], rejected_orders or [], artifact_type="risk_rejected_orders", producer="risk_controls")
    write_jsonl_artifact(paths["clipped_orders_path"], clipped_orders or [], artifact_type="risk_clipped_orders", producer="risk_controls")
    write_json_artifact(paths["kill_switch_state_path"], report.kill_switch, artifact_type="kill_switch_state", producer="risk_controls")
    paths["risk_control_report_md_path"].write_text(_markdown(payload), encoding="utf-8")
    return paths


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Risk Control Report",
        "",
        f"- policy: `{payload.get('policy_id')}`",
        f"- profile: `{payload.get('profile')}`",
        f"- status: `{payload.get('status')}`",
        f"- trade_date: `{payload.get('trade_date')}`",
        f"- accepted_orders: `{payload.get('accepted_orders')}`",
        f"- rejected_orders: `{payload.get('rejected_orders')}`",
        f"- clipped_orders: `{payload.get('clipped_orders')}`",
        f"- warning_count: `{payload.get('warning_count')}`",
        f"- error_count: `{payload.get('error_count')}`",
        f"- blocker_count: `{payload.get('blocker_count')}`",
        "",
        "| breach | severity | action | message |",
        "| --- | --- | --- | --- |",
    ]
    for breach in payload.get("breaches", [])[:50]:
        lines.append(
            f"| {breach.get('limit_id')} | {breach.get('severity')} | {breach.get('action')} | {str(breach.get('message', '')).replace('|', ' ')} |"
        )
    return "\n".join(lines) + "\n"

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



class LocalRiskControlState:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.state_path = self.root_dir / "risk_control_state.json"
        self.usage_path = self.root_dir / "risk_limit_usage.jsonl"
        self.audit_path = self.root_dir / "risk_control_audit_log.jsonl"
        self.kill_switch_path = self.root_dir / "kill_switch_state.json"
        self.override_records_path = self.root_dir / "risk_override_records.jsonl"

    def load_kill_switch(self) -> KillSwitchState:
        payload = _read_json(self.kill_switch_path)
        if not payload:
            return KillSwitchState(active=False)
        return KillSwitchState(
            active=bool(payload.get("active", False)),
            activated_at=payload.get("activated_at"),
            activated_by=payload.get("activated_by"),
            reason=str(payload.get("reason") or ""),
            deactivated_at=payload.get("deactivated_at"),
            deactivated_by=payload.get("deactivated_by"),
            deactivation_reason=str(payload.get("deactivation_reason") or ""),
            approval_id=payload.get("approval_id"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def save_kill_switch(self, state: KillSwitchState) -> Path:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        return write_json_artifact(self.kill_switch_path, state.to_dict(), artifact_type="kill_switch_state", producer="risk_controls")

    def activate_kill_switch(self, reason: str, actor: str = "local_user") -> KillSwitchState:
        state = KillSwitchState(active=True, activated_at=_controls_state_utc_now(), activated_by=actor, reason=reason)
        self.save_kill_switch(state)
        self.append_audit("kill_switch_activated", "active", reason, {"actor": actor})
        return state

    def deactivate_kill_switch(self, reason: str, actor: str = "local_user", approval_id: str | None = None) -> KillSwitchState:
        prior = self.load_kill_switch()
        state = KillSwitchState(
            active=False,
            activated_at=prior.activated_at,
            activated_by=prior.activated_by,
            reason=prior.reason,
            deactivated_at=_controls_state_utc_now(),
            deactivated_by=actor,
            deactivation_reason=reason,
            approval_id=approval_id,
            metadata=prior.metadata,
        )
        self.save_kill_switch(state)
        self.append_audit("kill_switch_deactivated", "inactive", reason, {"actor": actor, "approval_id": approval_id})
        return state

    def append_usage(self, records: list[RiskLimitUsageSnapshot]) -> None:
        if not records:
            return
        self.root_dir.mkdir(parents=True, exist_ok=True)
        existing = {str(row.get("usage_id") or "") for row in _controls_state_read_jsonl(self.usage_path)}
        new_records = [record for record in records if record.usage_id not in existing]
        if new_records:
            with self.usage_path.open("a", encoding="utf-8") as handle:
                for record in new_records:
                    handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
            write_jsonl_artifact(self.usage_path, _controls_state_read_jsonl(self.usage_path), artifact_type="risk_limit_usage", producer="risk_controls")

    def load_usage(self) -> list[dict[str, Any]]:
        return _controls_state_read_jsonl(self.usage_path)

    def append_audit(self, event_type: str, status: str, message: str = "", metadata: dict[str, Any] | None = None) -> RiskControlAuditEvent:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        event = RiskControlAuditEvent(
            event_id=f"rce_{_controls_state_safe_time(_controls_state_utc_now())}_{len(_controls_state_read_jsonl(self.audit_path)) + 1}",
            created_at=_controls_state_utc_now(),
            event_type=event_type,
            status=status,
            message=message,
            metadata=metadata or {},
        )
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return event

    def append_override_record(self, record: RiskOverrideApprovalSummary) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.override_records_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        write_jsonl_artifact(self.override_records_path, _controls_state_read_jsonl(self.override_records_path), artifact_type="risk_override_records", producer="risk_controls")

    def write_state_summary(self, payload: dict[str, Any] | None = None) -> Path:
        summary = {
            "created_at": _controls_state_utc_now(),
            "kill_switch": self.load_kill_switch().to_dict(),
            "usage_records": len(_controls_state_read_jsonl(self.usage_path)),
            "audit_events": len(_controls_state_read_jsonl(self.audit_path)),
            "override_records": len(_controls_state_read_jsonl(self.override_records_path)),
            **(payload or {}),
        }
        return write_json_artifact(self.state_path, summary, artifact_type="risk_control_state", producer="risk_controls")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _controls_state_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _controls_state_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _controls_state_safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")

from pathlib import Path



def activate_kill_switch(state_dir: str | Path, reason: str, actor: str = "local_user") -> KillSwitchState:
    return LocalRiskControlState(state_dir).activate_kill_switch(reason=reason, actor=actor)


def deactivate_kill_switch(state_dir: str | Path, reason: str, actor: str = "local_user", approval_id: str | None = None) -> KillSwitchState:
    return LocalRiskControlState(state_dir).deactivate_kill_switch(reason=reason, actor=actor, approval_id=approval_id)


def load_kill_switch(state_dir: str | Path) -> KillSwitchState:
    return LocalRiskControlState(state_dir).load_kill_switch()

import json
from pathlib import Path
from typing import Any



def evaluate_order_records(
    orders: list[Any],
    *,
    policy_path: str | Path | None = None,
    policy_profile: str = "cn_ashare_paper_default",
    state_dir: str | Path,
    output_dir: str | Path,
    batch_id: str = "",
    trade_date: str | None = None,
    scope: str = "order",
    allow_clipping: bool = False,
    available_cash: float | None = None,
    available_shares: dict[str, float] | None = None,
) -> tuple[RiskControlReport, dict[str, list[dict[str, Any]]], dict[str, Path]]:
    policy = load_policy(policy_path, profile=policy_profile)
    state = LocalRiskControlState(state_dir)
    normalized = [normalize_order(order, idx) for idx, order in enumerate(orders)]
    engine = RiskControlLimitEngine(
        policy,
        allow_clipping=allow_clipping,
        available_cash=available_cash,
        available_shares=available_shares,
        kill_switch=state.load_kill_switch(),
        batch_id=batch_id,
        scope=scope,
    )
    report = engine.evaluate(normalized, trade_date=trade_date)
    accepted, rejected, clipped = split_orders_by_decision(normalized, report)
    paths = write_risk_control_report(report, output_dir, accepted, rejected, clipped)
    state.append_usage(report.usage)
    state.append_audit("evaluate_orders", report.status, "pre-trade risk controls evaluated", {"batch_id": batch_id, "scope": scope})
    state.write_state_summary({"last_report_path": str(paths["risk_control_report_path"]), "last_status": report.status})
    return report, {"accepted": accepted, "rejected": rejected, "clipped": clipped}, paths


def evaluate_orders_file(
    orders_path: str | Path,
    *,
    policy_path: str | Path | None = None,
    policy_profile: str = "cn_ashare_paper_default",
    state_dir: str | Path,
    output_dir: str | Path,
    batch_id: str = "",
    trade_date: str | None = None,
    scope: str = "order",
    allow_clipping: bool = False,
) -> tuple[RiskControlReport, dict[str, list[dict[str, Any]]], dict[str, Path]]:
    records = _controls_order_gate_read_jsonl(Path(orders_path))
    return evaluate_order_records(
        records,
        policy_path=policy_path,
        policy_profile=policy_profile,
        state_dir=state_dir,
        output_dir=output_dir,
        batch_id=batch_id,
        trade_date=trade_date,
        scope=scope,
        allow_clipping=allow_clipping,
    )


def split_orders_by_decision(orders: list[dict[str, Any]], report: RiskControlReport) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {str(order.get("order_id")): dict(order) for order in orders}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    clipped: list[dict[str, Any]] = []
    for decision in report.decisions:
        order = dict(by_id.get(decision.order_id, {}))
        order["risk_control_decision_id"] = decision.decision_id
        order["risk_control_status"] = decision.status
        order["risk_control_reasons"] = decision.reasons
        if decision.status == RiskControlStatus.clipped:
            final_order = dict(decision.metadata.get("final_order", {}))
            final_order["risk_control_decision_id"] = decision.decision_id
            final_order["risk_control_status"] = decision.status
            final_order["risk_control_reasons"] = decision.reasons
            clipped.append(final_order)
            accepted.append(final_order)
        elif decision.status in {RiskControlStatus.passed, RiskControlStatus.warning}:
            accepted.append(order)
        else:
            rejected.append(order)
    return accepted, rejected, clipped


def _controls_order_gate_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records

from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.governance.approval import ApprovalBatch, ApprovalStatus, LocalApprovalStore
from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def create_override_approval(
    *,
    approval_store_dir: str | Path,
    output_dir: str | Path,
    state_dir: str | Path,
    scope: str,
    reason: str,
    requested_by: str = "local_user",
    expires_at: str | None = None,
    max_usage_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[RiskOverrideRequest, ApprovalBatch, Path]:
    created_at = _controls_overrides_utc_now()
    override_id = f"risk_override_{_controls_overrides_safe_time(created_at)}"
    approval_id = f"approval_risk_override_{_controls_overrides_safe_time(created_at)}"
    request = RiskOverrideRequest(
        override_id=override_id,
        created_at=created_at,
        scope=scope,
        reason=reason,
        requested_by=requested_by,
        expires_at=expires_at,
        max_usage_count=max_usage_count,
        approval_id=approval_id,
        metadata=metadata or {},
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    request_path = write_json_artifact(output / "risk_override_request.json", request.to_dict(), artifact_type="risk_override_request", producer="risk_controls")
    batch = ApprovalBatch(
        approval_id=approval_id,
        created_at=created_at,
        factor_id="risk_control_override",
        factor_type="risk_control",
        rebalance_date=str((metadata or {}).get("trade_date") or ""),
        portfolio_method="risk_control_override",
        orders=[],
        approval_type="risk_control_override",
        risk_override_request_path=str(request_path),
        risk_override_scope=scope,
        risk_override_expiry_date=expires_at,
        risk_override_max_usage_count=max_usage_count,
        metadata={"risk_override_request": request.to_dict(), "risk_control_state_dir": str(state_dir), **(metadata or {})},
    )
    LocalApprovalStore(approval_store_dir).save_batch(batch)
    LocalRiskControlState(state_dir).append_audit("risk_override_requested", "pending_approval", reason, {"approval_id": approval_id})
    return request, batch, request_path


def apply_approved_override(
    *,
    approval_store_dir: str | Path,
    approval_id: str,
    state_dir: str | Path,
    actor: str = "local_user",
    deactivate_kill_switch: bool = False,
) -> RiskOverrideApprovalSummary:
    batch = LocalApprovalStore(approval_store_dir).load_batch(approval_id)
    if batch.status != ApprovalStatus.approved:
        raise ValueError(f"risk override approval must be approved before use: {approval_id} is {batch.status}")
    request = dict((batch.metadata or {}).get("risk_override_request") or {})
    summary = RiskOverrideApprovalSummary(
        override_id=str(request.get("override_id") or approval_id),
        approval_id=approval_id,
        status="applied",
        scope=str(request.get("scope") or batch.risk_override_scope or "global"),
        expires_at=request.get("expires_at") or batch.risk_override_expiry_date,
        max_usage_count=request.get("max_usage_count") or batch.risk_override_max_usage_count,
        applied_at=_controls_overrides_utc_now(),
        metadata={"actor": actor, "deactivate_kill_switch": deactivate_kill_switch},
    )
    store = LocalRiskControlState(state_dir)
    store.append_override_record(summary)
    store.append_audit("risk_override_applied", "applied", "approved risk override applied", {"approval_id": approval_id, "actor": actor})
    if deactivate_kill_switch:
        store.deactivate_kill_switch("approved risk override", actor=actor, approval_id=approval_id)
    return summary


def _controls_overrides_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _controls_overrides_safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")

import argparse
import json
from pathlib import Path

from auto_alpha.platform.governance.approval import LocalApprovalStore



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local A-share pre-trade risk controls.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-policy")
    init.add_argument("--output-dir", required=True)
    init.add_argument("--profile", default="cn_ashare_paper_default")
    init.add_argument("--pretty", action="store_true")

    validate = sub.add_parser("validate-policy")
    validate.add_argument("--policy-path", required=True)
    validate.add_argument("--output-dir")
    validate.add_argument("--pretty", action="store_true")

    for name, scope in [
        ("evaluate-orders", "order"),
        ("evaluate-child-orders", "child_order"),
        ("evaluate-broker-requests", "broker_request"),
    ]:
        cmd = sub.add_parser(name)
        cmd.set_defaults(scope=scope)
        cmd.add_argument("--orders-path", required=True)
        cmd.add_argument("--policy-path")
        cmd.add_argument("--policy-profile", default="cn_ashare_paper_default")
        cmd.add_argument("--state-dir", required=True)
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--batch-id", default="")
        cmd.add_argument("--trade-date")
        cmd.add_argument("--allow-clipping", action="store_true")
        cmd.add_argument("--fail-on-breach", action="store_true")
        cmd.add_argument("--pretty", action="store_true")

    activate = sub.add_parser("activate-kill-switch")
    activate.add_argument("--state-dir", required=True)
    activate.add_argument("--reason", required=True)
    activate.add_argument("--actor", default="local_user")
    activate.add_argument("--pretty", action="store_true")

    deactivate = sub.add_parser("deactivate-kill-switch")
    deactivate.add_argument("--state-dir", required=True)
    deactivate.add_argument("--reason", required=True)
    deactivate.add_argument("--actor", default="local_user")
    deactivate.add_argument("--approval-id")
    deactivate.add_argument("--pretty", action="store_true")

    show = sub.add_parser("show-kill-switch")
    show.add_argument("--state-dir", required=True)
    show.add_argument("--pretty", action="store_true")

    create = sub.add_parser("create-override-approval")
    create.add_argument("--approval-store-dir", required=True)
    create.add_argument("--state-dir", required=True)
    create.add_argument("--output-dir", required=True)
    create.add_argument("--scope", default="global")
    create.add_argument("--reason", required=True)
    create.add_argument("--requested-by", default="local_user")
    create.add_argument("--expires-at")
    create.add_argument("--max-usage-count", type=int)
    create.add_argument("--pretty", action="store_true")

    apply = sub.add_parser("apply-approved-override")
    apply.add_argument("--approval-store-dir", required=True)
    apply.add_argument("--approval-id", required=True)
    apply.add_argument("--state-dir", required=True)
    apply.add_argument("--actor", default="local_user")
    apply.add_argument("--deactivate-kill-switch", action="store_true")
    apply.add_argument("--pretty", action="store_true")

    usage = sub.add_parser("show-usage")
    usage.add_argument("--state-dir", required=True)
    usage.add_argument("--pretty", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--state-dir", required=True)
    report.add_argument("--pretty", action="store_true")

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--output-dir", required=True)
    smoke.add_argument("--state-dir")
    smoke.add_argument("--policy-profile", default="strict_paper_gate")
    smoke.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command
    if command == "init-policy":
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        policy = default_policy(args.profile)
        policy_path = write_policy(policy, output / "risk_control_policy.json")
        manifest_path = write_policy_manifest(policy, output / "risk_control_policy_manifest.json", policy_path=policy_path)
        payload = {"policy_path": str(policy_path), "manifest_path": str(manifest_path), "policy": policy.to_dict()}
        _print(payload, args.pretty)
        return 0
    if command == "validate-policy":
        policy = load_policy(args.policy_path)
        manifest = validate_policy(policy, policy_path=args.policy_path)
        if args.output_dir:
            write_policy_manifest(policy, Path(args.output_dir) / "risk_control_policy_manifest.json", policy_path=args.policy_path)
        _print(manifest.to_dict(), args.pretty)
        return 0 if manifest.status == "valid" else 1
    if command.startswith("evaluate-"):
        report, _orders, paths = evaluate_orders_file(
            args.orders_path,
            policy_path=args.policy_path,
            policy_profile=args.policy_profile,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            batch_id=args.batch_id,
            trade_date=args.trade_date,
            scope=args.scope,
            allow_clipping=args.allow_clipping,
        )
        payload = report.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}
        _print(payload, args.pretty)
        return 1 if args.fail_on_breach and report.rejected_orders > 0 else 0
    if command == "activate-kill-switch":
        state = activate_kill_switch(args.state_dir, args.reason, args.actor)
        _print(state.to_dict(), args.pretty)
        return 0
    if command == "deactivate-kill-switch":
        state = deactivate_kill_switch(args.state_dir, args.reason, args.actor, approval_id=args.approval_id)
        _print(state.to_dict(), args.pretty)
        return 0
    if command == "show-kill-switch":
        _print(load_kill_switch(args.state_dir).to_dict(), args.pretty)
        return 0
    if command == "create-override-approval":
        request, batch, request_path = create_override_approval(
            approval_store_dir=args.approval_store_dir,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            scope=args.scope,
            reason=args.reason,
            requested_by=args.requested_by,
            expires_at=args.expires_at,
            max_usage_count=args.max_usage_count,
        )
        _print({"override_request": request.to_dict(), "approval_id": batch.approval_id, "request_path": str(request_path)}, args.pretty)
        return 0
    if command == "apply-approved-override":
        summary = apply_approved_override(
            approval_store_dir=args.approval_store_dir,
            approval_id=args.approval_id,
            state_dir=args.state_dir,
            actor=args.actor,
            deactivate_kill_switch=args.deactivate_kill_switch,
        )
        _print(summary.to_dict(), args.pretty)
        return 0
    if command == "show-usage":
        usage = LocalRiskControlState(args.state_dir).load_usage()
        _print({"records": len(usage), "usage": usage}, args.pretty)
        return 0
    if command == "report":
        path = LocalRiskControlState(args.state_dir).write_state_summary()
        _print({"risk_control_state_path": str(path), "state": json.loads(path.read_text(encoding="utf-8"))}, args.pretty)
        return 0
    if command == "smoke":
        output = Path(args.output_dir)
        state_dir = Path(args.state_dir) if args.state_dir else output / "state"
        orders_path = output / "smoke_orders.jsonl"
        output.mkdir(parents=True, exist_ok=True)
        orders_path.write_text(
            "\n".join(
                [
                    json.dumps({"trade_date": "20240104", "ts_code": "000001.SZ", "side": "BUY", "order_value": 1000.0, "shares": 100}),
                    json.dumps({"trade_date": "20240104", "ts_code": "688999.SH", "side": "BUY", "order_value": 2_000_000.0, "shares": 200000}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        report, _orders, paths = evaluate_orders_file(
            orders_path,
            policy_profile=args.policy_profile,
            state_dir=state_dir,
            output_dir=output,
            batch_id="risk_controls_smoke",
            trade_date="20240104",
        )
        _print(report.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}, args.pretty)
        return 0
    return 1


def _print(payload: dict, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "KillSwitchState",
    "LocalRiskControlState",
    "RiskBreachAction",
    "RiskControlBreach",
    "RiskControlDecision",
    "RiskControlLimitEngine",
    "RiskControlPolicy",
    "RiskControlReport",
    "RiskControlScope",
    "RiskControlSeverity",
    "RiskControlStatus",
    "RiskLimitDefinition",
    "RiskLimitUsageSnapshot",
    "RiskOverrideApprovalSummary",
    "RiskOverrideRequest",
    "activate_kill_switch",
    "deactivate_kill_switch",
    "default_policy",
    "evaluate_order_records",
    "evaluate_orders_file",
    "load_kill_switch",
    "load_policy",
    "validate_policy",
    "write_policy",
    "write_policy_manifest",
]
