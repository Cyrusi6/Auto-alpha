from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


EXPECTED_STATUS = "task055j_single_canary_production_closure_ready_no_network_executed"
EXPECTED_BLOCKED = "task055j_single_canary_production_closure_blocked_no_network_executed"
EXPECTED_PARENT_SEAL = "6c32e777374319026c1db23b10686bf9c245595b170a76f8e29e2f8259ca9b72"
EXPECTED_PLAN = "314aef9d0fca5e46980214fad97c15397dc309c3478ffc3278ca58cfce0bccae"
EXPECTED_CANARY = {
    "api_name": "daily",
    "ts_code": "000413.SZ",
    "trade_date": "20160726",
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
    "transport_hash": "6497cb48c414a9b4b0e2f5dc152c134fa66bf01938f598bdd79831f415a7464e",
    "evidence_use_hash": "a4241983bdd7616c60e02dc9444662be01e7ee43bb6fe81a2cc8637df59d4a5f",
}
REQUIRED_ROOT_ROLES = {
    "repository",
    "governed",
    "authority",
    "network_journal",
    "transport_spend",
    "cache",
    "receipts",
    "applications",
    "single_flight_lock",
    "application_lock",
}
REQUIRED_ARTIFACT_ROLES = {
    "source_tree_seal",
    "application_preflight",
    "application_tree_seal",
    "runtime_authority",
    "execution_authorization",
    "native_rehearsal",
    "rehearsal_independent_verification",
    "final_report",
    "final_independent_verification",
    "final_execution_seal",
}


class Task055JScrubbedEvidenceError(RuntimeError):
    pass


def verify_scrubbed_evidence(
    path: str | Path, *, repository_root: str | Path | None = None
) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if _canonical_hash(semantic) != payload.get("content_hash"):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_content_hash_mismatch")
    if payload.get("schema_version") != "task055j_scrubbed_execution_evidence_v1":
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_schema_invalid")
    if payload.get("status") not in {EXPECTED_STATUS, EXPECTED_BLOCKED}:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_status_invalid")
    if not _hash40(payload.get("implementation_commit")):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_implementation_commit_invalid")
    blockers = list(payload.get("engineering_blockers") or ())
    if (payload["status"] == EXPECTED_STATUS and blockers) or (
        payload["status"] == EXPECTED_BLOCKED and not blockers
    ):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_status_blocker_mismatch")
    if payload.get("parent_authorization_seal_hash") != EXPECTED_PARENT_SEAL or payload.get("parent_canary_plan_hash") != EXPECTED_PLAN:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_parent_lineage_invalid")
    ordered = list(payload.get("ordered_exact_daily_keys") or ())
    if len(ordered) != 17 or [row.get("ordinal") for row in ordered] != list(range(1, 18)):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_ordered_keys_invalid")
    if ordered[0] != {"ordinal": 1, **EXPECTED_CANARY} or payload.get("canary") != EXPECTED_CANARY:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_first_canary_invalid")
    if _canonical_hash(ordered) != payload.get("ordered_key_root"):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_ordered_key_root_invalid")
    if len({row.get("transport_hash") for row in ordered}) != 17:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_transport_duplicate")
    budgets = payload.get("budgets") or {}
    limits = budgets.get("limits") or {}
    if (
        int(budgets.get("physical_attempts") or 0) != 0
        or int(limits.get("unique_security_dates") or 0) != 64
        or int(limits.get("logical_requests") or 0) != 128
        or int(limits.get("physical_attempts") or 0) != 160
    ):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_budget_invalid")
    roots = payload.get("root_binding_hashes") or {}
    if set(roots) != REQUIRED_ROOT_ROLES or not all(_hash64(value) for value in roots.values()):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_root_bindings_invalid")
    source_entries = list(payload.get("source_entries") or ())
    source_paths = {row.get("path") for row in source_entries}
    required_sources = {
        "task_055_j/executor.py",
        "task_055_j/application.py",
        "task_055_j/authority.py",
        "task_055_j/verifier.py",
        "data_pipeline/ashare/providers/tushare_client.py",
        "validation_lab/materialization.py",
        "model_core/vm.py",
        "task_055_a/simulator.py",
    }
    if not required_sources.issubset(source_paths) or _canonical_hash(source_entries) != payload.get("source_root"):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_source_tree_invalid")
    for row in source_entries:
        if not isinstance(row.get("path"), str) or row["path"].startswith("/") or not _hash64(row.get("sha256")):
            raise Task055JScrubbedEvidenceError("task055j_scrubbed_source_entry_invalid")
    catalog = list(payload.get("artifact_catalog") or ())
    roles = {row.get("role") for row in catalog}
    if roles != REQUIRED_ARTIFACT_ROLES or _canonical_hash(catalog) != payload.get("artifact_catalog_root"):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_artifact_catalog_invalid")
    for row in catalog:
        if not _hash64(row.get("sha256")) or not _hash64(row.get("content_hash")):
            raise Task055JScrubbedEvidenceError("task055j_scrubbed_artifact_hash_invalid")
    lineage = payload.get("lineage") or {}
    required_lineage = {
        "runtime_authority_content_hash",
        "execution_authorization_content_hash",
        "application_preflight_content_hash",
        "application_tree_content_hash",
        "rehearsal_content_hash",
        "rehearsal_verification_content_hash",
        "final_report_content_hash",
        "final_verification_content_hash",
        "final_execution_seal_content_hash",
    }
    if set(lineage) != required_lineage or not all(_hash64(value) for value in lineage.values()):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_cross_lineage_invalid")
    catalog_hashes = {str(row["role"]): row["content_hash"] for row in catalog}
    expected_catalog_lineage = {
        "runtime_authority": lineage["runtime_authority_content_hash"],
        "execution_authorization": lineage["execution_authorization_content_hash"],
        "application_preflight": lineage["application_preflight_content_hash"],
        "application_tree_seal": lineage["application_tree_content_hash"],
        "native_rehearsal": lineage["rehearsal_content_hash"],
        "rehearsal_independent_verification": lineage["rehearsal_verification_content_hash"],
        "final_report": lineage["final_report_content_hash"],
        "final_independent_verification": lineage["final_verification_content_hash"],
        "final_execution_seal": lineage["final_execution_seal_content_hash"],
    }
    if any(catalog_hashes.get(role) != value for role, value in expected_catalog_lineage.items()):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_catalog_lineage_mismatch")
    role_roots = payload.get("application_role_roots") or {}
    if not role_roots or not all(_hash64(value) for value in role_roots.values()) or not _hash64(payload.get("application_tree_root")):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_application_tree_invalid")
    counters = payload.get("network_execution") or {}
    if any(int(counters.get(key) or 0) for key in ("credential_read_count", "tushare_post_count", "other_market_http_count")):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_offline_counter_invalid")
    if counters.get("prospective_holdout_accessed") is not False:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_holdout_boundary_invalid")
    max_read_date = str(counters.get("max_read_date") or "")
    if len(max_read_date) != 8 or not max_read_date.isdigit() or max_read_date > "20260630":
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_max_read_date_invalid")
    if payload.get("resume_authorized") is not False or payload.get("batch_authorized") is not False:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_resume_boundary_invalid")
    if any(payload.get(key) is not False for key in ("certification_ready", "portfolio_ready", "optimizer_ready", "paper_ready", "live_ready")):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_downstream_readiness_invalid")
    encoded = json.dumps(payload, sort_keys=True)
    forbidden = ("/home/", "TUSHARE_TOKEN", "credential_file", "token_suffix", "token_hash", "open\": 10")
    if any(value in encoded for value in forbidden):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_forbidden_content")
    if repository_root is not None:
        _verify_repository_source_tree(payload, Path(repository_root).resolve())
    return payload | {"verified": True, "verification_hash": _canonical_hash(payload)}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _hash64(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _hash40(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 40 and all(character in "0123456789abcdef" for character in text)


def _verify_repository_source_tree(payload: dict[str, Any], repository: Path) -> None:
    if not (repository / ".git").exists():
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_repository_invalid")
    implementation = str(payload["implementation_commit"])
    _git(repository, "cat-file", "-e", f"{implementation}^{{commit}}")
    head = _git(repository, "rev-parse", "HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", implementation, head],
        cwd=repository,
        check=False,
    ).returncode:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_implementation_not_head_ancestor")
    changed = _git(repository, "diff", "--name-only", f"{implementation}..{head}").splitlines()
    allowed_files = {"README.md", "CATREADME.md", "FRAMEWORK_UPDATE.md"}
    if any(name not in allowed_files and not name.startswith("evidence/task_055_j/") for name in changed):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_post_implementation_runtime_drift")
    expected_entries = []
    tracked = _git(repository, "ls-files", "-z").split("\0")
    for relative in sorted(name for name in tracked if name and _runtime_source(relative)):
        source = repository / relative
        if not source.is_file() or source.is_symlink():
            raise Task055JScrubbedEvidenceError(f"task055j_scrubbed_source_invalid:{relative}")
        expected_entries.append(
            {
                "path": relative,
                "sha256": _sha256(source),
                "size_bytes": source.stat().st_size,
                "mode": source.stat().st_mode & 0o777,
            }
        )
    if expected_entries != payload.get("source_entries"):
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_repository_source_tree_mismatch")


def _runtime_source(relative: str) -> bool:
    path = Path(relative)
    if relative.startswith(("tests/", "evidence/", "assets/", "paper/", "lord/")):
        return False
    if path.suffix == ".py":
        return True
    if relative in {"requirements.txt", "requirements-optional.txt", "environment.yml", ".env.example"}:
        return True
    return path.suffix in {".py", ".toml", ".lock", ".yml", ".yaml"} and (
        relative in {"pyproject.toml", "uv.lock"} or relative.startswith(".github/workflows/")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=repository, check=True, text=True, capture_output=True
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise Task055JScrubbedEvidenceError("task055j_scrubbed_git_verification_failed") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone Task 055-J scrubbed evidence verifier")
    parser.add_argument("path")
    parser.add_argument("--repository-root")
    args = parser.parse_args(argv)
    try:
        result = verify_scrubbed_evidence(args.path, repository_root=args.repository_root)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": "passed", "verification_hash": result["verification_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
