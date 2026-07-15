"""Immutable governed and modeled fee schedules for Task 055-B."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

FEE_SCHEDULE_SCHEMA = "task055b_fee_schedule_v1"
FEE_SCHEDULE_POINTER_SCHEMA = "task055b_fee_schedule_pointer_v1"
GOVERNED = "governed_statutory"
MODELED = "modeled_assumption"
VALID_EVIDENCE_CLASSES = {GOVERNED, MODELED}
VALID_SIDES = {"BUY", "SELL", "BOTH"}
VALID_RATE_TYPES = {"ad_valorem", "minimum_ad_valorem"}


class FeeScheduleError(RuntimeError):
    """Raised when a fee schedule is incomplete, mutable, or contradictory."""


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_fee_rules(*, acquired_at: str, statutory_sources: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    """Return the preregistered Task 055 fee rules matching the five scenarios.

    Statutory sources must provide a URL and immutable document hash. Broker
    commission, slippage, and impact are deliberately labeled modeled.
    """

    required_sources = {"stamp_duty", "transfer_fee"}
    if set(statutory_sources) != required_sources:
        raise FeeScheduleError("statutory_source_set_invalid")
    for name, source in statutory_sources.items():
        if not source.get("url") or not _is_sha256(source.get("document_sha256")):
            raise FeeScheduleError(f"statutory_source_proof_invalid:{name}")

    def governed(
        rule_id: str, fee_type: str, start: str, end: str, side: str, rate: float, source_name: str
    ) -> dict[str, Any]:
        source = statutory_sources[source_name]
        return {
            "rule_id": rule_id,
            "fee_type": fee_type,
            "effective_start": start,
            "effective_end": end,
            "market": "CN_A_SHARE_ALL",
            "side": side,
            "rate_type": "ad_valorem",
            "rate": rate,
            "minimum_cny": 0.0,
            "evidence_class": GOVERNED,
            "source_url": source["url"],
            "source_document_sha256": source["document_sha256"],
            "acquired_at": acquired_at,
        }

    def modeled(rule_id: str, fee_type: str, rate: float, minimum: float = 0.0) -> dict[str, Any]:
        return {
            "rule_id": rule_id,
            "fee_type": fee_type,
            "effective_start": "19000101",
            "effective_end": "99991231",
            "market": "CN_A_SHARE_ALL",
            "side": "BOTH",
            "rate_type": "minimum_ad_valorem" if minimum else "ad_valorem",
            "rate": rate,
            "minimum_cny": minimum,
            "evidence_class": MODELED,
            "model_name": "task055_preregistered_daily_bar_execution_proxy",
            "model_version": "v1",
            "calibration_status": "uncalibrated_modeled",
            "acquired_at": acquired_at,
        }

    return [
        governed("stamp_sell_pre_20230828", "stamp_duty", "19000101", "20230827", "SELL", 0.001, "stamp_duty"),
        governed("stamp_sell_from_20230828", "stamp_duty", "20230828", "99991231", "SELL", 0.0005, "stamp_duty"),
        governed("transfer_pre_20220429", "transfer_fee", "19000101", "20220428", "BOTH", 0.00002, "transfer_fee"),
        governed("transfer_from_20220429", "transfer_fee", "20220429", "99991231", "BOTH", 0.00001, "transfer_fee"),
        modeled("broker_commission", "commission", 0.0003, 5.0),
        modeled("slippage_proxy", "slippage", 0.0005),
        modeled("impact_proxy", "impact", 0.0005),
    ]


def publish_fee_schedule(
    *, output_root: str | Path, rules: Sequence[Mapping[str, Any]], acquired_at: str,
    policy_id: str = "cn_ashare_historical_fees_modeled_execution_v1",
) -> dict[str, Any]:
    normalized = [_normalize_rule(rule) for rule in rules]
    _validate_rule_set(normalized)
    semantic = {
        "schema_version": FEE_SCHEDULE_SCHEMA,
        "policy_id": str(policy_id),
        "currency": "CNY",
        "rate_unit": "fraction_of_notional",
        "money_rounding": "full_precision_ledger_verify_to_0.01_cny",
        "governed_fee_types": sorted({r["fee_type"] for r in normalized if r["evidence_class"] == GOVERNED}),
        "modeled_fee_types": sorted({r["fee_type"] for r in normalized if r["evidence_class"] == MODELED}),
        "rules": normalized,
        "acquired_at": str(acquired_at),
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"fee_schedule_{content_hash[:24]}"
    manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    root = Path(output_root)
    target = root / "generations" / generation_id
    root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads((target / "fee_schedule_manifest.json").read_text(encoding="utf-8"))
        if existing != manifest:
            raise FeeScheduleError("fee_schedule_content_address_collision")
    else:
        staging = Path(tempfile.mkdtemp(prefix=".task055b_fee.", dir=root))
        try:
            _write_json(staging / "fee_schedule_manifest.json", manifest)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    pointer = {
        "schema_version": FEE_SCHEDULE_POINTER_SCHEMA,
        "generation_id": generation_id,
        "content_hash": content_hash,
        "manifest": f"generations/{generation_id}/fee_schedule_manifest.json",
    }
    _atomic_json(root / "current.json", pointer)
    return manifest | {"root": str(target), "manifest_path": str(target / "fee_schedule_manifest.json")}


def validate_fee_schedule(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != FEE_SCHEDULE_SCHEMA:
        raise FeeScheduleError("fee_schedule_schema_invalid")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise FeeScheduleError("fee_schedule_content_hash_mismatch")
    if payload.get("generation_id") != f"fee_schedule_{payload['content_hash'][:24]}":
        raise FeeScheduleError("fee_schedule_generation_id_mismatch")
    rules = [_normalize_rule(rule) for rule in payload.get("rules") or ()]
    if rules != payload.get("rules"):
        raise FeeScheduleError("fee_schedule_rule_not_canonical")
    _validate_rule_set(rules)
    if manifest_path.name != "fee_schedule_manifest.json":
        raise FeeScheduleError("fee_schedule_manifest_name_invalid")
    return payload | {"root": str(manifest_path.parent), "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path)}


def fee_components_for_fill(
    fill: Mapping[str, Any], schedule: Mapping[str, Any], *, modeled_cost_multiplier: float = 1.0,
    zero_all_costs: bool = False,
) -> dict[str, float]:
    if zero_all_costs:
        return {name: 0.0 for name in ("commission", "stamp_duty", "transfer_fee", "slippage", "impact", "total")}
    date = _date(str(fill.get("date") or fill.get("execution_date") or ""), "fill_date")
    side = str(fill.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        raise FeeScheduleError("fill_side_invalid")
    notional = float(fill.get("notional", 0.0))
    if notional < 0:
        raise FeeScheduleError("fill_notional_invalid")
    components: dict[str, float] = {}
    for fee_type in ("commission", "stamp_duty", "transfer_fee", "slippage", "impact"):
        matches = [
            rule for rule in schedule.get("rules") or ()
            if rule["fee_type"] == fee_type
            and rule["effective_start"] <= date <= rule["effective_end"]
            and rule["side"] in {side, "BOTH"}
            and rule["market"] == "CN_A_SHARE_ALL"
        ]
        if len(matches) > 1:
            raise FeeScheduleError(f"fee_rule_ambiguous:{fee_type}:{date}:{side}")
        if not matches:
            components[fee_type] = 0.0
            continue
        rule = matches[0]
        value = notional * float(rule["rate"])
        if rule["rate_type"] == "minimum_ad_valorem":
            value = max(value, float(rule["minimum_cny"]))
        if rule["evidence_class"] == MODELED:
            value *= float(modeled_cost_multiplier)
        components[fee_type] = float(value)
    components["total"] = float(sum(components.values()))
    return components


def verify_fill_fees(
    fills: Sequence[Mapping[str, Any]], schedule: Mapping[str, Any], *, modeled_cost_multiplier: float = 1.0,
    zero_all_costs: bool = False, tolerance_cny: float = 0.01,
) -> list[str]:
    issues: list[str] = []
    for fill in fills:
        expected = fee_components_for_fill(
            fill, schedule, modeled_cost_multiplier=modeled_cost_multiplier, zero_all_costs=zero_all_costs
        )
        for name, value in expected.items():
            actual = float(fill.get("total_cost" if name == "total" else name, float("nan")))
            if abs(actual - value) > tolerance_cny:
                issues.append(f"fee_schedule_mismatch:{fill.get('fill_id')}:{name}:{actual}:{value}")
    return issues


def _normalize_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    evidence_class = str(rule.get("evidence_class") or "")
    normalized = {
        "rule_id": str(rule.get("rule_id") or ""),
        "fee_type": str(rule.get("fee_type") or ""),
        "effective_start": _date(str(rule.get("effective_start") or ""), "effective_start"),
        "effective_end": _date(str(rule.get("effective_end") or ""), "effective_end"),
        "market": str(rule.get("market") or ""),
        "side": str(rule.get("side") or "").upper(),
        "rate_type": str(rule.get("rate_type") or ""),
        "rate": float(rule.get("rate", 0.0)),
        "minimum_cny": float(rule.get("minimum_cny", 0.0)),
        "evidence_class": evidence_class,
        "acquired_at": str(rule.get("acquired_at") or ""),
    }
    if evidence_class == GOVERNED:
        normalized.update({
            "source_url": str(rule.get("source_url") or ""),
            "source_document_sha256": str(rule.get("source_document_sha256") or ""),
        })
    elif evidence_class == MODELED:
        normalized.update({
            "model_name": str(rule.get("model_name") or ""),
            "model_version": str(rule.get("model_version") or ""),
            "calibration_status": str(rule.get("calibration_status") or ""),
        })
    return normalized


def _validate_rule_set(rules: Sequence[Mapping[str, Any]]) -> None:
    if not rules or len({rule["rule_id"] for rule in rules}) != len(rules):
        raise FeeScheduleError("fee_rule_ids_invalid")
    required = {"commission", "stamp_duty", "transfer_fee", "slippage", "impact"}
    if {rule["fee_type"] for rule in rules} != required:
        raise FeeScheduleError("fee_type_set_invalid")
    for rule in rules:
        if not rule["rule_id"] or rule["evidence_class"] not in VALID_EVIDENCE_CLASSES:
            raise FeeScheduleError("fee_rule_identity_invalid")
        if rule["side"] not in VALID_SIDES or rule["rate_type"] not in VALID_RATE_TYPES:
            raise FeeScheduleError(f"fee_rule_contract_invalid:{rule['rule_id']}")
        if rule["effective_start"] > rule["effective_end"] or rule["rate"] < 0 or rule["minimum_cny"] < 0:
            raise FeeScheduleError(f"fee_rule_value_invalid:{rule['rule_id']}")
        if rule["evidence_class"] == GOVERNED:
            if not rule["source_url"] or not _is_sha256(rule["source_document_sha256"]):
                raise FeeScheduleError(f"governed_fee_source_invalid:{rule['rule_id']}")
        else:
            if not rule["model_name"] or not rule["model_version"] or rule["calibration_status"] != "uncalibrated_modeled":
                raise FeeScheduleError(f"modeled_fee_evidence_invalid:{rule['rule_id']}")
    for fee_type in required:
        relevant = [rule for rule in rules if rule["fee_type"] == fee_type]
        for side in ("BUY", "SELL"):
            applicable = [rule for rule in relevant if rule["side"] in {side, "BOTH"}]
            ordered = sorted(applicable, key=lambda row: row["effective_start"])
            for left, right in zip(ordered, ordered[1:]):
                if right["effective_start"] <= left["effective_end"]:
                    raise FeeScheduleError(f"fee_rule_overlap:{fee_type}:{side}")


def _resolve_manifest(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        row = json.loads(pointer.read_text(encoding="utf-8"))
        return value / str(row.get("manifest") or "")
    candidate = value / "fee_schedule_manifest.json"
    if candidate.is_file():
        return candidate
    raise FeeScheduleError("fee_schedule_manifest_missing")


def _date(value: str, field: str) -> str:
    digits = value.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise FeeScheduleError(f"fee_{field}_invalid")
    return digits


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    _write_json(temporary, value)
    os.replace(temporary, path)
