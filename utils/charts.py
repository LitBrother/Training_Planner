import pandas as pd
import plotly.graph_objects as go


def _layout(fig: go.Figure, title: str, ytitle: str) -> go.Figure:
    fig.update_layout(title=title, yaxis_title=ytitle, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    return fig


def weekly_volume_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(x=df["week_start"], y=df["actual"], name="Actual", marker_color="#2ca02c")
    fig.add_scatter(x=df["week_start"], y=df["planned"], name="Planned", mode="lines",
                    line=dict(dash="dash", color="#7f7f7f"))
    fig.add_scatter(x=df["week_start"], y=df["adjusted"], name="Adjusted", mode="lines+markers",
                    line=dict(color="#1f77b4"))
    return _layout(fig, "Weekly volume (planned / adjusted / actual)", "km")


def long_run_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=df["week_start"], y=df["long_planned"], name="Planned long run", mode="lines+markers")
    fig.add_scatter(x=df["week_start"], y=df["long_actual"], name="Actual long run", mode="markers",
                    marker=dict(size=10, color="#2ca02c"))
    return _layout(fig, "Long-run progression", "km")


def monthly_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(x=df["month"], y=df["planned"], name="Planned", marker_color="#c7c7c7")
    fig.add_bar(x=df["month"], y=df["adjusted"], name="Adjusted", marker_color="#1f77b4")
    fig.add_bar(x=df["month"], y=df["actual"], name="Actual", marker_color="#2ca02c")
    fig.update_layout(barmode="group")
    return _layout(fig, "Monthly distance", "km")


def consistency_chart(df: pd.DataFrame) -> go.Figure:
    logged = df[df["logged"]]
    fig = go.Figure()
    fig.add_bar(x=logged["week_start"], y=logged["completion"], name="Completion %", marker_color="#1f77b4")
    fig.add_hline(y=90, line_dash="dot", annotation_text="90% (no change)")
    fig.add_hline(y=75, line_dash="dot", annotation_text="75%")
    fig.update_yaxes(range=[0, 105])
    return _layout(fig, "Training consistency (weekly completion)", "%")


def weekly_garmin(runs: list[dict] | None) -> pd.DataFrame:
    if not runs:
        return pd.DataFrame(columns=["week_start", "garmin"])
    df = pd.DataFrame(runs)
    d = pd.to_datetime(df["date"])
    df["week_start"] = (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.normalize()
    totals = df.groupby("week_start")["distance_km"].sum()
    return pd.DataFrame({"week_start": totals.index, "garmin": totals.to_numpy()})


def weekly_volume_fig(weekly: pd.DataFrame, runs: list[dict] | None) -> go.Figure:
    weekly = weekly.copy()
    weekly["week_start"] = pd.to_datetime(weekly["week_start"])
    weekly = weekly[weekly["planned"].notna()]
    g = weekly.merge(weekly_garmin(runs), on="week_start", how="left")
    g["actual_all"] = g["actual"].where(g["actual"].fillna(0) > 0, g["garmin"])
    fig = go.Figure()
    fig.add_scatter(x=g["week_start"], y=g["planned"], name="Planned", line=dict(color="#6B7488", dash="dash"))
    fig.add_scatter(x=g["week_start"], y=g["adjusted"], name="Adjusted", mode="lines+markers",
                    line=dict(color="#4F8CFF"))
    fig.add_scatter(x=g["week_start"], y=g["actual_all"], name="Actual (Garmin or manual)",
                    mode="lines+markers", line=dict(color="#00D4AA", width=3), connectgaps=False)
    fig.update_layout(template="plotly_dark", yaxis_title="km", title="Weekly volume",
                      legend=dict(orientation="h", y=-0.15), margin=dict(t=40, b=10))
    return fig