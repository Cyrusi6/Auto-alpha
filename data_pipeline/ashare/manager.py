"""A-share local synchronization manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AShareDataConfig
from .pipeline import ASHARE_DATASETS
from .providers import AShareDataProvider, create_ashare_provider
from .quality import validate_all_datasets, write_quality_report
from .state import default_pipeline_state_path, load_pipeline_state, save_pipeline_state
from .storage import LocalAshareStorage, StorageWriteResult


@dataclass(frozen=True)
class SyncDatasetResult:
    dataset: str
    path: str
    records: int


@dataclass(frozen=True)
class SyncResult:
    provider: str
    universe: str
    start_date: str
    end_date: str | None
    adjust: str
    data_dir: str
    datasets: list[SyncDatasetResult]
    manifest_path: str
    state_path: str | None = None
    quality_report_path: str | None = None
    has_errors: bool = False
    quality_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "universe": self.universe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "adjust": self.adjust,
            "data_dir": self.data_dir,
            "datasets": [asdict(dataset) for dataset in self.datasets],
            "manifest_path": self.manifest_path,
            "state_path": self.state_path,
            "quality_report_path": self.quality_report_path,
            "has_errors": self.has_errors,
            "quality_summary": self.quality_summary,
        }


class AShareDataManager:
    def __init__(
        self,
        config: AShareDataConfig,
        provider: AShareDataProvider | None = None,
        storage: LocalAshareStorage | None = None,
    ):
        self.config = config
        self.provider = provider or create_ashare_provider(config)
        self.storage = storage or LocalAshareStorage(config.data_dir)

    def sync(
        self,
        datasets: list[str] | None = None,
        mode: str = "overwrite",
        validate: bool = False,
        write_state: bool = True,
        state_file: str | Path | None = None,
    ) -> SyncResult:
        if mode not in {"overwrite", "append"}:
            raise ValueError("mode must be one of: overwrite, append")

        selected = list(ASHARE_DATASETS if datasets is None else datasets)
        unsupported = sorted(set(selected) - set(ASHARE_DATASETS))
        if unsupported:
            raise ValueError(f"Unsupported A-share datasets: {', '.join(unsupported)}")

        write_results: list[StorageWriteResult] = []
        for dataset in selected:
            fetcher = getattr(self.provider, f"fetch_{dataset}")
            records = fetcher(self.config)
            write_results.append(self.storage.write_dataset(dataset, records, mode=mode))

        manifest = self.storage.write_manifest(self.config, write_results)
        state_path: str | None = None
        if write_state:
            target_state_path = Path(state_file) if state_file is not None else default_pipeline_state_path(self.config.data_dir)
            state = load_pipeline_state(target_state_path)
            for result in write_results:
                state.update_dataset(
                    dataset=result.dataset,
                    records=result.records,
                    start_date=self.config.start_date,
                    end_date=self.config.end_date,
                )
            state_path = str(save_pipeline_state(state, target_state_path))

        quality_report_path: str | None = None
        quality_summary: dict[str, Any] | None = None
        has_errors = False
        if validate:
            report = validate_all_datasets(self.storage)
            target_report_path = self.storage.data_dir / "quality_report.json"
            quality_report_path = str(write_quality_report(report, target_report_path))
            report_payload = report.to_dict()
            has_errors = bool(report_payload["has_errors"])
            quality_summary = {
                "total_errors": report_payload["total_errors"],
                "total_warnings": report_payload["total_warnings"],
                "datasets": [
                    {
                        "dataset": dataset["dataset"],
                        "records": dataset["records"],
                        "errors": dataset["errors"],
                        "warnings": dataset["warnings"],
                    }
                    for dataset in report_payload["datasets"]
                ],
            }

        return SyncResult(
            provider=self.config.provider,
            universe=self.config.universe,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            adjust=self.config.adjust,
            data_dir=str(self.config.data_dir),
            datasets=[
                SyncDatasetResult(
                    dataset=result.dataset,
                    path=result.path,
                    records=result.records,
                )
                for result in write_results
            ],
            manifest_path=manifest.path,
            state_path=state_path,
            quality_report_path=quality_report_path,
            has_errors=has_errors,
            quality_summary=quality_summary,
        )


def sync_ashare_datasets(
    config: AShareDataConfig,
    datasets: list[str] | None = None,
    mode: str = "overwrite",
    validate: bool = False,
    write_state: bool = True,
    state_file: str | Path | None = None,
) -> SyncResult:
    return AShareDataManager(config).sync(
        datasets=datasets,
        mode=mode,
        validate=validate,
        write_state=write_state,
        state_file=state_file,
    )
