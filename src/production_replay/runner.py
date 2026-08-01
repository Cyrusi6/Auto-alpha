"""Multi-day production replay runner."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from approval.run_approval import main as approval_main
from production_orchestrator.run_production import main as production_main

from .aggregation import aggregate_replay_days
from .models import (
    ProductionReplayConfig,
    ProductionReplayDayResult,
    ProductionReplayEvent,
    ProductionReplayReport,
    ReplayDayStatus,
    ReplayMode,
)
from .planner import build_replay_plan, utc_now
from .report import write_replay_plan, write_replay_report
from .state import LocalProductionReplayStore


class ProductionReplayRunner:
    def __init__(self, config: ProductionReplayConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.store = LocalProductionReplayStore(config.replay_state_dir)

    def plan(self) -> dict[str, Any]:
        plan = build_replay_plan(self.config)
        paths = write_replay_plan(plan, self.output_dir)
        self.store.append_event(
            ProductionReplayEvent(self.config.replay_id, None, "plan", "planned", utc_now(), metadata={"day_count": len(self.config.trade_dates)})
        )
        return {"status": "planned", "replay_id": self.config.replay_id, "paths": paths, "plan": plan.to_dict()}

    def run(self, resume: bool = False) -> dict[str, Any]:
        self.plan()
        results: list[ProductionReplayDayResult] = []
        failed_days = 0
        for index, trade_date in enumerate(self.config.trade_dates):
            existing = self.store.load_day(self.config.replay_id, trade_date) if resume else None
            if existing and existing.get("status") in {ReplayDayStatus.success, ReplayDayStatus.warning}:
                result = self._day_from_dict(existing)
            else:
                result = self._run_one_day(trade_date, index)
            self.store.save_day(result)
            self.store.append_event(
                ProductionReplayEvent(
                    self.config.replay_id,
                    trade_date,
                    "run_day",
                    result.status,
                    utc_now(),
                    message=result.error or "",
                    metadata={"production_run_id": result.production_run_id},
                )
            )
            results.append(result)
            if result.status == ReplayDayStatus.failed:
                failed_days += 1
            if result.status == ReplayDayStatus.blocked and self.config.stop_on_blocker:
                break
            if self.config.max_failed_days >= 0 and failed_days > self.config.max_failed_days:
                break
        return self.write_report(results)

    def aggregate(self) -> dict[str, Any]:
        results = [self._day_from_dict(row) for row in self.store.load_days(self.config.replay_id)]
        return self.write_report(results)

    def write_report(self, results: list[ProductionReplayDayResult]) -> dict[str, Any]:
        summary = aggregate_replay_days(results)
        status = summary.get("status", "success")
        report = ProductionReplayReport(
            replay_id=self.config.replay_id,
            replay_name=self.config.replay_name,
            replay_mode=self.config.replay_mode,
            status=str(status),
            created_at=utc_now(),
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            day_results=results,
            summary=summary,
            config=self.config.to_dict(),
        )
        events = self.store.load_events(self.config.replay_id)
        paths = write_replay_report(report, self.output_dir, events=events)
        payload = report.to_dict()
        payload["paths"] = paths
        return payload

    def _run_one_day(self, trade_date: str, index: int) -> ProductionReplayDayResult:
        started = utc_now()
        run_mode = self._day_run_mode(index)
        day_root = self.output_dir / "replay_days" / trade_date
        day_root.mkdir(parents=True, exist_ok=True)
        common = self._common_args(trade_date, run_mode, day_root)
        paths: dict[str, str] = {"day_output_dir": str(day_root)}
        try:
            plan_payload = self._call_json(production_main, ["plan-day", *common])
            paths.update(_extract_paths(plan_payload))
            run_args = ["run-day", *common]
            if run_mode in {ReplayMode.paper_simulated, ReplayMode.file_outbox_dry_run}:
                run_args.append("--require-order-approval")
                run_args.extend(["--stop-after-phase", "wait_for_approval"])
            run_payload = self._call_json(production_main, run_args)
            paths.update(_extract_paths(run_payload))
            approval_id = _approval_id(run_payload)
            resume_payload: dict[str, Any] = {}
            if run_mode in {ReplayMode.paper_simulated, ReplayMode.file_outbox_dry_run}:
                if approval_id and self.config.auto_approve_paper_local:
                    self._approve(approval_id)
                    self.store.record_approval(self.config.replay_id, trade_date, approval_id, "auto_approve_paper_local")
                if approval_id:
                    resume_payload = self._call_json(production_main, ["resume", *common, "--approval-id", approval_id])
                    paths.update(_extract_paths(resume_payload))
            close_payload = self._call_json(production_main, ["close-day", *common])
            paths.update(_extract_paths(close_payload))
            production_run_id = (
                close_payload.get("production_run_id")
                or resume_payload.get("production_run_id")
                or run_payload.get("production_run_id")
                or plan_payload.get("production_run_id")
            )
            status = self._resolve_status(run_payload, resume_payload, close_payload)
            summary = _merge_summary(plan_payload, run_payload, resume_payload, close_payload)
            return ProductionReplayDayResult(
                replay_id=self.config.replay_id,
                trade_date=trade_date,
                run_mode=run_mode,
                status=status,
                production_run_id=str(production_run_id) if production_run_id else None,
                approval_id=approval_id,
                plan_status=str(plan_payload.get("status", "")),
                run_status=str(run_payload.get("status", "")),
                resume_status=str(resume_payload.get("status", "")),
                close_status=str(close_payload.get("status", "")),
                gate_blocker_count=int(summary.get("gate_blocker_count", 0) or 0),
                gate_warning_count=int(summary.get("gate_warning_count", 0) or 0),
                phase_failed_count=int(summary.get("phase_failed_count", 0) or 0),
                phase_blocked_count=int(summary.get("phase_blocked_count", 0) or 0),
                shadow_fill_rate=float(summary.get("shadow_fill_rate", 0.0) or 0.0),
                paper_fill_rate=float(summary.get("fill_rate", summary.get("paper_fill_rate", 0.0)) or 0.0),
                broker_unfilled_value=float(summary.get("broker_unfilled_value", 0.0) or 0.0),
                broker_connectivity_status=str(summary.get("broker_connectivity_status") or ""),
                broker_readonly_mirror_status=str(summary.get("broker_readonly_mirror_status") or ""),
                readonly_mirror_break_count=int(summary.get("broker_readonly_break_count", summary.get("readonly_mirror_break_count", 0)) or 0),
                incident_open_count=int(summary.get("incident_open_count", 0) or 0),
                paths=paths,
                summary=summary,
                created_at=started,
                updated_at=utc_now(),
            )
        except Exception as exc:
            return ProductionReplayDayResult(
                replay_id=self.config.replay_id,
                trade_date=trade_date,
                run_mode=run_mode,
                status=ReplayDayStatus.failed,
                paths=paths,
                summary={},
                error=str(exc),
                created_at=started,
                updated_at=utc_now(),
            )

    def _day_run_mode(self, index: int) -> str:
        if self.config.replay_mode != ReplayMode.mixed:
            return self.config.replay_mode
        pivot = max(len(self.config.trade_dates) // 2, 1)
        return ReplayMode.shadow_only if index < pivot else ReplayMode.paper_simulated

    def _common_args(self, trade_date: str, run_mode: str, day_root: Path) -> list[str]:
        orders_dir = Path(self.config.orders_root_dir or self.output_dir / "orders") / trade_date
        shadow_dir = Path(self.config.shadow_root_dir or self.output_dir / "shadow") / trade_date
        risk_dir = Path(self.config.risk_control_output_root or self.output_dir / "risk_controls") / trade_date
        monitoring_dir = Path(self.config.monitoring_root_dir or self.output_dir / "monitoring") / trade_date
        incident_dir = Path(self.config.incident_store_dir or self.output_dir / "incidents") / trade_date
        args = [
            "--production-state-dir",
            str(Path(self.config.replay_state_dir) / "production_state"),
            "--output-dir",
            str(day_root),
            "--run-mode",
            "file_outbox" if run_mode == ReplayMode.file_outbox_dry_run else run_mode,
            "--trade-date",
            trade_date,
            "--as-of-date",
            trade_date,
            "--data-dir",
            self.config.data_dir,
            "--factor-store-dir",
            self.config.factor_store_dir or str(self.output_dir / "factor_store"),
            "--approval-store-dir",
            self.config.approval_store_dir or str(self.output_dir / "approvals"),
            "--paper-account-dir",
            self.config.paper_account_dir or str(self.output_dir / "account"),
            "--orders-dir",
            str(orders_dir),
            "--shadow-dir",
            str(shadow_dir),
            "--incident-store-dir",
            str(incident_dir),
            "--monitoring-dir",
            str(monitoring_dir),
            "--portfolio-value",
            str(self.config.portfolio_value),
            "--index-code",
            self.config.index_code,
            "--top-n",
            str(self.config.top_n),
            "--max-weight",
            str(self.config.max_weight),
            "--broker-adapter",
            "file" if run_mode == ReplayMode.file_outbox_dry_run else self.config.broker_adapter,
        ]
        if self.config.broker_store_dir:
            args.extend(["--broker-store-dir", str(Path(self.config.broker_store_dir) / trade_date)])
        if self.config.broker_connectivity_profile:
            args.extend(["--broker-connectivity-profile", self.config.broker_connectivity_profile])
        if self.config.broker_connectivity_profile_config:
            args.extend(["--broker-connectivity-profile-config", self.config.broker_connectivity_profile_config])
        if self.config.broker_connectivity_store_dir:
            args.extend(["--broker-connectivity-store-dir", str(Path(self.config.broker_connectivity_store_dir) / trade_date)])
        if self.config.broker_readonly_mirror_root_dir:
            args.extend(["--broker-readonly-mirror-dir", str(Path(self.config.broker_readonly_mirror_root_dir) / trade_date)])
        if self.config.broker_connectivity_approval_id:
            args.extend(["--broker-connectivity-approval-id", self.config.broker_connectivity_approval_id])
        if self.config.allow_broker_readonly_network:
            args.append("--allow-broker-readonly-network")
        if self.config.require_broker_connectivity:
            args.append("--require-broker-connectivity")
        if self.config.run_broker_readonly_mirror:
            args.append("--run-broker-readonly-mirror")
        if run_mode == ReplayMode.file_outbox_dry_run or self.config.broker_file_gateway:
            args.append("--broker-file-gateway")
            args.extend(["--broker-file-profile", self.config.broker_file_profile])
            args.append("--file-outbox-dry-run")
            if self.config.auto_confirm_local_smoke:
                args.append("--auto-confirm-local-smoke")
            if self.config.broker_file_profile_config:
                args.extend(["--broker-file-profile-config", self.config.broker_file_profile_config])
            gateway_root = Path(self.config.broker_file_gateway_store_dir or self.output_dir / "broker_file_gateway") / trade_date
            args.extend(["--broker-file-gateway-store-dir", str(gateway_root)])
            outbox_root = Path(self.config.broker_file_outbox_root_dir or gateway_root / "outbox")
            inbox_root = Path(self.config.broker_file_inbox_root_dir or gateway_root / "inbox")
            handoff_root = Path(self.config.broker_file_handoff_root_dir or self.output_dir / "handoff") / trade_date
            args.extend(["--broker-file-outbox-dir", str(outbox_root)])
            args.extend(["--broker-file-inbox-dir", str(inbox_root)])
            args.extend(["--broker-file-handoff-dir", str(handoff_root)])
            if self.config.operator_handoff_store_dir:
                args.extend(["--operator-handoff-store-dir", str(Path(self.config.operator_handoff_store_dir) / trade_date)])
            if self.config.operator_handoff_approval_store_dir:
                args.extend(["--operator-handoff-approval-store-dir", self.config.operator_handoff_approval_store_dir])
            if self.config.mapping_certification_decision_path:
                args.extend(["--mapping-certification-decision-path", self.config.mapping_certification_decision_path])
            if self.config.require_mapping_certification:
                args.append("--require-mapping-certification")
        if self.config.settlement_dir:
            args.extend(["--settlement-dir", str(Path(self.config.settlement_dir) / trade_date)])
        if self.config.risk_control_state_dir:
            args.extend(["--risk-control-state-dir", self.config.risk_control_state_dir])
        if self.config.risk_controls:
            args.extend(["--risk-controls", "--risk-control-output-dir", str(risk_dir)])
        if self.config.capacity_aware:
            args.append("--capacity-aware")
        if self.config.point_in_time:
            args.append("--point-in-time")
            args.extend(["--feature-cutoff-mode", self.config.feature_cutoff_mode])
        if self.config.corporate_action_aware:
            args.append("--corporate-action-aware")
        if self.config.apply_corporate_actions:
            args.append("--apply-corporate-actions")
        if self.config.corporate_action_dir:
            args.extend(["--corporate-action-dir", self.config.corporate_action_dir])
        if self.config.target_return_mode:
            args.extend(["--target-return-mode", self.config.target_return_mode])
        if self.config.settlement_aware:
            args.append("--settlement-aware")
        if self.config.data_freeze_dir:
            args.extend(["--data-freeze-dir", self.config.data_freeze_dir])
        if self.config.data_version_manifest_path:
            args.extend(["--data-version-manifest-path", self.config.data_version_manifest_path])
        if self.config.require_data_freeze:
            args.append("--require-data-freeze")
        if self.config.model_registry_dir:
            args.extend(["--model-registry-dir", self.config.model_registry_dir])
        if self.config.require_active_model:
            args.append("--require-active-model")
        if self.config.require_active_optimizer_policy:
            args.append("--require-active-optimizer-policy")
        if self.config.certified_portfolio_policy_path:
            args.extend(["--certified-portfolio-policy-path", self.config.certified_portfolio_policy_path])
        if self.config.portfolio_certification_decision_path:
            args.extend(["--portfolio-certification-decision-path", self.config.portfolio_certification_decision_path])
        if self.config.require_certified_portfolio_policy:
            args.append("--require-certified-portfolio-policy")
        if self.config.continue_on_warning:
            args.append("--continue-on-warning")
        return args

    def _approve(self, approval_id: str) -> None:
        store_dir = self.config.approval_store_dir or str(self.output_dir / "approvals")
        payload = self._call_json(
            approval_main,
            [
                "--store-dir",
                store_dir,
                "approve",
                "--approval-id",
                approval_id,
                "--reviewer",
                self.config.paper_local_reviewer,
                "--comment",
                f"auto approved for replay {self.config.replay_id}",
            ],
        )
        if payload.get("error"):
            raise RuntimeError(str(payload.get("error")))

    def _call_json(self, func, argv: list[str]) -> dict[str, Any]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = func(argv)
        text = stdout.getvalue()
        payload = json.loads(text) if text.strip() else {}
        if code != 0:
            payload.setdefault("status", "failed")
            payload.setdefault("error", f"command exited with {code}")
        return payload

    @staticmethod
    def _resolve_status(run_payload: dict[str, Any], resume_payload: dict[str, Any], close_payload: dict[str, Any]) -> str:
        for payload in [run_payload, resume_payload, close_payload]:
            if payload and payload.get("status") in {"failed"}:
                return ReplayDayStatus.failed
            if payload and payload.get("status") == "blocked":
                return ReplayDayStatus.blocked
        if close_payload.get("status") == "closed":
            return ReplayDayStatus.success
        if run_payload.get("status") == "waiting_approval" and not resume_payload:
            return ReplayDayStatus.warning
        return ReplayDayStatus.success

    @staticmethod
    def _day_from_dict(row: dict[str, Any]) -> ProductionReplayDayResult:
        allowed = set(ProductionReplayDayResult.__dataclass_fields__)
        return ProductionReplayDayResult(**{key: row.get(key) for key in allowed if key in row})


def _extract_paths(payload: dict[str, Any]) -> dict[str, str]:
    paths = dict(payload.get("paths") or {}) if isinstance(payload.get("paths"), dict) else {}
    for key, value in payload.items():
        if key.endswith("_path") and value:
            paths[key] = str(value)
    return paths


def _merge_summary(*payloads: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for payload in payloads:
        if not payload:
            continue
        readiness = payload.get("readiness") or {}
        readiness_summary = readiness.get("summary") if isinstance(readiness, dict) else {}
        if isinstance(readiness_summary, dict):
            summary["gate_blocker_count"] = max(int(summary.get("gate_blocker_count", 0) or 0), int(readiness_summary.get("blocker_count", 0) or 0))
            summary["gate_warning_count"] = max(int(summary.get("gate_warning_count", 0) or 0), int(readiness_summary.get("warning_count", 0) or 0))
        payload_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        if isinstance(payload_summary, dict):
            summary.update(payload_summary)
    return summary


def _approval_id(payload: dict[str, Any]) -> str | None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    value = payload.get("approval_id") or summary.get("approval_id")
    return str(value) if value else None
