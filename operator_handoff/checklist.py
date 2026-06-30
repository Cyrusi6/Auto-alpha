"""Checklist definitions for manual broker file handoff."""

from __future__ import annotations

from .models import HandoffChecklistItem


_CHECKLIST: tuple[tuple[str, str], ...] = (
    ("data_freeze_validated", "Data freeze has been validated."),
    ("active_model_confirmed", "Active production factor/model has been confirmed."),
    ("active_optimizer_policy_confirmed", "Active optimizer policy has been confirmed."),
    ("factor_certification_checked", "Factor certification evidence has been checked."),
    ("portfolio_certification_checked", "Portfolio policy certification has been checked."),
    ("risk_gate_passed", "Pre-trade risk gate has passed."),
    ("kill_switch_inactive", "Kill switch is inactive."),
    ("order_approval_approved", "Order approval is approved."),
    ("broker_file_manifest_reviewed", "Broker file manifest has been reviewed."),
    ("checksum_verified", "Outbox file checksums have been verified."),
    ("outbox_record_count_checked", "Outbox record count has been checked."),
    ("order_notional_checked", "Order notional has been checked."),
    ("restricted_symbol_absent", "Restricted symbols are absent."),
    ("operator_readme_reviewed", "Operator readme has been reviewed."),
    ("handoff_directory_confirmed", "Handoff directory has been confirmed."),
    ("no_real_auto_submit_confirmed", "No real auto submit path is enabled."),
    ("inbox_expected_files_documented", "Expected inbox files are documented."),
    ("rollback_contact_or_runbook_reviewed", "Rollback contact/runbook has been reviewed."),
    ("second_reviewer_confirmed", "Second reviewer has confirmed the package."),
)


def default_handoff_checklist() -> list[HandoffChecklistItem]:
    return [HandoffChecklistItem(item_id=item_id, title=title, description=title) for item_id, title in _CHECKLIST]


def required_item_ids() -> list[str]:
    return [item_id for item_id, _title in _CHECKLIST]
