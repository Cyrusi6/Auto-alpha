from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping

from task_055_g.fees import independent_verify_fee_schedule, validate_fee_schedule_v2

from .contracts import FEE_ATTESTATION_SCHEMA, OFFICIAL_DOCUMENT_IDS
from .io import canonical_hash, publish_generation, sha256_file, validate_generation


PRODUCTION_SPEC_HASH = "49ec200524518ee5026007dcd7c27d4011533b58d39f561f55a7bc13d6f9ce5f"
ALL_COMPONENTS = (
    "commission",
    "stamp_duty",
    "transfer_fee",
    "handling_fee",
    "securities_management_fee",
    "slippage",
    "impact",
)
MODELED_COMPONENTS = {"commission", "slippage", "impact"}
PASS_THROUGH_COMPONENTS = {"handling_fee", "securities_management_fee"}


class Task055HFeeError(RuntimeError):
    pass


def attest_fee_schedule(schedule_path: str | Path, output_root: str | Path) -> dict[str, Any]:
    schedule = validate_fee_schedule_v2(schedule_path)
    independent = independent_verify_fee_schedule(schedule=schedule_path)
    root = Path(schedule["manifest_path"]).parent
    native = schedule.get("native_artifacts") or {}
    plan_path = root / str(native.get("plan") or "")
    acquisition_path = root / str(native.get("acquisition") or "")
    if not plan_path.is_file() or not acquisition_path.is_file():
        raise Task055HFeeError("fee_native_plan_or_acquisition_missing")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    documents = list(plan.get("documents") or ())
    extractors = list(plan.get("extractors") or ())
    actual_spec_hash = canonical_hash({"documents": documents, "extractors": extractors})
    if actual_spec_hash != PRODUCTION_SPEC_HASH:
        raise Task055HFeeError("fee_production_spec_hash_mismatch")
    document_ids = tuple(str(row.get("document_id")) for row in documents)
    if document_ids != OFFICIAL_DOCUMENT_IDS:
        raise Task055HFeeError("fee_production_document_set_or_order_mismatch")
    acquired = list(acquisition.get("documents") or ())
    acquired_by_id = {str(row.get("document_id")): row for row in acquired}
    if set(acquired_by_id) != set(OFFICIAL_DOCUMENT_IDS):
        raise Task055HFeeError("fee_acquired_document_set_mismatch")
    document_catalog = []
    for document_id in OFFICIAL_DOCUMENT_IDS:
        row = acquired_by_id[document_id]
        relative = Path(str(row.get("artifact_relative_path") or ""))
        document = acquisition_path.parent / relative
        if not document.is_file() or document.is_symlink() or sha256_file(document) != row.get("sha256"):
            raise Task055HFeeError(f"fee_document_bytes_mismatch:{document_id}")
        document_catalog.append({
            "document_id": document_id,
            "sha256": row["sha256"],
            "size_bytes": document.stat().st_size,
            "publisher": row.get("publisher"),
            "transport_receipt_hash": row.get("transport_receipt_hash"),
        })
    rules = [dict(row) for row in schedule.get("rules") or ()]
    official = [row for row in rules if row.get("evidence_class") != "uncalibrated_modeled"]
    modeled = [row for row in rules if row.get("evidence_class") == "uncalibrated_modeled"]
    if len(official) != 28 or len(modeled) != 12:
        raise Task055HFeeError(f"fee_rule_evidence_count_invalid:{len(official)}:{len(modeled)}")
    projected_rules = []
    for row in rules:
        projected = dict(row)
        if projected.get("component") in PASS_THROUGH_COMPONENTS:
            projected["evidence_class"] = "official_rate_modeled_pass_through"
        projected_rules.append(projected)
    evidence_counts = Counter(str(row.get("evidence_class")) for row in projected_rules)
    semantic = {
        "schema_version": FEE_ATTESTATION_SCHEMA,
        "status": "passed",
        "production_spec_hash": PRODUCTION_SPEC_HASH,
        "schedule_content_hash": schedule["content_hash"],
        "schedule_manifest_sha256": sha256_file(schedule["manifest_path"]),
        "independent_verification_content_hash": independent["content_hash"],
        "policy_seal_hash": schedule["policy_seal_hash"],
        "document_count": 7,
        "document_catalog": document_catalog,
        "official_rate_or_statutory_interval_record_count": 28,
        "uncalibrated_modeled_record_count": 12,
        "evidence_counts": dict(sorted(evidence_counts.items())),
        "projected_rules_root": canonical_hash(projected_rules),
        "commission_interpretations": {
            "net_commission_3bp": {
                "commission_contract": "3bp_exclusive_of_official_rate_modeled_pass_through",
                "pass_through_components_charged_separately": True,
                "certification_ready": False,
            },
            "all_in_commission_3bp": {
                "commission_contract": "3bp_inclusive_of_handling_and_securities_management_pass_through",
                "pass_through_components_charged_separately": False,
                "certification_ready": False,
            },
        },
    }
    return publish_generation(
        output_root,
        prefix="fee_attestation",
        manifest_name="fee_attestation.json",
        semantic=semantic,
    )


def validate_fee_attestation(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=FEE_ATTESTATION_SCHEMA, manifest_name="fee_attestation.json")
    if payload.get("production_spec_hash") != PRODUCTION_SPEC_HASH:
        raise Task055HFeeError("fee_attestation_spec_hash_invalid")
    if payload.get("official_rate_or_statutory_interval_record_count") != 28 or payload.get("uncalibrated_modeled_record_count") != 12:
        raise Task055HFeeError("fee_attestation_rule_counts_invalid")
    return payload


class FeeProjectionCalculator:
    """Independent fee evaluator with explicit commission interpretation."""

    def __init__(self, schedule_path: str | Path, *, commission_mode: str) -> None:
        self.schedule = validate_fee_schedule_v2(schedule_path)
        if commission_mode not in {"net_commission_3bp", "all_in_commission_3bp"}:
            raise Task055HFeeError("commission_mode_invalid")
        self.commission_mode = commission_mode
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in self.schedule["rules"]:
            grouped[(str(row["component"]), str(row["market"]), str(row["side"]))].append(dict(row))
        self.rules = {key: tuple(sorted(rows, key=lambda row: row["effective_start"])) for key, rows in grouped.items()}

    def calculate(
        self,
        *,
        date: str,
        market: str,
        side: str,
        notional: float,
        shares: int,
        zero_all_costs: bool,
        modeled_multiplier: float,
    ) -> dict[str, float]:
        if zero_all_costs:
            return {component: 0.0 for component in ALL_COMPONENTS} | {"total": 0.0}
        values: dict[str, float] = {}
        for component in ALL_COMPONENTS:
            matches = [
                row for row in self.rules.get((component, market, side), ())
                if str(row["effective_start"]) <= date <= str(row["effective_end"])
            ]
            if len(matches) != 1:
                raise Task055HFeeError(f"fee_rule_match_invalid:{component}:{market}:{side}:{date}")
            row = matches[0]
            base = Decimal(str(notional)) if row["basis"] == "notional" else Decimal(int(shares))
            amount = Decimal("0") if row["explicit_zero"] else max(Decimal(str(row["minimum_cny"])), base * Decimal(str(row["rate"])))
            if component in MODELED_COMPONENTS:
                amount *= Decimal(str(modeled_multiplier))
            if self.commission_mode == "all_in_commission_3bp" and component in PASS_THROUGH_COMPONENTS:
                amount = Decimal("0")
            if not amount.is_finite() or amount < 0 or not math.isfinite(float(notional)):
                raise Task055HFeeError("fee_calculation_numeric_invalid")
            values[component] = float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        values["total"] = float(
            sum((Decimal(str(value)) for value in values.values()), Decimal("0")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        return values
