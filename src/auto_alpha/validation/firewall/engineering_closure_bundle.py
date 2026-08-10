"""Read-only validator for historical canonical engineering bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from auto_alpha.validation.firewall.engineering_closure_factor_store import validate_normalized_replay_store
from auto_alpha.validation.firewall.engineering_closure_validators import (
    canonical_hash,
    resolve_and_validate_overlay,
    sha256_file,
    validate_strict_matrix_generation,
    validate_v3_tensor_generation,
)


def validate_bundle(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic = {key: value for key, value in manifest.items() if key not in {"generation_id", "content_hash", "artifact_paths"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise RuntimeError("bundle_content_hash_mismatch")
    if len(manifest.get("exact20_ids") or []) != 20:
        raise RuntimeError("bundle_exact20_mismatch")
    paths = manifest.get("artifact_paths") or {}
    required = {"freeze_manifest", "universe_manifest", "matrix_root", "tensor_root", "overlay_root", "normalized_store_root", "promotion_policy"}
    if set(paths) != required:
        raise RuntimeError("bundle_artifact_paths_invalid")
    freeze = Path(paths["freeze_manifest"])
    universe = Path(paths["universe_manifest"])
    promotion = Path(paths["promotion_policy"])
    if sha256_file(freeze) != manifest["freeze_manifest_sha256"] or json.loads(freeze.read_text()).get("content_hash") != manifest["freeze_content_hash"]:
        raise RuntimeError("bundle_freeze_lineage_mismatch")
    if sha256_file(universe) != manifest["universe_manifest_sha256"] or json.loads(universe.read_text()).get("content_hash") != manifest["universe_content_hash"]:
        raise RuntimeError("bundle_universe_lineage_mismatch")
    if sha256_file(promotion) != manifest["promotion_policy_sha256"]:
        raise RuntimeError("bundle_promotion_policy_mismatch")
    matrix = validate_strict_matrix_generation(paths["matrix_root"], expected_content_hash=manifest["matrix_content_hash"])
    tensor = validate_v3_tensor_generation(paths["tensor_root"], matrix=matrix, expected_content_hash=manifest["tensor_content_hash"])
    overlay = resolve_and_validate_overlay(paths["overlay_root"], expected_content_hash=manifest["overlay_content_hash"])
    store = validate_normalized_replay_store(paths["normalized_store_root"], expected_ids=manifest["exact20_ids"])
    store_manifest = Path(paths["normalized_store_root"]) / "normalized_replay_store_manifest.json"
    checks = (
        matrix["manifest_sha256"] == manifest["matrix_manifest_sha256"],
        tensor["manifest_sha256"] == manifest["tensor_manifest_sha256"],
        overlay["manifest_sha256"] == manifest["overlay_manifest_sha256"],
        sha256_file(store_manifest) == manifest["normalized_store_manifest_sha256"],
        store["content_hash"] == manifest["normalized_store_content_hash"],
        store["identity_root"] == manifest["exact20_identity_root"],
    )
    if not all(checks):
        raise RuntimeError("bundle_native_artifact_lineage_mismatch")
    return manifest
