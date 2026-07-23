from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from task_055_h.io import canonical_hash, read_json, validate_generation
from task_055_j.ledger import DurableHashJournal, event_rows

from .contracts import APPLICATION_JOURNAL_SCHEMA, APPLICATION_SCHEMA, APPLICATION_STAGE_SCHEMA
from .immutable import (
    publish_current_pointer,
    validate_current_pointer,
    write_immutable_generation,
)


class Task055KStageMachineError(RuntimeError):
    pass


class Task055KInjectedCrash(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeStageResult:
    outputs: dict[str, Any]
    semantic_summary: dict[str, Any]
    native_artifacts: tuple[dict[str, Any], ...]
    cache_status: str


@dataclass(frozen=True)
class StageDefinition:
    name: str
    executor: Callable[["StageRuntime"], NativeStageResult]
    validator: Callable[[Mapping[str, Any], "StageRuntime"], None]
    validator_fqn: str


@dataclass(frozen=True)
class StageRuntime:
    application_root: Path
    stage_work_root: Path
    application_spec_hash: str
    evidence_scope: str
    accepted: Any
    context: Mapping[str, Any]
    prior_stages: Mapping[str, Mapping[str, Any]]


class ApplicationStageMachine:
    def __init__(
        self,
        *,
        application_root: str | Path,
        application_spec_hash: str,
        evidence_scope: str,
        accepted: Any,
        context: Mapping[str, Any],
        stages: Sequence[StageDefinition],
    ) -> None:
        self.root = Path(application_root).resolve()
        self.spec_hash = application_spec_hash
        self.evidence_scope = evidence_scope
        self.accepted = accepted
        self.context = context
        self.stages = tuple(stages)
        source_hash = str(context.get("runtime_semantic_source_hash") or "")
        if len(source_hash) != 64 or any(
            character not in "0123456789abcdef" for character in source_hash
        ):
            raise Task055KStageMachineError("task055k_runtime_semantic_source_hash_missing")
        if len({stage.name for stage in self.stages}) != len(self.stages):
            raise Task055KStageMachineError("task055k_duplicate_application_stage")

    def run(self, *, crash_point: str | None = None) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "application.lock"
        if lock_path.is_symlink():
            raise Task055KStageMachineError("task055k_application_lock_symlink")
        try:
            lock_path.touch(exist_ok=False)
        except FileExistsError:
            if not lock_path.is_file() or lock_path.is_symlink():
                raise Task055KStageMachineError("task055k_application_lock_invalid")
        lock_identity = self._load_or_seal_lock_identity(lock_path)
        with lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self._validate_open_lock(lock_path, lock.fileno(), lock_identity)
                existing = self._existing_application()
                if existing is not None:
                    payload = self.validate_application(existing)
                    return payload | {
                        "resume_summary": {
                            "executed_stage_count": 0,
                            "reused_stage_count": len(self.stages),
                            "recomputed_stage_count": 0,
                        }
                    }
                journal = DurableHashJournal(self.root / "stage_journal", name="task055kr_application")
                prior: dict[str, dict[str, Any]] = {}
                previous = self.spec_hash
                executed = reused = recomputed = 0
                for ordinal, definition in enumerate(self.stages, start=1):
                    if crash_point == f"before:{definition.name}":
                        raise Task055KInjectedCrash(f"task055k_crash_before_stage:{definition.name}")
                    stage_root = self.root / "stages" / f"{ordinal:02d}_{definition.name}"
                    runtime = StageRuntime(
                        application_root=self.root,
                        stage_work_root=stage_root / "work",
                        application_spec_hash=self.spec_hash,
                        evidence_scope=self.evidence_scope,
                        accepted=self.accepted,
                        context=self.context,
                        prior_stages=prior,
                    )
                    current = self._current_stage(stage_root)
                    if current is not None:
                        stage_payload = self._validate_stage(
                            current,
                            definition=definition,
                            ordinal=ordinal,
                            input_root=previous,
                            runtime=runtime,
                        )
                        starts = [
                            row
                            for row in event_rows(journal.rows(), event="stage_started")
                            if row.get("stage") == definition.name
                            and row.get("ordinal") == ordinal
                        ]
                        if len(starts) != 1:
                            raise Task055KStageMachineError(
                                f"task055k_stage_pointer_without_start_journal:{definition.name}"
                            )
                        journal.append(
                            {
                                "event_id": f"commit:{ordinal}:{definition.name}",
                                "event": "stage_committed",
                                "stage": definition.name,
                                "ordinal": ordinal,
                                "input_root": previous,
                                "output_content_hash": stage_payload["content_hash"],
                                "cache_status": stage_payload["cache_status"],
                                "application_spec_hash": self.spec_hash,
                            }
                        )
                        prior[definition.name] = stage_payload
                        previous = stage_payload["content_hash"]
                        reused += 1
                        continue
                    journal.append(
                        {
                            "event_id": f"start:{ordinal}:{definition.name}",
                            "event": "stage_started",
                            "stage": definition.name,
                            "ordinal": ordinal,
                            "input_root": previous,
                            "application_spec_hash": self.spec_hash,
                        }
                    )
                    incomplete_native_work = runtime.stage_work_root.exists() and any(
                        runtime.stage_work_root.iterdir()
                    )
                    native = definition.executor(runtime)
                    if incomplete_native_work and native.cache_status == "miss_written":
                        native = NativeStageResult(
                            outputs=native.outputs,
                            semantic_summary=native.semantic_summary,
                            native_artifacts=native.native_artifacts,
                            cache_status="recomputed_after_incomplete_stage",
                        )
                    executed += 1
                    if native.cache_status == "recomputed_after_incomplete_stage":
                        recomputed += 1
                    if crash_point == f"after_native:{definition.name}":
                        raise Task055KInjectedCrash(f"task055k_crash_after_native:{definition.name}")
                    semantic = {
                        "schema_version": APPLICATION_STAGE_SCHEMA,
                        "status": "committed",
                        "stage_name": definition.name,
                        "ordinal": ordinal,
                        "application_spec_hash": self.spec_hash,
                        "evidence_scope": self.evidence_scope,
                        "production_seal_eligible": self.evidence_scope == "real_production",
                        "input_root": previous,
                        "canonical_input_roots": self._canonical_input_roots(previous),
                        "validator_fqn": definition.validator_fqn,
                        "native_outputs": native.outputs,
                        "native_artifacts": list(native.native_artifacts),
                        "semantic_summary": native.semantic_summary,
                        "cache_status": native.cache_status,
                        "execution_count": 1,
                    }
                    stage_manifest = write_immutable_generation(
                        stage_root / "publication",
                        prefix=f"task055kr_stage_{ordinal:02d}_{definition.name}",
                        manifest_name="stage_manifest.json",
                        semantic=semantic,
                    )
                    self._validate_stage(
                        stage_manifest["manifest_path"],
                        definition=definition,
                        ordinal=ordinal,
                        input_root=previous,
                        runtime=runtime,
                    )
                    publish_current_pointer(
                        stage_root / "publication",
                        manifest=stage_manifest,
                        manifest_name="stage_manifest.json",
                        pointer_schema="task055kr_application_stage_pointer_v1",
                    )
                    journal.append(
                        {
                            "event_id": f"commit:{ordinal}:{definition.name}",
                            "event": "stage_committed",
                            "stage": definition.name,
                            "ordinal": ordinal,
                            "input_root": previous,
                            "output_content_hash": stage_manifest["content_hash"],
                            "cache_status": native.cache_status,
                            "application_spec_hash": self.spec_hash,
                        }
                    )
                    prior[definition.name] = stage_manifest
                    previous = stage_manifest["content_hash"]
                    if crash_point == f"after_commit:{definition.name}":
                        raise Task055KInjectedCrash(f"task055k_crash_after_commit:{definition.name}")
                    self._validate_open_lock(lock_path, lock.fileno(), lock_identity)
                stage_rows = [
                    {
                        "stage": definition.name,
                        "ordinal": index,
                        "input_root": prior[definition.name]["input_root"],
                        "output_content_hash": prior[definition.name]["content_hash"],
                        "validator_fqn": prior[definition.name]["validator_fqn"],
                        "cache_status": prior[definition.name]["cache_status"],
                    }
                    for index, definition in enumerate(self.stages, start=1)
                ]
                committed_rows = event_rows(journal.rows(), event="stage_committed")
                snapshot_semantic = {
                    "schema_version": APPLICATION_JOURNAL_SCHEMA,
                    "status": "completed",
                    "application_spec_hash": self.spec_hash,
                    "evidence_scope": self.evidence_scope,
                    "stages": stage_rows,
                    "stage_count": len(stage_rows),
                    "final_stage_root": previous,
                    "journal_checkpoint": journal.checkpoint(),
                    "stage_execution_counts": {
                        definition.name: sum(
                            row.get("stage") == definition.name for row in committed_rows
                        )
                        for definition in self.stages
                    },
                }
                snapshot = write_immutable_generation(
                    self.root / "journal_snapshots",
                    prefix="task055kr_application_journal",
                    manifest_name="stage_journal.json",
                    semantic=snapshot_semantic,
                )
                final_stage = prior[self.stages[-1].name]
                semantic = {
                    "schema_version": APPLICATION_SCHEMA,
                    "status": "applied",
                    "evidence_scope": self.evidence_scope,
                    "production_seal_eligible": self.evidence_scope == "real_production",
                    "application_spec_hash": self.spec_hash,
                    "stage_journal_content_hash": snapshot["content_hash"],
                    "stage_journal_relative_path": Path(snapshot["manifest_path"])
                    .relative_to(self.root)
                    .as_posix(),
                    "stage_count": len(stage_rows),
                    "stages": stage_rows,
                    "final_stage_root": previous,
                    "final_outputs": final_stage["native_outputs"],
                    "terminal_pair_count": final_stage["semantic_summary"].get("terminal_pair_count"),
                    "terminal_counts": final_stage["semantic_summary"].get("terminal_counts"),
                    "candidate_reselection_allowed": False,
                    "network_executed": False if self.evidence_scope == "synthetic_rehearsal_only" else True,
                }
                application = write_immutable_generation(
                    self.root,
                    prefix="task055kr_response_application",
                    manifest_name="response_application.json",
                    semantic=semantic,
                )
                if crash_point == "before_final_pointer":
                    raise Task055KInjectedCrash("task055k_crash_before_final_pointer")
                publish_current_pointer(
                    self.root,
                    manifest=application,
                    manifest_name="response_application.json",
                    pointer_schema="task055kr_response_application_pointer_v1",
                )
                validated = self.validate_application(application["manifest_path"])
                return validated | {
                    "resume_summary": {
                        "executed_stage_count": executed,
                        "reused_stage_count": reused,
                        "recomputed_stage_count": recomputed,
                    }
                }
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def validate_application(self, path: str | Path) -> dict[str, Any]:
        payload = validate_generation(path, schema=APPLICATION_SCHEMA, manifest_name="response_application.json")
        if (
            payload.get("status") != "applied"
            or payload.get("application_spec_hash") != self.spec_hash
            or payload.get("evidence_scope") != self.evidence_scope
            or payload.get("stage_count") != len(self.stages)
        ):
            raise Task055KStageMachineError("task055k_application_contract_invalid")
        journal_relative = Path(str(payload.get("stage_journal_relative_path") or ""))
        if journal_relative.is_absolute() or ".." in journal_relative.parts:
            raise Task055KStageMachineError("task055k_application_journal_path_invalid")
        journal = validate_generation(
            self.root / journal_relative,
            schema=APPLICATION_JOURNAL_SCHEMA,
            manifest_name="stage_journal.json",
        )
        if journal["content_hash"] != payload.get("stage_journal_content_hash"):
            raise Task055KStageMachineError("task055k_application_journal_hash_invalid")
        durable = DurableHashJournal(self.root / "stage_journal", name="task055kr_application")
        if journal.get("journal_checkpoint") != durable.checkpoint():
            raise Task055KStageMachineError("task055k_application_durable_journal_drift")
        counts = journal.get("stage_execution_counts") or {}
        if set(counts) != {definition.name for definition in self.stages} or any(
            counts.get(definition.name) != 1 for definition in self.stages
        ):
            raise Task055KStageMachineError("task055k_application_stage_execution_count_invalid")
        prior: dict[str, dict[str, Any]] = {}
        previous = self.spec_hash
        expected_rows = []
        for ordinal, definition in enumerate(self.stages, start=1):
            stage_root = self.root / "stages" / f"{ordinal:02d}_{definition.name}"
            current = validate_current_pointer(
                stage_root / "publication",
                manifest_name="stage_manifest.json",
                pointer_schema="task055kr_application_stage_pointer_v1",
            )
            runtime = StageRuntime(
                application_root=self.root,
                stage_work_root=stage_root / "work",
                application_spec_hash=self.spec_hash,
                evidence_scope=self.evidence_scope,
                accepted=self.accepted,
                context=self.context,
                prior_stages=prior,
            )
            row = self._validate_stage(
                current,
                definition=definition,
                ordinal=ordinal,
                input_root=previous,
                runtime=runtime,
            )
            expected_rows.append(
                {
                    "stage": definition.name,
                    "ordinal": ordinal,
                    "input_root": previous,
                    "output_content_hash": row["content_hash"],
                    "validator_fqn": row["validator_fqn"],
                    "cache_status": row["cache_status"],
                }
            )
            prior[definition.name] = row
            previous = row["content_hash"]
        if payload.get("stages") != expected_rows or payload.get("final_stage_root") != previous:
            raise Task055KStageMachineError("task055k_application_stage_cross_lineage_invalid")
        if journal.get("stages") != expected_rows or journal.get("final_stage_root") != previous:
            raise Task055KStageMachineError("task055k_application_journal_cross_lineage_invalid")
        if payload.get("final_outputs") != prior[self.stages[-1].name].get("native_outputs"):
            raise Task055KStageMachineError("task055k_application_final_outputs_invalid")
        pointer = self.root / "current.json"
        if pointer.exists():
            current = validate_current_pointer(
                self.root,
                manifest_name="response_application.json",
                pointer_schema="task055kr_response_application_pointer_v1",
            )
            if current.resolve() != Path(path).resolve():
                raise Task055KStageMachineError("task055k_application_current_pointer_drift")
        return payload | {"stage_payloads": prior, "stage_journal": journal}

    def _validate_stage(
        self,
        path: str | Path,
        *,
        definition: StageDefinition,
        ordinal: int,
        input_root: str,
        runtime: StageRuntime,
    ) -> dict[str, Any]:
        payload = validate_generation(path, schema=APPLICATION_STAGE_SCHEMA, manifest_name="stage_manifest.json")
        if (
            payload.get("status") != "committed"
            or payload.get("stage_name") != definition.name
            or payload.get("ordinal") != ordinal
            or payload.get("application_spec_hash") != self.spec_hash
            or payload.get("evidence_scope") != self.evidence_scope
            or payload.get("input_root") != input_root
            or payload.get("canonical_input_roots") != self._canonical_input_roots(input_root)
            or payload.get("validator_fqn") != definition.validator_fqn
            or payload.get("execution_count") != 1
        ):
            raise Task055KStageMachineError(f"task055k_stage_contract_invalid:{definition.name}")
        definition.validator(payload, runtime)
        return payload

    def _canonical_input_roots(self, previous: str) -> dict[str, str]:
        return {
            "previous_stage_or_spec": previous,
            "application_spec_hash": self.spec_hash,
            "acceptance_content_hash": self.accepted.acceptance["content_hash"],
            "reservation_content_hash": self.accepted.reservation["content_hash"],
            "receipt_content_hash": self.accepted.receipt["content_hash"],
            "cache_sha256": self.accepted.acceptance["cache_sha256"],
            "context_root": str(self.context["context_root"]),
            "runtime_semantic_source_hash": str(
                self.context["runtime_semantic_source_hash"]
            ),
        }

    def _current_stage(self, stage_root: Path) -> Path | None:
        pointer = stage_root / "publication" / "current.json"
        if not pointer.exists():
            return None
        return validate_current_pointer(
            stage_root / "publication",
            manifest_name="stage_manifest.json",
            pointer_schema="task055kr_application_stage_pointer_v1",
        )

    def _existing_application(self) -> Path | None:
        pointer = self.root / "current.json"
        if pointer.exists():
            return validate_current_pointer(
                self.root,
                manifest_name="response_application.json",
                pointer_schema="task055kr_response_application_pointer_v1",
            )
        matches = []
        for path in (self.root / "generations").glob("*/response_application.json"):
            row = read_json(path)
            if row.get("application_spec_hash") == self.spec_hash:
                matches.append(path)
        if len(matches) > 1:
            raise Task055KStageMachineError("task055k_application_generation_duplicate")
        if len(matches) == 1:
            # Crash after generation write but before current pointer.
            validated = self.validate_application(matches[0])
            publish_current_pointer(
                self.root,
                manifest=validated,
                manifest_name="response_application.json",
                pointer_schema="task055kr_response_application_pointer_v1",
            )
            return matches[0]
        return None

    def _load_or_seal_lock_identity(self, lock_path: Path) -> dict[str, int]:
        identity_path = self.root / "application_lock_identity.json"
        actual = lock_path.stat()
        identity = {"st_dev": actual.st_dev, "st_ino": actual.st_ino}
        if identity_path.is_file():
            expected = json.loads(identity_path.read_text(encoding="utf-8"))
            if expected != identity:
                raise Task055KStageMachineError("task055k_application_lock_inode_replaced")
            return expected
        payload = json.dumps(identity, sort_keys=True) + "\n"
        try:
            with identity_path.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            return identity
        except FileExistsError:
            expected = json.loads(identity_path.read_text(encoding="utf-8"))
            if expected != identity:
                raise Task055KStageMachineError("task055k_application_lock_inode_replaced")
            return expected

    @staticmethod
    def _validate_open_lock(lock_path: Path, descriptor: int, expected: Mapping[str, int]) -> None:
        current = lock_path.stat()
        opened = os.fstat(descriptor)
        if (
            current.st_dev != expected.get("st_dev")
            or current.st_ino != expected.get("st_ino")
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise Task055KStageMachineError("task055k_application_lock_inode_replaced")
