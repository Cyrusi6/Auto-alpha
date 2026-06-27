"""A-share local synchronization manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import AShareDataConfig
from .pipeline import ASHARE_DATASETS
from .providers import AShareDataProvider, create_ashare_provider
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

    def sync(self, datasets: list[str] | None = None) -> SyncResult:
        selected = list(ASHARE_DATASETS if datasets is None else datasets)
        unsupported = sorted(set(selected) - set(ASHARE_DATASETS))
        if unsupported:
            raise ValueError(f"Unsupported A-share datasets: {', '.join(unsupported)}")

        write_results: list[StorageWriteResult] = []
        for dataset in selected:
            fetcher = getattr(self.provider, f"fetch_{dataset}")
            records = fetcher(self.config)
            write_results.append(self.storage.write_dataset(dataset, records))

        manifest = self.storage.write_manifest(self.config, write_results)
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
        )


def sync_ashare_datasets(
    config: AShareDataConfig,
    datasets: list[str] | None = None,
) -> SyncResult:
    return AShareDataManager(config).sync(datasets=datasets)
