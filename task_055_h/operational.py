from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from task_055_g.operational import OPERATIONAL_STATES, build_authoritative_writer_registry

from .contracts import OPERATIONAL_SEAL_SCHEMA
from .io import canonical_hash, publish_generation, sha256_file, validate_generation


class Task055HOperationalError(RuntimeError):
    pass


_ENV_BY_WRITER = {
    "validation_campaign_store": "ASHARE_DASHBOARD_VALIDATION_CAMPAIGN_STORE_DIR",
    "certification_campaign_store": "ASHARE_DASHBOARD_FACTOR_CERTIFICATION_CAMPAIGN_DIR",
    "portfolio_campaign_store": "ASHARE_DASHBOARD_PORTFOLIO_CAMPAIGN_DIR",
    "model_registry": "ASHARE_DASHBOARD_MODEL_REGISTRY_DIR",
    "paper_account": "ASHARE_DASHBOARD_PAPER_ACCOUNT_DIR",
    "production_orchestrator": "ASHARE_DASHBOARD_PRODUCTION_ORCHESTRATOR_DIR",
}


def publish_operational_seal(
    *,
    repository_root: str | Path,
    governed_root: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    source_registry = build_authoritative_writer_registry(governed)
    writers = []
    state_counts = {name: 0 for name in OPERATIONAL_STATES}
    blockers: list[str] = []
    for writer in source_registry["writers"]:
        writer_id = str(writer["writer_id"])
        env_name = _ENV_BY_WRITER[writer_id]
        override = os.environ.get(env_name)
        default_relative = str(writer["canonical_root"])
        canonical = Path(override).expanduser() if override else repository / default_relative
        if not canonical.is_absolute():
            canonical = repository / canonical
        canonical = canonical.resolve(strict=False)
        if canonical.is_symlink():
            blockers.append(f"operational_root_symlink:{writer_id}")
            continue
        if not canonical.exists():
            canonical.mkdir(parents=True, exist_ok=False)
        roots = [("runtime_canonical", canonical)]
        historical = (governed / default_relative).resolve(strict=False)
        if historical != canonical:
            roots.append(("governed_historical", historical))
        root_rows = []
        for role, root in roots:
            result = _scan_writer_root(root, writer)
            root_rows.append({"root_role": role, **result})
            for state, count in result["state_counts"].items():
                state_counts[state] += int(count)
            blockers.extend(result["blockers"])
        writers.append({
            "writer_id": writer_id,
            "writer_fqn": writer["writer_fqn"],
            "configured_by": env_name if override else "repository_default",
            "root_identity": canonical_hash([str(canonical), canonical.stat().st_dev, canonical.stat().st_ino]),
            "source_proofs": writer["source_proofs"],
            "roots": root_rows,
        })
    semantic = {
        "schema_version": OPERATIONAL_SEAL_SCHEMA,
        "status": "passed" if not blockers and not any(state_counts.values()) else "blocked",
        "writer_registry_source_hash": source_registry["content_hash"],
        "writer_count": len(writers),
        "writers": writers,
        "state_counts": state_counts,
        "blockers": sorted(set(blockers)),
        "shadow_governed_artifacts_authoritative": False,
        "runtime_default_roots_scanned": True,
    }
    return publish_generation(
        output_root,
        prefix="operational_seal",
        manifest_name="operational_seal.json",
        semantic=semantic,
    )


def validate_operational_seal(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=OPERATIONAL_SEAL_SCHEMA, manifest_name="operational_seal.json")
    if payload.get("status") != "passed" or payload.get("blockers") or any(int(value) for value in (payload.get("state_counts") or {}).values()):
        raise Task055HOperationalError("operational_state_unproven_or_nonempty")
    return payload


def _scan_writer_root(root: Path, writer: dict[str, Any]) -> dict[str, Any]:
    state_counts = {name: 0 for name in OPERATIONAL_STATES}
    blockers: list[str] = []
    files = []
    contracts = {str(row["filename"]): row for row in writer["file_contracts"]}
    if root.is_symlink() or not root.is_dir():
        return {"root_identity": canonical_hash([str(root)]), "files": [], "state_counts": state_counts, "blockers": [f"operational_root_invalid:{writer['writer_id']}"]}
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            if (current_path / name).is_symlink():
                blockers.append(f"operational_nested_symlink:{writer['writer_id']}:{name}")
        for name in filenames:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                blockers.append(f"operational_file_symlink:{writer['writer_id']}:{relative}")
                continue
            if name.endswith(".schema.json"):
                continue
            contract = contracts.get(name)
            if contract is None:
                blockers.append(f"operational_unknown_format:{writer['writer_id']}:{relative}")
                continue
            try:
                rows = _parse(path, str(contract["kind"]))
            except Exception as exc:
                blockers.append(f"operational_parse_error:{writer['writer_id']}:{relative}:{exc}")
                continue
            for row in rows:
                for rule in contract.get("state_rules") or ():
                    if all(str(row.get(field, "")) == str(expected) for field, expected in rule.get("equals") or ()):
                        state_counts[str(rule["state"])] += 1
            files.append({"relative_path": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size, "record_count": len(rows)})
    return {
        "root_identity": canonical_hash([str(root), root.stat().st_dev, root.stat().st_ino]),
        "files": sorted(files, key=lambda row: row["relative_path"]),
        "content_root": canonical_hash(sorted(files, key=lambda row: row["relative_path"])),
        "state_counts": state_counts,
        "blockers": blockers,
    }


def _parse(path: Path, kind: str) -> list[dict[str, Any]]:
    if kind == "text":
        path.read_text(encoding="utf-8")
        return []
    if kind == "jsonl":
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("jsonl_row_not_object")
                result.append(row)
        return result
    if kind == "json_object":
        row = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            raise ValueError("json_not_object")
        return [row]
    raise ValueError("unknown_kind")
