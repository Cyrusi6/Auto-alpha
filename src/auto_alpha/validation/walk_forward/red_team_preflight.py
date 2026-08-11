"""Preflight current canonical data without relabeling observed history as holdout."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_alpha.data.lake.store.canonical_freeze import validate_canonical_research_freeze

from auto_alpha.validation.walk_forward.red_team_candidate_pool import validate_candidate_pool_manifest
from auto_alpha.validation.walk_forward.red_team_io import publish_generation
from auto_alpha.validation.walk_forward.red_team_io import sha256_file


def preflight_canonical_holdout(
    canonical_freeze_manifest_path: str | Path,
    output_root: str | Path,
    *,
    candidate_pool_manifest_path: str | Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Validate the governed freeze and report whether a future view may be prepared."""

    freeze = validate_canonical_research_freeze(canonical_freeze_manifest_path)
    sealed = freeze.get("sealed_holdout") if isinstance(freeze.get("sealed_holdout"), dict) else {}
    candidate = (
        validate_candidate_pool_manifest(candidate_pool_manifest_path, revalidate_sources=True)
        if candidate_pool_manifest_path
        else None
    )
    blockers: list[str] = []
    if sealed.get("historically_observed") is not False:
        blockers.append("sealed_period_already_observed")
    if sealed.get("untouched") is not True:
        blockers.append("sealed_period_not_untouched")
    if candidate is None:
        blockers.append("candidate_pool_not_frozen")
    if freeze.get("certification_ready") is True:
        blockers.append("canonical_freeze_improperly_claims_certification")
    capability_issuable = not blockers and candidate is not None
    core = {
        "status": "eligible_for_future_holdout_view_publication" if capability_issuable else "blocked",
        "canonical_freeze_content_hash": freeze.get("content_hash"),
        "canonical_freeze_manifest_sha256": sha256_file(canonical_freeze_manifest_path),
        "candidate_pool_root": candidate.get("content_hash") if candidate else None,
        "candidate_pool_frozen": candidate is not None,
        "sealed_period": sealed.get("period"),
        "sealed_period_historically_observed": sealed.get("historically_observed"),
        "sealed_period_untouched": sealed.get("untouched"),
        "holdout_capability_issuable": capability_issuable,
        "holdout_market_values_read": False,
        "holdout_evaluation_executed": False,
        "blockers": blockers,
        "certification_ready": False,
        "portfolio_ready": False,
    }
    return publish_generation(
        output_root,
        generation_prefix="sealed_holdout_preflight",
        manifest_name="sealed_holdout_preflight.json",
        artifact_type="sealed_holdout_preflight",
        producer="validation_red_team",
        core=core,
    )
