"""Dry-run planning utilities for the A-share data pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AShareDataConfig


ASHARE_DATASETS = (
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "financial_features",
    "daily_limits",
    "adjustment_factors",
    "index_members",
)


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
    dataset_descriptions = {
        "securities": "Listed A-share securities.",
        "trade_calendar": "Exchange trading calendar.",
        "daily_bars": "Daily price and volume bars.",
        "daily_basic": "Daily market indicators.",
        "financial_features": "Financial features aligned by announcement date.",
        "daily_limits": "Daily limit up/down prices.",
        "adjustment_factors": "Daily adjustment factors.",
        "index_members": "Index constituent weights.",
    }
    datasets = [
        DatasetPlan(
            name=name,
            target=str(config.data_dir / name / "records.jsonl"),
            description=dataset_descriptions[name],
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
