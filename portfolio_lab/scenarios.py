"""Portfolio lab scenarios."""

from __future__ import annotations

from artifact_schema.writer import write_json_artifact

from .models import PortfolioPolicyScenario


def default_portfolio_scenarios(profile: str = "sample") -> list[PortfolioPolicyScenario]:
    if profile == "production":
        return [
            PortfolioPolicyScenario("base", "Base", 1.0, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("high_cost", "High Cost", 2.0, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("low_capacity", "Low Capacity", 1.0, 0.05, 0.8, 1.0),
            PortfolioPolicyScenario("tight_risk", "Tight Risk", 1.0, 0.10, 0.6, 0.7),
        ]
    if profile == "research":
        return [
            PortfolioPolicyScenario("base", "Base", 1.0, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("high_cost", "High Cost", 1.5, 0.10, 1.0, 1.0),
            PortfolioPolicyScenario("low_capacity", "Low Capacity", 1.0, 0.05, 0.8, 1.0),
        ]
    return [PortfolioPolicyScenario("base", "Base", 1.0, 0.10, 1.0, 1.0)]


def write_scenarios(scenarios: list[PortfolioPolicyScenario], output_dir) -> str:
    from pathlib import Path

    path = Path(output_dir) / "portfolio_scenarios.json"
    write_json_artifact(path, {"scenarios": [item.to_dict() for item in scenarios]}, "portfolio_scenarios", "portfolio_lab")
    return str(path)
