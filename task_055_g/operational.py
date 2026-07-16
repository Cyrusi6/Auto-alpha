"""Authoritative operational-state registry, seal, and independent verifier.

The Task 055-A/055-D compatibility scanners accepted caller supplied roots and
could therefore prove an empty shadow directory while production writers used
different locations.  This module binds each production writer to a single
canonical root derived from repository configuration, scans its registered
legacy roots as well, and counts physical records rather than summary fields.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


WRITER_REGISTRY_SCHEMA = "task055g_authoritative_writer_root_registry_v1"
SCAN_LEDGER_SCHEMA = "task055g_operational_physical_scan_ledger_v1"
GENESIS_SCHEMA = "task055g_operational_genesis_v1"
OPERATIONAL_SEAL_SCHEMA = "task055g_authoritative_operational_seal_v1"
OPERATIONAL_POINTER_SCHEMA = "task055g_authoritative_operational_pointer_v1"

OPERATIONAL_STATES = (
    "certification_queue",
    "certified_pool",
    "portfolio_campaign",
    "production_candidate",
    "optimizer_activation",
    "paper_registry",
    "live_registry",
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class OperationalSealError(RuntimeError):
    """Raised when operational evidence cannot be proven fail closed."""


@dataclass(frozen=True)
class StateRule:
    state: str
    equals: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class FileContract:
    filename: str
    kind: str
    required_fields: tuple[str, ...] = ()
    state_rules: tuple[StateRule, ...] = ()


@dataclass(frozen=True)
class WriterContract:
    writer_id: str
    writer_fqn: str
    source_files: tuple[str, ...]
    canonical_root: str
    legacy_roots: tuple[str, ...]
    files: tuple[FileContract, ...]


def build_authoritative_writer_registry(authority_root: str | Path) -> dict[str, Any]:
    """Build the machine-readable writer/root registry from production code.

    ``authority_root`` is only the filesystem anchor.  Callers cannot replace
    individual queue roots; those are fixed by the production writer and
    dashboard/strategy configuration contracts below.
    """

    root = _validate_authority_root(authority_root, allow_missing=True)
    contracts = _writer_contracts()
    canonical = [contract.canonical_root for contract in contracts]
    if len(canonical) != len(set(canonical)):
        raise OperationalSealError("task055g_writer_canonical_root_not_unique")
    resolved_roots: set[Path] = set()
    writers: list[dict[str, Any]] = []
    for contract in contracts:
        canonical_path = _safe_join(root, contract.canonical_root)
        if canonical_path in resolved_roots:
            raise OperationalSealError(f"task055g_writer_root_collision:{contract.writer_id}")
        resolved_roots.add(canonical_path)
        source_proofs = []
        for relative in contract.source_files:
            source = _safe_join(_REPOSITORY_ROOT, relative)
            if not source.is_file() or source.is_symlink():
                raise OperationalSealError(f"task055g_writer_source_missing:{contract.writer_id}:{relative}")
            source_proofs.append({"relative_path": relative, "sha256": _sha256(source)})
        writers.append(
            {
                "writer_id": contract.writer_id,
                "writer_fqn": contract.writer_fqn,
                "canonical_root": contract.canonical_root,
                "legacy_roots": list(contract.legacy_roots),
                "source_proofs": source_proofs,
                "file_contracts": [_file_contract_payload(item) for item in contract.files],
            }
        )
    semantic = {
        "schema_version": WRITER_REGISTRY_SCHEMA,
        "authority_layout": "repository_config_relative_v1",
        "operational_states": list(OPERATIONAL_STATES),
        "writers": writers,
        "shadow_operational_state_accepted": False,
    }
    return semantic | {"content_hash": _canonical_hash(semantic)}


def initialize_operational_genesis(
    authority_root: str | Path,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create canonical writer directories only when every registered root is empty.

    The initializer never writes fake queue files or self-declared zero counts.
    Its evidence is returned to the caller and is published beside the seal.
    """

    root = _validate_authority_root(authority_root, allow_missing=True)
    registry_payload = dict(registry or build_authoritative_writer_registry(root))
    _validate_registry_payload(registry_payload)
    occupied: list[str] = []
    for writer in registry_payload["writers"]:
        for relative in [writer["canonical_root"], *writer["legacy_roots"]]:
            candidate = _safe_join(root, relative)
            if candidate.is_symlink():
                raise OperationalSealError(f"task055g_operational_root_symlink:{relative}")
            if candidate.exists():
                _assert_no_symlinks(candidate, root)
                if candidate.is_file() or any(candidate.iterdir()):
                    occupied.append(relative)
    if occupied:
        raise OperationalSealError(f"task055g_genesis_requires_empty_registered_roots:{sorted(set(occupied))}")
    created = []
    for writer in registry_payload["writers"]:
        candidate = _safe_join(root, str(writer["canonical_root"]))
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            created.append(str(writer["canonical_root"]))
    semantic = {
        "schema_version": GENESIS_SCHEMA,
        "writer_registry_content_hash": registry_payload["content_hash"],
        "created_canonical_roots": sorted(created),
        "created_record_files": 0,
        "physical_zero_claim": False,
    }
    return semantic | {"content_hash": _canonical_hash(semantic)}


def scan_authoritative_operational_state(
    authority_root: str | Path,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan all registered current/legacy roots and count physical records."""

    root = _validate_authority_root(authority_root, allow_missing=False)
    registry_payload = dict(registry or build_authoritative_writer_registry(root))
    _validate_registry_payload(registry_payload)
    state_counts = {name: 0 for name in OPERATIONAL_STATES}
    writer_rows: list[dict[str, Any]] = []
    seen_physical_roots: dict[Path, str] = {}
    blockers: list[str] = []
    for writer in registry_payload["writers"]:
        roots = []
        for role, relative in (
            [("canonical", writer["canonical_root"])]
            + [("legacy", value) for value in writer["legacy_roots"]]
        ):
            candidate = _safe_join(root, str(relative))
            if candidate.is_symlink():
                raise OperationalSealError(f"task055g_operational_root_symlink:{relative}")
            if not candidate.exists():
                if role == "canonical":
                    blockers.append(f"canonical_root_missing:{writer['writer_id']}:{relative}")
                roots.append(
                    {
                        "root_role": role,
                        "relative_root": str(relative),
                        "status": "missing",
                        "file_count": 0,
                        "physical_record_count": 0,
                        "content_root": _canonical_hash([]),
                        "files": [],
                    }
                )
                continue
            resolved = candidate.resolve()
            previous = seen_physical_roots.get(resolved)
            if previous is not None:
                raise OperationalSealError(
                    f"task055g_registered_root_alias:{previous}:{writer['writer_id']}:{relative}"
                )
            seen_physical_roots[resolved] = str(writer["writer_id"])
            scanned = _scan_root(root, candidate, writer)
            for state, count in scanned.pop("state_counts").items():
                state_counts[state] += int(count)
            roots.append({"root_role": role, "relative_root": str(relative), **scanned})
        writer_rows.append(
            {
                "writer_id": writer["writer_id"],
                "canonical_root": writer["canonical_root"],
                "roots": roots,
                "physical_record_count": sum(int(item["physical_record_count"]) for item in roots),
            }
        )
    total = sum(state_counts.values())
    semantic = {
        "schema_version": SCAN_LEDGER_SCHEMA,
        "writer_registry_content_hash": registry_payload["content_hash"],
        "status": "passed" if not blockers and total == 0 else "blocked",
        "state_counts": state_counts,
        "total_operational_record_count": total,
        "blockers": sorted(blockers),
        "writers": writer_rows,
    }
    return semantic | {"content_hash": _canonical_hash(semantic)}


def publish_authoritative_operational_seal(
    authority_root: str | Path,
    output_root: str | Path,
    *,
    initialize_genesis: bool = False,
) -> dict[str, Any]:
    """Publish registry, physical scan ledger, and immutable operational seal."""

    root = _validate_authority_root(authority_root, allow_missing=True)
    output = Path(output_root)
    if output.is_symlink():
        raise OperationalSealError("task055g_operational_output_symlink")
    registry = build_authoritative_writer_registry(root)
    genesis = initialize_operational_genesis(root, registry) if initialize_genesis else None
    scan = scan_authoritative_operational_state(root, registry)
    semantic = {
        "schema_version": OPERATIONAL_SEAL_SCHEMA,
        "status": scan["status"],
        "writer_registry_content_hash": registry["content_hash"],
        "physical_scan_content_hash": scan["content_hash"],
        "genesis_content_hash": None if genesis is None else genesis["content_hash"],
        "state_counts": scan["state_counts"],
        "total_operational_record_count": scan["total_operational_record_count"],
        "blockers": scan["blockers"],
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "immutable": True,
    }
    content_hash = _canonical_hash(semantic)
    generation_id = f"operational_seal_{content_hash[:24]}"
    seal = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    target = output / "generations" / generation_id
    files = {
        "writer_registry.json": registry,
        "physical_scan_ledger.json": scan,
        "operational_seal.json": seal,
    }
    if genesis is not None:
        files["operational_genesis.json"] = genesis
    _publish_generation(target, files)
    _atomic_json(
        output / "current.json",
        {
            "schema_version": OPERATIONAL_POINTER_SCHEMA,
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/operational_seal.json",
        },
    )
    return seal | {"manifest_path": str(target / "operational_seal.json")}


def verify_authoritative_operational_seal(
    authority_root: str | Path,
    seal_or_root: str | Path,
) -> dict[str, Any]:
    """Independently rebuild the registry and rescan physical writer roots."""

    root = _validate_authority_root(authority_root, allow_missing=False)
    seal_path = _resolve_seal_path(seal_or_root)
    if seal_path.is_symlink() or not seal_path.is_file():
        raise OperationalSealError("task055g_operational_seal_missing")
    generation = seal_path.parent
    seal = _read_json(seal_path)
    registry = _read_json(generation / "writer_registry.json")
    recorded_scan = _read_json(generation / "physical_scan_ledger.json")
    _validate_content_hash(seal, "task055g_operational_seal_content_hash_mismatch")
    expected_generation_id = f"operational_seal_{str(seal.get('content_hash') or '')[:24]}"
    if seal.get("generation_id") != expected_generation_id or generation.name != expected_generation_id:
        raise OperationalSealError("task055g_operational_generation_identity_mismatch")
    _validate_registry_payload(registry)
    _validate_content_hash(recorded_scan, "task055g_operational_scan_content_hash_mismatch")
    genesis_hash = seal.get("genesis_content_hash")
    genesis_path = generation / "operational_genesis.json"
    if genesis_hash is not None:
        genesis = _read_json(genesis_path)
        _validate_content_hash(genesis, "task055g_operational_genesis_content_hash_mismatch")
        if genesis.get("schema_version") != GENESIS_SCHEMA or genesis.get("content_hash") != genesis_hash:
            raise OperationalSealError("task055g_operational_genesis_lineage_mismatch")
    elif genesis_path.exists():
        raise OperationalSealError("task055g_unreferenced_operational_genesis")
    rebuilt_registry = build_authoritative_writer_registry(root)
    if registry != rebuilt_registry:
        raise OperationalSealError("task055g_writer_registry_drift")
    rescanned = scan_authoritative_operational_state(root, rebuilt_registry)
    if recorded_scan != rescanned:
        raise OperationalSealError("task055g_operational_physical_state_drift")
    if seal.get("writer_registry_content_hash") != rebuilt_registry["content_hash"]:
        raise OperationalSealError("task055g_operational_seal_registry_hash_mismatch")
    if seal.get("physical_scan_content_hash") != rescanned["content_hash"]:
        raise OperationalSealError("task055g_operational_seal_scan_hash_mismatch")
    expected = {
        "schema_version": OPERATIONAL_SEAL_SCHEMA,
        "status": rescanned["status"],
        "writer_registry_content_hash": rebuilt_registry["content_hash"],
        "physical_scan_content_hash": rescanned["content_hash"],
        "genesis_content_hash": seal.get("genesis_content_hash"),
        "state_counts": rescanned["state_counts"],
        "total_operational_record_count": rescanned["total_operational_record_count"],
        "blockers": rescanned["blockers"],
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "immutable": True,
    }
    if seal.get("content_hash") != _canonical_hash(expected):
        raise OperationalSealError("task055g_operational_seal_semantic_mismatch")
    if seal.get("status") != "passed" or int(seal.get("total_operational_record_count", -1)) != 0:
        raise OperationalSealError("task055g_operational_state_not_empty")
    return {
        "status": "passed",
        "content_hash": seal["content_hash"],
        "writer_registry_content_hash": rebuilt_registry["content_hash"],
        "physical_scan_content_hash": rescanned["content_hash"],
        "state_counts": rescanned["state_counts"],
    }


# Stable public aliases used by the Task 055-G DAG.
build_authoritative_writer_root_registry = build_authoritative_writer_registry
scan_operational_state = scan_authoritative_operational_state
publish_operational_seal = publish_authoritative_operational_seal
verify_operational_seal = verify_authoritative_operational_seal


def _writer_contracts() -> tuple[WriterContract, ...]:
    json_object = "json_object"
    jsonl = "jsonl"
    text = "text"
    no_state: tuple[StateRule, ...] = ()
    return (
        WriterContract(
            writer_id="validation_campaign_store",
            writer_fqn="validation_campaign_store.registry.LocalValidationCampaignStore",
            source_files=("validation_campaign_store/registry.py", "dashboard/config.py"),
            canonical_root="artifacts/validation_campaign_store",
            legacy_roots=("validation_campaign_store",),
            files=(
                FileContract("validation_campaign_registry.json", json_object),
                FileContract("validation_campaigns.jsonl", jsonl, ("validation_campaign_id",)),
                FileContract("validation_candidates.jsonl", jsonl, ("validation_candidate_id", "factor_id")),
                FileContract("validation_shards.jsonl", jsonl, ("shard_id", "validation_campaign_id", "status")),
                FileContract("validation_candidate_results.jsonl", jsonl, ("validation_candidate_id", "factor_id", "validation_status")),
                FileContract("validation_leaderboard.jsonl", jsonl, ("rank", "validation_candidate_id", "factor_id")),
                FileContract(
                    "factor_certification_queue.jsonl",
                    jsonl,
                    ("queue_id", "validation_candidate_id", "factor_id", "priority"),
                    (StateRule("certification_queue"),),
                ),
                FileContract("validation_campaign_store_report.json", json_object),
                FileContract("validation_campaign_artifact_catalog.json", json_object),
                FileContract("validation_candidate_pool_results.jsonl", jsonl),
            ),
        ),
        WriterContract(
            writer_id="certification_campaign_store",
            writer_fqn="certification_campaign_store.registry.LocalFactorCertificationCampaignStore",
            source_files=("certification_campaign_store/registry.py", "dashboard/config.py"),
            canonical_root="artifacts/factor_certification_campaign",
            legacy_roots=("factor_certification_campaign",),
            files=(
                FileContract("factor_certification_campaign_registry.json", json_object),
                FileContract("factor_certification_campaigns.jsonl", jsonl, ("certification_campaign_id",)),
                FileContract("factor_certification_items.jsonl", jsonl, ("item_id", "factor_id", "status")),
                FileContract("factor_certification_campaign_report.json", json_object),
                FileContract(
                    "certified_factor_pool.jsonl",
                    jsonl,
                    ("certified_factor_pool_id", "factor_id", "certification_status"),
                    (StateRule("certified_pool"),),
                ),
                FileContract("certified_factor_leaderboard.jsonl", jsonl, ("rank", "certified_factor_pool_id", "factor_id")),
                FileContract("factor_certification_campaign_artifact_catalog.json", json_object),
            ),
        ),
        WriterContract(
            writer_id="portfolio_campaign_store",
            writer_fqn="portfolio_campaign_store.registry.LocalPortfolioCampaignStore",
            source_files=("portfolio_campaign_store/registry.py", "dashboard/config.py"),
            canonical_root="artifacts/portfolio_campaign",
            legacy_roots=("portfolio_campaign",),
            files=(
                FileContract("portfolio_certification_campaign_registry.json", json_object),
                FileContract(
                    "portfolio_certification_campaigns.jsonl",
                    jsonl,
                    ("portfolio_campaign_id",),
                    (StateRule("portfolio_campaign"),),
                ),
                FileContract("portfolio_candidate_items.jsonl", jsonl, ("item_id", "factor_id", "status")),
                FileContract("portfolio_certification_campaign_report.json", json_object),
                FileContract(
                    "production_candidate_bundle.jsonl",
                    jsonl,
                    ("production_candidate_bundle_id", "factor_id", "portfolio_certification_status"),
                    (StateRule("production_candidate"),),
                ),
                FileContract("production_candidate_bundle_report.json", json_object),
                FileContract(
                    "optimizer_policy_activation_queue.jsonl",
                    jsonl,
                    ("activation_queue_id", "factor_id", "status"),
                    (StateRule("optimizer_activation"),),
                ),
                FileContract("portfolio_campaign_artifact_catalog.json", json_object),
            ),
        ),
        WriterContract(
            writer_id="model_registry",
            writer_fqn="model_registry.store.LocalModelRegistry",
            source_files=("model_registry/store.py", "dashboard/config.py"),
            canonical_root="artifacts/model_registry",
            legacy_roots=("model_registry",),
            files=(
                FileContract(
                    "model_versions.jsonl",
                    jsonl,
                    ("model_version_id", "factor_id", "lifecycle_status"),
                    (StateRule("production_candidate", (("lifecycle_status", "production_candidate"),)),),
                ),
                FileContract(
                    "model_deployments.jsonl",
                    jsonl,
                    ("deployment_id", "model_version_id", "status"),
                    (
                        StateRule("paper_registry", (("status", "active"), ("environment", "paper"))),
                        StateRule("live_registry", (("status", "active"), ("environment", "live"))),
                        StateRule("optimizer_activation", (("status", "active"), ("model_kind", "optimizer_policy"))),
                    ),
                ),
                FileContract("lifecycle_events.jsonl", jsonl, ("event_id", "model_version_id", "action")),
                FileContract("model_state.json", json_object),
                FileContract("model_registry_manifest.json", json_object),
                FileContract("model_registry_report.json", json_object),
                FileContract("model_lineage_graph.json", json_object),
                FileContract("production_candidate_bundle_registry.json", json_object),
            ),
        ),
        WriterContract(
            writer_id="paper_account",
            writer_fqn="paper_account.ledger.LocalPaperAccount",
            source_files=("paper_account/ledger.py", "strategy_manager/config.py", "dashboard/config.py"),
            canonical_root="artifacts/account",
            legacy_roots=("account",),
            files=(
                FileContract("account_state.json", json_object, ("account_id", "cash", "positions"), (StateRule("paper_registry"),)),
                *tuple(
                    FileContract(name, jsonl, state_rules=(StateRule("paper_registry"),))
                    for name in (
                        "positions.jsonl",
                        "cash_ledger.jsonl",
                        "trade_ledger.jsonl",
                        "account_snapshots.jsonl",
                        "corporate_action_ledger.jsonl",
                        "settlement_ledger.jsonl",
                        "position_lots.jsonl",
                        "settlement_events.jsonl",
                        "cash_buckets.jsonl",
                        "position_availability.jsonl",
                        "realized_pnl.jsonl",
                        "account_nav.jsonl",
                        "adjustment_ledger.jsonl",
                    )
                ),
                FileContract("account_performance_report.json", json_object),
            ),
        ),
        WriterContract(
            writer_id="production_orchestrator",
            writer_fqn="production_orchestrator.state.LocalProductionStateStore",
            source_files=("production_orchestrator/state.py", "production_orchestrator/report.py", "dashboard/config.py"),
            canonical_root="artifacts/production_orchestrator",
            legacy_roots=("production_orchestrator", "artifacts/production"),
            files=(
                FileContract("production_runs.jsonl", jsonl, state_rules=(StateRule("live_registry"),)),
                FileContract("production_run_state.json", json_object, state_rules=(StateRule("live_registry"),)),
                FileContract("production_phase_runs.jsonl", jsonl),
                FileContract("production_gate_results.jsonl", jsonl),
                FileContract("production_run_events.jsonl", jsonl),
                FileContract("production_runbook.json", json_object),
                FileContract("production_run_plan.json", json_object),
                FileContract("production_orchestrator_report.json", json_object),
                FileContract("production_readiness_report.json", json_object),
                FileContract("production_day_package.json", json_object),
                FileContract("production_run.json", json_object, state_rules=(StateRule("live_registry"),)),
                FileContract("production_run.md", text),
                FileContract("production_run_plan.md", text),
                FileContract("production_orchestrator_report.md", text),
            ),
        ),
    )


def _scan_root(authority_root: Path, root: Path, writer: Mapping[str, Any]) -> dict[str, Any]:
    if not root.is_dir():
        raise OperationalSealError(f"task055g_registered_root_not_directory:{root.relative_to(authority_root)}")
    _assert_no_symlinks(root, authority_root)
    contract_by_name = {row["filename"]: row for row in writer["file_contracts"]}
    files = []
    state_counts = {name: 0 for name in OPERATIONAL_STATES}
    for candidate in _walk_files_no_symlinks(root):
        relative = candidate.relative_to(authority_root).as_posix()
        relative_within = candidate.relative_to(root).as_posix()
        if candidate.name.endswith(".schema.json"):
            base_name = candidate.name[: -len(".schema.json")]
            if base_name not in contract_by_name:
                raise OperationalSealError(f"task055g_unknown_schema_sidecar:{relative}")
            payload = _read_json(candidate)
            if not isinstance(payload, dict):
                raise OperationalSealError(f"task055g_schema_sidecar_invalid:{relative}")
            files.append(
                {
                    "relative_path": relative,
                    "relative_to_writer_root": relative_within,
                    "kind": "schema_sidecar",
                    "sha256": _sha256(candidate),
                    "size": candidate.stat().st_size,
                    "physical_record_count": 0,
                    "state_counts": {},
                }
            )
            continue
        contract = contract_by_name.get(candidate.name)
        if contract is None:
            raise OperationalSealError(f"task055g_unknown_operational_format_or_schema:{writer['writer_id']}:{relative}")
        rows = _parse_file(candidate, contract)
        local_counts = {name: 0 for name in OPERATIONAL_STATES}
        for row in rows:
            for rule in contract["state_rules"]:
                if _matches_rule(row, rule):
                    local_counts[str(rule["state"])] += 1
        for state, count in local_counts.items():
            state_counts[state] += count
        files.append(
            {
                "relative_path": relative,
                "relative_to_writer_root": relative_within,
                "kind": contract["kind"],
                "sha256": _sha256(candidate),
                "size": candidate.stat().st_size,
                "physical_record_count": len(rows),
                "state_counts": {key: value for key, value in local_counts.items() if value},
            }
        )
    files.sort(key=lambda row: row["relative_path"])
    operational_count = sum(state_counts.values())
    return {
        "status": "empty" if operational_count == 0 else "nonempty",
        "file_count": len(files),
        "physical_record_count": sum(int(row["physical_record_count"]) for row in files),
        "operational_record_count": operational_count,
        "content_root": _canonical_hash(files),
        "files": files,
        "state_counts": state_counts,
    }


def _parse_file(path: Path, contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    kind = str(contract["kind"])
    if kind == "text":
        path.read_text(encoding="utf-8")
        return []
    if kind == "jsonl":
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OperationalSealError(f"task055g_jsonl_invalid:{path.name}:{line_number}") from exc
            if not isinstance(row, dict):
                raise OperationalSealError(f"task055g_jsonl_record_not_object:{path.name}:{line_number}")
            _validate_required_fields(row, contract, path.name, line_number)
            rows.append(row)
        return rows
    if kind == "json_object":
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise OperationalSealError(f"task055g_json_object_required:{path.name}")
        _validate_required_fields(payload, contract, path.name, 1)
        return [payload]
    raise OperationalSealError(f"task055g_unknown_file_contract_kind:{kind}")


def _validate_required_fields(
    row: Mapping[str, Any],
    contract: Mapping[str, Any],
    filename: str,
    line_number: int,
) -> None:
    missing = [field for field in contract["required_fields"] if field not in row]
    if missing:
        raise OperationalSealError(
            f"task055g_operational_schema_required_field_missing:{filename}:{line_number}:{missing}"
        )


def _matches_rule(row: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    conditions = rule.get("equals") or []
    return all(str(row.get(str(field), "")) == str(expected) for field, expected in conditions)


def _file_contract_payload(contract: FileContract) -> dict[str, Any]:
    return {
        "filename": contract.filename,
        "kind": contract.kind,
        "required_fields": list(contract.required_fields),
        "state_rules": [
            {"state": rule.state, "equals": [[field, value] for field, value in rule.equals]}
            for rule in contract.state_rules
        ],
    }


def _validate_registry_payload(payload: Mapping[str, Any]) -> None:
    _validate_content_hash(payload, "task055g_writer_registry_content_hash_mismatch")
    if payload.get("schema_version") != WRITER_REGISTRY_SCHEMA:
        raise OperationalSealError("task055g_writer_registry_schema_invalid")
    writers = payload.get("writers")
    if not isinstance(writers, list) or not writers:
        raise OperationalSealError("task055g_writer_registry_empty")
    roots = [str(row.get("canonical_root") or "") for row in writers if isinstance(row, dict)]
    if not all(roots) or len(roots) != len(set(roots)):
        raise OperationalSealError("task055g_writer_registry_root_uniqueness_invalid")
    states = set(payload.get("operational_states") or [])
    if states != set(OPERATIONAL_STATES):
        raise OperationalSealError("task055g_writer_registry_state_set_invalid")


def _validate_content_hash(payload: Mapping[str, Any], error: str) -> None:
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if payload.get("content_hash") != _canonical_hash(semantic):
        raise OperationalSealError(error)


def _resolve_seal_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_file():
        return path
    pointer_path = path / "current.json"
    pointer = _read_json(pointer_path)
    if pointer.get("schema_version") != OPERATIONAL_POINTER_SCHEMA:
        raise OperationalSealError("task055g_operational_pointer_schema_invalid")
    manifest = str(pointer.get("manifest") or "")
    if not manifest:
        raise OperationalSealError("task055g_operational_pointer_manifest_missing")
    resolved = _safe_join(path, manifest)
    if resolved.parent.parent.parent != path.resolve():
        raise OperationalSealError("task055g_operational_pointer_escape")
    return resolved


def _publish_generation(target: Path, files: Mapping[str, Mapping[str, Any]]) -> None:
    if target.is_symlink():
        raise OperationalSealError("task055g_operational_generation_symlink")
    if target.exists():
        for name, payload in files.items():
            existing = target / name
            if not existing.is_file() or _read_json(existing) != dict(payload):
                raise OperationalSealError("task055g_operational_generation_collision")
        extra = sorted(path.name for path in target.iterdir() if path.name not in files)
        if extra:
            raise OperationalSealError(f"task055g_operational_generation_extra_files:{extra}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for name, payload in files.items():
            _atomic_json(temporary / name, payload)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _walk_files_no_symlinks(root: Path) -> Iterable[Path]:
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise OperationalSealError(f"task055g_nested_operational_symlink:{candidate.relative_to(root)}")
        for name in filenames:
            candidate = current_path / name
            if candidate.is_symlink():
                raise OperationalSealError(f"task055g_nested_operational_symlink:{candidate.relative_to(root)}")
            if not candidate.is_file():
                raise OperationalSealError(f"task055g_operational_entry_not_file:{candidate.relative_to(root)}")
            yield candidate


def _assert_no_symlinks(path: Path, authority_root: Path) -> None:
    current = path
    while current != authority_root:
        if current.is_symlink():
            raise OperationalSealError(f"task055g_operational_symlink_forbidden:{current}")
        if authority_root not in current.parents:
            raise OperationalSealError("task055g_operational_root_escape")
        current = current.parent
    for _ in _walk_files_no_symlinks(path):
        pass


def _validate_authority_root(value: str | Path, *, allow_missing: bool) -> Path:
    path = Path(value)
    if path.is_symlink():
        raise OperationalSealError("task055g_authority_root_symlink")
    if not path.exists():
        if not allow_missing:
            raise OperationalSealError("task055g_authority_root_missing")
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise OperationalSealError("task055g_authority_root_not_directory")
    return path.resolve()


def _safe_join(root: Path, relative: str) -> Path:
    candidate = root / relative
    resolved = candidate.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise OperationalSealError(f"task055g_operational_path_escape:{relative}")
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise OperationalSealError(f"task055g_operational_json_missing:{path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationalSealError(f"task055g_operational_json_invalid:{path.name}") from exc
    if not isinstance(payload, dict):
        raise OperationalSealError(f"task055g_operational_json_object_required:{path.name}")
    return payload


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
