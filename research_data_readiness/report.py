"""Research readiness report builders and writers."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .checks import build_dataset_readiness_checks, summarize_checks
from .dataset_policy import ALL_RESEARCH_DATASETS
from .decision import decide_research_readiness
from .feature_readiness import build_feature_readiness_catalog
from .models import ResearchDataReadinessReport


def build_research_data_readiness_report(
    data_dir: str | Path,
    *,
    run_dir: str | Path | None = None,
    observer_report_path: str | Path | None = None,
    dataset_progress_path: str | Path | None = None,
    raw_landing_report_path: str | Path | None = None,
    freeze_readiness_path: str | Path | None = None,
    repair_plan_path: str | Path | None = None,
    postprocess_plan_path: str | Path | None = None,
    real_data_sla_report_path: str | Path | None = None,
    matrix_freshness_report_path: str | Path | None = None,
    data_quality_freeze_gate_path: str | Path | None = None,
    profile_name: str | None = None,
    strict: bool = False,
) -> ResearchDataReadinessReport:
    del run_dir, real_data_sla_report_path
    dataset_checks = build_dataset_readiness_checks(
        data_dir=data_dir,
        datasets=list(ALL_RESEARCH_DATASETS),
        raw_landing_report_path=raw_landing_report_path,
        dataset_progress_path=dataset_progress_path,
    )
    feature_readiness = build_feature_readiness_catalog(dataset_checks)
    summary = summarize_checks(
        dataset_checks,
        observer_report_path=observer_report_path,
        freeze_readiness_path=freeze_readiness_path,
        repair_plan_path=repair_plan_path,
        postprocess_plan_path=postprocess_plan_path,
        matrix_freshness_report_path=matrix_freshness_report_path,
    )
    decision = decide_research_readiness(dataset_checks, feature_readiness, summary, strict=strict)
    data_quality_gate = _read_json(data_quality_freeze_gate_path)
    if data_quality_gate:
        decision = _apply_data_quality_gate(decision, data_quality_gate)
    summary.update(
        {
            "research_data_readiness_status": decision.status,
            "research_readiness_blocker_count": decision.blocker_count,
            "feature_ready_family_count": sum(1 for item in feature_readiness if item.readiness_status == "ready"),
            "feature_blocked_family_count": sum(1 for item in feature_readiness if item.readiness_status == "blocked"),
            "v3_core_price_volume_ready": _family_status(feature_readiness, "v3_core_price_volume"),
            "v3_financial_statement_ready": _family_status(feature_readiness, "v3_financial_statement"),
            "v3_moneyflow_ready": _family_status(feature_readiness, "v3_moneyflow"),
            "v3_margin_ready": _family_status(feature_readiness, "v3_margin"),
            "v3_event_ready": _family_status(feature_readiness, "v3_event"),
            "v3_holder_ready": _family_status(feature_readiness, "v3_holder"),
            "v3_northbound_ready": _family_status(feature_readiness, "v3_northbound"),
            "can_run_core_alpha_factory": decision.can_run_core_alpha_factory,
            "can_run_v3_expanded_alpha_factory": decision.can_run_v3_expanded_alpha_factory,
            "can_run_financial_alpha_factory": decision.can_run_financial_alpha_factory,
            "can_run_event_alpha_factory": decision.can_run_event_alpha_factory,
            "data_quality_status": data_quality_gate.get("status", "") if data_quality_gate else "",
            "data_quality_blocker_count": int(data_quality_gate.get("blocker_count", 0) or 0) if data_quality_gate else 0,
            "data_quality_core_blocker_count": int(data_quality_gate.get("core_blocker_count", 0) or 0) if data_quality_gate else 0,
            "data_quality_expanded_blocker_count": int(data_quality_gate.get("expanded_blocker_count", 0) or 0) if data_quality_gate else 0,
            "data_quality_can_create_freeze": data_quality_gate.get("can_create_freeze") if data_quality_gate else None,
            "data_quality_can_run_expanded_alpha": data_quality_gate.get("can_run_expanded_alpha") if data_quality_gate else None,
        }
    )
    now = utc_now()
    return ResearchDataReadinessReport(
        report_id=f"research_readiness_{now.replace(':', '').replace('-', '')}",
        generated_at=now,
        profile_name=profile_name,
        data_dir=str(data_dir),
        dataset_checks=dataset_checks,
        feature_readiness=feature_readiness,
        decision=decision,
        summary=summary,
    )


def write_research_data_readiness_artifacts(report: ResearchDataReadinessReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_path = write_json_artifact(
        root / "research_data_readiness_report.json",
        report.to_dict(),
        "research_data_readiness_report",
        "research_data_readiness",
    )
    dataset_path = write_jsonl_artifact(
        root / "research_dataset_readiness.jsonl",
        [item.to_dict() for item in report.dataset_checks],
        "research_dataset_readiness",
        "research_data_readiness",
    )
    feature_path = write_json_artifact(
        root / "feature_readiness_catalog.json",
        {"feature_families": [item.to_dict() for item in report.feature_readiness], "summary": report.summary},
        "feature_readiness_catalog",
        "research_data_readiness",
    )
    decision_path = write_json_artifact(
        root / "research_readiness_decision.json",
        report.decision.to_dict(),
        "research_readiness_decision",
        "research_data_readiness",
    )
    remediations_path = write_jsonl_artifact(
        root / "research_readiness_remediations.jsonl",
        [{"message": item} for item in report.decision.required_remediations],
        "research_readiness_remediations",
        "research_data_readiness",
    )
    report_md = root / "research_data_readiness_report.md"
    report_md.write_text(_report_markdown(report), encoding="utf-8")
    feature_md = root / "feature_readiness_catalog.md"
    feature_md.write_text(_feature_markdown(report), encoding="utf-8")
    return {
        "research_data_readiness_report_path": str(report_path),
        "research_data_readiness_report_md_path": str(report_md),
        "research_dataset_readiness_path": str(dataset_path),
        "feature_readiness_catalog_path": str(feature_path),
        "feature_readiness_catalog_md_path": str(feature_md),
        "research_readiness_decision_path": str(decision_path),
        "research_readiness_remediations_path": str(remediations_path),
    }


def stdout_payload(report: ResearchDataReadinessReport, paths: dict[str, str] | None = None) -> dict:
    return {"status": report.decision.status, "summary": report.summary, "decision": report.decision.to_dict(), "paths": paths or {}}


def dumps(payload: dict, pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)


def _report_markdown(report: ResearchDataReadinessReport) -> str:
    lines = [
        "# Research Data Readiness Report",
        "",
        f"- Status: `{report.decision.status}`",
        f"- Core ready: `{report.decision.core_ready}`",
        f"- Matrix ready: `{report.decision.matrix_ready}`",
        f"- Alpha ready: `{report.decision.alpha_ready}`",
        f"- Blockers: {report.decision.blocker_count}",
        f"- Warnings: {report.decision.warning_count}",
        "",
        "| Dataset | Tier | Status | PIT safety | Records | Coverage |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for item in report.dataset_checks:
        coverage = "" if item.coverage_ratio is None else f"{item.coverage_ratio:.2%}"
        lines.append(f"| {item.dataset} | {item.tier} | {item.status} | {item.pit_safety} | {item.record_count} | {coverage} |")
    lines.extend(["", "## Required Remediations"])
    lines.extend(f"- {item}" for item in report.decision.required_remediations)
    lines.extend(["", "## Recommended Next Commands", "", "```bash", "\n\n".join(report.decision.recommended_next_commands), "```"])
    return "\n".join(lines) + "\n"


def _feature_markdown(report: ResearchDataReadinessReport) -> str:
    lines = [
        "# Feature Readiness Catalog",
        "",
        "| Family | Status | Required datasets | Blockers | Weak PIT warnings |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.feature_readiness:
        lines.append(
            f"| {item.feature_family} | {item.readiness_status} | {', '.join(item.required_datasets)} | {'; '.join(item.blockers)} | {'; '.join(item.weak_pit_warnings)} |"
        )
    return "\n".join(lines) + "\n"


def _family_status(feature_readiness, family: str) -> bool:
    for item in feature_readiness:
        if item.feature_family == family:
            return item.readiness_status in {"ready", "warning"} and not item.blockers
    return False


def _read_json(path: str | Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_data_quality_gate(decision, gate: dict):
    can_freeze = bool(gate.get("can_create_freeze", True))
    can_matrix = bool(gate.get("can_build_matrix", True))
    can_core = bool(gate.get("can_run_core_alpha", True))
    can_expanded = bool(gate.get("can_run_expanded_alpha", True))
    core_blockers = int(gate.get("core_blocker_count", 0) or 0)
    blockers = int(gate.get("blocker_count", 0) or 0)
    reason = str(gate.get("recommended_next_action") or "semantic data quality gate is not ready")
    remediations = list(decision.required_remediations)
    if not can_freeze and reason not in remediations:
        remediations.append(reason)
    status = decision.status
    blocked_reason = decision.blocked_reason
    next_action = decision.next_required_action
    if core_blockers or not can_freeze:
        status = "not_ready"
        blocked_reason = reason
        next_action = reason
    return replace(
        decision,
        status=status,
        can_create_freeze=decision.can_create_freeze and can_freeze,
        can_build_matrix=decision.can_build_matrix and can_matrix,
        can_run_core_alpha_factory=decision.can_run_core_alpha_factory and can_core,
        can_run_expanded_alpha_factory=decision.can_run_expanded_alpha_factory and can_expanded,
        can_run_v3_expanded_alpha_factory=decision.can_run_v3_expanded_alpha_factory and can_expanded,
        can_run_financial_alpha_factory=decision.can_run_financial_alpha_factory and can_expanded,
        can_run_event_alpha_factory=decision.can_run_event_alpha_factory and can_expanded,
        blocker_count=decision.blocker_count + blockers,
        required_remediations=remediations,
        blocked_reason=blocked_reason,
        next_required_action=next_action,
    )
