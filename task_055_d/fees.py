"""Rule-level immutable fee schedule v2 with document-backed verification."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_c.evidence import sha256_file

SCHEMA = "task055d_fee_schedule_v2"
STATUTORY = {"stamp_duty", "transfer_fee", "handling_fee"}
MODELED = {"commission", "slippage", "impact"}
COMPONENTS = STATUTORY | MODELED
MARKETS = {"SSE", "SZSE"}
SIDES = {"BUY", "SELL"}
OFFICIAL_HOST_SUFFIXES = (
    ".gov.cn",
    ".chinatax.gov.cn",
    ".sse.com.cn",
    ".szse.cn",
    ".chinaclear.cn",
)


class FeeScheduleV2Error(RuntimeError):
    pass


def publish_fee_schedule_v2(
    *, output_root: str | Path, document_root: str | Path, rules: Iterable[Mapping[str, Any]],
    simulation_start: str, simulation_end: str,
) -> dict[str, Any]:
    document_root_path = Path(document_root).resolve()
    normalized = [_normalize_rule(rule, document_root_path) for rule in rules]
    normalized.sort(key=lambda row: (row["component"], row["market"], row["side"], row["effective_start"], row["rule_id"]))
    _validate_rules(normalized, simulation_start, simulation_end)
    documents = _document_index(normalized, document_root_path)
    semantic = {
        "schema_version": SCHEMA,
        "status": "passed",
        "simulation_start": simulation_start,
        "simulation_end": simulation_end,
        "rules": normalized,
        "documents": documents,
    }
    content_hash = stable_json_hash(semantic)
    payload = semantic | {"content_hash": content_hash, "generation_id": f"fee_schedule_v2_{content_hash[:24]}"}
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055d_fee_v2.", dir=root))
    try:
        for document in documents:
            source = document_root_path / document["relative_path"]
            target = staging / "documents" / document["relative_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (staging / "fee_schedule_manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        target = root / "generations" / payload["generation_id"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = root / "current.json"
        temporary = root / ".current.json.tmp"
        temporary.write_text(json.dumps({"generation_id": payload["generation_id"], "content_hash": content_hash, "manifest": f"generations/{payload['generation_id']}/fee_schedule_manifest.json"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, pointer)
        return payload | {"manifest_path": str(target / "fee_schedule_manifest.json"), "root": str(target)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_fee_schedule_v2(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA or payload.get("status") != "passed":
        raise FeeScheduleV2Error("fee_schedule_v2_schema_or_status_invalid")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if stable_json_hash(semantic) != payload.get("content_hash"):
        raise FeeScheduleV2Error("fee_schedule_v2_content_hash_mismatch")
    root = manifest_path.parent
    for document in payload.get("documents", []):
        artifact = root / "documents" / document["relative_path"]
        if not artifact.is_file() or sha256_file(artifact) != document["sha256"]:
            raise FeeScheduleV2Error("fee_schedule_v2_document_sha_mismatch")
        _validate_document(artifact, document["official_url"], document["publisher"], document["effective_clause"])
    _validate_rules(payload.get("rules") or [], payload["simulation_start"], payload["simulation_end"], manifest_root=root)
    return payload | {"manifest_path": str(manifest_path)}


def fee_components_for_fill_v2(fill: Mapping[str, Any], schedule_path: str | Path, *, modeled_multiplier: float = 1.0, zero_modeled: bool = False) -> dict[str, float]:
    schedule = validate_fee_schedule_v2(schedule_path)
    date = str(fill["date"]).replace("-", "")
    market = str(fill["market"])
    side = str(fill["side"]).upper()
    notional = float(fill["notional"])
    quantity = float(fill.get("quantity", 0.0))
    result: dict[str, float] = {}
    for component in sorted(COMPONENTS):
        matching = [rule for rule in schedule["rules"] if rule["component"] == component and rule["market"] == market and rule["side"] == side and rule["effective_start"] <= date <= rule["effective_end"]]
        if len(matching) != 1:
            raise FeeScheduleV2Error(f"fee_rule_match_invalid:{component}:{market}:{side}:{date}")
        rule = matching[0]
        base = notional if rule["basis"] == "notional" else quantity
        value = 0.0 if rule["explicit_zero"] else max(float(rule["minimum_cny"]), base * float(rule["rate"]))
        if component in MODELED:
            value = 0.0 if zero_modeled else value * modeled_multiplier
        result[component] = _round(value, rule["rounding"])
    result["total"] = round(sum(result.values()), 2)
    return result


def _normalize_rule(rule: Mapping[str, Any], document_root: Path) -> dict[str, Any]:
    component = str(rule.get("component") or "")
    evidence_class = str(rule.get("evidence_class") or "")
    normalized = {
        "rule_id": str(rule.get("rule_id") or ""),
        "component": component,
        "market": str(rule.get("market") or ""),
        "side": str(rule.get("side") or "").upper(),
        "effective_start": _date(rule.get("effective_start")),
        "effective_end": _date(rule.get("effective_end")),
        "rate": float(rule.get("rate", 0.0)),
        "basis": str(rule.get("basis") or "notional"),
        "rounding": str(rule.get("rounding") or "cent_half_up"),
        "minimum_cny": float(rule.get("minimum_cny", 0.0)),
        "explicit_zero": bool(rule.get("explicit_zero", False)),
        "evidence_class": evidence_class,
        "publisher": str(rule.get("publisher") or ""),
        "official_url": str(rule.get("official_url") or ""),
        "document_relative_path": str(rule.get("document_relative_path") or ""),
        "document_sha256": str(rule.get("document_sha256") or ""),
        "retrieval_receipt": str(rule.get("retrieval_receipt") or ""),
        "page_or_clause": str(rule.get("page_or_clause") or ""),
        "effective_clause": str(rule.get("effective_clause") or ""),
        "model_name": str(rule.get("model_name") or ""),
        "model_version": str(rule.get("model_version") or ""),
        "calibration_status": str(rule.get("calibration_status") or ""),
    }
    if component in STATUTORY and normalized["document_relative_path"]:
        document = (document_root / normalized["document_relative_path"]).resolve()
        if document_root != document and document_root not in document.parents:
            raise FeeScheduleV2Error("fee_document_path_escape")
    return normalized


def _validate_rules(rules: list[Mapping[str, Any]], start: str, end: str, manifest_root: Path | None = None) -> None:
    if not rules or len({rule["rule_id"] for rule in rules}) != len(rules):
        raise FeeScheduleV2Error("fee_rule_identity_invalid")
    for rule in rules:
        if rule["component"] not in COMPONENTS or rule["market"] not in MARKETS or rule["side"] not in SIDES:
            raise FeeScheduleV2Error("fee_rule_scope_invalid")
        if rule["basis"] not in {"notional", "shares"} or rule["rounding"] not in {"cent_half_up", "none"}:
            raise FeeScheduleV2Error("fee_rule_calculation_contract_invalid")
        if rule["effective_start"] > rule["effective_end"] or rule["rate"] < 0 or rule["minimum_cny"] < 0:
            raise FeeScheduleV2Error("fee_rule_value_invalid")
        if rule["explicit_zero"] and (rule["rate"] != 0 or rule["minimum_cny"] != 0):
            raise FeeScheduleV2Error("fee_explicit_zero_rule_invalid")
        if rule["component"] in STATUTORY:
            if rule["evidence_class"] != "governed_official" or not all(rule[field] for field in ("publisher", "official_url", "document_relative_path", "document_sha256", "retrieval_receipt", "page_or_clause", "effective_clause")):
                raise FeeScheduleV2Error("statutory_fee_evidence_incomplete")
            if manifest_root is not None:
                document = manifest_root / "documents" / rule["document_relative_path"]
                if not document.is_file() or sha256_file(document) != rule["document_sha256"]:
                    raise FeeScheduleV2Error("statutory_fee_document_mismatch")
        else:
            if rule["evidence_class"] != "modeled" or not rule["model_name"] or not rule["model_version"] or rule["calibration_status"] != "uncalibrated_modeled":
                raise FeeScheduleV2Error("modeled_fee_evidence_invalid")
    for component in COMPONENTS:
        for market in MARKETS:
            for side in SIDES:
                scoped = sorted((rule for rule in rules if rule["component"] == component and rule["market"] == market and rule["side"] == side), key=lambda row: row["effective_start"])
                if not scoped or scoped[0]["effective_start"] > start or scoped[-1]["effective_end"] < end:
                    raise FeeScheduleV2Error(f"fee_rule_coverage_gap:{component}:{market}:{side}")
                cursor = start
                for rule in scoped:
                    if rule["effective_end"] < start or rule["effective_start"] > end:
                        continue
                    effective_start = max(start, rule["effective_start"])
                    if effective_start > cursor:
                        raise FeeScheduleV2Error(f"fee_rule_coverage_gap:{component}:{market}:{side}:{cursor}")
                    if effective_start < cursor:
                        raise FeeScheduleV2Error(f"fee_rule_overlap:{component}:{market}:{side}")
                    cursor = _next_date(rule["effective_end"])
                if cursor <= end:
                    raise FeeScheduleV2Error(f"fee_rule_coverage_gap:{component}:{market}:{side}:{cursor}")


def _document_index(rules: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    documents = {}
    for rule in rules:
        if rule["component"] not in STATUTORY:
            continue
        relative = rule["document_relative_path"]
        path = root / relative
        if not path.is_file() or sha256_file(path) != rule["document_sha256"]:
            raise FeeScheduleV2Error("fee_source_document_missing_or_mismatch")
        _validate_document(path, rule["official_url"], rule["publisher"], rule["effective_clause"])
        documents[relative] = {"relative_path": relative, "sha256": rule["document_sha256"], "official_url": rule["official_url"], "publisher": rule["publisher"], "retrieval_receipt": rule["retrieval_receipt"], "effective_clause": rule["effective_clause"]}
    return [documents[key] for key in sorted(documents)]


def _validate_document(path: Path, url: str, publisher: str, effective_clause: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or not any(host == suffix[1:] or host.endswith(suffix) for suffix in OFFICIAL_HOST_SUFFIXES):
        raise FeeScheduleV2Error("fee_source_url_not_official")
    data = path.read_bytes()
    text = data.decode("utf-8", errors="ignore")
    if len(data) < 256 or not publisher or not effective_clause or effective_clause not in text:
        raise FeeScheduleV2Error("fee_source_document_content_invalid")


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        row = json.loads(pointer.read_text(encoding="utf-8"))
        return value / row["manifest"]
    candidate = value / "fee_schedule_manifest.json"
    if candidate.is_file():
        return candidate
    raise FeeScheduleV2Error("fee_schedule_v2_manifest_missing")


def _date(value: Any) -> str:
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise FeeScheduleV2Error("fee_rule_date_invalid")
    return text


def _next_date(value: str) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(value, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")


def _round(value: float, policy: str) -> float:
    if policy == "none":
        return value
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
