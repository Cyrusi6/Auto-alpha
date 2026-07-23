from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from task_055_k.verifier import (
    Task055KVerifierError,
    verify_mutated_payload_against_trusted_evidence,
)


Mutation = Callable[[dict[str, Any]], None]


def run_mutation_matrix(
    *, evidence_path: str | Path, repository_root: str | Path
) -> dict[str, Any]:
    trusted = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    results = []
    for name, mutation in _mutations():
        payload = copy.deepcopy(trusted)
        mutation(payload)
        _rehash_visible(payload)
        try:
            verify_mutated_payload_against_trusted_evidence(
                payload,
                trusted_payload=trusted,
                repository_root=repository_root,
            )
        except Task055KVerifierError as exc:
            results.append({"scenario": name, "blocked": True, "blocker": str(exc)})
        else:
            results.append({"scenario": name, "blocked": False, "blocker": None})
    if not all(row["blocked"] for row in results):
        raise RuntimeError("task055kr_mutation_matrix_has_unblocked_scenario")
    return {
        "status": "passed",
        "scenario_count": len(results),
        "blocked_count": sum(row["blocked"] for row in results),
        "results": results,
    }


def _mutations() -> list[tuple[str, Mutation]]:
    return [
        ("delete_application_role", lambda row: row["application_role_roots"].pop("valuation")),
        ("add_application_role", lambda row: row["application_role_roots"].update({"forged": "0" * 64})),
        ("modify_second_frontier_key", _modify_second_key),
        ("logical_budget_drift", lambda row: row["budgets"].update({"logical_requests": 18})),
        ("unique_budget_drift", lambda row: row["budgets"].update({"unique_security_dates": 18})),
        ("http_budget_drift", lambda row: row["budgets"]["limits"].update({"physical_attempts": 161})),
        ("credential_budget_drift", lambda row: row["budgets"]["limits"].update({"credential_reads": 2})),
        ("operational_flag_forged", lambda row: row.update({"operational_state_unproven": False})),
        ("contains_credentials_forged", lambda row: row.update({"contains_credentials": True})),
        ("contains_market_values_forged", lambda row: row.update({"contains_market_values": True})),
        ("contains_absolute_paths_forged", lambda row: row.update({"contains_absolute_paths": True})),
        ("authority_root_substitution", lambda row: _replace_role(row, "candidate_authority", "1" * 64)),
        ("checkpoint_root_substitution", lambda row: _replace_role(row, "candidate_checkpoint", "2" * 64)),
        ("seal_root_substitution", lambda row: _replace_role(row, "final_candidate_seal", "3" * 64)),
        ("broker_contract_substitution", lambda row: row.update({"broker_contract_hash": "a" * 64})),
        ("receipt_root_substitution", lambda row: row["synthetic_receipt_attestations"]["positive"].update({"receipt_content_hash": "b" * 64})),
        ("reservation_root_substitution", lambda row: row["synthetic_receipt_attestations"]["positive"].update({"reservation_content_hash": "c" * 64})),
        ("receipt_public_key_substitution", lambda row: row["synthetic_receipt_attestations"]["positive"].update({"broker_public_key_sha256": "4" * 64})),
        ("receipt_payload_substitution", lambda row: row["synthetic_receipt_attestations"]["positive"].update({"response_payload_hash": "5" * 64})),
        ("receipt_tls_substitution", lambda row: row["synthetic_receipt_attestations"]["positive"].update({"tls_attestation_hash": "6" * 64})),
        ("receipt_attempt_substitution", lambda row: row["synthetic_receipt_attestations"]["positive"].update({"attempt_id": "7" * 64})),
        ("empty_semantics_substitution", lambda row: row["synthetic_receipt_attestations"]["empty"].update({"empty_response_semantics": "official_no_trade"})),
        ("stage_output_substitution", lambda row: row["application_role_roots"].update({"firewall_sentinel": "8" * 64})),
        ("cross_lineage_substitution", lambda row: row["cross_lineage"]["checkpoint"].update({"native_rehearsal": "d" * 64})),
        ("synthetic_ancestor_promoted", lambda row: row.update({"production_execution_ancestor": True})),
        ("implementation_commit_substitution", lambda row: row.update({"implementation_commit": "0" * 40})),
        ("source_root_substitution", lambda row: row.update({"source_root": "9" * 64})),
        ("blocked_authorization_rewrapped_ready", lambda row: row.update({"authorization_eligible": True, "status": "task055k_single_canary_engineering_ready_waiting_operator_authorization_no_network_executed"})),
        ("delete_artifact_role", _delete_catalog_role),
        ("add_artifact_role", _add_catalog_role),
        ("duplicate_artifact_role", _duplicate_catalog_role),
    ]


def _modify_second_key(row: dict[str, Any]) -> None:
    row["ordered_exact_daily_keys"][1]["trade_date"] = "20160101"
    row["ordered_key_root"] = _hash(row["ordered_exact_daily_keys"])


def _replace_role(row: dict[str, Any], role: str, value: str) -> None:
    for item in row["artifact_catalog"]:
        if item["role"] == role:
            item["content_hash"] = value
    row["lineage"][role] = value
    checkpoint = row["cross_lineage"]["checkpoint"]
    execution = row["cross_lineage"]["final_seal_execution"]
    report = row["cross_lineage"]["report"]
    verification = row["cross_lineage"]["final_verification"]
    aliases = {
        "candidate_authority": "candidate_authority",
        "candidate_checkpoint": "candidate_checkpoint",
        "final_candidate_seal": None,
    }
    alias = aliases[role]
    if alias:
        if alias in checkpoint:
            checkpoint[alias] = value
        if alias in execution:
            execution[alias] = value
        if alias in report:
            report[alias] = value
        if alias in verification:
            verification[alias] = value


def _duplicate_catalog_role(row: dict[str, Any]) -> None:
    row["artifact_catalog"][-1]["role"] = row["artifact_catalog"][0]["role"]


def _delete_catalog_role(row: dict[str, Any]) -> None:
    removed = row["artifact_catalog"].pop()
    row["lineage"].pop(removed["role"], None)


def _add_catalog_role(row: dict[str, Any]) -> None:
    row["artifact_catalog"].append(
        {
            "role": "forged_extra_role",
            "relative_path": "validation_runs/task_055_k_kr_20260723/forged.json",
            "sha256": "e" * 64,
            "content_hash": "f" * 64,
        }
    )
    row["lineage"]["forged_extra_role"] = "f" * 64


def _rehash_visible(row: dict[str, Any]) -> None:
    row["artifact_catalog_root"] = _hash(row["artifact_catalog"])
    semantic = {key: value for key, value in row.items() if key != "content_hash"}
    row["content_hash"] = _hash(semantic)


def _hash(value: Any) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR self-hash mutation matrix")
    parser.add_argument("evidence")
    parser.add_argument("--repository-root", default=".")
    args = parser.parse_args(argv)
    result = run_mutation_matrix(
        evidence_path=args.evidence,
        repository_root=args.repository_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
