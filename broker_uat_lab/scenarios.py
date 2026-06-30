"""Default BrokerAdapter UAT scenarios."""

from __future__ import annotations

from .models import BrokerUatScenario, BrokerUatScenarioType, BrokerUatStatus


def build_default_uat_scenarios(profile: str = "sample", *, include_readonly: bool = False, readonly_metadata: dict | None = None) -> list[BrokerUatScenario]:
    base = [
        BrokerUatScenario("uat_submit_idempotency", BrokerUatScenarioType.submit_idempotency, "Submit idempotency"),
        BrokerUatScenario("uat_full_fill", BrokerUatScenarioType.full_fill, "Full fill"),
        BrokerUatScenario("uat_partial_fill", BrokerUatScenarioType.partial_fill, "Partial fill"),
        BrokerUatScenario("uat_reject_order", BrokerUatScenarioType.reject_order, "Reject order"),
        BrokerUatScenario("uat_cancel_order", BrokerUatScenarioType.cancel_order, "Cancel order"),
        BrokerUatScenario("uat_replace_order", BrokerUatScenarioType.replace_order, "Replace order"),
        BrokerUatScenario("uat_duplicate_fill", BrokerUatScenarioType.duplicate_fill, "Duplicate fill idempotency"),
        BrokerUatScenario("uat_out_of_order_fill", BrokerUatScenarioType.out_of_order_fill, "Out-of-order event handling"),
        BrokerUatScenario("uat_reconnect_replay", BrokerUatScenarioType.reconnect_replay, "Reconnect replay"),
        BrokerUatScenario("uat_kill_switch_block", BrokerUatScenarioType.kill_switch_block, "Kill switch blocks submit"),
        BrokerUatScenario("uat_file_outbox_roundtrip", BrokerUatScenarioType.file_outbox_roundtrip, "File outbox roundtrip", expected_status=BrokerUatStatus.warning),
    ]
    if profile == "strict":
        base.extend(
            [
                BrokerUatScenario("uat_missing_ack", BrokerUatScenarioType.missing_ack, "Missing ack", expected_status=BrokerUatStatus.warning),
                BrokerUatScenario("uat_rate_limit", BrokerUatScenarioType.rate_limit, "Rate limit handling", expected_status=BrokerUatStatus.warning),
                BrokerUatScenario("uat_eod_reconciliation", BrokerUatScenarioType.eod_reconciliation, "EOD reconciliation"),
                BrokerUatScenario("uat_settlement_reconciliation", BrokerUatScenarioType.settlement_reconciliation, "Settlement reconciliation"),
            ]
        )
    if include_readonly:
        metadata = dict(readonly_metadata or {})
        base.extend(
            [
                BrokerUatScenario("uat_readonly_connectivity", BrokerUatScenarioType.readonly_connectivity, "Read-only broker connectivity", metadata=metadata),
                BrokerUatScenario("uat_credential_redaction", BrokerUatScenarioType.credential_redaction, "Credential reference redaction", metadata=metadata),
                BrokerUatScenario("uat_network_guard", BrokerUatScenarioType.network_guard, "Network guard remains read-only", metadata=metadata),
                BrokerUatScenario("uat_readonly_mirror_reconciliation", BrokerUatScenarioType.readonly_mirror_reconciliation, "Read-only mirror reconciliation", metadata=metadata),
            ]
        )
    return base
