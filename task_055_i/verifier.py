from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPECTED_PARENT_SEAL = "6c32e777374319026c1db23b10686bf9c245595b170a76f8e29e2f8259ca9b72"
EXPECTED_PARENT_GIT = "2ef732ecb20eebcbf0dede46a058cb5e1730ea2bea94a98f02afac9d09b2fa20"
EXPECTED_PLAN = "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae"
EXPECTED_CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
    "transport_hash": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_hash": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}


class ScrubbedEvidenceError(RuntimeError):
    pass


def verify_scrubbed_evidence(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ScrubbedEvidenceError("task055i_scrubbed_evidence_not_object")
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if _canonical_hash(semantic) != payload.get("content_hash"):
        raise ScrubbedEvidenceError("task055i_scrubbed_content_hash_mismatch")
    if payload.get("schema_version") != "task055i_scrubbed_execution_authorization_v1":
        raise ScrubbedEvidenceError("task055i_scrubbed_schema_invalid")
    if payload.get("status") != "single_canary_execution_ready_no_network_executed":
        raise ScrubbedEvidenceError("task055i_scrubbed_status_invalid")
    if payload.get("parent_authorization_seal_hash") != EXPECTED_PARENT_SEAL:
        raise ScrubbedEvidenceError("task055i_scrubbed_parent_seal_invalid")
    if payload.get("parent_git_evidence_hash") != EXPECTED_PARENT_GIT:
        raise ScrubbedEvidenceError("task055i_scrubbed_parent_git_invalid")
    if payload.get("single_request_plan_hash") != EXPECTED_PLAN or payload.get("canary") != EXPECTED_CANARY:
        raise ScrubbedEvidenceError("task055i_scrubbed_canary_invalid")
    budgets = payload.get("budgets") or {}
    limits = budgets.get("limits") or {}
    if (
        int(budgets.get("physical_attempts") or 0) != 0
        or int(limits.get("unique_security_dates") or 0) != 64
        or int(limits.get("logical_requests") or 0) != 128
        or int(limits.get("physical_attempts") or 0) != 160
    ):
        raise ScrubbedEvidenceError("task055i_scrubbed_budget_invalid")
    execution = payload.get("network_execution") or {}
    if any(int(execution.get(key) or 0) for key in ("credential_read_count", "tushare_request_count", "other_network_request_count")):
        raise ScrubbedEvidenceError("task055i_scrubbed_offline_counter_invalid")
    if execution.get("prospective_holdout_accessed") is not False:
        raise ScrubbedEvidenceError("task055i_scrubbed_holdout_boundary_invalid")
    if payload.get("resume_authorized") is not False or payload.get("batch_authorized") is not False:
        raise ScrubbedEvidenceError("task055i_scrubbed_resume_boundary_invalid")
    if payload.get("operational_state_unproven") is not True:
        raise ScrubbedEvidenceError("task055i_scrubbed_operational_boundary_invalid")
    if any(payload.get(key) is not False for key in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
        raise ScrubbedEvidenceError("task055i_scrubbed_downstream_readiness_invalid")
    catalog = list(payload.get("artifact_catalog") or ())
    if not catalog or len({row.get("role") for row in catalog}) != len(catalog):
        raise ScrubbedEvidenceError("task055i_scrubbed_artifact_catalog_invalid")
    for row in catalog:
        if not _hash64(row.get("sha256")) or not _hash64(row.get("content_hash")):
            raise ScrubbedEvidenceError("task055i_scrubbed_artifact_hash_invalid")
    if _canonical_hash(catalog) != payload.get("artifact_catalog_root"):
        raise ScrubbedEvidenceError("task055i_scrubbed_artifact_catalog_root_invalid")
    for key in ("semantic_source_root", "runtime_authority_content_hash", "execution_authorization_content_hash", "rehearsal_content_hash"):
        if not _hash64(payload.get(key)):
            raise ScrubbedEvidenceError(f"task055i_scrubbed_hash_invalid:{key}")
    encoded = json.dumps(payload, sort_keys=True)
    forbidden = ("/home/", "TUSHARE_TOKEN", "credential_file", "token_suffix", "token_hash")
    if any(value in encoded for value in forbidden):
        raise ScrubbedEvidenceError("task055i_scrubbed_forbidden_content")
    return payload | {"verified": True, "verification_hash": _canonical_hash(payload)}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _hash64(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone Task 055-I scrubbed evidence verifier")
    parser.add_argument("path")
    args = parser.parse_args(argv)
    try:
        result = verify_scrubbed_evidence(args.path)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": "passed", "verification_hash": result["verification_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
