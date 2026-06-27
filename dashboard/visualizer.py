"""Plotly visualizations for local A-share artifacts."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def empty_figure(title: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(title=title, template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
    return fig


def plot_equity_curve(equity_curve: pd.DataFrame) -> go.Figure:
    if equity_curve.empty or "equity" not in equity_curve.columns:
        return empty_figure("Equity Curve")
    x_values = equity_curve["trade_date"] if "trade_date" in equity_curve.columns else equity_curve.index
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_values, y=equity_curve["equity"], mode="lines+markers", name="equity"))
    fig.update_layout(title="Equity Curve", template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
    return fig


def plot_backtest_metrics(metrics: dict[str, float]) -> go.Figure:
    if not metrics:
        return empty_figure("Backtest Metrics")
    names = list(metrics.keys())
    values = [float(metrics[name]) for name in names]
    fig = go.Figure(data=[go.Bar(x=names, y=values)])
    fig.update_layout(title="Backtest Metrics", template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
    return fig


def plot_factor_split_metrics(metrics_by_split: dict[str, dict[str, float]]) -> go.Figure:
    if not metrics_by_split:
        return empty_figure("Factor Split Metrics")
    splits = list(metrics_by_split.keys())
    score_values = [float(metrics_by_split[split].get("score", 0.0)) for split in splits]
    ic_values = [float(metrics_by_split[split].get("rank_ic_mean", 0.0)) for split in splits]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=splits, y=score_values, name="score"))
    fig.add_trace(go.Bar(x=splits, y=ic_values, name="rank_ic_mean"))
    fig.update_layout(
        title="Factor Split Metrics",
        barmode="group",
        template="plotly_white",
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def plot_order_distribution(orders: pd.DataFrame) -> go.Figure:
    if orders.empty or "side" not in orders.columns:
        return empty_figure("Orders")
    grouped = orders.groupby("side", dropna=False)["order_value"].sum() if "order_value" in orders.columns else orders.groupby("side").size()
    fig = go.Figure(data=[go.Pie(labels=list(grouped.index), values=list(grouped.values), hole=0.45)])
    fig.update_layout(title="Order Value by Side", template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
    return fig
