"""Independent Task 054-C bundle, sentinel, seal, and replay verifier."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from validation_campaign_store.replay_evidence import compare_replay_evidence, validate_task054_replay_evidence

from .bundle import validate_bundle
from .factor_store import validate_normalized_replay_store
from .run import validate_sentinel
from .seal import validate_pre_gpu_seal
from .validators import canonical_hash, sha256_file


def verify_task054c(
    *,
    bundle_manifest: str | Path,
    sentinel_manifest: str | Path,
    seal_manifest: str | Path,
    output_path: str | Path,
    primary_evidence_paths: list[str] | None = None,
    sibling_evidence_paths: list[str] | None = None,
    resume_evidence_paths: list[str] | None = None,
    resume_summary_path: str | Path | None = None,
    resume_state_path: str | Path | None = None,
) -> dict[str, Any]:
    bundle = validate_bundle(bundle_manifest)
    normalized = validate_normalized_replay_store(
        bundle["artifact_paths"]["normalized_store_root"], expected_ids=bundle["exact20_ids"]
    )
    sentinel_path = Path(sentinel_manifest)
    sentinel = validate_sentinel(sentinel_path, root=sentinel_path.parent)
    seal = validate_pre_gpu_seal(seal_manifest)
    if seal["bundle_hash"] != bundle["content_hash"] or seal["stages"]["sentinel"]["content_hash"] != sentinel["content_hash"]:
        raise RuntimeError("final_verifier_seal_lineage_mismatch")

    replay: dict[str, Any] = {"executed": False, "verified": False}
    groups = [primary_evidence_paths or [], sibling_evidence_paths or [], resume_evidence_paths or []]
    if any(groups):
        if not all(len(group) == 4 for group in groups):
            raise RuntimeError("final_verifier_replay_requires_three_exact_four_shard_groups")
        primary = validate_task054_replay_evidence(groups[0], bundle["exact20_ids"], require_uncached_materialization=True)
        sibling = validate_task054_replay_evidence(groups[1], bundle["exact20_ids"], require_uncached_materialization=True)
        resume = validate_task054_replay_evidence(groups[2], bundle["exact20_ids"], require_uncached_materialization=False)
        primary_lineage = _validate_replay_bundle_lineage(groups[0], bundle_manifest, seal_manifest)
        sibling_lineage = _validate_replay_bundle_lineage(groups[1], bundle_manifest, seal_manifest)
        resume_lineage = _validate_replay_bundle_lineage(groups[2], bundle_manifest, seal_manifest)
        if primary_lineage["computation_identity"] != sibling_lineage["computation_identity"] or primary_lineage != resume_lineage:
            raise RuntimeError("final_verifier_replay_bundle_lineage_mismatch")
        comparison = compare_replay_evidence(primary["shards"], sibling["shards"])
        if comparison.get("deterministic") is not True:
            raise RuntimeError("final_verifier_uncached_replay_mismatch")
        if any(not shard.get("terminal_outputs") for shard in resume["shards"]):
            raise RuntimeError("final_verifier_resume_terminal_outputs_missing")
        resume_summary = _validate_resume_summary(
            resume_summary_path,
            expected_candidate_ids=bundle["exact20_ids"],
            expected_evidence_hashes=[shard["evidence_hash"] for shard in resume["shards"]],
            resume_state_path=resume_state_path,
        )
        replay = {
            "executed": True,
            "verified": True,
            "primary_truth_hash": primary["replay_truth_hash"],
            "sibling_truth_hash": sibling["replay_truth_hash"],
            "resume_truth_hash": resume["replay_truth_hash"],
            "deterministic_comparison": comparison,
            "status_counts": primary["status_counts"],
            "physical_gpu_uuid_count": primary["physical_gpu_uuid_count"],
            "immutable_resume_4_of_4": True,
            "resume_summary_sha256": sha256_file(resume_summary_path),
            "resume_bundle_hash": resume_summary["replay_bundle_hash"],
            "replay_computation_identity": primary_lineage["computation_identity"],
        }

    semantic = {
        "schema_version": "task054c_final_verification_v1",
        "status": (
            "task054c_engineering_baseline_completed_historical_selection_contaminated_certification_blocked"
            if replay["verified"] else "task054c_engineering_baseline_blocked"
        ),
        "bundle_hash": bundle["content_hash"],
        "normalized_store_content_hash": normalized["content_hash"],
        "exact20_identity_root": bundle["exact20_identity_root"],
        "sentinel_hash": sentinel["content_hash"],
        "seal_hash": seal["seal_hash"],
        "replay": replay,
        "historical_selection_contaminated": True,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_queue_count": 0,
        "portfolio_queue_count": 0,
        "paper_queue_count": 0,
        "live_queue_count": 0,
        "source_artifact_sha256": {
            "bundle": sha256_file(bundle_manifest),
            "sentinel": sha256_file(sentinel_manifest),
            "seal": sha256_file(seal_manifest),
        },
    }
    semantic["content_hash"] = canonical_hash(semantic)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(semantic, indent=2, sort_keys=True) + "\n")
    return semantic


def _validate_resume_summary(
    path: str | Path | None,
    *,
    expected_candidate_ids: list[str],
    expected_evidence_hashes: list[str],
    resume_state_path: str | Path | None,
) -> dict[str, Any]:
    if path is None:
        raise RuntimeError("final_verifier_resume_summary_required")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    checks = payload.get("resume_checks") or {}
    if (
        payload.get("status") != "success"
        or payload.get("execution_mode") != "resume_4_of_4"
        or payload.get("immutable_resume_4_of_4") is not True
        or payload.get("first_run") is not False
        or int(payload.get("shard_count", -1)) != 4
        or int(payload.get("candidate_count", -1)) != len(expected_candidate_ids)
        or set(checks) != {"0", "1", "2", "3"}
        or any(row.get("valid") is not True or row.get("reason") != "valid" for row in checks.values())
    ):
        raise RuntimeError("final_verifier_resume_summary_invalid")
    if sorted(payload.get("shard_evidence_hashes") or []) != sorted(expected_evidence_hashes):
        raise RuntimeError("final_verifier_resume_evidence_hash_mismatch")
    observed = sorted(
        str(candidate_id)
        for shard in payload.get("shards") or []
        for candidate_id in shard.get("candidate_ids") or []
    )
    if observed != sorted(expected_candidate_ids):
        raise RuntimeError("final_verifier_resume_candidate_set_mismatch")
    if resume_state_path is None:
        raise RuntimeError("final_verifier_resume_state_required")
    state = json.loads(Path(resume_state_path).read_text(encoding="utf-8"))
    jobs = state.get("jobs") or {}
    if len(jobs) != 4 or any(
        row.get("status") != "success" or row.get("return_code") != 0 or row.get("attempts") != 1
        for row in jobs.values()
    ):
        raise RuntimeError("final_verifier_resume_scheduler_state_invalid")
    return payload


def _validate_replay_bundle_lineage(
    evidence_paths: list[str],
    bundle_manifest: str | Path,
    seal_manifest: str | Path,
) -> dict[str, str]:
    expected_bundle_sha = sha256_file(bundle_manifest)
    expected_seal_sha = sha256_file(seal_manifest)
    observed: list[dict[str, str]] = []
    for evidence_path in evidence_paths:
        evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        manifests = [
            Path(path)
            for path in (evidence.get("input_manifest") or {}).get("files", {})
            if path.endswith("replay_bundle_manifest.json")
        ]
        if len(manifests) != 1 or not manifests[0].is_file():
            raise RuntimeError("final_verifier_replay_bundle_manifest_missing")
        recorded = (evidence.get("input_manifest") or {}).get("files", {}).get(str(manifests[0])) or {}
        if sha256_file(manifests[0]) != recorded.get("sha256"):
            raise RuntimeError("final_verifier_replay_bundle_recorded_sha_mismatch")
        replay_bundle = json.loads(manifests[0].read_text(encoding="utf-8"))
        if replay_bundle.get("bundle_hash") != evidence.get("bundle_hash"):
            raise RuntimeError("final_verifier_replay_bundle_hash_mismatch")
        if canonical_hash({"inputs": replay_bundle.get("inputs") or {}, "extra": replay_bundle.get("extra") or {}}) != replay_bundle.get("bundle_hash"):
            raise RuntimeError("final_verifier_replay_bundle_content_hash_mismatch")
        input_shas = {item.get("sha256") for item in (replay_bundle.get("inputs") or {}).values()}
        if expected_bundle_sha not in input_shas or expected_seal_sha not in input_shas:
            raise RuntimeError("final_verifier_replay_bundle_input_mismatch")
        extra = {key: value for key, value in (replay_bundle.get("extra") or {}).items() if key != "campaign_id"}
        computation_identity = hashlib.sha256(
            json.dumps({"inputs": replay_bundle.get("inputs") or {}, "extra": extra}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        observed.append({"bundle_hash": replay_bundle["bundle_hash"], "computation_identity": computation_identity})
    if len({row["bundle_hash"] for row in observed}) != 1 or len({row["computation_identity"] for row in observed}) != 1:
        raise RuntimeError("final_verifier_replay_shard_bundle_mismatch")
    return observed[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify native Task 054-C production evidence.")
    parser.add_argument("--bundle-manifest", required=True)
    parser.add_argument("--sentinel-manifest", required=True)
    parser.add_argument("--seal-manifest", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--primary-evidence-path", action="append", default=[])
    parser.add_argument("--sibling-evidence-path", action="append", default=[])
    parser.add_argument("--resume-evidence-path", action="append", default=[])
    parser.add_argument("--resume-summary-path")
    parser.add_argument("--resume-state-path")
    args = parser.parse_args(argv)
    result = verify_task054c(
        bundle_manifest=args.bundle_manifest,
        sentinel_manifest=args.sentinel_manifest,
        seal_manifest=args.seal_manifest,
        output_path=args.output_path,
        primary_evidence_paths=args.primary_evidence_path,
        sibling_evidence_paths=args.sibling_evidence_path,
        resume_evidence_paths=args.resume_evidence_path,
        resume_summary_path=args.resume_summary_path,
        resume_state_path=args.resume_state_path,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
