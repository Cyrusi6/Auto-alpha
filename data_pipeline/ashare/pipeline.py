"""Dry-run planning utilities for the A-share data pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AShareDataConfig
from .dataset_registry import FULL_RESEARCH_DATASETS, dataset_description


ASHARE_DATASETS = FULL_RESEARCH_DATASETS


@dataclass(frozen=True)
class DatasetPlan:
    name: str
    target: str
    enabled: bool = True
    description: str | None = None


@dataclass(frozen=True)
class PipelinePlan:
    provider: str
    universe: str
    start_date: str
    end_date: str | None
    adjust: str
    data_dir: str
    datasets: list[DatasetPlan]

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "universe": self.universe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "adjust": self.adjust,
            "data_dir": self.data_dir,
            "datasets": [
                {
                    "name": dataset.name,
                    "target": dataset.target,
                    "enabled": dataset.enabled,
                    "description": dataset.description,
                }
                for dataset in self.datasets
            ],
        }


def build_pipeline_plan(config: AShareDataConfig) -> PipelinePlan:
    datasets = [
        DatasetPlan(
            name=name,
            target=str(config.data_dir / name / "records.jsonl"),
            description=dataset_description(name),
        )
        for name in ASHARE_DATASETS
    ]

    return PipelinePlan(
        provider=config.provider,
        universe=config.universe,
        start_date=config.start_date,
        end_date=config.end_date,
        adjust=config.adjust,
        data_dir=str(config.data_dir),
        datasets=datasets,
    )
