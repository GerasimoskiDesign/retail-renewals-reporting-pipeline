"""README charts: naive vs fixed digital share, and daily volume by channel."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from renewals.validation.checks import ReportBundle  # noqa: E402
from renewals.world.model import CHANNEL_LABELS, CHANNELS  # noqa: E402

log = logging.getLogger(__name__)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"  # fixed logic / categorical slot 1
ORANGE = "#eb6834"  # naive logic / categorical slot 2
CHANNEL_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # fixed slot order 1-4


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))


def _campaign(ax: plt.Axes, start: dt.date, end: dt.date) -> None:
    ax.axvspan(
        pd.Timestamp(start) - pd.Timedelta(hours=12),
        pd.Timestamp(end) + pd.Timedelta(hours=12),
        color="#efeee9",
        zorder=0,
    )
    ax.text(
        pd.Timestamp(start),
        1.0,
        " campaign week",
        transform=ax.get_xaxis_transform(),
        va="top",
        ha="left",
        fontsize=9,
        color=INK_2,
    )


def _month_limits(days: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    first = days.min().replace(day=1)
    return first - pd.Timedelta(hours=18), first + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=18)


def naive_vs_fixed_chart(
    naive: ReportBundle,
    fixed: ReportBundle,
    campaign: tuple[dt.date, dt.date],
    path: Path,
) -> Path:
    """Left: network digital share per day. Right: shop/days that break the 100% cap."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE)
    for bundle, color, label in (
        (naive, ORANGE, "Naive row counting"),
        (fixed, BLUE, "Fixed pipeline"),
    ):
        sd = bundle.shop_day
        net = sd.groupby("day")[["digital", "combined"]].sum()
        share = net["digital"] / net["combined"].where(net["combined"] > 0)
        ax1.plot(
            share.index,
            share.values * 100,
            color=color,
            linewidth=2,
            marker="o",
            markersize=4,
            label=label,
        )
        over = (sd["digital"] > sd["combined"]).groupby(sd["day"]).sum()
        offset = pd.Timedelta(hours=-5 if color == ORANGE else 5)
        ax2.bar(over.index + offset, over.values, width=0.4, color=color, label=label)
    ax1.axhline(100, color=INK, linewidth=1, linestyle="--")
    ax1.set_title("Network digital share per day", color=INK, fontsize=11, loc="left")
    ax1.set_ylabel("Digital share, %", color=INK_2, fontsize=9)
    ax2.set_title("Shop/days with digital share > 100%", color=INK, fontsize=11, loc="left")
    ax2.set_ylabel("Shop/days", color=INK_2, fontsize=9)
    ax2.text(
        0.02,
        0.6,
        "Fixed pipeline: 0 on every day",
        transform=ax2.transAxes,
        ha="left",
        fontsize=9,
        color=BLUE,
    )
    lo, hi = _month_limits(naive.shop_day["day"])
    for ax in (ax1, ax2):
        _style(ax)
        _campaign(ax, *campaign)
        ax.set_xlim(lo, hi)
    ax1.text(lo, 101, " 100% = impossible", color=INK, fontsize=9, va="bottom")
    ax1.legend(frameon=False, fontsize=9, loc="lower right")
    fig.suptitle("Synthetic data, September 2026", x=0.01, ha="left", color=INK_2, fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    log.info("Wrote %s", path)
    return path


def volume_chart(facts: pd.DataFrame, campaign: tuple[dt.date, dt.date], path: Path) -> Path:
    """Counted gross adds per start day, stacked by channel."""
    pivot = (
        facts.groupby(["day", "channel"])
        .size()
        .unstack("channel")
        .reindex(columns=CHANNELS)
        .fillna(0)
    )
    fig, ax = plt.subplots(figsize=(11, 4), facecolor=SURFACE)
    bottom = pd.Series(0.0, index=pivot.index)
    for channel, color in zip(CHANNELS, CHANNEL_COLORS, strict=True):
        ax.bar(
            pivot.index,
            pivot[channel],
            bottom=bottom,
            width=0.8,
            color=color,
            edgecolor=SURFACE,
            linewidth=1,
            label=CHANNEL_LABELS[channel],
        )
        bottom += pivot[channel]
    _style(ax)
    _campaign(ax, *campaign)
    ax.set_title(
        "Counted gross adds per day by channel (fixed pipeline)", color=INK, fontsize=11, loc="left"
    )
    ax.set_ylabel("Gross adds", color=INK_2, fontsize=9)
    ax.legend(frameon=False, fontsize=9, ncol=4, loc="upper right", bbox_to_anchor=(1, 1.13))
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    log.info("Wrote %s", path)
    return path
