from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from auto_alpha.platform.network_authority.storage import canonical_hash, read_json, validate_generation
from auto_alpha.platform.network_authority.journal import DurableHashJournal, event_rows

from .contracts import APPLICATION_JOURNAL_SCHEMA, APPLICATION_SCHEMA, APPLICATION_STAGE_SCHEMA
from .immutable import (
    publish_current_pointer,
    validate_current_pointer,
    write_immutable_generation,
)
from .lease import ReplacementSafeLease, Task055KLeaseError, validate_historical_lease_binding


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
        try:
            lease = ReplacementSafeLease.acquire(
                parent=self.root,
                lock_name="application.lock",
                scope="task055kr2_response_application",
                root_binding=self.spec_hash,
                attempt=self.spec_hash,
                allow_legacy_empty_bootstrap=True,
            )
        except Task055KLeaseError as exc:
            raise Task055KStageMachineError(str(exc)) from None
        with lease:
            try:
                lease.checkpoint("application_after_acquisition")
                existing = self._existing_application(lease)
                if existing is not None:
                    lease.checkpoint("application_completed_fast_path_before_validation")
                    payload = self.validate_application(existing)
                    lease.checkpoint("application_completed_fast_path_after_validation")
                    lease.checkpoint("application_completed_fast_path_before_return")
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
                    lease.checkpoint(f"stage:{ordinal}:before_start")
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
                    starts = [
                        row
                        for row in event_rows(journal.rows(), event="stage_started")
                        if row.get("stage") == definition.name
                        and row.get("ordinal") == ordinal
                    ]
                    commits = [
                        row
                        for row in event_rows(journal.rows(), event="stage_committed")
                        if row.get("stage") == definition.name
                        and row.get("ordinal") == ordinal
                    ]
                    if len(starts) > 1 or len(commits) > 1:
                        raise Task055KStageMachineError(
                            f"task055kr2_stage_journal_cardinality_invalid:{definition.name}"
                        )
                    if current is not None:
                        lease.checkpoint(f"stage:{ordinal}:before_existing_validation")
                        stage_payload = self._validate_stage(
                            current,
                            definition=definition,
                            ordinal=ordinal,
                            input_root=previous,
                            runtime=runtime,
                        )
                        lease.checkpoint(f"stage:{ordinal}:after_existing_validation")
                        if len(starts) != 1:
                            raise Task055KStageMachineError(
                                f"task055k_stage_pointer_without_start_journal:{definition.name}"
                            )
                        self._validate_stage_event(
                            starts[0], event="stage_started", ordinal=ordinal,
                            definition=definition, input_root=previous,
                        )
                        if commits:
                            self._validate_stage_event(
                                commits[0], event="stage_committed", ordinal=ordinal,
                                definition=definition, input_root=previous,
                                output_content_hash=stage_payload["content_hash"],
                            )
                        else:
                            lease.checkpoint(f"stage:{ordinal}:before_recovered_commit")
                            journal.append(
                                self._stage_event(
                                    lease=lease,
                                    event="stage_committed",
                                    ordinal=ordinal,
                                    definition=definition,
                                    input_root=previous,
                                    output_content_hash=stage_payload["content_hash"],
                                    cache_status=stage_payload["cache_status"],
                                )
                            )
                            lease.checkpoint(f"stage:{ordinal}:recovered_commit_fsynced")
                        prior[definition.name] = stage_payload
                        previous = stage_payload["content_hash"]
                        reused += 1
                        lease.checkpoint(f"stage:{ordinal}:before_next_stage")
                        continue
                    if commits:
                        raise Task055KStageMachineError(
                            f"task055kr2_stage_commit_without_pointer:{definition.name}"
                        )
                    if not starts:
                        lease.checkpoint(f"stage:{ordinal}:before_start_journal")
                        journal.append(
                            self._stage_event(
                                lease=lease,
                                event="stage_started",
                                ordinal=ordinal,
                                definition=definition,
                                input_root=previous,
                            )
                        )
                        lease.checkpoint(f"stage:{ordinal}:start_journal_fsynced")
                    else:
                        self._validate_stage_event(
                            starts[0], event="stage_started", ordinal=ordinal,
                            definition=definition, input_root=previous,
                        )
                    incomplete_native_work = runtime.stage_work_root.exists() and any(
                        runtime.stage_work_root.iterdir()
                    )
                    lease.checkpoint(f"stage:{ordinal}:before_component")
                    native = definition.executor(runtime)
                    lease.checkpoint(f"stage:{ordinal}:after_component")
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
                        "publication_lease_binding": lease.binding(),
                    }
                    lease.checkpoint(f"stage:{ordinal}:before_artifact_publication")
                    stage_manifest = write_immutable_generation(
                        stage_root / "publication",
                        prefix=f"task055kr_stage_{ordinal:02d}_{definition.name}",
                        manifest_name="stage_manifest.json",
                        semantic=semantic,
                    )
                    lease.checkpoint(f"stage:{ordinal}:artifact_publication_fsynced")
                    lease.checkpoint(f"stage:{ordinal}:before_native_validation")
                    self._validate_stage(
                        stage_manifest["manifest_path"],
                        definition=definition,
                        ordinal=ordinal,
                        input_root=previous,
                        runtime=runtime,
                    )
                    lease.checkpoint(f"stage:{ordinal}:after_native_validation")
                    lease.checkpoint(f"stage:{ordinal}:before_stage_pointer")
                    publish_current_pointer(
                        stage_root / "publication",
                        manifest=stage_manifest,
                        manifest_name="stage_manifest.json",
                        pointer_schema="task055kr_application_stage_pointer_v1",
                    )
                    lease.checkpoint(f"stage:{ordinal}:stage_pointer_fsynced")
                    if crash_point == f"after_pointer:{definition.name}":
                        raise Task055KInjectedCrash(
                            f"task055k_crash_after_stage_pointer:{definition.name}"
                        )
                    lease.checkpoint(f"stage:{ordinal}:before_stage_commit")
                    journal.append(
                        self._stage_event(
                            lease=lease,
                            event="stage_committed",
                            ordinal=ordinal,
                            definition=definition,
                            input_root=previous,
                            output_content_hash=stage_manifest["content_hash"],
                            cache_status=native.cache_status,
                        )
                    )
                    lease.checkpoint(f"stage:{ordinal}:stage_commit_fsynced")
                    prior[definition.name] = stage_manifest
                    previous = stage_manifest["content_hash"]
                    if crash_point == f"after_commit:{definition.name}":
                        raise Task055KInjectedCrash(f"task055k_crash_after_commit:{definition.name}")
                    lease.checkpoint(f"stage:{ordinal}:before_next_stage")
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
                lease.checkpoint("application_before_journal_snapshot")
                snapshot = write_immutable_generation(
                    self.root / "journal_snapshots",
                    prefix="task055kr_application_journal",
                    manifest_name="stage_journal.json",
                    semantic=snapshot_semantic,
                )
                lease.checkpoint("application_journal_snapshot_fsynced")
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
                    "publication_lease_binding": lease.binding(),
                }
                lease.checkpoint("application_before_generation_publication")
                application = write_immutable_generation(
                    self.root,
                    prefix="task055kr_response_application",
                    manifest_name="response_application.json",
                    semantic=semantic,
                )
                lease.checkpoint("application_generation_fsynced")
                if crash_point == "before_final_pointer":
                    raise Task055KInjectedCrash("task055k_crash_before_final_pointer")
                lease.checkpoint("application_before_final_pointer")
                publish_current_pointer(
                    self.root,
                    manifest=application,
                    manifest_name="response_application.json",
                    pointer_schema="task055kr_response_application_pointer_v1",
                )
                lease.checkpoint("application_final_pointer_replaced")
                _fsync_dir(self.root)
                lease.checkpoint("application_final_pointer_directory_fsynced")
                if crash_point == "after_final_pointer":
                    raise Task055KInjectedCrash("task055k_crash_after_final_pointer")
                validated = self.validate_application(application["manifest_path"])
                lease.checkpoint("application_after_final_validation")
                lease.checkpoint("application_before_completed_return")
                return validated | {
                    "resume_summary": {
                        "executed_stage_count": executed,
                        "reused_stage_count": reused,
                        "recomputed_stage_count": recomputed,
                    }
                }
            except Task055KLeaseError as exc:
                raise Task055KStageMachineError(str(exc)) from None

    def validate_application(self, path: str | Path) -> dict[str, Any]:
        payload = validate_generation(path, schema=APPLICATION_SCHEMA, manifest_name="response_application.json")
        if (
            payload.get("status") != "applied"
            or payload.get("application_spec_hash") != self.spec_hash
            or payload.get("evidence_scope") != self.evidence_scope
            or payload.get("stage_count") != len(self.stages)
        ):
            raise Task055KStageMachineError("task055k_application_contract_invalid")
        self._validate_historical_binding(payload.get("publication_lease_binding") or {})
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
        starts = event_rows(durable.rows(), event="stage_started")
        commits = event_rows(durable.rows(), event="stage_committed")
        if len(starts) != len(self.stages) or len(commits) != len(self.stages):
            raise Task055KStageMachineError("task055kr2_application_stage_event_count_invalid")
        for ordinal, definition in enumerate(self.stages, start=1):
            expected_input = self.spec_hash if ordinal == 1 else expected_rows[ordinal - 2][
                "output_content_hash"
            ]
            start = [
                row for row in starts
                if row.get("ordinal") == ordinal and row.get("stage") == definition.name
            ]
            commit = [
                row for row in commits
                if row.get("ordinal") == ordinal and row.get("stage") == definition.name
            ]
            if len(start) != 1 or len(commit) != 1:
                raise Task055KStageMachineError(
                    f"task055kr2_application_stage_event_cardinality:{definition.name}"
                )
            self._validate_stage_event(
                start[0], event="stage_started", ordinal=ordinal,
                definition=definition, input_root=expected_input,
            )
            self._validate_stage_event(
                commit[0], event="stage_committed", ordinal=ordinal,
                definition=definition, input_root=expected_input,
                output_content_hash=expected_rows[ordinal - 1]["output_content_hash"],
            )
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
        self._validate_historical_binding(payload.get("publication_lease_binding") or {})
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

    def _existing_application(self, lease: ReplacementSafeLease) -> Path | None:
        pointer = self.root / "current.json"
        if pointer.exists():
            lease.checkpoint("application_existing_pointer_before_validation")
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
            lease.checkpoint("application_orphan_generation_before_validation")
            validated = self.validate_application(matches[0])
            lease.checkpoint("application_orphan_generation_after_validation")
            lease.checkpoint("application_orphan_generation_before_pointer")
            publish_current_pointer(
                self.root,
                manifest=validated,
                manifest_name="response_application.json",
                pointer_schema="task055kr_response_application_pointer_v1",
            )
            lease.checkpoint("application_orphan_generation_pointer_fsynced")
            return matches[0]
        return None

    def _stage_event(
        self,
        *,
        lease: ReplacementSafeLease,
        event: str,
        ordinal: int,
        definition: StageDefinition,
        input_root: str,
        output_content_hash: str | None = None,
        cache_status: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "event_id": (
                f"start:{ordinal}:{definition.name}"
                if event == "stage_started"
                else f"commit:{ordinal}:{definition.name}"
            ),
            "event": event,
            "stage": definition.name,
            "ordinal": ordinal,
            "input_root": input_root,
            "application_spec_hash": self.spec_hash,
            "lease_binding": lease.binding(),
        }
        if output_content_hash is not None:
            row["output_content_hash"] = output_content_hash
        if cache_status is not None:
            row["cache_status"] = cache_status
        return row

    def _validate_stage_event(
        self,
        row: Mapping[str, Any],
        *,
        event: str,
        ordinal: int,
        definition: StageDefinition,
        input_root: str,
        output_content_hash: str | None = None,
    ) -> None:
        expected = {
            "event": event,
            "stage": definition.name,
            "ordinal": ordinal,
            "input_root": input_root,
            "application_spec_hash": self.spec_hash,
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise Task055KStageMachineError(
                f"task055kr2_stage_event_contract_invalid:{definition.name}:{event}"
            )
        if output_content_hash is not None and row.get("output_content_hash") != output_content_hash:
            raise Task055KStageMachineError(
                f"task055kr2_stage_commit_output_invalid:{definition.name}"
            )
        self._validate_historical_binding(row.get("lease_binding") or {})

    def _validate_historical_binding(self, binding: Mapping[str, Any]) -> None:
        if not binding:
            raise Task055KStageMachineError("task055kr2_application_lease_binding_missing")
        try:
            validate_historical_lease_binding(
                parent=self.root,
                lock_name="application.lock",
                binding=binding,
            )
        except Task055KLeaseError as exc:
            raise Task055KStageMachineError(str(exc)) from None


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
