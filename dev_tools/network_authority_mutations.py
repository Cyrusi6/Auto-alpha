from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from auto_alpha.platform.governance.network.verifier import (
    Task055KVerifierError,
    verify_candidate_semantics,
)


Mutation = Callable[[dict[str, Any], Mapping[str, Any]], None]


def run_mutation_matrix(
    *,
    anchor_path: str | Path,
    anchor_digest: str,
    evidence_path: str | Path,
) -> dict[str, Any]:
    anchor_file = Path(anchor_path)
    anchor_bytes = anchor_file.read_bytes()
    if hashlib.sha256(anchor_bytes).hexdigest() != anchor_digest:
        raise RuntimeError("task055kr2_external_oracle_digest_invalid")
    anchor = _object(anchor_bytes)
    candidate = _object(Path(evidence_path).read_bytes())
    positive = verify_candidate_semantics(anchor=anchor, candidate=candidate)
    results = []
    for name, mutation in _mutations():
        payload = copy.deepcopy(candidate)
        mutation(payload, anchor)
        _rehash_candidate(payload)
        try:
            verify_candidate_semantics(anchor=anchor, candidate=payload)
        except Task055KVerifierError as exc:
            results.append(
                {
                    "scenario": name,
                    "expected_block_layer": "fixed_anchor_semantic",
                    "actual_block_layer": _block_layer(str(exc)),
                    "blocked": True,
                    "exit_status": 2,
                    "blocker": str(exc),
                    "full_visible_hash_chain_recomputed": True,
                }
            )
        else:
            results.append(
                {
                    "scenario": name,
                    "expected_block_layer": "fixed_anchor_semantic",
                    "actual_block_layer": "none",
                    "blocked": False,
                    "exit_status": 0,
                    "blocker": None,
                    "full_visible_hash_chain_recomputed": True,
                }
            )
    if hashlib.sha256(anchor_file.read_bytes()).hexdigest() != anchor_digest:
        raise RuntimeError("task055kr2_external_oracle_mutated")
    if not all(row["blocked"] for row in results):
        raise RuntimeError("task055kr2_mutation_matrix_has_unblocked_scenario")
    return {
        "status": "passed",
        "positive_control": positive,
        "oracle_digest": anchor_digest,
        "oracle_unchanged": True,
        "scenario_count": len(results),
        "blocked_count": len(results),
        "results": results,
    }


def _mutations() -> list[tuple[str, Mutation]]:
    return [
        ("replace_all_stage_and_application_roots", _replace_all_stage_roots),
        ("replace_full_authority_to_verification_chain", _replace_full_chain),
        ("attacker_public_key_and_receipt_chain", _attacker_public_key),
        ("frontier_first_key", lambda row, _a: row["ordered_exact_daily_keys"][0].update({"trade_date": "20160725"})),
        ("frontier_middle_key", lambda row, _a: row["ordered_exact_daily_keys"][8].update({"trade_date": "20160101"})),
        ("request_fingerprint", lambda row, _a: row["ordered_exact_daily_keys"][0].update({"request_fingerprint": "1" * 64})),
        ("transport_identity", lambda row, _a: row["ordered_exact_daily_keys"][0].update({"transport_identity": "2" * 64})),
        ("evidence_use_identity", lambda row, _a: row["ordered_exact_daily_keys"][0].update({"evidence_use_identity": "3" * 64})),
        ("attempt_identity", lambda row, _a: row["synthetic_receipt_attestations"]["positive"].update({"attempt_id": "4" * 64})),
        ("logical_budget", lambda row, _a: row["budgets"].update({"logical_requests": 18})),
        ("unique_budget", lambda row, _a: row["budgets"].update({"unique_security_dates": 18})),
        ("http_budget", lambda row, _a: row["budgets"]["limits"].update({"physical_attempts": 161})),
        ("credential_budget", lambda row, _a: row["budgets"]["limits"].update({"credential_reads": 2})),
        ("network_flag", lambda row, _a: row.update({"network_authorized": True})),
        ("operational_flag", lambda row, _a: row.update({"operational_state_unproven": False})),
        ("contains_credentials", lambda row, _a: row.update({"contains_credentials": True})),
        ("contains_market_values", lambda row, _a: row.update({"contains_market_values": True})),
        ("contains_absolute_paths", lambda row, _a: row.update({"contains_absolute_paths": True})),
        ("holdout_flag", lambda row, _a: row.update({"prospective_holdout_accessed": True})),
        ("delete_artifact_role", _delete_role),
        ("add_artifact_role", _add_role),
        ("duplicate_artifact_role", _duplicate_role),
        ("reorder_artifact_roles", lambda row, _a: row["artifact_catalog"].reverse()),
        ("receipt_payload", lambda row, _a: row["synthetic_receipt_attestations"]["positive"].update({"response_payload_hash": "5" * 64})),
        ("receipt_tls", lambda row, _a: row["synthetic_receipt_attestations"]["positive"].update({"tls_attestation_hash": "6" * 64})),
        ("empty_semantics", lambda row, _a: row["synthetic_receipt_attestations"]["empty"].update({"empty_response_semantics": "official_no_trade"})),
        ("synthetic_promoted", lambda row, _a: row.update({"production_execution_ancestor": True})),
        ("blocked_authorization_rewrapped_ready", lambda row, _a: row.update({"authorization_eligible": True, "executable": True})),
        ("implementation_baseline", lambda row, a: row.update({"implementation_commit": a["release_topology"]["baseline_commit"]})),
        ("implementation_evidence_commit", lambda row, a: row.update({"implementation_commit": a["release_topology"]["evidence_commit"]})),
        ("implementation_anchor_placeholder", lambda row, _a: row.update({"implementation_commit": "a" * 40})),
        ("source_root", lambda row, _a: row.update({"source_root": "7" * 64})),
        ("broker_contract", lambda row, _a: row.update({"broker_contract_hash": "8" * 64})),
        ("cross_lineage", lambda row, _a: row["cross_lineage"]["checkpoint"].update({"native_rehearsal": "9" * 64})),
    ]


def _replace_all_stage_roots(row: dict[str, Any], _anchor: Mapping[str, Any]) -> None:
    row["application_role_roots"] = {
        key: hashlib.sha256(f"forged:{key}".encode()).hexdigest()
        for key in row["application_role_roots"]
    }
    _replace_full_chain(row, _anchor)


def _replace_full_chain(row: dict[str, Any], _anchor: Mapping[str, Any]) -> None:
    replacements = {
        role: hashlib.sha256(f"forged:{role}".encode()).hexdigest()
        for role in row["lineage"]
    }
    row["lineage"] = replacements
    for artifact in row["artifact_catalog"]:
        artifact["content_hash"] = replacements[artifact["role"]]
        artifact["sha256"] = hashlib.sha256(
            f"bytes:{artifact['role']}".encode()
        ).hexdigest()
    cross = row["cross_lineage"]
    for section in cross.values():
        if not isinstance(section, dict):
            continue
        for key in list(section):
            if key in replacements:
                section[key] = replacements[key]
            elif key == "report":
                section[key] = replacements.get("final_report", section[key])
    row["artifact_catalog_root"] = _hash(row["artifact_catalog"])


def _attacker_public_key(row: dict[str, Any], anchor: Mapping[str, Any]) -> None:
    del anchor
    for receipt in row["synthetic_receipt_attestations"].values():
        receipt["broker_public_key_sha256"] = "b" * 64
        receipt["receipt_content_hash"] = "c" * 64
        receipt["reservation_content_hash"] = "d" * 64


def _delete_role(row: dict[str, Any], _anchor: Mapping[str, Any]) -> None:
    removed = row["artifact_catalog"].pop()
    row["lineage"].pop(removed["role"], None)


def _add_role(row: dict[str, Any], _anchor: Mapping[str, Any]) -> None:
    row["artifact_catalog"].append(
        {
            "role": "forged_extra_role",
            "relative_path": "forged/extra.json",
            "sha256": "e" * 64,
            "content_hash": "f" * 64,
        }
    )
    row["lineage"]["forged_extra_role"] = "f" * 64


def _duplicate_role(row: dict[str, Any], _anchor: Mapping[str, Any]) -> None:
    row["artifact_catalog"][-1]["role"] = row["artifact_catalog"][0]["role"]


def _rehash_candidate(row: dict[str, Any]) -> None:
    if "artifact_catalog" in row:
        row["artifact_catalog_root"] = _hash(row["artifact_catalog"])
    if "ordered_exact_daily_keys" in row:
        row["ordered_key_root"] = _hash(row["ordered_exact_daily_keys"])
    semantic = {key: value for key, value in row.items() if key != "content_hash"}
    row["content_hash"] = _hash(semantic)


def _block_layer(message: str) -> str:
    if "semantic_mismatch" in message:
        return "fixed_anchor_semantic"
    if "artifact" in message:
        return "artifact_role_closure"
    if "ordered" in message or "canary" in message:
        return "fixed_frontier_contract"
    return "fixed_anchor_contract"


def _object(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("task055kr2_json_object_required")
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR2 fixed-anchor mutation matrix")
    parser.add_argument("--anchor-file", required=True)
    parser.add_argument("--anchor-digest", required=True)
    parser.add_argument("--candidate-evidence", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = run_mutation_matrix(
        anchor_path=args.anchor_file,
        anchor_digest=args.anchor_digest,
        evidence_path=args.candidate_evidence,
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
