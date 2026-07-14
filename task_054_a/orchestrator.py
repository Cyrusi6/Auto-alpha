"""Content-verified production DAG for Task 054-A."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from artifact_schema.validator import validate_artifact
from artifact_schema.writer import attach_artifact_metadata
from validation_campaign_store.replay_evidence import validate_task054_replay_evidence


TASK054_STAGE_ORDER = (
    "governed_source",
    "immutable_freeze",
    "historical_universe",
    "strict_matrix",
    "v3_tensor",
    "production_firewall_sentinel",
    "materialization",
    "four_gpu_validation",
    "consolidation",
    "evidence_package",
)


@dataclass(frozen=True)
class Task054StageContract:
    name: str
    proof_path: str
    dependencies: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    expected_artifact_type: str | None = None
    expected_candidate_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.name not in TASK054_STAGE_ORDER:
            raise ValueError(f"unknown Task 054 stage:{self.name}")
        if any(dependency not in TASK054_STAGE_ORDER for dependency in self.dependencies):
            raise ValueError(f"unknown Task 054 dependency:{self.name}")
        if any(TASK054_STAGE_ORDER.index(dependency) >= TASK054_STAGE_ORDER.index(self.name) for dependency in self.dependencies):
            raise ValueError(f"non-DAG dependency order:{self.name}")


class Task054ProductionDAG:
    """Run missing stages and recompute proof truth from immutable artifacts."""

    def __init__(self, contracts: Iterable[Task054StageContract], output_dir: str | Path):
        self.contracts = {contract.name: contract for contract in contracts}
        self.output_dir = Path(output_dir)
        if set(self.contracts) != set(TASK054_STAGE_ORDER):
            missing = sorted(set(TASK054_STAGE_ORDER) - set(self.contracts))
            extra = sorted(set(self.contracts) - set(TASK054_STAGE_ORDER))
            raise ValueError(f"Task 054 stage set mismatch:missing={missing}:extra={extra}")
        for contract in self.contracts.values():
            contract.validate()

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stages: dict[str, dict[str, Any]] = {}
        blockers: list[str] = []
        for name in TASK054_STAGE_ORDER:
            contract = self.contracts[name]
            dependency_failures = [dependency for dependency in contract.dependencies if not stages[dependency]["verified"]]
            if dependency_failures:
                stages[name] = {
                    "stage": name,
                    "verified": False,
                    "execution_mode": "blocked_by_dependency",
                    "blockers": [f"dependency_not_verified:{item}" for item in dependency_failures],
                }
                blockers.extend(f"{name}:{item}" for item in stages[name]["blockers"])
                continue
            proof_path = Path(contract.proof_path)
            execution_mode = "content_hash_reuse"
            try:
                proof = self._validate_stage(contract, stages)
            except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as initial_error:
                if not contract.command:
                    stages[name] = {
                        "stage": name,
                        "verified": False,
                        "execution_mode": "blocked",
                        "blockers": [str(initial_error)],
                    }
                    blockers.append(f"{name}:{initial_error}")
                    continue
                self._run_stage_command(contract)
                execution_mode = "executed"
                try:
                    proof = self._validate_stage(contract, stages)
                except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as final_error:
                    stages[name] = {
                        "stage": name,
                        "verified": False,
                        "execution_mode": execution_mode,
                        "blockers": [str(final_error)],
                    }
                    blockers.append(f"{name}:{final_error}")
                    continue
            stages[name] = {
                "stage": name,
                "verified": True,
                "execution_mode": execution_mode,
                "proof_path": str(proof_path.resolve()),
                "proof_sha256": _sha256_file(proof_path),
                "content_hash": proof["content_hash"],
                "axes": proof.get("axes", {}),
                "candidate_ids": proof.get("candidate_ids", []),
                "validation": proof.get("validation", {}),
            }

        replay_verified = stages["four_gpu_validation"]["verified"]
        complete = not blockers and all(stage["verified"] for stage in stages.values()) and replay_verified
        status = (
            "task054_engineering_baseline_verified_certification_blocked"
            if complete
            else "task054_engineering_baseline_blocked"
        )
        semantic = {
            "schema_version": "task_054a_production_dag_v1",
            "status": status,
            "stages": stages,
            "engineering_blockers": blockers,
            "task053_baseline_status": "superseded" if complete else "provisional",
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
            "certification_queue_count": 0,
            "portfolio_queue_count": 0,
            "paper_queue_count": 0,
            "live_queue_count": 0,
        }
        semantic["content_hash"] = _canonical_hash(semantic)
        payload = attach_artifact_metadata(semantic, "task_054a_production_dag_report", "task_054_a")
        _atomic_json(self.output_dir / "task_054a_production_dag_report.json", payload)
        return payload

    def _run_stage_command(self, contract: Task054StageContract) -> None:
        log_path = self.output_dir / "logs" / f"{contract.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("wb") as handle:
            completed = subprocess.run(
                list(contract.command),
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(f"Task 054 stage command failed:{contract.name}:exit={completed.returncode}")

    def _validate_stage(
        self,
        contract: Task054StageContract,
        completed_stages: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        proof_path = Path(contract.proof_path)
        if not proof_path.is_file():
            raise FileNotFoundError(f"Task 054 stage proof missing:{contract.name}:{proof_path}")
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        if proof.get("stage") != contract.name or proof.get("status") != "complete":
            raise RuntimeError(f"Task 054 stage proof status mismatch:{contract.name}")
        if contract.expected_artifact_type and proof.get("artifact_type") != contract.expected_artifact_type:
            raise RuntimeError(f"Task 054 stage artifact type mismatch:{contract.name}")
        claimed_content_hash = str(proof.get("content_hash") or "")
        if claimed_content_hash != task054_stage_content_hash(proof):
            raise RuntimeError(f"Task 054 stage content hash mismatch:{contract.name}")
        artifact_manifest_path = Path(str(proof.get("artifact_manifest_path") or ""))
        if not artifact_manifest_path.is_file():
            raise RuntimeError(f"Task 054 artifact manifest missing:{contract.name}")
        if proof.get("artifact_manifest_sha256") != _sha256_file(artifact_manifest_path):
            raise RuntimeError(f"Task 054 artifact manifest SHA mismatch:{contract.name}")
        schema_result = validate_artifact(artifact_manifest_path, strict=True)
        if not schema_result.valid:
            codes = ",".join(issue.code for issue in schema_result.issues)
            raise RuntimeError(f"Task 054 artifact schema invalid:{contract.name}:{codes}")
        self._validate_partitions(proof_path.parent, proof)
        self._validate_axes(proof_path.parent, proof)
        lineage = proof.get("lineage") or {}
        for dependency in contract.dependencies:
            expected_hash = completed_stages[dependency]["content_hash"]
            if lineage.get(dependency) != expected_hash:
                raise RuntimeError(f"Task 054 upstream lineage mismatch:{contract.name}:{dependency}")
        expected_candidates = sorted(contract.expected_candidate_ids)
        if expected_candidates and sorted(proof.get("candidate_ids") or []) != expected_candidates:
            raise RuntimeError(f"Task 054 candidate exact set mismatch:{contract.name}")
        if contract.name == "four_gpu_validation":
            evidence_paths = proof.get("replay_evidence_paths") or []
            replay = validate_task054_replay_evidence(
                evidence_paths,
                expected_candidates,
                require_uncached_materialization=bool(proof.get("require_uncached_materialization", True)),
                expected_bundle_hash=proof.get("replay_bundle_hash"),
            )
            if proof.get("replay_truth_hash") != replay["replay_truth_hash"]:
                raise RuntimeError("Task 054 replay truth hash mismatch")
            proof["validation"] = replay
        return proof

    @staticmethod
    def _validate_partitions(root: Path, proof: Mapping[str, Any]) -> None:
        partitions = proof.get("partitions")
        if not isinstance(partitions, dict) or not partitions:
            raise RuntimeError(f"Task 054 stage partitions missing:{proof.get('stage')}")
        for logical_name, record in partitions.items():
            path = root / str(record.get("path") or "")
            if not path.is_file() or record.get("sha256") != _sha256_file(path):
                raise RuntimeError(f"Task 054 partition mismatch:{proof.get('stage')}:{logical_name}")
            if int(record.get("size_bytes", -1)) != path.stat().st_size:
                raise RuntimeError(f"Task 054 partition size mismatch:{proof.get('stage')}:{logical_name}")

    @staticmethod
    def _validate_axes(root: Path, proof: Mapping[str, Any]) -> None:
        axes = proof.get("axes")
        if not isinstance(axes, dict) or not axes:
            raise RuntimeError(f"Task 054 stage axes missing:{proof.get('stage')}")
        for name, record in axes.items():
            path = root / str(record.get("path") or "")
            if not path.is_file() or record.get("sha256") != _sha256_file(path):
                raise RuntimeError(f"Task 054 axis mismatch:{proof.get('stage')}:{name}")


def build_stage_proof(
    *,
    stage: str,
    artifact_manifest_path: str | Path,
    partitions: Mapping[str, str | Path],
    axes: Mapping[str, str | Path],
    lineage: Mapping[str, str],
    output_path: str | Path,
    candidate_ids: Iterable[str] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the standardized proof envelope consumed by the production DAG."""
    target = Path(output_path)
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    manifest = Path(artifact_manifest_path)
    payload: dict[str, Any] = {
        "stage": stage,
        "status": "complete",
        "artifact_manifest_path": str(manifest.resolve()),
        "artifact_manifest_sha256": _sha256_file(manifest),
        "partitions": {
            name: _relative_file_record(root, Path(path)) for name, path in sorted(partitions.items())
        },
        "axes": {
            name: {"path": os.path.relpath(Path(path), root), "sha256": _sha256_file(Path(path))}
            for name, path in sorted(axes.items())
        },
        "lineage": dict(sorted(lineage.items())),
        "candidate_ids": sorted(str(item) for item in candidate_ids),
        **dict(extra or {}),
    }
    payload["content_hash"] = task054_stage_content_hash(payload)
    _atomic_json(target, payload)
    return payload


def build_stage_manifest(
    *,
    stage: str,
    source_manifest_path: str | Path,
    source_content_hash: str,
    output_path: str | Path,
) -> dict[str, Any]:
    source = Path(source_manifest_path)
    payload = attach_artifact_metadata(
        {
            "stage": stage,
            "status": "verified_source_wrapper",
            "source_manifest_sha256": _sha256_file(source),
            "source_content_hash": str(source_content_hash),
        },
        "task_054a_stage_manifest",
        "task_054_a",
    )
    _atomic_json(Path(output_path), payload)
    return payload


def _relative_file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": os.path.relpath(path, root),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _semantic_proof(payload: Mapping[str, Any]) -> dict[str, Any]:
    semantic = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "content_hash",
            "created_at",
            "artifact_metadata",
            "producer",
            "schema_version",
            "artifact_manifest_path",
        }
    }
    semantic["partitions"] = {
        name: {key: value for key, value in record.items() if key != "path"}
        for name, record in sorted((payload.get("partitions") or {}).items())
    }
    semantic["axes"] = {
        name: {key: value for key, value in record.items() if key != "path"}
        for name, record in sorted((payload.get("axes") or {}).items())
    }
    return semantic


def task054_stage_content_hash(payload: Mapping[str, Any]) -> str:
    return _canonical_hash(_semantic_proof(payload))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Task 054 stage-contract JSON")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
    contracts = [
        Task054StageContract(
            name=str(row["name"]),
            proof_path=str(row["proof_path"]),
            dependencies=tuple(str(item) for item in row.get("dependencies") or ()),
            command=tuple(str(item) for item in row.get("command") or ()),
            expected_artifact_type=row.get("expected_artifact_type"),
            expected_candidate_ids=tuple(str(item) for item in row.get("expected_candidate_ids") or ()),
        )
        for row in payload.get("stages") or []
    ]
    report = Task054ProductionDAG(contracts, args.output_dir).run()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "task054_engineering_baseline_verified_certification_blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
