"""Artifact schema registry, writers, validation, scanning, and manifests."""

from .manifest import build_artifact_manifest, write_artifact_manifest
from .models import (
    ArtifactCompatibilityMode,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactMetadata,
    ArtifactSchemaDefinition,
    ArtifactSchemaVersion,
    ArtifactSeverity,
    ArtifactValidationIssue,
    ArtifactValidationReport,
    ArtifactValidationResult,
)
from .registry import ARTIFACT_SCHEMA_REGISTRY, get_definition, get_registry, infer_artifact_type
from .scanner import paths_from_artifact_catalog, scan_artifact_dirs
from .validator import validate_artifact, validate_jsonl_records, validate_payload
from .writer import attach_artifact_metadata, write_artifact_sidecar, write_json_artifact, write_jsonl_artifact

__all__ = [
    "ARTIFACT_SCHEMA_REGISTRY",
    "ArtifactCompatibilityMode",
    "ArtifactManifest",
    "ArtifactManifestEntry",
    "ArtifactMetadata",
    "ArtifactSchemaDefinition",
    "ArtifactSchemaVersion",
    "ArtifactSeverity",
    "ArtifactValidationIssue",
    "ArtifactValidationReport",
    "ArtifactValidationResult",
    "attach_artifact_metadata",
    "build_artifact_manifest",
    "get_definition",
    "get_registry",
    "infer_artifact_type",
    "paths_from_artifact_catalog",
    "scan_artifact_dirs",
    "validate_artifact",
    "validate_jsonl_records",
    "validate_payload",
    "write_artifact_manifest",
    "write_artifact_sidecar",
    "write_json_artifact",
    "write_jsonl_artifact",
]
