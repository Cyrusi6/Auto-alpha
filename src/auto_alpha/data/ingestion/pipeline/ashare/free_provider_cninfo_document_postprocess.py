"""Resumable, exact-cover projection of a finalized CNINFO document closure.

This module deliberately lives outside ``free_provider_cninfo_document_closure``.
The active v5 capture contract binds that module's complete file hash, so a
post-download concern must not mutate the acquisition implementation identity.

The public seam stores small, deterministic document-reference shards.  Raw
document bodies remain in their signed parent generations and are replayed one
at a time by :func:`iter_cninfo_postprocessed_documents`; this avoids a second
hundreds-of-GiB copy while retaining end-to-end body verification.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Any

from auto_alpha.platform.artifacts.storage import canonical_hash, read_json, sha256_file

from . import free_provider_cninfo_document_closure as closure_module


_CONTRACT_SCHEMA = "cninfo_document_postprocess_contract_v1"
_CHECKPOINT_SCHEMA = "cninfo_document_postprocess_checkpoint_v1"
_DOCUMENT_SCHEMA = "cninfo_document_postprocess_record_v1"
_SHARD_SCHEMA = "cninfo_document_postprocess_shard_v1"
_MANIFEST_SCHEMA = "cninfo_document_postprocess_manifest_v1"
_MAX_DOCUMENTS_PER_SHARD = 60_000
_MAX_INVENTORY_ROWS_PER_PARENT = 60_000
_MAX_SOURCE_REQUESTS_PER_PARENT = 60_000
_SQLITE_CACHE_KIB = 8 * 1024
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _SourceContext:
    root: Path
    request_by_id: Mapping[str, Mapping[str, Any]]
    terminal_by_id: Mapping[str, Mapping[str, Any]]


class _SourceResolver:
    """Index every parent once on disk and resolve one signed body at a time."""

    def __init__(
        self,
        replayed: closure_module.SealedDocumentClosurePlan,
        evidence: closure_module.DocumentClosureEvidence,
    ) -> None:
        parents = [*replayed.reusable_parents]
        if evidence.downloaded_parent is not None:
            parents.append(evidence.downloaded_parent)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="cninfo-postprocess-source-index-"
        )
        self._connection = sqlite3.connect(
            Path(self._temporary.name) / "sources.sqlite3"
        )
        self._connection.execute("PRAGMA mmap_size=0")
        self._connection.execute(f"PRAGMA cache_size=-{_SQLITE_CACHE_KIB}")
        self._connection.execute("PRAGMA temp_store=FILE")
        self._connection.executescript(
            """
            CREATE TABLE parents (
                generation_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                root_path TEXT NOT NULL,
                PRIMARY KEY (generation_id, content_hash)
            ) WITHOUT ROWID;
            CREATE TABLE source_rows (
                generation_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                request_id TEXT NOT NULL,
                request_json BLOB NOT NULL,
                terminal_json BLOB NOT NULL,
                PRIMARY KEY (generation_id, content_hash, request_id)
            ) WITHOUT ROWID;
            """
        )
        observed_parents: set[tuple[str, str]] = set()
        for parent in parents:
            key = (parent.generation_id, parent.content_hash)
            if key in observed_parents:
                raise ValueError(
                    "cninfo_document_postprocess_parent_duplicate"
                )
            observed_parents.add(key)
            self._index_parent(parent)
        self._connection.commit()

    def _index_parent(self, parent: Any) -> None:
        root = closure_module._manifest_root(Path(parent.manifest_path))
        request_plan = read_json(root / "request_plan.json")
        requests = request_plan.get("requests")
        if (
            not isinstance(requests, list)
            or not requests
            or len(requests) > _MAX_SOURCE_REQUESTS_PER_PARENT
            or any(type(row) is not dict for row in requests)
        ):
            raise ValueError(
                "cninfo_document_postprocess_request_plan_invalid"
            )
        request_by_id = {
            str(row.get("request_id") or ""): row for row in requests
        }
        if len(request_by_id) != len(requests) or "" in request_by_id:
            raise ValueError(
                "cninfo_document_postprocess_request_duplicate"
            )
        terminal_by_id = closure_module._terminal_events(
            root / "capture_journal.jsonl"
        )
        if (
            len(terminal_by_id) > _MAX_SOURCE_REQUESTS_PER_PARENT
            or set(terminal_by_id) != set(request_by_id)
        ):
            raise ValueError(
                "cninfo_document_postprocess_terminal_closure_invalid"
            )
        try:
            self._connection.execute(
                "INSERT INTO parents VALUES (?, ?, ?)",
                (parent.generation_id, parent.content_hash, str(root)),
            )
            batch: list[tuple[str, str, str, bytes, bytes]] = []
            for request_id in sorted(request_by_id):
                batch.append(
                    (
                        parent.generation_id,
                        parent.content_hash,
                        request_id,
                        _canonical_json_bytes(request_by_id[request_id]),
                        _canonical_json_bytes(terminal_by_id[request_id]),
                    )
                )
                if len(batch) == 1_000:
                    self._connection.executemany(
                        "INSERT INTO source_rows VALUES (?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                self._connection.executemany(
                    "INSERT INTO source_rows VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "cninfo_document_postprocess_source_index_duplicate"
            ) from exc

    def context(self, disposition: Any) -> _SourceContext:
        key = (
            disposition.parent_generation_id,
            disposition.parent_content_hash,
        )
        row = self._connection.execute(
            """
            SELECT p.root_path, s.request_json, s.terminal_json
            FROM source_rows AS s
            JOIN parents AS p USING (generation_id, content_hash)
            WHERE s.generation_id = ? AND s.content_hash = ?
              AND s.request_id = ?
            """,
            (*key, disposition.parent_request_id),
        ).fetchone()
        if row is None:
            raise ValueError("cninfo_document_postprocess_parent_missing")
        request = _exact_json_bytes(bytes(row[1]))
        terminal = _exact_json_bytes(bytes(row[2]))
        return _SourceContext(
            root=Path(str(row[0])),
            request_by_id={disposition.parent_request_id: request},
            terminal_by_id={disposition.parent_request_id: terminal},
        )

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
            self._temporary = None

    def __del__(self) -> None:
        self.close()


class _InventoryMetadataIndex:
    """Content-bound disk index; resident cache is capped at eight MiB."""

    def __init__(
        self,
        replayed: closure_module.SealedDocumentClosurePlan,
    ) -> None:
        self.identity = canonical_hash(
            {
                "sealed_plan_root": replayed.plan_root,
                "inventory_parents": [
                    row.semantic() for row in replayed.inventory_parents
                ],
                "demand_count": replayed.demand_count,
            }
        )
        self._temporary = tempfile.TemporaryDirectory(
            prefix=f"cninfo-postprocess-{self.identity[:12]}-"
        )
        self._connection = sqlite3.connect(
            Path(self._temporary.name) / "inventory.sqlite3"
        )
        self._connection.execute("PRAGMA mmap_size=0")
        self._connection.execute(f"PRAGMA cache_size=-{_SQLITE_CACHE_KIB}")
        self._connection.execute("PRAGMA temp_store=FILE")
        self._connection.executescript(
            """
            CREATE TABLE demands (
                identity TEXT PRIMARY KEY,
                inventory_content_hash TEXT NOT NULL,
                leaf_profile TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE inventory_rows (
                inventory_content_hash TEXT NOT NULL,
                announcement_id TEXT NOT NULL,
                adjunct_url TEXT NOT NULL,
                payload_json BLOB NOT NULL,
                PRIMARY KEY (
                    inventory_content_hash,
                    announcement_id,
                    adjunct_url
                )
            ) WITHOUT ROWID;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;
            """
        )
        self._connection.execute(
            "INSERT INTO metadata(key, value) VALUES('identity', ?)",
            (self.identity,),
        )
        self._connection.executemany(
            "INSERT INTO demands VALUES (?, ?, ?)",
            (
                (row.identity, row.inventory_content_hash, row.leaf_profile)
                for row in replayed.demands
            ),
        )
        for parent in replayed.inventory_parents:
            replayed_parent, rows = closure_module._replay_inventory(
                Path(parent.manifest_path)
            )
            if replayed_parent.semantic() != parent.semantic():
                raise ValueError(
                    "cninfo_document_postprocess_inventory_parent_invalid"
                )
            if len(rows) > _MAX_INVENTORY_ROWS_PER_PARENT:
                raise ValueError(
                    "cninfo_document_postprocess_inventory_parent_budget_exceeded"
                )
            batch: list[tuple[str, str, str, bytes]] = []
            for row in rows:
                year = closure_module._announcement_year(row)
                if year not in replayed.years:
                    continue
                batch.append(
                    (
                        parent.content_hash,
                        closure_module._announcement_id(row),
                        closure_module._canonical_adjunct_url(
                            row.get("adjunct_url")
                        ),
                        _canonical_json_bytes(row),
                    )
                )
                if len(batch) == 1_000:
                    self._insert_inventory_batch(batch)
                    batch.clear()
            if batch:
                self._insert_inventory_batch(batch)
        self._connection.commit()

    def _insert_inventory_batch(
        self,
        batch: Sequence[tuple[str, str, str, bytes]],
    ) -> None:
        try:
            self._connection.executemany(
                "INSERT INTO inventory_rows VALUES (?, ?, ?, ?)",
                batch,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "cninfo_document_postprocess_inventory_duplicate"
            ) from exc

    def candidates(self, physical: Any) -> tuple[dict[str, Any], ...]:
        candidates: list[dict[str, Any]] = []
        for demand_identity in physical.demand_identities:
            demand = self._connection.execute(
                """
                SELECT inventory_content_hash, leaf_profile
                FROM demands WHERE identity = ?
                """,
                (demand_identity,),
            ).fetchone()
            if demand is None:
                raise ValueError("cninfo_document_postprocess_demand_missing")
            source_row = self._connection.execute(
                """
                SELECT payload_json FROM inventory_rows
                WHERE inventory_content_hash = ?
                  AND announcement_id = ? AND adjunct_url = ?
                """,
                (
                    demand[0],
                    physical.announcement_id,
                    physical.adjunct_url,
                ),
            ).fetchone()
            if source_row is None:
                raise ValueError(
                    "cninfo_document_postprocess_inventory_row_missing"
                )
            source = _exact_json_bytes(bytes(source_row[0]))
            matched_leaves = source.get("matched_leaves")
            if (
                not isinstance(matched_leaves, list)
                or not matched_leaves
                or any(type(value) is not str for value in matched_leaves)
                or len(set(matched_leaves)) != len(matched_leaves)
            ):
                raise ValueError(
                    "cninfo_document_postprocess_inventory_scope_invalid"
                )
            candidates.append(
                {
                    "demand_identity": demand_identity,
                    "inventory_content_hash": str(demand[0]),
                    "leaf_profile": str(demand[1]),
                    "sec_code": str(source.get("sec_code") or ""),
                    "sec_name": str(source.get("sec_name") or ""),
                    "org_id": str(source.get("org_id") or ""),
                    "announcement_title": str(
                        source.get("announcement_title") or ""
                    ),
                    "announcement_type": str(
                        source.get("announcement_type") or ""
                    ),
                    "column_id": str(source.get("column_id") or ""),
                    "matched_leaves": sorted(matched_leaves),
                }
            )
        return tuple(
            sorted(candidates, key=lambda row: row["demand_identity"])
        )

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
            self._temporary = None

    def __del__(self) -> None:
        self.close()


def build_cninfo_document_postprocess(
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
    output_root: str | Path,
    *,
    max_documents_per_shard: int = _MAX_DOCUMENTS_PER_SHARD,
    shard_budget: int | None = None,
) -> dict[str, Any]:
    """Build or resume one contract-bound postprocess under an output lock."""

    _validate_resource_budget(max_documents_per_shard, shard_budget)
    root = Path(output_root).absolute()
    claim_root = _lock_claim_root(
        sealed_plan,
        closure_evidence,
        max_documents_per_shard=max_documents_per_shard,
    )
    with _output_lock(root, claim_root=claim_root):
        return _build_cninfo_document_postprocess_unlocked(
            sealed_plan,
            closure_evidence,
            root,
            max_documents_per_shard=max_documents_per_shard,
            shard_budget=shard_budget,
        )


def _build_cninfo_document_postprocess_unlocked(
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
    output_root: str | Path,
    *,
    max_documents_per_shard: int = _MAX_DOCUMENTS_PER_SHARD,
    shard_budget: int | None = None,
) -> dict[str, Any]:
    """Build or resume immutable reference shards for one finalized closure.

    Every invocation independently replays the sealed plan and terminal closure
    evidence before trusting a checkpoint.  ``shard_budget`` bounds newly
    completed shards for an invocation; ``None`` runs to completion.  A
    completed shard is skipped only after its manifest and complete JSONL
    content have been replayed against the expected records.
    """

    _validate_resource_budget(max_documents_per_shard, shard_budget)
    replayed, evidence = _replay_inputs(sealed_plan, closure_evidence)
    metadata = _inventory_metadata(replayed)
    contract = _contract_semantic(
        replayed,
        evidence,
        max_documents_per_shard=max_documents_per_shard,
    )
    root = Path(output_root).absolute()
    _ensure_directory(root, create=True)
    shards_root = root / "shards"
    _ensure_directory(shards_root, create=True)
    contract_path = root / "postprocess_contract.json"
    expected_contract = contract | {"contract_id": canonical_hash(contract)}
    if contract_path.exists():
        if _exact_json_file(contract_path) != expected_contract:
            raise ValueError(
                "cninfo_document_postprocess_input_or_implementation_changed"
            )
    else:
        _atomic_json(contract_path, expected_contract)

    shard_count = math.ceil(
        evidence.physical_document_count / max_documents_per_shard
    )
    checkpoint_path = root / "checkpoint.json"
    checkpoint = _load_checkpoint(
        checkpoint_path,
        contract_id=str(expected_contract["contract_id"]),
        shard_count=shard_count,
    )
    completed_by_ordinal = {
        int(row["ordinal"]): dict(row)
        for row in checkpoint["completed_shards"]
    }
    newly_completed = 0
    for ordinal in range(shard_count):
        start = ordinal * max_documents_per_shard
        stop = min(
            evidence.physical_document_count,
            start + max_documents_per_shard,
        )
        expected_checkpoint = completed_by_ordinal.get(ordinal)
        if expected_checkpoint is not None:
            replayed_shard = _validate_shard(
                shards_root,
                ordinal=ordinal,
                start=start,
                stop=stop,
                replayed=replayed,
                evidence=evidence,
                metadata=metadata,
                contract_id=str(expected_contract["contract_id"]),
            )
            if expected_checkpoint != _checkpoint_shard(replayed_shard):
                raise ValueError(
                    "cninfo_document_postprocess_checkpoint_shard_invalid"
                )
            continue
        if _shard_candidates(shards_root, ordinal):
            recovered = _validate_shard(
                shards_root,
                ordinal=ordinal,
                start=start,
                stop=stop,
                replayed=replayed,
                evidence=evidence,
                metadata=metadata,
                contract_id=str(expected_contract["contract_id"]),
            )
            completed_by_ordinal[ordinal] = _checkpoint_shard(recovered)
            checkpoint = _checkpoint_semantic(
                contract_id=str(expected_contract["contract_id"]),
                shard_count=shard_count,
                completed=completed_by_ordinal,
            )
            _atomic_json(checkpoint_path, checkpoint)
            continue
        if shard_budget is not None and newly_completed >= shard_budget:
            break
        shard = _write_shard(
            shards_root,
            ordinal=ordinal,
            start=start,
            stop=stop,
            replayed=replayed,
            evidence=evidence,
            metadata=metadata,
            contract_id=str(expected_contract["contract_id"]),
        )
        completed_by_ordinal[ordinal] = _checkpoint_shard(shard)
        newly_completed += 1
        checkpoint = _checkpoint_semantic(
            contract_id=str(expected_contract["contract_id"]),
            shard_count=shard_count,
            completed=completed_by_ordinal,
        )
        _atomic_json(checkpoint_path, checkpoint)

    completed = len(completed_by_ordinal) == shard_count
    if not completed:
        return {
            "status": "paused",
            "contract_id": expected_contract["contract_id"],
            "closure_root": evidence.closure_root,
            "completed_shard_count": len(completed_by_ordinal),
            "shard_count": shard_count,
            "remaining_shard_count": shard_count - len(completed_by_ordinal),
            "data_admission_eligible": False,
        }

    ordered_shards = [
        _validate_shard(
            shards_root,
            ordinal=ordinal,
            start=ordinal * max_documents_per_shard,
            stop=min(
                evidence.physical_document_count,
                (ordinal + 1) * max_documents_per_shard,
            ),
            replayed=replayed,
            evidence=evidence,
            metadata=metadata,
            contract_id=str(expected_contract["contract_id"]),
        )
        for ordinal in range(shard_count)
    ]
    manifest = _manifest_semantic(
        contract=expected_contract,
        evidence=evidence,
        shards=ordered_shards,
    )
    _atomic_json(root / "postprocess_manifest.json", manifest)
    validated = _validate_output(
        root,
        replayed=replayed,
        evidence=evidence,
        metadata=metadata,
        verify_bodies=False,
    )
    return {"status": "succeeded", **validated}


def validate_cninfo_document_postprocess(
    output_root: str | Path,
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
    *,
    verify_bodies: bool = True,
) -> dict[str, Any]:
    """Replay a completed postprocess generation and its exact cover."""

    root = Path(output_root).absolute()
    with _output_lock(
        root,
        claim_root=_lock_claim_root(
            sealed_plan,
            closure_evidence,
            max_documents_per_shard=None,
        ),
    ):
        replayed, evidence = _replay_inputs(
            sealed_plan,
            closure_evidence,
        )
        return _validate_output(
            root,
            replayed=replayed,
            evidence=evidence,
            metadata=_inventory_metadata(replayed),
            verify_bodies=verify_bodies,
        )


def _validate_output(
    root: Path,
    *,
    replayed: closure_module.SealedDocumentClosurePlan,
    evidence: closure_module.DocumentClosureEvidence,
    metadata: _InventoryMetadataIndex,
    verify_bodies: bool,
) -> dict[str, Any]:
    _ensure_directory(root, create=False)
    contract = _exact_json_file(root / "postprocess_contract.json")
    contract_id = str(contract.get("contract_id") or "")
    contract_semantic = {
        key: value
        for key, value in contract.items()
        if key != "contract_id"
    }
    max_per_shard = contract_semantic.get("max_documents_per_shard")
    if type(max_per_shard) is not int:
        raise ValueError("cninfo_document_postprocess_contract_invalid")
    expected_contract = _contract_semantic(
        replayed,
        evidence,
        max_documents_per_shard=max_per_shard,
    )
    if (
        contract_semantic != expected_contract
        or contract_id != canonical_hash(expected_contract)
    ):
        raise ValueError("cninfo_document_postprocess_contract_invalid")
    shard_count = math.ceil(evidence.physical_document_count / max_per_shard)
    checkpoint = _load_checkpoint(
        root / "checkpoint.json",
        contract_id=contract_id,
        shard_count=shard_count,
    )
    if len(checkpoint["completed_shards"]) != shard_count:
        raise ValueError("cninfo_document_postprocess_incomplete")
    completed_by_ordinal = {
        int(row["ordinal"]): dict(row)
        for row in checkpoint["completed_shards"]
    }
    shards: list[dict[str, Any]] = []
    for ordinal in range(shard_count):
        shard = _validate_shard(
            root / "shards",
            ordinal=ordinal,
            start=ordinal * max_per_shard,
            stop=min(
                evidence.physical_document_count,
                (ordinal + 1) * max_per_shard,
            ),
            replayed=replayed,
            evidence=evidence,
            metadata=metadata,
            contract_id=contract_id,
        )
        if completed_by_ordinal.get(ordinal) != _checkpoint_shard(shard):
            raise ValueError(
                "cninfo_document_postprocess_checkpoint_shard_invalid"
            )
        shards.append(shard)
    expected_manifest = _manifest_semantic(
        contract=contract,
        evidence=evidence,
        shards=shards,
    )
    _validate_artifact_closure(root, shards=shards)
    if _exact_json_file(root / "postprocess_manifest.json") != expected_manifest:
        raise ValueError("cninfo_document_postprocess_manifest_invalid")
    body_replay_root: str | None = None
    if verify_bodies:
        source_contexts = _source_contexts(replayed, evidence)
        replay_digest = hashlib.sha256()
        replay_count = 0
        for index, disposition in enumerate(evidence.dispositions):
            _replay_body(
                disposition,
                replayed.physical_documents[index],
                source_contexts=source_contexts,
            )
            replay_digest.update(
                _canonical_json_bytes(
                    _body_reference(index=index, disposition=disposition)
                )
                + b"\n"
            )
            replay_count += 1
        body_replay_root = replay_digest.hexdigest()
        if (
            replay_count != evidence.physical_document_count
            or body_replay_root != contract["body_reference_root"]
        ):
            raise ValueError(
                "cninfo_document_postprocess_body_exact_cover_invalid"
            )
    return {
        "generation_id": expected_manifest["generation_id"],
        "content_hash": expected_manifest["content_hash"],
        "contract_id": contract_id,
        "closure_root": evidence.closure_root,
        "document_count": evidence.physical_document_count,
        "shard_count": shard_count,
        "max_documents_per_shard": max_per_shard,
        "shards": [dict(row) for row in expected_manifest["shards"]],
        "blockers": list(evidence.blockers),
        "weak_source_ancestry": evidence.weak_source_ancestry,
        "exact_cover_verified": True,
        "body_replay_verified": verify_bodies,
        "body_replay_root": body_replay_root,
        "data_admission_eligible": False,
        "pit_evidence_eligible": False,
    }


def iter_cninfo_postprocessed_documents(
    output_root: str | Path,
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
    *,
    shard_ordinal: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield under the same output lock used by build and validation."""

    root = Path(output_root).absolute()
    with _output_lock(
        root,
        claim_root=_lock_claim_root(
            sealed_plan,
            closure_evidence,
            max_documents_per_shard=None,
        ),
    ):
        yield from _iter_cninfo_postprocessed_documents_unlocked(
            root,
            sealed_plan,
            closure_evidence,
            shard_ordinal=shard_ordinal,
        )


def _iter_cninfo_postprocessed_documents_unlocked(
    output_root: str | Path,
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
    *,
    shard_ordinal: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield verified parser-ready records, retaining at most one body here."""

    replayed, evidence = _replay_inputs(sealed_plan, closure_evidence)
    metadata = _inventory_metadata(replayed)
    validated = _validate_output(
        Path(output_root).absolute(),
        replayed=replayed,
        evidence=evidence,
        metadata=metadata,
        verify_bodies=False,
    )
    if validated["exact_cover_verified"] is not True:
        raise ValueError("cninfo_document_postprocess_exact_cover_invalid")
    if shard_ordinal is None:
        start = 0
        stop = evidence.physical_document_count
    else:
        if (
            type(shard_ordinal) is not int
            or shard_ordinal < 0
            or shard_ordinal >= len(validated["shards"])
        ):
            raise ValueError("cninfo_document_postprocess_shard_ordinal_invalid")
        selected_shard = validated["shards"][shard_ordinal]
        start = int(selected_shard["start_ordinal"])
        stop = int(selected_shard["stop_ordinal_exclusive"])
    source_contexts = _source_contexts(replayed, evidence)
    for index in range(start, stop):
        disposition = evidence.dispositions[index]
        physical = replayed.physical_documents[index]
        record = _document_record(
            index=index,
            disposition=disposition,
            physical=physical,
            metadata=metadata,
            evidence=evidence,
        )
        body = _replay_body(
            disposition,
            physical,
            source_contexts=source_contexts,
        )
        yield record | {
            "body": body,
            "document_body_replay_verified": True,
        }


def _replay_inputs(
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
) -> tuple[
    closure_module.SealedDocumentClosurePlan,
    closure_module.DocumentClosureEvidence,
]:
    if type(closure_evidence) is not closure_module.DocumentClosureEvidence:
        raise ValueError("cninfo_document_postprocess_evidence_type_invalid")
    missing_capture = (
        closure_evidence.downloaded_parent.manifest_path
        if closure_evidence.downloaded_parent is not None
        else None
    )
    expected = closure_module.finalize_document_closure(
        sealed_plan,
        missing_capture,
    )
    if expected != closure_evidence or expected.complete is not True:
        raise ValueError("cninfo_document_postprocess_evidence_invalid")
    if len(expected.dispositions) != len(sealed_plan.physical_documents):
        raise ValueError("cninfo_document_postprocess_exact_cover_invalid")
    for disposition, physical in zip(
        expected.dispositions,
        sealed_plan.physical_documents,
        strict=True,
    ):
        if (
            disposition.announcement_id != physical.announcement_id
            or disposition.adjunct_url != physical.adjunct_url
        ):
            raise ValueError("cninfo_document_postprocess_order_invalid")
    return sealed_plan, expected


def _contract_semantic(
    replayed: closure_module.SealedDocumentClosurePlan,
    evidence: closure_module.DocumentClosureEvidence,
    *,
    max_documents_per_shard: int,
) -> dict[str, Any]:
    _validate_resource_budget(max_documents_per_shard, None)
    return {
        "schema_version": _CONTRACT_SCHEMA,
        "implementation_root": _implementation_root(),
        "sealed_plan_root": replayed.plan_root,
        "closure_root": evidence.closure_root,
        "physical_document_count": evidence.physical_document_count,
        "dispositions_root": _dispositions_root(evidence),
        "body_reference_root": _body_reference_root(evidence),
        "inventory_parents": [row.semantic() for row in replayed.inventory_parents],
        "document_parents": [
            row.semantic()
            for row in (
                *replayed.reusable_parents,
                *((evidence.downloaded_parent,) if evidence.downloaded_parent else ()),
            )
        ],
        "closure_blockers": list(evidence.blockers),
        "weak_source_ancestry": evidence.weak_source_ancestry,
        "max_documents_per_shard": max_documents_per_shard,
        "body_storage_policy": "signed_parent_reference_replay_one_body_v1",
        "data_admission_eligible": False,
        "pit_evidence_eligible": False,
        "independent_data_admission_verdict_required": True,
    }


def _inventory_metadata(
    replayed: closure_module.SealedDocumentClosurePlan,
) -> _InventoryMetadataIndex:
    return _InventoryMetadataIndex(replayed)


def _document_record(
    *,
    index: int,
    disposition: Any,
    physical: Any,
    metadata: _InventoryMetadataIndex,
    evidence: closure_module.DocumentClosureEvidence,
) -> dict[str, Any]:
    candidates = metadata.candidates(physical)
    if not candidates:
        raise ValueError("cninfo_document_postprocess_inventory_metadata_missing")
    metadata_blockers: list[str] = []

    def agreed(field: str) -> str:
        observed = {str(row.get(field) or "") for row in candidates}
        nonempty = {value for value in observed if value}
        if len(nonempty) > 1 or (nonempty and "" in observed):
            metadata_blockers.append(f"inventory_{field}_conflict")
            return ""
        return next(iter(nonempty), "")

    sec_code = agreed("sec_code")
    sec_name = agreed("sec_name")
    org_id = agreed("org_id")
    title = agreed("announcement_title")
    announcement_type = agreed("announcement_type")
    column_id = agreed("column_id")
    matched_leaves = sorted(
        {
            leaf
            for candidate in candidates
            for leaf in candidate["matched_leaves"]
        }
    )
    source_scope_roles = sorted(
        {_scope_role_from_leaf(leaf) for leaf in matched_leaves}
    )
    if not sec_code:
        metadata_blockers.append("inventory_sec_code_missing")
    if not title:
        metadata_blockers.append("inventory_announcement_title_missing")
    source_blockers = sorted(
        set(disposition.blockers)
        | set(evidence.blockers)
        | set(metadata_blockers)
        | {
            "security_identity_projection_required",
            "independent_data_admission_verdict_required",
        }
    )
    semantic = {
        "schema_version": _DOCUMENT_SCHEMA,
        "ordinal": index,
        "announcement_id": physical.announcement_id,
        "announcement_time": physical.announcement_time,
        "announcement_time_precision_proven": False,
        "announcement_title": title,
        "announcement_type": announcement_type,
        "column_id": column_id,
        "matched_leaves": matched_leaves,
        "source_scope_roles": source_scope_roles,
        "sec_code": sec_code,
        "sec_name": sec_name,
        "org_id": org_id,
        "security_id": "",
        "adjunct_url": physical.adjunct_url,
        "declared_adjunct_size_kb": physical.adjunct_size_kb,
        "document_format": _format_from_url(physical.adjunct_url),
        "document_sha256": disposition.document_body_sha256,
        "document_size_bytes": disposition.document_size_bytes,
        "source_request_id": disposition.parent_request_id,
        "source_request_semantic_hash": disposition.parent_request_semantic_hash,
        "source_raw_envelope_sha256": disposition.parent_raw_envelope_sha256,
        "source_raw_payload_sha256": disposition.parent_raw_payload_sha256,
        "source_parent_generation_id": disposition.parent_generation_id,
        "source_parent_content_hash": disposition.parent_content_hash,
        "source_parent_terminal_signature": disposition.parent_terminal_signature,
        "source_parent_publication_signature": disposition.parent_publication_signature,
        "source_inventory_records": [dict(row) for row in candidates],
        "source_inventory_content_hash": canonical_hash(
            sorted(row["inventory_content_hash"] for row in candidates)
        ),
        "source_inventory_scope_root": canonical_hash(
            {
                "source_inventory_records": [dict(row) for row in candidates],
                "source_scope_roles": source_scope_roles,
            }
        ),
        "source_document_closure_root": evidence.closure_root,
        "source_lineage_complete": bool(
            not disposition.weak_source_ancestry
            and not disposition.blockers
            and not evidence.weak_source_ancestry
        ),
        "source_governed_evidence_eligible": False,
        "closure_complete": evidence.complete,
        "closure_downstream_eligible": evidence.downstream_eligible,
        "closure_blockers": list(evidence.blockers),
        "governance_blockers": source_blockers,
        "data_admission_eligible": False,
        "pit_evidence_eligible": False,
        "independent_data_admission_verdict_required": True,
    }
    return semantic | {"document_record_id": canonical_hash(semantic)}


def _body_reference(*, index: int, disposition: Any) -> dict[str, Any]:
    return {
        "ordinal": index,
        "announcement_id": disposition.announcement_id,
        "adjunct_url": disposition.adjunct_url,
        "document_sha256": disposition.document_body_sha256,
        "document_size_bytes": disposition.document_size_bytes,
        "source_parent_generation_id": disposition.parent_generation_id,
        "source_parent_content_hash": disposition.parent_content_hash,
        "source_request_id": disposition.parent_request_id,
        "source_raw_envelope_sha256": (
            disposition.parent_raw_envelope_sha256
        ),
        "source_raw_payload_sha256": disposition.parent_raw_payload_sha256,
    }


def _body_reference_root(
    evidence: closure_module.DocumentClosureEvidence,
) -> str:
    digest = hashlib.sha256()
    for index, disposition in enumerate(evidence.dispositions):
        digest.update(
            _canonical_json_bytes(
                _body_reference(index=index, disposition=disposition)
            )
            + b"\n"
        )
    return digest.hexdigest()


def _dispositions_root(
    evidence: closure_module.DocumentClosureEvidence,
) -> str:
    digest = hashlib.sha256()
    for disposition in evidence.dispositions:
        digest.update(_canonical_json_bytes(disposition.semantic()) + b"\n")
    return digest.hexdigest()


def _write_shard(
    shards_root: Path,
    *,
    ordinal: int,
    start: int,
    stop: int,
    replayed: closure_module.SealedDocumentClosurePlan,
    evidence: closure_module.DocumentClosureEvidence,
    metadata: _InventoryMetadataIndex,
    contract_id: str,
) -> dict[str, Any]:
    stage = shards_root / f".shard-{ordinal:06d}.incomplete"
    if stage.exists():
        if stage.is_symlink() or not stage.is_dir():
            raise ValueError("cninfo_document_postprocess_stage_invalid")
        shutil.rmtree(stage)
    stage.mkdir()
    incomplete = stage / "records.jsonl"
    digest = hashlib.sha256()
    size = 0
    try:
        with incomplete.open("xb") as handle:
            for index in range(start, stop):
                row = _document_record(
                    index=index,
                    disposition=evidence.dispositions[index],
                    physical=replayed.physical_documents[index],
                    metadata=metadata,
                    evidence=evidence,
                )
                payload = _canonical_json_bytes(row) + b"\n"
                handle.write(payload)
                digest.update(payload)
                size += len(payload)
            handle.flush()
            os.fsync(handle.fileno())
        shard_semantic = _shard_semantic(
            contract_id=contract_id,
            ordinal=ordinal,
            start=start,
            stop=stop,
            records_sha256=digest.hexdigest(),
            records_size_bytes=size,
        )
        shard_id = "cninfo_documents_" + canonical_hash(shard_semantic)[:24]
        target = shards_root / shard_id
        if _path_entry_exists(target):
            raise ValueError("cninfo_document_postprocess_shard_collision")
        manifest = shard_semantic | {
            "shard_id": shard_id,
            "content_hash": canonical_hash(shard_semantic),
        }
        _atomic_json(stage / "shard_manifest.json", manifest)
        _fsync_directory(stage)
        os.replace(stage, target)
        _fsync_directory(shards_root)
        return manifest
    finally:
        if stage.exists() and stage.is_dir() and not stage.is_symlink():
            shutil.rmtree(stage)


def _validate_shard(
    shards_root: Path,
    *,
    ordinal: int,
    start: int,
    stop: int,
    replayed: closure_module.SealedDocumentClosurePlan,
    evidence: closure_module.DocumentClosureEvidence,
    metadata: _InventoryMetadataIndex,
    contract_id: str,
) -> dict[str, Any]:
    candidates = _shard_candidates(shards_root, ordinal)
    if len(candidates) != 1:
        raise ValueError("cninfo_document_postprocess_shard_missing_or_duplicate")
    shard_root, manifest = candidates[0]
    records_path = shard_root / "records.jsonl"
    if not records_path.is_file() or records_path.is_symlink():
        raise ValueError("cninfo_document_postprocess_records_invalid")
    digest = hashlib.sha256()
    size = 0
    observed_count = 0
    with records_path.open("rb") as handle:
        for expected_index in range(start, stop):
            line = handle.readline()
            if not line or not line.endswith(b"\n"):
                raise ValueError("cninfo_document_postprocess_records_truncated")
            digest.update(line)
            size += len(line)
            observed = _exact_json_bytes(line[:-1])
            expected = _document_record(
                index=expected_index,
                disposition=evidence.dispositions[expected_index],
                physical=replayed.physical_documents[expected_index],
                metadata=metadata,
                evidence=evidence,
            )
            if (
                observed != expected
                or line != _canonical_json_bytes(expected) + b"\n"
            ):
                raise ValueError("cninfo_document_postprocess_record_mismatch")
            observed_count += 1
        if handle.read(1):
            raise ValueError("cninfo_document_postprocess_records_extra")
    if observed_count != stop - start:
        raise ValueError("cninfo_document_postprocess_record_count_invalid")
    semantic = _shard_semantic(
        contract_id=contract_id,
        ordinal=ordinal,
        start=start,
        stop=stop,
        records_sha256=digest.hexdigest(),
        records_size_bytes=size,
    )
    expected_manifest = semantic | {
        "shard_id": "cninfo_documents_" + canonical_hash(semantic)[:24],
        "content_hash": canonical_hash(semantic),
    }
    if manifest != expected_manifest or shard_root.name != manifest.get("shard_id"):
        raise ValueError("cninfo_document_postprocess_shard_manifest_invalid")
    return expected_manifest


def _shard_candidates(
    shards_root: Path,
    ordinal: int,
) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    if shards_root.is_dir() and not shards_root.is_symlink():
        for path in shards_root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            manifest_path = path / "shard_manifest.json"
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            manifest = _exact_json_file(manifest_path)
            if manifest.get("ordinal") == ordinal:
                candidates.append((path, manifest))
    return candidates


def _shard_semantic(
    *,
    contract_id: str,
    ordinal: int,
    start: int,
    stop: int,
    records_sha256: str,
    records_size_bytes: int,
) -> dict[str, Any]:
    return {
        "schema_version": _SHARD_SCHEMA,
        "contract_id": contract_id,
        "ordinal": ordinal,
        "start_ordinal": start,
        "stop_ordinal_exclusive": stop,
        "document_count": stop - start,
        "records_role": "cninfo_document_postprocess_records",
        "records_sha256": records_sha256,
        "records_size_bytes": records_size_bytes,
        "body_storage_policy": "signed_parent_reference_replay_one_body_v1",
        "data_admission_eligible": False,
    }


def _checkpoint_shard(shard: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ordinal": shard["ordinal"],
        "shard_id": shard["shard_id"],
        "content_hash": shard["content_hash"],
        "document_count": shard["document_count"],
        "records_sha256": shard["records_sha256"],
    }


def _checkpoint_semantic(
    *,
    contract_id: str,
    shard_count: int,
    completed: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    semantic = {
        "schema_version": _CHECKPOINT_SCHEMA,
        "contract_id": contract_id,
        "shard_count": shard_count,
        "completed_shards": [dict(completed[key]) for key in sorted(completed)],
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def _load_checkpoint(
    path: Path,
    *,
    contract_id: str,
    shard_count: int,
) -> dict[str, Any]:
    if not path.exists():
        return _checkpoint_semantic(
            contract_id=contract_id,
            shard_count=shard_count,
            completed={},
        )
    payload = _exact_json_file(path)
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    rows = semantic.get("completed_shards")
    checkpoint_keys = {
        "ordinal",
        "shard_id",
        "content_hash",
        "document_count",
        "records_sha256",
    }
    row_shapes_valid = bool(
        isinstance(rows, list)
        and all(
            type(row) is dict
            and set(row) == checkpoint_keys
            and type(row.get("ordinal")) is int
            and 0 <= row["ordinal"] < shard_count
            and type(row.get("document_count")) is int
            and row["document_count"] > 0
            and type(row.get("shard_id")) is str
            and row["shard_id"].startswith("cninfo_documents_")
            and _HEX_64.fullmatch(str(row.get("content_hash") or ""))
            is not None
            and _HEX_64.fullmatch(str(row.get("records_sha256") or ""))
            is not None
            for row in rows
        )
    )
    ordinals = [row["ordinal"] for row in rows] if row_shapes_valid else []
    if (
        semantic.get("schema_version") != _CHECKPOINT_SCHEMA
        or semantic.get("contract_id") != contract_id
        or semantic.get("shard_count") != shard_count
        or not row_shapes_valid
        or ordinals != sorted(set(ordinals))
        or payload.get("content_hash") != canonical_hash(semantic)
    ):
        raise ValueError("cninfo_document_postprocess_checkpoint_invalid")
    return payload


def _manifest_semantic(
    *,
    contract: Mapping[str, Any],
    evidence: closure_module.DocumentClosureEvidence,
    shards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    shard_refs = [
        {
            "ordinal": row["ordinal"],
            "shard_id": row["shard_id"],
            "content_hash": row["content_hash"],
            "document_count": row["document_count"],
            "start_ordinal": row["start_ordinal"],
            "stop_ordinal_exclusive": row["stop_ordinal_exclusive"],
            "records_sha256": row["records_sha256"],
        }
        for row in shards
    ]
    semantic = {
        "schema_version": _MANIFEST_SCHEMA,
        "contract_id": contract["contract_id"],
        "sealed_plan_root": contract["sealed_plan_root"],
        "closure_root": evidence.closure_root,
        "document_count": evidence.physical_document_count,
        "shard_count": len(shard_refs),
        "shards": shard_refs,
        "shards_root": canonical_hash(shard_refs),
        "closure_blockers": list(evidence.blockers),
        "weak_source_ancestry": evidence.weak_source_ancestry,
        "exact_cover_verified": True,
        "body_storage_policy": "signed_parent_reference_replay_one_body_v1",
        "data_admission_eligible": False,
        "pit_evidence_eligible": False,
        "independent_data_admission_verdict_required": True,
        "safety": {
            "alpha_search_authorized": False,
            "holdout_activation_authorized": False,
            "paper_trading_authorized": False,
            "shadow_trading_authorized": False,
            "live_trading_authorized": False,
        },
    }
    content_hash = canonical_hash(semantic)
    return semantic | {
        "content_hash": content_hash,
        "generation_id": "cninfo_document_postprocess_" + content_hash[:24],
    }


def _validate_artifact_closure(
    root: Path,
    *,
    shards: Sequence[Mapping[str, Any]],
) -> None:
    expected_root_entries = {
        "checkpoint.json",
        "postprocess_contract.json",
        "postprocess_manifest.json",
        "shards",
    }
    if (
        root.is_symlink()
        or {path.name for path in root.iterdir()} != expected_root_entries
        or any(path.is_symlink() for path in root.iterdir())
    ):
        raise ValueError("cninfo_document_postprocess_artifact_closure_invalid")
    shards_root = root / "shards"
    expected_shard_ids = {str(row["shard_id"]) for row in shards}
    if (
        not shards_root.is_dir()
        or shards_root.is_symlink()
        or {path.name for path in shards_root.iterdir()} != expected_shard_ids
    ):
        raise ValueError("cninfo_document_postprocess_artifact_closure_invalid")
    for shard_id in sorted(expected_shard_ids):
        shard_root = shards_root / shard_id
        if (
            not shard_root.is_dir()
            or shard_root.is_symlink()
            or {path.name for path in shard_root.iterdir()}
            != {"records.jsonl", "shard_manifest.json"}
            or any(
                path.is_symlink() or not path.is_file()
                for path in shard_root.iterdir()
            )
        ):
            raise ValueError(
                "cninfo_document_postprocess_artifact_closure_invalid"
            )


def _source_contexts(
    replayed: closure_module.SealedDocumentClosurePlan,
    evidence: closure_module.DocumentClosureEvidence,
) -> _SourceResolver:
    return _SourceResolver(replayed, evidence)


def _replay_body(
    disposition: Any,
    physical: Any,
    *,
    source_contexts: _SourceResolver,
) -> bytes:
    context = source_contexts.context(disposition)
    request = context.request_by_id.get(disposition.parent_request_id)
    terminal = context.terminal_by_id.get(disposition.parent_request_id)
    if request is None or terminal is None:
        raise ValueError("cninfo_document_postprocess_source_request_missing")
    if (
        canonical_hash(request) != disposition.parent_request_semantic_hash
        or terminal.get("request_semantic_hash")
        != disposition.parent_request_semantic_hash
        or terminal.get("raw_envelope_sha256")
        != disposition.parent_raw_envelope_sha256
    ):
        raise ValueError("cninfo_document_postprocess_source_binding_invalid")
    body, raw_payload_sha256 = closure_module._replay_official_body(
        context.root,
        request=request,
        terminal=terminal,
    )
    document_format = closure_module._document_format(body, physical.adjunct_url)
    if (
        hashlib.sha256(body).hexdigest() != disposition.document_body_sha256
        or len(body) != disposition.document_size_bytes
        or raw_payload_sha256 != disposition.parent_raw_payload_sha256
        or document_format != _format_from_url(physical.adjunct_url)
        or closure_module._document_block_reason(body) is not None
        or not closure_module._document_structure_valid(
            body,
            document_format=document_format,
            announcement_id=physical.announcement_id,
            announcement_time=physical.announcement_time,
        )
    ):
        raise ValueError("cninfo_document_postprocess_body_replay_invalid")
    return body


def _format_from_url(adjunct_url: str) -> str:
    suffix = Path(adjunct_url).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    raise ValueError("cninfo_document_postprocess_document_format_invalid")


def _scope_role_from_leaf(leaf_id: str) -> str:
    match = re.fullmatch(
        r"(?P<role>[a-z_]+)_[0-9]{6}(?:_d[0-9]{2}_[0-9]{2})?",
        leaf_id,
    )
    if match is None:
        raise ValueError("cninfo_document_postprocess_inventory_scope_invalid")
    return str(match.group("role"))


def _validate_resource_budget(
    max_documents_per_shard: int,
    shard_budget: int | None,
) -> None:
    if (
        type(max_documents_per_shard) is not int
        or max_documents_per_shard <= 0
        or max_documents_per_shard > _MAX_DOCUMENTS_PER_SHARD
        or (
            shard_budget is not None
            and (type(shard_budget) is not int or shard_budget <= 0)
        )
    ):
        raise ValueError("cninfo_document_postprocess_resource_budget_invalid")


def _ensure_directory(path: Path, *, create: bool) -> None:
    _reject_symlink_components(path)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not create:
            raise ValueError(
                "cninfo_document_postprocess_output_root_invalid"
            ) from None
        try:
            path.mkdir()
            _fsync_directory(path.parent)
            _reject_symlink_components(path)
            mode = path.lstat().st_mode
        except (FileExistsError, FileNotFoundError, NotADirectoryError) as exc:
            raise ValueError(
                "cninfo_document_postprocess_output_root_invalid"
            ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ValueError("cninfo_document_postprocess_output_root_invalid")


def _lock_claim_root(
    sealed_plan: closure_module.SealedDocumentClosurePlan,
    closure_evidence: closure_module.DocumentClosureEvidence,
    *,
    max_documents_per_shard: int | None,
) -> str:
    return canonical_hash(
        {
            "sealed_plan_root": getattr(sealed_plan, "plan_root", None),
            "closure_root": getattr(closure_evidence, "closure_root", None),
            "implementation_root": _implementation_root(),
            "max_documents_per_shard": max_documents_per_shard,
        }
    )


@contextmanager
def _output_lock(
    output_root: Path,
    *,
    claim_root: str,
) -> Iterator[None]:
    _reject_symlink_components(output_root)
    _ensure_directory(output_root.parent, create=False)
    if _HEX_64.fullmatch(claim_root) is None:
        raise ValueError("cninfo_document_postprocess_lock_claim_invalid")
    lock_path = output_root.parent / f".{output_root.name}.postprocess.lock"
    _reject_symlink_components(lock_path)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError("cninfo_document_postprocess_lock_invalid") from exc
    with os.fdopen(descriptor, "r+b", closefd=True) as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("cninfo_document_postprocess_output_locked") from exc
        try:
            binding = _canonical_json_bytes(
                {
                    "schema_version": "cninfo_document_postprocess_lock_v1",
                    "claim_root": claim_root,
                }
            ) + b"\n"
            handle.seek(0)
            handle.truncate()
            handle.write(binding)
            handle.flush()
            os.fsync(handle.fileno())
            _fsync_directory(lock_path.parent)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _implementation_root() -> str:
    return canonical_hash(
        {
            "module_sha256": sha256_file(Path(__file__)),
            "closure_module_sha256": sha256_file(
                Path(str(closure_module.__file__))
            ),
            "schemas": {
                "contract": _CONTRACT_SCHEMA,
                "checkpoint": _CHECKPOINT_SCHEMA,
                "document": _DOCUMENT_SCHEMA,
                "shard": _SHARD_SCHEMA,
                "manifest": _MANIFEST_SCHEMA,
            },
            "max_documents_per_shard": _MAX_DOCUMENTS_PER_SHARD,
            "max_inventory_rows_per_parent": (
                _MAX_INVENTORY_ROWS_PER_PARENT
            ),
            "body_storage_policy": "signed_parent_reference_replay_one_body_v1",
        }
    )


def _exact_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("cninfo_document_postprocess_artifact_missing")
    payload = path.read_bytes()
    value = _exact_json_bytes(payload)
    if payload != _canonical_json_bytes(value) + b"\n":
        raise ValueError("cninfo_document_postprocess_json_noncanonical")
    return value


def _exact_json_bytes(payload: bytes) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("cninfo_document_postprocess_json_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=hook)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cninfo_document_postprocess_json_invalid") from exc
    if type(value) is not dict:
        raise ValueError("cninfo_document_postprocess_json_object_required")
    return value


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical_json_bytes(value) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if path.is_symlink() or _path_entry_exists(temporary):
        raise ValueError("cninfo_document_postprocess_atomic_target_invalid")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _reject_symlink_components(path: Path) -> None:
    lexical = path.absolute()
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(
                "cninfo_document_postprocess_output_root_invalid"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ValueError(
                "cninfo_document_postprocess_output_root_invalid"
            )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            "cninfo_document_postprocess_directory_fsync_failed"
        ) from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ValueError(
            "cninfo_document_postprocess_directory_fsync_failed"
        ) from exc
    finally:
        os.close(descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build resumable CNINFO document-reference shards."
    )
    parser.add_argument("--inventory-manifest", action="append", required=True)
    parser.add_argument("--reusable-document-manifest", action="append", default=[])
    parser.add_argument("--year", action="append", type=int, required=True)
    parser.add_argument("--missing-document-capture")
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--max-documents-per-shard",
        type=int,
        default=_MAX_DOCUMENTS_PER_SHARD,
    )
    parser.add_argument("--shard-budget", type=int)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    exit_code = 0
    try:
        plan = closure_module.prepare_document_closure(
            args.inventory_manifest,
            args.reusable_document_manifest,
            args.year,
        )
        evidence = closure_module.finalize_document_closure(
            plan,
            args.missing_document_capture,
        )
        result = build_cninfo_document_postprocess(
            plan,
            evidence,
            args.output_root,
            max_documents_per_shard=args.max_documents_per_shard,
            shard_budget=args.shard_budget,
        )
    except (OSError, ValueError) as exc:
        result = {
            "status": "blocked",
            "reason": str(exc) or type(exc).__name__,
            "network_called": False,
            "data_admission_eligible": False,
            "pit_evidence_eligible": False,
        }
        exit_code = 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
