"""Reader for local A-share matrix cache artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch
import hashlib


class MatrixStoreReader:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)

    def load_metadata(self) -> dict[str, Any]:
        path = self.cache_dir / "metadata.json"
        if not path.exists():
            raise FileNotFoundError(f"missing matrix cache metadata: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_ts_codes(self) -> list[str]:
        return [str(item) for item in self._read_json("ts_codes.json")]

    def load_trade_dates(self) -> list[str]:
        return [str(item) for item in self._read_json("trade_dates.json")]

    def load_fields(self) -> list[str]:
        payload = self._read_json("fields.json")
        if not payload:
            return []
        first = payload[0]
        if isinstance(first, dict):
            return [str(item["name"]) for item in payload]
        return [str(item) for item in payload]

    def load_field(self, field: str) -> np.ndarray:
        path = self.cache_dir / f"{field}.npy"
        if not path.exists():
            raise FileNotFoundError(f"missing matrix field: {path}")
        return np.load(path, allow_pickle=False)

    def load_all(self, fields: list[str] | None = None) -> dict[str, np.ndarray]:
        selected = fields or self.load_fields()
        return {field: self.load_field(field) for field in selected}

    def to_raw_data_cache(
        self,
        fields: list[str] | None = None,
        device: torch.device | str | None = None,
    ) -> dict[str, torch.Tensor]:
        target_device = torch.device(device) if device is not None else torch.device("cpu")
        arrays = self.load_all(fields)
        raw: dict[str, torch.Tensor] = {}
        for field, array in arrays.items():
            if field == "industry_codes":
                raw[field] = torch.tensor(array, dtype=torch.long, device=target_device)
            else:
                raw[field] = torch.tensor(array, dtype=torch.float32, device=target_device)

        n_dates = len(self.load_trade_dates())
        if "industry_codes" in raw and "industry_code_matrix" not in raw:
            raw["industry_code_matrix"] = raw["industry_codes"].unsqueeze(1).expand(-1, n_dates)
        if "total_mv" in raw and "log_mkt_cap" not in raw:
            raw["log_mkt_cap"] = torch.log1p(torch.clamp(raw["total_mv"], min=0.0))
        if "adjusted_open" not in raw and {"open", "adj_factor"} <= set(raw):
            raw["adjusted_open"] = raw["open"] * raw["adj_factor"]
        return raw

    def to_feature_inputs(self, device: torch.device | str | None = None) -> dict[str, torch.Tensor]:
        return self.to_raw_data_cache(device=device)

    def validate_strict_historical(self) -> dict[str, Any]:
        metadata = self.load_metadata()
        required = [
            "index_member_matrix.npy", "index_weight.npy", "membership_known.npy", "active_mask.npy",
            "bar_observed_mask.npy", "suspension_known_mask.npy", "suspended_mask.npy",
            "unexplained_data_gap_mask.npy", "tradable_mask.npy", "buyable_mask.npy", "sellable_mask.npy",
            "target_available_mask.npy", "snapshot_proof_manifest.json",
        ]
        missing = [name for name in required if not (self.cache_dir / name).exists()]
        errors = []
        if missing: errors.append(f"missing:{','.join(missing)}")
        if metadata.get("universe_mode") != "daily_pit_constituents": errors.append("universe_mode_not_daily_pit")
        if not metadata.get("historical_constituent_proof"): errors.append("historical_constituent_proof_false")
        shape = (len(self.load_ts_codes()), len(self.load_trade_dates()))
        hashes = {}
        for name in required:
            path = self.cache_dir / name
            if not path.exists(): continue
            hashes[name] = _sha256(path)
            if name.endswith(".npy"):
                array = np.load(path, mmap_mode="r", allow_pickle=False)
                if tuple(array.shape) not in {shape, (shape[1],)}: errors.append(f"shape_mismatch:{name}")
        return {"valid": not errors, "errors": errors, "shape": list(shape), "partition_sha256": hashes}

    def _read_json(self, filename: str) -> Any:
        path = self.cache_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"missing matrix cache file: {path}")
        return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()
