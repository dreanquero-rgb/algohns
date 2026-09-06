"""Algohns charting layer — one visual system across the whole platform.

Every chart in the dashboard is built here so marks, colours, grid treatment and
hover behaviour stay identical everywhere.

Colour policy
-------------
Series colours come from a palette **validated with the data-viz validator**
against this app's panel surface (#0F172A, dark mode): lightness band, chroma
floor, adjacent-pair CVD separation, normal-vision floor and 3:1 contrast all
PASS. The brand gold/cyan are deliberately *not* used as series colours (they
fail the dark lightness band and glare); they stay on KPI values and chrome,
where they are text tokens rather than data marks.

Mark specs (fixed): 2px lines with round caps · markers >= 8px with a 2px surface
ring · bars <= 24px with 4px rounded data-ends · area fills at ~10% opacity ·
hairline recessive gridlines · hover on by default · legend whenever >= 2 series.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# --- Surfaces & ink tokens ---------------------------------------------------
SURFACE = "#0F172A"     # chart surface (app panel)
PAGE = "#070B13"
INK = "#F8FAFC"         # primary text
INK_2 = "#94A3B8"       # secondary text
INK_3 = "#64748B"       # muted text
GRID = "#1E293B"        # one step off surface, recessive

# --- Brand (chrome only, never a data mark) ---------------------------------
BRAND_GOLD = "#E2B86B"
BRAND_CYAN = "#38BDF8"

# --- Validated categorical series palette (dark, surface #0F172A) -----------
SERIES: list[str] = [
    "#3987e5",  # 1 blue
    "#d95926",  # 2 orange
    "#199e70",  # 3 aqua
    "#c98500",  # 4 yellow
    "#d55181",  # 5 magenta
    "#008300",  # 6 green
    "#9085e9",  # 7 violet
    "#e66767",  # 8 red
]
# Scatter / all-pairs forms are capped at the first three slots (validated
# all-pairs); beyond that, fold into "Other" or facet.
SERIES_ALLPAIRS_CAP = 3

# Sequential ramp (single hue, light -> dark) for magnitude encodings.
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
# Diverging: blue <-> red with a neutral gray midpoint.
DIVERGING = [[0.0, "#184f95"], [0.5, "#383835"], [1.0, "#e34948"]]

# Status colours — reserved, never reused as "series N".
GOOD = "#199e70"
BAD = "#e66767"

_FONT = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"


def color(i: int) -> str:
    """Categorical slot i, assigned in fixed order (never cycled past 8)."""
    return SERIES[i % len(SERIES)]


def _base(fig: go.Figure, title: str = "", height: int = 340, legend: bool = False,
          hovermode: str = "x unified") -> go.Figure:
    """Apply the shared layout: recessive axes, ink tokens, hover, legend."""
    fig.update_layout(
        title=dict(text=title, font=dict(color=INK, size=15, family=_FONT), x=0, xanchor="left")
        if title else None,
        height=height,
        margin=dict(l=8, r=8, t=52 if (title and legend) else (38 if title else 12), b=8),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=_FONT, color=INK_2, size=12),
        hovermode=hovermode,
        hoverlabel=dict(bgcolor=PAGE, bordercolor=GRID,
                        font=dict(color=INK, family=_FONT, size=12)),
        showlegend=legend,
        # Right-aligned so it never collides with the left-aligned title.
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(color=INK_2, size=11), bgcolor="rgba(0,0,0,0)"),
    )
    axis = dict(gridcolor=GRID, gridwidth=1, griddash="solid", zeroline=False,
                linecolor=GRID, linewidth=1, tickfont=dict(color=INK_3, size=11),
                title_font=dict(color=INK_3, size=11))
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Line / area — change over time
# ---------------------------------------------------------------------------
def line(df: pd.DataFrame, title: str = "", height: int = 340, yfmt: str | None = None,
         log_y: bool = False, end_labels: bool = True) -> go.Figure:
    """Multi-series line chart. Legend whenever >= 2 series; end dots always."""
    if isinstance(df, pd.Series):
        df = df.to_frame()
    fig = go.Figure()
    cols = list(df.columns)
    for i, c in enumerate(cols):
        s = df[c].dropna()
        if s.empty:
            continue
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=str(c), mode="lines",
            line=dict(color=color(i), width=2, shape="linear"),
            hovertemplate=f"<b>{c}</b>: %{{y:,.2f}}<extra></extra>",
        ))
        # end marker: >= 8px with a 2px surface ring
        fig.add_trace(go.Scatter(
            x=[s.index[-1]], y=[s.values[-1]], mode="markers", showlegend=False,
            marker=dict(color=color(i), size=9, line=dict(color=SURFACE, width=2)),
            hoverinfo="skip",
        ))
    fig = _base(fig, title, height, legend=len(cols) >= 2)
    if log_y:
        fig.update_yaxes(type="log")
    if yfmt:
        fig.update_yaxes(tickformat=yfmt)
    return fig


def area(s: pd.Series, title: str = "", height: int = 240, slot: int = 0,
         negative: bool = False) -> go.Figure:
    """Single-series filled area (10% wash). Used for drawdown etc."""
    s = s.dropna()
    c = BAD if negative else color(slot)
    fig = go.Figure(go.Scatter(
        x=s.index, y=s.values, mode="lines", name=s.name or "",
        line=dict(color=c, width=2), fill="tozeroy", fillcolor=_rgba(c, 0.10),
        hovertemplate="%{y:,.2%}<extra></extra>" if negative else "%{y:,.2f}<extra></extra>",
    ))
    fig = _base(fig, title, height, legend=False)
    if negative:
        fig.update_yaxes(tickformat=".0%")   # read as -10%, not -0.1
    return fig


# ---------------------------------------------------------------------------
# Bars — magnitude
# ---------------------------------------------------------------------------
def hbar(labels, values, title: str = "", height: int = 380, slot: int = 0,
         value_fmt: str = "{:,.2f}", suffix: str = "", color_by_sign: bool = False) -> go.Figure:
    """Horizontal bars with direct value labels (sorted by the caller)."""
    colors = ([GOOD if v >= 0 else BAD for v in values] if color_by_sign else color(slot))
    text = [f"{value_fmt.format(v)}{suffix}" for v in values]
    fig = go.Figure(go.Bar(
        x=list(values), y=[str(l) for l in labels], orientation="h",
        marker=dict(color=colors, cornerradius=4),
        text=text, textposition="outside",
        textfont=dict(color=INK_2, size=11, family=_FONT),
        hovertemplate="<b>%{y}</b>: %{x:,.3f}" + suffix + "<extra></extra>",
        cliponaxis=False,
    ))
    fig = _base(fig, title, height, legend=False, hovermode="closest")
    fig.update_layout(bargap=0.35)
    fig.update_xaxes(showgrid=True)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    return fig


def bar(x, y, title: str = "", height: int = 320, slot: int = 0, suffix: str = "",
        color_by_sign: bool = False, yfmt: str | None = None) -> go.Figure:
    """Vertical bars, <= 24px thick with 4px rounded data-ends."""
    colors = ([GOOD if v >= 0 else BAD for v in y] if color_by_sign else color(slot))
    fig = go.Figure(go.Bar(
        x=list(x), y=list(y), marker=dict(color=colors, cornerradius=4),
        hovertemplate="<b>%{x}</b>: %{y:,.2f}" + suffix + "<extra></extra>",
    ))
    fig = _base(fig, title, height, legend=False, hovermode="closest")
    fig.update_layout(bargap=0.35)
    fig.update_traces(width=None)
    if yfmt:
        fig.update_yaxes(tickformat=yfmt)
    return fig


def grouped_bar(df: pd.DataFrame, title: str = "", height: int = 340,
                yfmt: str | None = None) -> go.Figure:
    """Grouped bars: index = categories on x, one series per column."""
    fig = go.Figure()
    for i, c in enumerate(df.columns):
        fig.add_trace(go.Bar(
            x=[str(v) for v in df.index], y=df[c].values, name=str(c),
            marker=dict(color=color(i), cornerradius=4, line=dict(width=0)),
            hovertemplate=f"<b>{c}</b> %{{x}}: %{{y:,.0f}}<extra></extra>",
        ))
    fig = _base(fig, title, height, legend=df.shape[1] >= 2, hovermode="x unified")
    # 2px surface gap between touching bars
    fig.update_layout(barmode="group", bargap=0.30, bargroupgap=0.08)
    if yfmt:
        fig.update_yaxes(tickformat=yfmt)
    return fig


def stacked_bar(df: pd.DataFrame, title: str = "", height: int = 340) -> go.Figure:
    """Stacked composition bars with a 2px surface gap between segments."""
    fig = go.Figure()
    for i, c in enumerate(df.columns):
        fig.add_trace(go.Bar(
            x=[str(v) for v in df.index], y=df[c].values, name=str(c),
            marker=dict(color=color(i), line=dict(color=SURFACE, width=2)),
            hovertemplate=f"<b>{c}</b> %{{x}}: %{{y:,.0f}}<extra></extra>",
        ))
    fig = _base(fig, title, height, legend=True, hovermode="x unified")
    fig.update_layout(barmode="stack", bargap=0.35)
    return fig


# ---------------------------------------------------------------------------
# Scatter — relationship (all-pairs palette cap applies)
# ---------------------------------------------------------------------------
def scatter(df: pd.DataFrame, x: str, y: str, label: str, group: str | None = None,
            title: str = "", height: int = 420, xtitle: str = "", ytitle: str = "",
            suffix: str = "") -> go.Figure:
    """Scatter with optional grouping (capped at 3 validated all-pairs colours)."""
    fig = go.Figure()
    if group and group in df.columns:
        top = df[group].value_counts().index[:SERIES_ALLPAIRS_CAP].tolist()
        df = df.copy()
        df["_g"] = df[group].where(df[group].isin(top), "Other")
        groups = top + (["Other"] if (df["_g"] == "Other").any() else [])
        for i, g in enumerate(groups):
            sub = df[df["_g"] == g]
            c = INK_3 if g == "Other" else color(i)
            fig.add_trace(go.Scatter(
                x=sub[x], y=sub[y], mode="markers", name=str(g),
                marker=dict(color=c, size=9, line=dict(color=SURFACE, width=2)),
                customdata=sub[[label]].values,
                hovertemplate=("<b>%{customdata[0]}</b><br>" + f"{xtitle or x}: %{{x:,.2f}}<br>"
                               + f"{ytitle or y}: %{{y:,.3f}}{suffix}<extra></extra>"),
            ))
        legend = True
    else:
        fig.add_trace(go.Scatter(
            x=df[x], y=df[y], mode="markers", name=y,
            marker=dict(color=color(0), size=9, line=dict(color=SURFACE, width=2)),
            customdata=df[[label]].values,
            hovertemplate=("<b>%{customdata[0]}</b><br>" + f"{xtitle or x}: %{{x:,.2f}}<br>"
                           + f"{ytitle or y}: %{{y:,.3f}}{suffix}<extra></extra>"),
        ))
        legend = False
    fig = _base(fig, title, height, legend=legend, hovermode="closest")
    fig.update_xaxes(title_text=xtitle or x)
    fig.update_yaxes(title_text=ytitle or y)
    return fig


# ---------------------------------------------------------------------------
# Heatmap — magnitude (sequential single hue) / correlation (diverging)
# ---------------------------------------------------------------------------
def heatmap(matrix: pd.DataFrame, title: str = "", height: int = 420,
            diverging: bool = True) -> go.Figure:
    fig = go.Figure(go.Heatmap(
        z=matrix.values, x=[str(c) for c in matrix.columns], y=[str(i) for i in matrix.index],
        colorscale=DIVERGING if diverging else [[i / (len(SEQ_BLUE) - 1), c]
                                                for i, c in enumerate(SEQ_BLUE)],
        zmid=0 if diverging else None,
        xgap=2, ygap=2,  # 2px surface gap between cells
        colorbar=dict(outlinewidth=0, tickfont=dict(color=INK_3, size=10), thickness=10),
        hovertemplate="<b>%{y} · %{x}</b>: %{z:.2f}<extra></extra>",
    ))
    fig = _base(fig, title, height, legend=False, hovermode="closest")
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    return fig


# ---------------------------------------------------------------------------
# Donut — part-to-whole (few slices only)
# ---------------------------------------------------------------------------
def donut(labels, values, title: str = "", height: int = 340, center: str = "") -> go.Figure:
    fig = go.Figure(go.Pie(
        labels=[str(l) for l in labels], values=list(values), hole=0.62, sort=True,
        direction="clockwise",
        marker=dict(colors=[color(i) for i in range(len(labels))],
                    line=dict(color=SURFACE, width=2)),  # 2px surface gap
        textinfo="label+percent", textposition="outside",
        textfont=dict(color=INK_2, size=11, family=_FONT),
        hovertemplate="<b>%{label}</b>: %{percent} (%{value:.3f})<extra></extra>",
    ))
    fig = _base(fig, title, height, legend=False, hovermode="closest")
    if center:
        fig.add_annotation(text=center, showarrow=False,
                           font=dict(color=INK, size=16, family=_FONT))
    return fig


# ---------------------------------------------------------------------------
# Waterfall — how a total is built (income statement)
# ---------------------------------------------------------------------------
def waterfall(labels, values, measures, title: str = "", height: int = 380) -> go.Figure:
    # Plotly labels a "total" bar with its y value, so compute the running total
    # rather than passing None (which would print a misleading 0).
    values = list(values)
    running = 0.0
    for i, (v, m) in enumerate(zip(values, measures)):
        if m in ("absolute",):
            running = float(v or 0)
        elif m in ("relative",):
            running += float(v or 0)
        elif m == "total" and v is None:
            values[i] = running
    fig = go.Figure(go.Waterfall(
        x=[str(l) for l in labels], y=values, measure=list(measures),
        connector=dict(line=dict(color=GRID, width=1)),
        increasing=dict(marker=dict(color=GOOD)),
        decreasing=dict(marker=dict(color=BAD)),
        totals=dict(marker=dict(color=color(0))),
        textposition="outside", texttemplate="%{y:,.0f}",
        textfont=dict(color=INK_2, size=10, family=_FONT),
        hovertemplate="<b>%{x}</b>: %{y:,.0f}<extra></extra>",
    ))
    fig = _base(fig, title, height, legend=False, hovermode="closest")
    fig.update_layout(bargap=0.35)
    return fig
