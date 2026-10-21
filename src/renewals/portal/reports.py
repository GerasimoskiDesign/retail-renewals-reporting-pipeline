"""The six source reports, built from the synthetic world exactly as the portals serve them.

Each report mimics a different source system, so column names, channel spellings and layout
differ on purpose. The transform layer must normalise all of them.

=====================  ======  ==========================================================
Report                 Portal  Content for report day D
=====================  ======  ==========================================================
Contract Renewals      BI      renewal processes completed on D (all channels)
Tariff Migrations      BI      migration processes completed on D (all channels)
Digital Orders         BI      digital-channel processes completed on D
Store Completions      BI      in-store sales *and* click & collect pickups completed on D
Web Orders             BI      digital orders placed from D to D+1 (inclusive range)
CRM Process Events     CRM     cancellation / restart events logged on D
=====================  ======  ==========================================================
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from openpyxl import Workbook

from renewals.world.generator import World
from renewals.world.model import (
    CLICK_COLLECT,
    DIGITAL_CHANNELS,
    EXPRESS,
    IN_STORE,
    MIGRATION,
    RENEWAL,
    SELF_SERVICE,
)

# How each source system spells things. Kept here so the transform can import the inverse.
BI_SALES_CHANNEL = {
    IN_STORE: "STORE",
    CLICK_COLLECT: "STORE",
    SELF_SERVICE: "ONLINE",
    EXPRESS: "ONLINE",
}
BI_DIGITAL_CHANNEL = {
    SELF_SERVICE: "Self-Service",
    EXPRESS: "Express",
    CLICK_COLLECT: "Click & Collect",
}
BI_STREAM = {RENEWAL: "Renewal", MIGRATION: "Tariff Migration"}

BI_TITLE_ROWS = 3  # title, report date, blank line before the header row


@dataclass(frozen=True)
class ReportSpec:
    """Metadata for one downloadable report."""

    key: str
    title: str
    portal: str  # "bi" or "crm"
    range_days: int = 0  # 0 = single day, 1 = "day -> next day" range


CONTRACT_RENEWALS = ReportSpec("contract_renewals", "Contract Renewals", "bi")
TARIFF_MIGRATIONS = ReportSpec("tariff_migrations", "Tariff Migrations", "bi")
DIGITAL_ORDERS = ReportSpec("digital_orders", "Digital Orders", "bi")
STORE_COMPLETIONS = ReportSpec("store_completions", "Store Completions", "bi")
WEB_ORDERS = ReportSpec("web_orders", "Web Orders", "bi", range_days=1)
CRM_EVENTS = ReportSpec("crm_process_events", "CRM Process Events", "crm")

REPORTS: tuple[ReportSpec, ...] = (
    CONTRACT_RENEWALS,
    TARIFF_MIGRATIONS,
    DIGITAL_ORDERS,
    STORE_COMPLETIONS,
    WEB_ORDERS,
    CRM_EVENTS,
)
REPORTS_BY_KEY = {r.key: r for r in REPORTS}


def _on_day(series: pd.Series, day: dt.date) -> pd.Series:
    return series.dt.date == day


def _between(series: pd.Series, start: dt.date, end: dt.date) -> pd.Series:
    d = series.dt.date
    return (d >= start) & (d <= end)


def _process_report(world: World, day: dt.date, stream: str) -> pd.DataFrame:
    p = world.processes
    sel = p[(p["stream"] == stream) & p["completed_at"].notna() & _on_day(p["completed_at"], day)]
    return pd.DataFrame(
        {
            "Process ID": sel["process_id"],
            "Contract ID": sel["contract_id"],
            "MSISDN": sel["msisdn"],
            "Shop Code": sel["shop_id"],
            "Agent": sel["agent_id"].fillna(""),
            "Sales Channel": sel["channel"].map(BI_SALES_CHANNEL),
            "Process Start": sel["started_at"],
            "Process End": sel["completed_at"],
        }
    )


def build_contract_renewals(world: World, day: dt.date) -> pd.DataFrame:
    return _process_report(world, day, RENEWAL)


def build_tariff_migrations(world: World, day: dt.date) -> pd.DataFrame:
    return _process_report(world, day, MIGRATION)


def build_digital_orders(world: World, day: dt.date) -> pd.DataFrame:
    p = world.processes
    sel = p[
        p["channel"].isin(DIGITAL_CHANNELS)
        & p["completed_at"].notna()
        & _on_day(p["completed_at"], day)
    ]
    return pd.DataFrame(
        {
            "Order ID": "WO-" + sel["process_id"].str[1:],
            "Process Ref": sel["process_id"],
            "Contract ID": sel["contract_id"],
            "Product Stream": sel["stream"].map(BI_STREAM),
            "Digital Channel": sel["channel"].map(BI_DIGITAL_CHANNEL),
            "Shop Code": sel["shop_id"],
            "Completed On": sel["completed_at"],
        }
    )


def build_store_completions(world: World, day: dt.date) -> pd.DataFrame:
    p = world.processes
    sel = p[
        p["channel"].isin([IN_STORE, CLICK_COLLECT])
        & p["completed_at"].notna()
        & _on_day(p["completed_at"], day)
    ]
    return pd.DataFrame(
        {
            "Shop Code": sel["shop_id"],
            "Agent": sel["agent_id"],
            "Contract ID": sel["contract_id"],
            "Process Ref": sel["process_id"],
            "Stream": sel["stream"].map(BI_STREAM),
            "Completion Type": sel["channel"].map({IN_STORE: "Sale", CLICK_COLLECT: "Pickup"}),
            "Completed On": sel["completed_at"],
        }
    )


def build_web_orders(world: World, day: dt.date) -> pd.DataFrame:
    """Orders *placed* in the inclusive range D .. D+1.

    The source portal only offers a from/to picker and the business always pulled
    "day to next day", so consecutive files overlap by one full day.
    """
    p = world.processes
    end = day + dt.timedelta(days=WEB_ORDERS.range_days)
    sel = p[p["channel"].isin(DIGITAL_CHANNELS) & _between(p["started_at"], day, end)]
    return pd.DataFrame(
        {
            "Order ID": "WO-" + sel["process_id"].str[1:],
            "Process Ref": sel["process_id"],
            "Contract ID": sel["contract_id"],
            "Product Stream": sel["stream"].map(BI_STREAM),
            "Digital Channel": sel["channel"].map(BI_DIGITAL_CHANNEL),
            "Shop Code": sel["shop_id"],
            "Order Placed": sel["started_at"],
        }
    )


def build_crm_events(world: World, day: dt.date) -> pd.DataFrame:
    ev = world.events
    sel = ev[_on_day(ev["event_ts"], day)]
    out = sel[["process_id", "contract_id", "event_type", "event_ts", "restart_of"]].copy()
    out["restart_of"] = out["restart_of"].fillna("")
    return out


BUILDERS: dict[str, Callable[[World, dt.date], pd.DataFrame]] = {
    CONTRACT_RENEWALS.key: build_contract_renewals,
    TARIFF_MIGRATIONS.key: build_tariff_migrations,
    DIGITAL_ORDERS.key: build_digital_orders,
    STORE_COMPLETIONS.key: build_store_completions,
    WEB_ORDERS.key: build_web_orders,
    CRM_EVENTS.key: build_crm_events,
}


def build_report(world: World, spec: ReportSpec, day: dt.date) -> pd.DataFrame:
    """Return the report rows for ``day`` sorted deterministically."""
    df = BUILDERS[spec.key](world, day)
    return df.sort_values(list(df.columns[:2])).reset_index(drop=True)


def report_to_xlsx(spec: ReportSpec, day: dt.date, df: pd.DataFrame) -> bytes:
    """Serialise a report the way its source system exports it.

    BI exports carry three title lines above the header; the CRM export is a bare table.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    if spec.portal == "bi":
        ws.append([spec.title])
        if spec.range_days:
            end = day + dt.timedelta(days=spec.range_days)
            ws.append([f"Order date: {day:%d.%m.%Y} - {end:%d.%m.%Y}"])
        else:
            ws.append([f"Report date: {day:%d.%m.%Y}"])
        ws.append([])
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([v.to_pydatetime() if isinstance(v, pd.Timestamp) else v for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
