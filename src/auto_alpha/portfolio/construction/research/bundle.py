"""Content-addressed inputs for certified-factor portfolio auto_alpha.research.discovery.studies."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from auto_alpha.portfolio.simulation.fees import validate_fee_schedule_v2

from .contracts import PortfolioResearchError, stable_hash
from .engine import PortfolioResearchData


BUNDLE_SCHEMA = "factor_certified_portfolio_research_bundle_v1"


def publish_portfolio_research_bundle(
    output_root: str | Path,
    data: PortfolioResearchData,
    *,
    fee_schedule_manifest: str | Path,
    source_lineage: Mapping[str, str],
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    root = Path(output_root).resolve()
    if root.is_symlink():
        raise PortfolioResearchError("portfolio_bundle_output_symlink_forbidden")
    fee = validate_fee_schedule_v2(
        fee_schedule_manifest,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    fee_path = Path(fee["manifest_path"]).resolve()
    staging_parent = root / "generations"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".portfolio_bundle.", dir=staging_parent))
    artifacts: dict[str, dict[str, Any]] = {}
    try:
        _write_json(staging / "trade_dates.json", list(data.trade_dates))
        _register(artifacts, staging, "trade_dates", staging / "trade_dates.json")
        _write_json(staging / "assets.json", list(data.assets))
        _register(artifacts, staging, "assets", staging / "assets.json")
        _write_json(staging / "factor_certified_records.json", list(data.factor_records))
        _register(artifacts, staging, "factor_records", staging / "factor_certified_records.json")
        _write_json(staging / "auto_alpha.data.pit.corporate_actions.json", list(data.corporate_actions))
        _register(artifacts, staging, "corporate_actions", staging / "auto_alpha.data.pit.corporate_actions.json")
        _write_array(staging, artifacts, "factor_values", data.factor_values)
        _write_array(staging, artifacts, "factor_validity", data.factor_validity)
        _write_array(staging, artifacts, "target", data.target)
        _write_array(staging, artifacts, "target_available", data.target_available)
        for name, value in sorted(data.market.items()):
            _write_array(staging, artifacts, f"market:{name}", value)
        for name, value in sorted(data.masks.items()):
            _write_array(staging, artifacts, f"mask:{name}", value)
        for name, value in sorted(data.universes.items()):
            _write_array(staging, artifacts, f"universe:{name}", value)
        for name, payload in sorted(data.benchmarks.items()):
            _write_array(staging, artifacts, f"benchmark:{name}:returns", payload["returns"])
            _write_array(staging, artifacts, f"benchmark:{name}:validity", payload["validity"])
        for name, value in sorted(data.regimes.items()):
            _write_array(staging, artifacts, f"regime:{name}", value)
        fee_target = staging / "fee_schedule"
        shutil.copytree(fee_path.parent, fee_target)
        fee_relative = fee_target / fee_path.name
        for file_path in sorted(candidate for candidate in fee_target.rglob("*") if candidate.is_file()):
            _register(artifacts, staging, f"fee:{file_path.relative_to(fee_target)}", file_path)
        source = dict(sorted((str(key), str(value)) for key, value in source_lineage.items()))
        if not source or any(len(value) != 64 for value in source.values()):
            raise PortfolioResearchError("portfolio_bundle_source_lineage_invalid")
        semantic = {
            "schema_version": BUNDLE_SCHEMA,
            "status": "ready",
            "source_lineage": source,
            "source_lineage_root": stable_hash(source),
            "factor_ids": [str(row["factor_id"]) for row in data.factor_records],
            "formula_hashes": [str(row["formula_hash"]) for row in data.factor_records],
            "stock_axis_hash": stable_hash(list(data.assets)),
            "date_axis_hash": stable_hash(list(data.trade_dates)),
            "factor_axis_hash": stable_hash([str(row["factor_id"]) for row in data.factor_records]),
            "universe_names": sorted(data.universes),
            "benchmark_names": sorted(data.benchmarks),
            "regime_names": sorted(data.regimes),
            "fee_schedule_relative_path": str(fee_relative.relative_to(staging)),
            "fee_schedule_content_hash": str(fee["content_hash"]),
            "artifacts": artifacts,
            "fallback_allowed": False,
            "factor_values_storage": "float32_npy",
            "factor_validity_storage": "bool_npy",
        }
        content_hash = stable_hash(semantic)
        generation_id = f"portfolio_bundle_{content_hash[:24]}"
        manifest = {**semantic, "content_hash": content_hash, "generation_id": generation_id}
        _write_json(staging / "portfolio_research_bundle_manifest.json", manifest)
        target = staging_parent / generation_id
        if target.exists():
            existing = validate_portfolio_research_bundle(target / "portfolio_research_bundle_manifest.json", allow_synthetic_test_fixture=allow_synthetic_test_fixture)
            if existing["content_hash"] != content_hash:
                raise PortfolioResearchError("portfolio_bundle_generation_collision")
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/portfolio_research_bundle_manifest.json",
        }
        _atomic_json(root / "current.json", pointer)
        return validate_portfolio_research_bundle(
            target / "portfolio_research_bundle_manifest.json",
            allow_synthetic_test_fixture=allow_synthetic_test_fixture,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_portfolio_research_bundle(
    manifest_path: str | Path,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    if path.is_symlink() or not path.is_file():
        raise PortfolioResearchError("portfolio_bundle_manifest_missing_or_symlink")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != BUNDLE_SCHEMA or manifest.get("status") != "ready":
        raise PortfolioResearchError("portfolio_bundle_schema_or_status_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if stable_hash(semantic) != manifest.get("content_hash"):
        raise PortfolioResearchError("portfolio_bundle_content_hash_mismatch")
    expected_generation = f"portfolio_bundle_{manifest['content_hash'][:24]}"
    if manifest.get("generation_id") != expected_generation or path.parent.name != expected_generation:
        raise PortfolioResearchError("portfolio_bundle_generation_identity_mismatch")
    if manifest.get("fallback_allowed") is not False:
        raise PortfolioResearchError("portfolio_bundle_fallback_forbidden")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise PortfolioResearchError("portfolio_bundle_artifact_catalog_missing")
    registered = set()
    for name, entry in artifacts.items():
        target = _contained(path.parent, entry.get("path"))
        registered.add(str(target.relative_to(path.parent)))
        if _sha256(target) != entry.get("sha256") or target.stat().st_size != entry.get("size_bytes"):
            raise PortfolioResearchError(f"portfolio_bundle_artifact_integrity_invalid:{name}")
        if target.suffix == ".npy":
            array = np.load(target, mmap_mode="r", allow_pickle=False)
            if list(array.shape) != entry.get("shape") or str(array.dtype) != entry.get("dtype"):
                raise PortfolioResearchError(f"portfolio_bundle_array_contract_invalid:{name}")
    observed = {
        str(candidate.relative_to(path.parent))
        for candidate in path.parent.rglob("*")
        if candidate.is_file() and candidate != path
    }
    if observed != registered:
        raise PortfolioResearchError("portfolio_bundle_unregistered_file_detected")
    fee_path = _contained(path.parent, manifest.get("fee_schedule_relative_path"))
    fee = validate_fee_schedule_v2(fee_path, allow_synthetic_test_fixture=allow_synthetic_test_fixture)
    if fee.get("content_hash") != manifest.get("fee_schedule_content_hash"):
        raise PortfolioResearchError("portfolio_bundle_fee_lineage_mismatch")
    if stable_hash(manifest.get("source_lineage")) != manifest.get("source_lineage_root"):
        raise PortfolioResearchError("portfolio_bundle_source_lineage_root_mismatch")
    return {**manifest, "manifest_path": str(path)}


def load_portfolio_research_bundle(
    manifest_path: str | Path,
    *,
    allow_synthetic_test_fixture: bool = False,
) -> tuple[PortfolioResearchData, Path, dict[str, Any]]:
    manifest = validate_portfolio_research_bundle(
        manifest_path,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    root = Path(manifest["manifest_path"]).parent
    artifacts = manifest["artifacts"]

    def array(name: str) -> np.ndarray:
        return np.load(root / artifacts[name]["path"], mmap_mode="r", allow_pickle=False)

    dates = tuple(_read_json(root / artifacts["trade_dates"]["path"]))
    assets = tuple(_read_json(root / artifacts["assets"]["path"]))
    factors = tuple(_read_json(root / artifacts["factor_records"]["path"]))
    actions = tuple(_read_json(root / artifacts["corporate_actions"]["path"]))
    market = {name.removeprefix("market:"): array(name) for name in artifacts if name.startswith("market:")}
    masks = {name.removeprefix("mask:"): array(name) for name in artifacts if name.startswith("mask:")}
    universes = {name.removeprefix("universe:"): array(name) for name in artifacts if name.startswith("universe:")}
    benchmark_names = manifest["benchmark_names"]
    benchmarks = {
        name: {
            "returns": array(f"benchmark:{name}:returns"),
            "validity": array(f"benchmark:{name}:validity"),
        }
        for name in benchmark_names
    }
    regimes = {name: array(f"regime:{name}") for name in manifest["regime_names"]}
    data = PortfolioResearchData(
        trade_dates=dates,
        assets=assets,
        factor_records=factors,
        factor_values=array("factor_values"),
        factor_validity=array("factor_validity"),
        target=array("target"),
        target_available=array("target_available"),
        market=market,
        masks=masks,
        universes=universes,
        benchmarks=benchmarks,
        regimes=regimes,
        corporate_actions=actions,
        lineage=manifest["source_lineage"] | {"bundle_content_hash": manifest["content_hash"]},
    )
    return data, root / manifest["fee_schedule_relative_path"], manifest


def _write_array(root, artifacts, name, value):
    safe = name.replace(":", "__") + ".npy"
    path = root / "arrays" / safe
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(value)
    if array.dtype == object:
        array = array.astype(str)
    if name == "factor_values":
        array = array.astype(np.float32)
    elif "valid" in name or name.startswith(("mask:", "universe:", "regime:")) or name == "target_available":
        array = array.astype(np.bool_)
    np.save(path, array, allow_pickle=False)
    _register(artifacts, root, name, path, array=array)


def _register(artifacts, root, name, path, *, array=None):
    entry = {
        "path": str(path.relative_to(root)),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }
    if array is not None:
        entry.update({"shape": list(array.shape), "dtype": str(array.dtype)})
    artifacts[name] = entry


def _contained(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise PortfolioResearchError("portfolio_bundle_relative_path_invalid")
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents or candidate.is_symlink() or not candidate.is_file():
        raise PortfolioResearchError("portfolio_bundle_artifact_containment_invalid")
    return candidate


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
