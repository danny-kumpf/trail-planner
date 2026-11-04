"""Plotly figures for the Fitness tab, as JSON the pages draw.

Each figure is built once per color theme, and the page draws whichever matches the browser's. The
dark one carries only its styling and borrows the light one's data, which halves what's sent.

Two measures of different scale never share a plot through a second y-axis; they're stacked as
panels on a shared x-axis instead.
"""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trail_planner.climb_blocks import CLIMB_FLOOR_GRADE_FRAC, ClimbAnalysis, ClimbRun
from trail_planner.climb_model import BlockColumns, ClimbModel, ft_per_hr_per_w_per_kg
from trail_planner.climb_signals import true_runs
from trail_planner.fitness import (
    MIN_MODELLED_HEART_RATE_BPM,
    STANDARD_CONDITIONS,
    Effort,
    FitnessOverview,
    modelled_blocks,
)

M_PER_KM = 1000.0
# Lines are thinned to about this many points; more only slows the page.
MAX_LINE_POINTS = 5000


@dataclass(frozen=True)
class Theme:
    surface: str
    text: str
    muted: str
    gridline: str
    axis: str
    # Categorical colors, distinguishable with color vision deficiencies in both themes.
    series: tuple[str, str, str]
    # Low to high magnitude, growing away from the surface color.
    sequential: tuple[str, ...]
    shade_kept: str
    shade_rejected: str


# Surfaces and text match the tokens in site.css.
LIGHT = Theme(
    surface="#ffffff",
    text="#1d1d1b",
    muted="#6b6b66",
    gridline="#e7e7e1",
    axis="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a"),
    sequential=("#b7d3f6", "#86b6ef", "#3987e5", "#256abf", "#104281"),
    shade_kept="rgba(42,120,214,0.13)",
    shade_rejected="rgba(137,135,129,0.16)",
)
DARK = Theme(
    surface="#1f201e",
    text="#ecece8",
    muted="#9a9a93",
    gridline="#2f302b",
    axis="#454540",
    series=("#3987e5", "#d95926", "#199e70"),
    sequential=("#104281", "#256abf", "#3987e5", "#86b6ef", "#b7d3f6"),
    shade_kept="rgba(57,135,229,0.20)",
    shade_rejected="rgba(137,135,129,0.20)",
)

LINE_WIDTH = 2
MARKER_SIZE = 8
# The keys of a trace that hold data rather than styling.
DATA_KEYS = ("x", "y", "z", "customdata", "text", "ids", "hovertext", "width", "base")


def panel(title: str, caption: str, build: Callable[[Theme], go.Figure], starts_collapsed: bool = False) -> dict:
    """A figure with its heading and caption, built for both themes. A panel that starts collapsed
    shows only its heading until it's opened."""
    light = json.loads(build(LIGHT).to_json())
    dark = json.loads(build(DARK).to_json())
    dark["data"] = [{k: v for k, v in trace.items() if k not in DATA_KEYS} for trace in dark["data"]]
    return {
        "title": title,
        "caption": caption,
        "figure": {"light": light, "dark": dark},
        "starts_collapsed": starts_collapsed,
    }


def styled(figure: go.Figure, theme: Theme, height_px: int, has_legend: bool = False) -> go.Figure:
    figure.update_layout(
        height=height_px,
        paper_bgcolor=theme.surface,
        plot_bgcolor=theme.surface,
        font=dict(color=theme.muted, family='system-ui, -apple-system, "Segoe UI", sans-serif', size=12),
        margin=dict(l=64, r=24, t=48 if has_legend else 24, b=48),
        hovermode="closest",
        showlegend=has_legend,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=theme.muted),
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
        ),
    )
    axis_style = dict(
        gridcolor=theme.gridline,
        zeroline=False,
        linecolor=theme.axis,
        tickfont=dict(color=theme.muted),
        title_font=dict(color=theme.muted, size=12),
        title_standoff=8,
    )
    figure.update_xaxes(**axis_style)
    figure.update_yaxes(**axis_style)
    for annotation in figure.layout.annotations or ():
        # Subplot titles, which plotly makes annotations.
        if annotation.font.size is None:
            annotation.font.update(color=theme.text, size=12)
    return figure


def colorscale(theme: Theme) -> list[list]:
    return [[i / (len(theme.sequential) - 1), color] for i, color in enumerate(theme.sequential)]


def colorbar(title: str, theme: Theme) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=theme.muted, size=11)),
        tickfont=dict(color=theme.muted, size=11),
        outlinewidth=0,
        thickness=12,
    )


def thinned(values: np.ndarray) -> np.ndarray:
    return values[:: max(1, math.ceil(len(values) / MAX_LINE_POINTS))]


def minutes_of(analysis: ClimbAnalysis, time_s) -> np.ndarray:
    """Minutes since the recording started."""
    return (np.asarray(time_s, dtype=float) - analysis.signals.time_s[0]) / 60


def shade(figure: go.Figure, x0: float, x1: float, fill: str, row: int, **annotation) -> None:
    figure.add_vrect(x0=x0, x1=x1, fillcolor=fill, line_width=0, layer="below", row=row, col=1, **annotation)


# ---- One effort ----


def elevation_and_grade(analysis: ClimbAnalysis) -> dict:
    signals = analysis.signals

    def build(theme: Theme) -> go.Figure:
        figure = make_subplots(
            rows=3,
            cols=1,
            vertical_spacing=0.12,
            subplot_titles=("Elevation over time", "Elevation over distance", "Grade"),
        )
        minutes = thinned(minutes_of(analysis, signals.time_s))
        km = thinned(signals.along_track_m / M_PER_KM)
        for row, (x, unit, fmt) in enumerate([(minutes, "min", ".1f"), (km, "km", ".2f")], start=1):
            for name, values, color, width in (
                ("smoothed", signals.elevation_ft, theme.series[1], LINE_WIDTH),
                # Raw on top of smoothed, so anywhere smoothing removed real terrain shows.
                ("raw", signals.raw_elevation_ft, theme.series[0], 1),
            ):
                figure.add_trace(
                    go.Scatter(
                        x=x,
                        y=thinned(values),
                        name=name,
                        mode="lines",
                        line=dict(color=color, width=width),
                        showlegend=row == 1,
                        hovertemplate=f"%{{x:{fmt}}} {unit}<br>%{{y:,.0f}} ft {name}<extra></extra>",
                    ),
                    row=row,
                    col=1,
                )
        figure.add_trace(
            go.Scatter(
                x=km,
                y=thinned(signals.grade_frac) * 100,
                name="grade",
                mode="lines",
                line=dict(color=theme.series[2], width=1.5),
                showlegend=False,
                hovertemplate="%{x:.2f} km<br>%{y:.1f}% grade<extra></extra>",
            ),
            row=3,
            col=1,
        )
        figure.add_hline(
            y=CLIMB_FLOOR_GRADE_FRAC * 100,
            line=dict(color=theme.muted, width=1),
            annotation_text=f"{CLIMB_FLOOR_GRADE_FRAC:.0%} climb floor",
            annotation_position="top left",
            annotation_font=dict(color=theme.muted, size=11),
            row=3,
            col=1,
        )
        figure.update_yaxes(title_text="ft", row=1, col=1)
        figure.update_yaxes(title_text="ft", row=2, col=1)
        figure.update_yaxes(title_text="grade %", row=3, col=1)
        figure.update_xaxes(title_text="minutes", row=1, col=1)
        figure.update_xaxes(title_text="km", row=2, col=1)
        figure.update_xaxes(title_text="km", row=3, col=1)
        return styled(figure, theme, height_px=820, has_legend=True)

    return panel(
        "Elevation and grade",
        "Elevation over time is what vertical rate comes from, and elevation over distance is what "
        "grade comes from; each is drawn raw and smoothed.",
        build,
    )


@dataclass(frozen=True)
class ClimbSummary:
    start_min: float
    end_min: float
    vert_rate_ft_per_hr: float
    start_elevation_ft: float
    end_elevation_ft: float

    @property
    def duration_min(self) -> float:
        return self.end_min - self.start_min

    @property
    def mid_min(self) -> float:
        return (self.start_min + self.end_min) / 2


def climb_summaries(analysis: ClimbAnalysis) -> list[ClimbSummary]:
    signals = analysis.signals
    minutes = minutes_of(analysis, signals.time_s)
    return [
        ClimbSummary(
            start_min=float(minutes[c.start]),
            end_min=float(minutes[c.stop - 1]),
            vert_rate_ft_per_hr=float(np.mean(signals.vert_rate_ft_per_hr[c.start : c.stop])),
            start_elevation_ft=float(signals.elevation_ft[c.start]),
            end_elevation_ft=float(signals.elevation_ft[c.stop - 1]),
        )
        for c in analysis.climbs
    ]


# A label needs about this share of the x-axis to sit over its own climb, and this much room from
# its neighbours' labels.
MIN_LABELLED_CLIMB_FRAC = 0.02
MIN_LABEL_SEPARATION_FRAC = 0.07
MAX_LABELS = 8


def labelled_climbs(summaries: list[ClimbSummary], span_min: float) -> list[ClimbSummary]:
    """The climbs to label: longest first, never crowding a label already chosen."""
    chosen: list[ClimbSummary] = []
    for summary in sorted(summaries, key=lambda s: -s.duration_min):
        if len(chosen) >= MAX_LABELS or summary.duration_min < MIN_LABELLED_CLIMB_FRAC * span_min:
            continue
        if all(abs(summary.mid_min - other.mid_min) >= MIN_LABEL_SEPARATION_FRAC * span_min for other in chosen):
            chosen.append(summary)
    return chosen


def add_climb_averages(figure: go.Figure, analysis: ClimbAnalysis, theme: Theme, row: int) -> None:
    """A bar at each climb's average vertical rate, labelled where there's room."""
    summaries = climb_summaries(analysis)
    if not summaries:
        return
    for summary in summaries:
        shade(figure, summary.start_min, summary.end_min, theme.shade_kept, row)
    # One trace for every bar, broken by None so they don't join up.
    x: list[float | None] = []
    y: list[float | None] = []
    meta: list[list | None] = []
    for s in summaries:
        x += [s.start_min, s.end_min, None]
        y += [s.vert_rate_ft_per_hr, s.vert_rate_ft_per_hr, None]
        meta += [[s.start_elevation_ft, s.end_elevation_ft, s.duration_min]] * 2 + [None]
    figure.add_trace(
        go.Scatter(
            x=x,
            y=y,
            name="climb average",
            mode="lines",
            line=dict(color=theme.series[0], width=3),
            connectgaps=False,
            customdata=meta,
            hovertemplate=(
                "climb average %{y:,.0f} ft/hr<br>%{customdata[0]:,.0f} → %{customdata[1]:,.0f} ft"
                " over %{customdata[2]:.1f} min<extra></extra>"
            ),
        ),
        row=row,
        col=1,
    )
    minutes = minutes_of(analysis, analysis.signals.time_s)
    labelled = labelled_climbs(summaries, float(minutes[-1] - minutes[0]))
    if labelled:
        # Lifted clear of the bar, or the label's second line lands on it.
        offset = 0.07 * float(np.nanmax(analysis.signals.vert_rate_ft_per_hr) - np.nanmin(analysis.signals.vert_rate_ft_per_hr))
        figure.add_trace(
            go.Scatter(
                x=[s.mid_min for s in labelled],
                y=[s.vert_rate_ft_per_hr + offset for s in labelled],
                mode="text",
                text=[
                    f"{s.vert_rate_ft_per_hr:,.0f} ft/hr<br>{s.start_elevation_ft / 1000:.1f}–{s.end_elevation_ft / 1000:.1f}k"
                    for s in labelled
                ],
                textposition="top center",
                textfont=dict(color=theme.text, size=10),
                showlegend=False,
                hoverinfo="skip",
            ),
            row=row,
            col=1,
        )


def shade_runs(figure: go.Figure, analysis: ClimbAnalysis, runs: list[ClimbRun], fill: str, row: int) -> None:
    minutes = minutes_of(analysis, analysis.signals.time_s)
    for run in runs:
        shade(figure, float(minutes[run.start]), float(minutes[run.stop - 1]), fill, row)


def track_overview(analysis: ClimbAnalysis) -> dict:
    signals = analysis.signals
    impossible = true_runs(signals.rate_is_implausible)
    has_heart_rate = signals.heart_rate_bpm is not None
    misread = true_runs(signals.heart_rate_misread) if has_heart_rate else []
    num_rows = 3 if has_heart_rate else 2

    def build(theme: Theme) -> go.Figure:
        titles = ["Elevation", "Vertical rate"] + (["Heart rate"] if has_heart_rate else [])
        figure = make_subplots(rows=num_rows, cols=1, shared_xaxes=True, vertical_spacing=0.08, subplot_titles=titles)
        minutes = minutes_of(analysis, signals.time_s)
        figure.add_trace(
            go.Scatter(
                x=thinned(minutes),
                y=thinned(signals.elevation_ft),
                name="elevation",
                mode="lines",
                line=dict(color=theme.series[0], width=LINE_WIDTH),
                hovertemplate="%{x:.1f} min<br>%{y:,.0f} ft<extra></extra>",
            ),
            row=1,
            col=1,
        )
        # Shading goes after a subplot's first trace: add_vrect skips subplots without one.
        shade_runs(figure, analysis, analysis.short_climbs, theme.shade_rejected, row=1)
        shade_runs(figure, analysis, analysis.climbs, theme.shade_kept, row=1)

        figure.add_trace(
            go.Scatter(
                x=thinned(minutes),
                y=thinned(signals.vert_rate_ft_per_hr),
                name="vertical rate",
                mode="lines",
                line=dict(color=theme.series[1], width=1.5),
                # A rate the recording couldn't have meant is drawn as missing, not bridged.
                connectgaps=False,
                hovertemplate="%{x:.1f} min<br>%{y:,.0f} ft/hr<extra></extra>",
            ),
            row=2,
            col=1,
        )
        figure.add_hline(y=0, line=dict(color=theme.axis, width=1), row=2, col=1)
        for i, (start, stop) in enumerate(impossible):
            # Only the first is labelled; the caption covers the rest.
            label = (
                dict(
                    annotation_text="impossible",
                    annotation_position="top left",
                    annotation_font=dict(color=theme.muted, size=11),
                )
                if i == 0
                else {}
            )
            shade(figure, float(minutes[start]), float(minutes[stop - 1]), theme.shade_rejected, row=2, **label)
        add_climb_averages(figure, analysis, theme, row=2)

        if has_heart_rate:
            figure.add_trace(
                go.Scatter(
                    x=thinned(minutes),
                    y=thinned(signals.heart_rate_bpm),
                    name="heart rate",
                    mode="lines",
                    line=dict(color=theme.series[2], width=1.5),
                    connectgaps=False,
                    hovertemplate="%{x:.1f} min<br>%{y:.0f} bpm<extra></extra>",
                ),
                row=3,
                col=1,
            )
            for i, (start, stop) in enumerate(misread):
                # Only the first is labelled; the caption covers the rest.
                label = (
                    dict(
                        annotation_text="not trusted",
                        annotation_position="top left",
                        annotation_font=dict(color=theme.muted, size=11),
                    )
                    if i == 0
                    else {}
                )
                shade(figure, float(minutes[start]), float(minutes[stop - 1]), theme.shade_rejected, row=3, **label)
            figure.update_yaxes(title_text="bpm", row=3, col=1)

        figure.update_yaxes(title_text="ft", row=1, col=1)
        figure.update_yaxes(title_text="ft/hr", row=2, col=1)
        figure.update_xaxes(title_text="minutes", row=num_rows, col=1)
        figure.update_layout(hovermode="x unified")
        return styled(figure, theme, height_px=230 * num_rows + 80, has_legend=True)

    caption = (
        f"Climbs steeper than {CLIMB_FLOOR_GRADE_FRAC:.0%} are shaded blue: {len(analysis.climbs)} long enough to "
        f"model, and {len(analysis.short_climbs)} in grey too brief for heart rate to catch up. Each modelled climb "
        "has a bar at its average vertical rate; the widest are labelled with it and the elevations they span, and "
        "hovering any bar shows the same."
    )
    if impossible:
        caption += (
            f" {len(impossible)} span(s) of vertical rate are blank: the recording there claims a rate no one "
            "could climb or descend at, so the rate is unknown."
        )
    if misread:
        caption += (
            f" {len(misread)} span(s) of heart rate aren't used: the monitor read low there, then jumped up faster "
            "than a heart can while the effort held steady."
        )
    return panel("Climbing", caption, build)


def block_steps(blocks: BlockColumns, start_min: np.ndarray, values: np.ndarray) -> tuple[list, list]:
    """A flat segment per block at its value, joined to the next block where it starts as this one
    ends (within a climb) and broken where it doesn't (between climbs)."""
    end_min = start_min + blocks.duration_s / 60
    x: list[float | None] = []
    y: list[float | None] = []
    for i, value in enumerate(values):
        x += [float(start_min[i]), float(end_min[i])]
        y += [float(value), float(value)]
        if i + 1 < len(values) and not np.isclose(start_min[i + 1], end_min[i]):
            x.append(None)
            y.append(None)
    return x, y


def predicted_vs_actual(analysis: ClimbAnalysis, comparison: ClimbModel, compared_without_effort: bool) -> dict:
    blocks = modelled_blocks(analysis.blocks)
    columns = BlockColumns.of(blocks)
    start_min = minutes_of(analysis, [b.start_s for b in blocks])
    predicted = comparison.predict_blocks_ft_per_hr(columns)
    difference = columns.vert_rate_ft_per_hr - predicted

    def build(theme: Theme) -> go.Figure:
        figure = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.62, 0.38],
            subplot_titles=("Vertical rate on each climb block", "Climbed minus predicted"),
        )
        for name, values, line in (
            ("predicted", predicted, dict(color=theme.text, width=2, dash="dot")),
            ("climbed", columns.vert_rate_ft_per_hr, dict(color=theme.series[0], width=3)),
        ):
            x, y = block_steps(columns, start_min, values)
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    name=name,
                    mode="lines",
                    line=line,
                    connectgaps=False,
                    hovertemplate=f"%{{x:.1f}} min<br>%{{y:,.0f}} ft/hr {name}<extra></extra>",
                ),
                row=1,
                col=1,
            )
        figure.add_trace(
            go.Bar(
                x=start_min + columns.duration_s / 120,
                y=difference,
                width=columns.duration_s / 60,
                name="difference",
                showlegend=False,
                marker=dict(color=[theme.series[2] if d >= 0 else theme.series[1] for d in difference], line_width=0),
                customdata=np.column_stack(
                    [columns.heart_rate_bpm, columns.elevation_ft, columns.grade_frac * 100]
                ),
                hovertemplate=(
                    "%{x:.1f} min<br>%{y:+,.0f} ft/hr vs predicted<br>HR %{customdata[0]:.0f} · "
                    "%{customdata[1]:,.0f} ft · %{customdata[2]:.0f}% grade<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )
        figure.add_hline(y=0, line=dict(color=theme.axis, width=1), row=2, col=1)
        figure.update_yaxes(title_text="ft/hr", row=1, col=1)
        figure.update_yaxes(title_text="ft/hr", row=2, col=1)
        figure.update_xaxes(title_text="minutes", row=2, col=1)
        figure.update_layout(bargap=0, hovermode="x unified")
        return styled(figure, theme, height_px=560, has_legend=True)

    model_note = (
        "the model fit without this effort, so it isn't compared with itself"
        if compared_without_effort
        else "the current model"
    )
    return panel(
        "Predicted vs climbed",
        f"Each climb block's vertical rate at {MIN_MODELLED_HEART_RATE_BPM:.0f} bpm or more, against what "
        + model_note
        + " predicts for the block's heart rate, "
        "elevation, grade and gait. Below, green blocks climbed faster than predicted and orange ones slower.",
        build,
    )


def adjusted_ft_per_hr(model: ClimbModel, columns: BlockColumns, keep: str) -> np.ndarray:
    """Each block's rate moved through the model to the standard conditions, except in `keep`, which
    is "heart_rate" or "elevation".

    A line through the raw cloud against one variable would mix in the others. Moved to common
    conditions in the others, the points line up with the model's line in the kept one, and still
    show whether that relationship is really straight. Grade and gait are always moved, so every
    block's power is spent on the same terrain.
    """
    standard = STANDARD_CONDITIONS
    adjusted_w_per_kg = columns.power_w_per_kg
    if keep != "heart_rate":
        adjusted_w_per_kg = adjusted_w_per_kg - model.hr_slope_w_per_kg_per_bpm * (
            columns.heart_rate_bpm - standard.heart_rate_bpm
        )
    if keep != "elevation":
        adjusted_w_per_kg = adjusted_w_per_kg - model.elevation_slope_w_per_kg_per_ft * (
            columns.elevation_ft - standard.elevation_ft
        )
    return adjusted_w_per_kg * ft_per_hr_per_w_per_kg(standard.grade_frac, standard.running_frac)


def gait_text(running_frac: float) -> str:
    if running_frac == 0:
        return "hiking"
    if running_frac == 1:
        return "running"
    return f"{running_frac:.0%} running"


def rate_against(
    effort: Effort,
    model: ClimbModel,
    at_standard_ft_per_hr: float,
    keep: str,
    x: np.ndarray,
    standard_x: float,
    x_title: str,
    x_hover: str,
    color_by: np.ndarray,
    color_title: str,
    line_name: str,
    predict_line: Callable[[np.ndarray], np.ndarray],
) -> Callable[[Theme], go.Figure]:
    columns = effort.columns
    adjusted = adjusted_ft_per_hr(model, columns, keep)
    line_x = np.linspace(min(x.min(), standard_x), max(x.max(), standard_x), 50)

    def build(theme: Theme) -> go.Figure:
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=x,
                y=adjusted,
                mode="markers",
                name="climb blocks",
                marker=dict(
                    size=MARKER_SIZE,
                    color=color_by,
                    colorscale=colorscale(theme),
                    showscale=True,
                    colorbar=colorbar(color_title, theme),
                    line=dict(width=1.5, color=theme.surface),
                ),
                customdata=np.column_stack([columns.vert_rate_ft_per_hr, columns.grade_frac * 100]),
                hovertemplate=(
                    f"{x_hover}<br>%{{y:,.0f}} ft/hr adjusted<br>%{{customdata[0]:,.0f}} ft/hr climbed · "
                    "%{customdata[1]:.0f}% grade<extra></extra>"
                ),
            )
        )
        figure.add_trace(
            go.Scatter(
                x=line_x,
                y=predict_line(line_x),
                mode="lines",
                name=line_name,
                line=dict(color=theme.series[1], width=LINE_WIDTH),
                hovertemplate=f"{x_hover} → %{{y:,.0f}} ft/hr model<extra></extra>",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=[standard_x],
                y=[at_standard_ft_per_hr],
                mode="markers",
                name="this effort at standard conditions",
                marker=dict(size=14, symbol="diamond", color=theme.series[2], line=dict(width=1.5, color=theme.surface)),
                hovertemplate="%{y:,.0f} ft/hr at standard conditions<extra></extra>",
            )
        )
        figure.update_layout(xaxis_title=x_title, yaxis_title="vertical rate (ft/hr, adjusted)")
        return styled(figure, theme, height_px=500, has_legend=True)

    return build


def rate_vs_heart_rate(effort: Effort, model: ClimbModel, at_standard_ft_per_hr: float) -> dict:
    columns = effort.columns
    standard = STANDARD_CONDITIONS
    build = rate_against(
        effort,
        model,
        at_standard_ft_per_hr,
        keep="heart_rate",
        x=columns.heart_rate_bpm,
        standard_x=standard.heart_rate_bpm,
        x_title="heart rate (bpm)",
        x_hover="HR %{x:.0f}",
        color_by=columns.elevation_ft,
        color_title="elevation<br>(ft)",
        line_name=f"model: {model.hr_slope_ft_per_hr_per_bpm(standard):+.0f} ft/hr per bpm",
        predict_line=lambda hr: model.predict_ft_per_hr(
            hr, standard.elevation_ft, standard.grade_frac, standard.running_frac
        ),
    )
    return panel(
        "Vertical rate vs heart rate",
        f"Each climb block moved through the model to {standard.elevation_ft:,.0f} ft, "
        f"{standard.grade_frac:.0%} grade and {gait_text(standard.running_frac)}, so what's left is heart rate; "
        "color is the block's real elevation. The diamond is this effort's rate at standard conditions: its blocks' "
        f"average, at {standard.heart_rate_bpm:.0f} bpm.",
        build,
    )


def rate_vs_elevation(effort: Effort, model: ClimbModel, at_standard_ft_per_hr: float) -> dict:
    columns = effort.columns
    standard = STANDARD_CONDITIONS
    build = rate_against(
        effort,
        model,
        at_standard_ft_per_hr,
        keep="elevation",
        x=columns.elevation_ft,
        standard_x=standard.elevation_ft,
        x_title="elevation (ft)",
        x_hover="%{x:,.0f} ft",
        color_by=columns.heart_rate_bpm,
        color_title="heart rate<br>(bpm)",
        line_name=f"model: {model.elevation_slope_ft_per_hr_per_ft(standard) * 1000:+.0f} ft/hr per 1,000 ft",
        predict_line=lambda elevation_ft: model.predict_ft_per_hr(
            standard.heart_rate_bpm, elevation_ft, standard.grade_frac, standard.running_frac
        ),
    )
    return panel(
        "Vertical rate vs elevation",
        f"Each climb block moved through the model to {standard.heart_rate_bpm:.0f} bpm, "
        f"{standard.grade_frac:.0%} grade and {gait_text(standard.running_frac)}, so what's left is elevation; "
        "color is the block's real heart rate. The diamond is this effort's rate at standard conditions, at "
        f"{standard.elevation_ft:,.0f} ft.",
        build,
    )


# ---- Every effort ----

COVERAGE_HR_BIN_BPM = 5.0
COVERAGE_ELEVATION_BIN_FT = 500.0
# A cell's hover lists at most this many efforts, the ones with the most climbing in it.
MAX_COVERAGE_HOVER_EFFORTS = 4


def bin_edges(values: list[float], width: float) -> np.ndarray:
    """Edges at multiples of `width`, spanning every value."""
    low = math.floor(min(values) / width) * width
    high = (math.floor(max(values) / width) + 1) * width
    return np.arange(low, high + width / 2, width)


def effort_label(effort: Effort) -> str:
    return f"{effort.activity.name} ({effort.activity.start_local:%b} {effort.activity.start_local.day})"


def coverage_hover(hr_low_bpm: float, elevation_low_ft: float, minutes_by_effort: dict[str, float]) -> str:
    ranges = (
        f"{hr_low_bpm:.0f}–{hr_low_bpm + COVERAGE_HR_BIN_BPM:.0f} bpm · "
        f"{elevation_low_ft:,.0f}–{elevation_low_ft + COVERAGE_ELEVATION_BIN_FT:,.0f} ft"
    )
    largest = sorted(minutes_by_effort.items(), key=lambda item: -item[1])
    lines = [f"{label}: {minutes:.0f} min" for label, minutes in largest[:MAX_COVERAGE_HOVER_EFFORTS]]
    if len(largest) > MAX_COVERAGE_HOVER_EFFORTS:
        lines.append(f"and {len(largest) - MAX_COVERAGE_HOVER_EFFORTS} more")
    return "<br>".join([ranges, f"{sum(minutes_by_effort.values()):.0f} min of climbing", *lines])


def model_coverage(overview: FitnessOverview, effort: Effort | None = None) -> dict | None:
    """Minutes of climbing the model is fit on, by heart rate and elevation, with every grade
    together. Given an effort, it's the model that effort is compared with, and the effort's blocks
    are drawn on top. None if the model is fit on nothing."""
    leaving_out = effort.id if effort is not None and effort.id in overview.membership else None
    fit_on = [e for e in overview.efforts if e.id in overview.membership and e.id != leaving_out]
    if not fit_on:
        return None
    shown = [*fit_on, *([effort] if effort is not None else [])]
    hr_edges = bin_edges(
        [b.heart_rate_bpm for e in shown for b in e.blocks] + [STANDARD_CONDITIONS.heart_rate_bpm], COVERAGE_HR_BIN_BPM
    )
    elevation_edges = bin_edges(
        [b.elevation_ft for e in shown for b in e.blocks] + [STANDARD_CONDITIONS.elevation_ft], COVERAGE_ELEVATION_BIN_FT
    )
    # Each cell's minutes by effort, indexed [elevation bin][heart rate bin].
    cells = [[{} for _ in hr_edges[:-1]] for _ in elevation_edges[:-1]]
    for fit_effort in fit_on:
        label = effort_label(fit_effort)
        for block in fit_effort.blocks:
            column = int(np.searchsorted(hr_edges, block.heart_rate_bpm, side="right")) - 1
            row = int(np.searchsorted(elevation_edges, block.elevation_ft, side="right")) - 1
            cells[row][column][label] = cells[row][column].get(label, 0.0) + block.duration_s / 60
    minutes = [[sum(cell.values()) if cell else None for cell in row] for row in cells]
    hover = [
        [coverage_hover(hr_low, elevation_low, cell) if cell else "" for hr_low, cell in zip(hr_edges, row)]
        for elevation_low, row in zip(elevation_edges, cells)
    ]

    def build(theme: Theme) -> go.Figure:
        figure = go.Figure(
            go.Heatmap(
                x=(hr_edges[:-1] + hr_edges[1:]) / 2,
                y=(elevation_edges[:-1] + elevation_edges[1:]) / 2,
                z=minutes,
                text=hover,
                hovertemplate="%{text}<extra></extra>",
                hoverongaps=False,
                colorscale=colorscale(theme),
                colorbar=colorbar("minutes", theme),
                xgap=1,
                ygap=1,
                name="model's climbing",
            )
        )
        if effort is not None:
            columns = effort.columns
            figure.add_trace(
                go.Scatter(
                    x=columns.heart_rate_bpm,
                    y=columns.elevation_ft,
                    mode="markers",
                    name="this effort's climb blocks",
                    marker=dict(size=6, color=theme.series[1], line=dict(width=1, color=theme.surface)),
                    hovertemplate="this effort<br>HR %{x:.0f} · %{y:,.0f} ft<extra></extra>",
                )
            )
        figure.add_trace(
            go.Scatter(
                x=[STANDARD_CONDITIONS.heart_rate_bpm],
                y=[STANDARD_CONDITIONS.elevation_ft],
                mode="markers",
                name="standard conditions",
                marker=dict(size=14, symbol="diamond", color=theme.series[2], line=dict(width=1.5, color=theme.surface)),
                hovertemplate="standard conditions<br>HR %{x:.0f} · %{y:,.0f} ft<extra></extra>",
            )
        )
        figure.update_layout(xaxis_title="heart rate (bpm)", yaxis_title="elevation (ft)")
        return styled(figure, theme, height_px=520, has_legend=True)

    if effort is None:
        caption = (
            "Minutes of climbing the model is fit on, by heart rate and elevation, with every grade together. Where "
            "cells are blank, the model has no climbing and is extrapolating. Hover a cell for the efforts in it."
        )
    elif leaving_out is not None:
        caption = (
            "Minutes of climbing in the model this effort is compared with, which leaves it out, by heart rate and "
            "elevation; its own climb blocks are on top. Where they land on blank cells, the comparison extrapolates."
        )
    else:
        caption = (
            "Minutes of climbing the model is fit on, by heart rate and elevation; this effort's climb blocks are on "
            "top. Where they land on blank cells, the comparison extrapolates."
        )
    # Not usually the first thing to look at, so it waits to be opened.
    return panel("Model coverage", caption, build, starts_collapsed=True)


def fitness_trend(overview: FitnessOverview) -> dict:
    """Each effort's rate at standard conditions, over time."""
    model = overview.model

    def build(theme: Theme) -> go.Figure:
        figure = go.Figure()
        for name, in_model, color in (
            ("not in model", False, theme.muted),
            ("in model", True, theme.series[0]),
        ):
            efforts = [e for e in overview.efforts if (e.id in overview.membership) == in_model]
            scores = [overview.scores[e.id] for e in efforts]
            figure.add_trace(
                go.Scatter(
                    x=[e.activity.start_local.isoformat() for e in efforts],
                    y=[s.at_standard_ft_per_hr for s in scores],
                    mode="markers",
                    name=name,
                    marker=dict(size=MARKER_SIZE, color=color, line=dict(width=1.5, color=theme.surface)),
                    customdata=[
                        [e.id, e.activity.name, "–" if s.vs_model_frac is None else f"{s.vs_model_frac:+.1%}"]
                        for e, s in zip(efforts, scores)
                    ],
                    hovertemplate=(
                        "%{customdata[1]}<br>%{x|%b %-d, %Y}<br>%{y:,.0f} ft/hr at standard conditions<br>"
                        "%{customdata[2]} vs model<extra></extra>"
                    ),
                )
            )
        figure.add_hline(
            y=model.predict_at(STANDARD_CONDITIONS),
            line=dict(color=theme.series[1], width=1, dash="dot"),
            annotation_text="model",
            annotation_position="top left",
            annotation_font=dict(color=theme.muted, size=11),
        )
        figure.update_layout(yaxis_title="ft/hr at standard conditions")
        return styled(figure, theme, height_px=360, has_legend=True)

    return panel(
        "Over time",
        "Each effort's rate at standard conditions. The dotted line is the model's own rate there. Click a point to "
        "explore that effort.",
        build,
    )
