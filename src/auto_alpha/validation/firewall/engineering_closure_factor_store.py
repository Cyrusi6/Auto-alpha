"""Read-only validator for the historical exact-20 normalized factor store."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.validation.firewall.engineering_closure_validators import canonical_hash, sha256_file


def validate_normalized_replay_store(root: str | Path, *, expected_ids: list[str]) -> dict[str, Any]:
    generation = Path(root)
    manifest = json.loads((generation / "normalized_replay_store_manifest.json").read_text())
    records = LocalFactorStore(generation).load_factors()
    if sorted(record.factor_id for record in records) != sorted(expected_ids) or len(records) != 20:
        raise RuntimeError("normalized_store_exact20_mismatch")
    if sha256_file(generation / "factors.jsonl") != manifest["records_sha256"]:
        raise RuntimeError("normalized_store_records_sha_mismatch")
    for record in records:
        if int((record.metadata or {}).get("required_observations", -1)) != record.lookback_days + 1:
            raise RuntimeError(f"normalized_store_lookback_unit_mismatch:{record.factor_id}")
    payloads = [asdict(record) for record in sorted(records, key=lambda row: row.factor_id)]
    identity_root = canonical_hash(
        [{"factor_id": row["factor_id"], "formula_hash": row["formula_hash"], "record_hash": canonical_hash(row)} for row in payloads]
    )
    if identity_root != manifest.get("identity_root"):
        raise RuntimeError("normalized_store_identity_root_mismatch")
    semantic = {key: manifest[key] for key in ("schema_version", "record_count", "identity_root", "semantics_contract_hash", "overlay_content_hash", "overlay_manifest_sha256", "source_store_factors_sha256")}
    content_hash = canonical_hash({"semantic": semantic, "records": [canonical_hash(row) for row in payloads]})
    if content_hash != manifest.get("content_hash"):
        raise RuntimeError("normalized_store_content_hash_mismatch")
    return manifest | {"generation_dir": str(generation), "records": records}
