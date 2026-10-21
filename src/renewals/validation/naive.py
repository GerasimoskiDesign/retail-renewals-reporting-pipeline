"""The old manual counting logic, reproduced on purpose.

Before automation, the daily report was assembled by hand in Excel:

* renewals   = rows in *Contract Renewals* for the report day
* migrations = rows in *Tariff Migrations* for the report day
* combined   = renewals + migrations
* digital    = rows in *Digital Orders* + "Pickup" rows in *Store Completions*
  (click & collect was believed to be missing from Digital Orders)

Each step is reasonable in isolation. Together they double count click & collect in the
digital figure, count companion migrations and cancelled-then-restarted contracts twice,
and count prior-month starts in the wrong month. On normal days the errors are small
enough to look plausible; in the campaign week, when click & collect volume jumps, digital
share goes above 100% at pickup-heavy shops. This module exists so a test can prove that.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from renewals.transform.sources import SourceData
from renewals.validation.checks import ReportBundle
from renewals.world.model import MIGRATION, RENEWAL


def _in_month(df: pd.DataFrame, month: dt.date) -> pd.DataFrame:
    if df.empty:
        return df
    days = pd.to_datetime(df["report_day"])
    return df[(days.dt.year == month.year) & (days.dt.month == month.month)]


def naive_bundle(src: SourceData, month: dt.date) -> ReportBundle:
    comp = _in_month(src.completions, month)
    digital_orders = _in_month(src.digital_orders, month)
    store = _in_month(src.store_completions, month)
    pickups = store[store["completion_type"] == "pickup"] if not store.empty else store

    keys = ["report_day", "shop_id"]
    counts = (
        comp.assign(
            renewals=(comp["stream"] == RENEWAL).astype(int),
            migrations=(comp["stream"] == MIGRATION).astype(int),
        )
        .groupby(keys)[["renewals", "migrations"]]
        .sum()
    )
    dig = pd.concat([digital_orders[keys], pickups[keys]]).groupby(keys).size()
    shop_day = counts.join(dig.rename("digital"), how="outer").fillna(0).astype(int)
    shop_day["combined"] = shop_day["renewals"] + shop_day["migrations"]
    shop_day = shop_day.reset_index().rename(columns={"report_day": "day"})
    shop_day["day"] = pd.to_datetime(shop_day["day"])
    shop_day["digital_share"] = shop_day["digital"] / shop_day["combined"].where(
        shop_day["combined"] > 0
    )
    shop_day = shop_day[
        ["day", "shop_id", "renewals", "migrations", "combined", "digital", "digital_share"]
    ]

    gross_adds = comp[["process_id", "contract_id", "stream"]].reset_index(drop=True)
    audit = {
        "source_rows": len(comp),
        "duplicate_rows": 0,
        "unique_processes": int(comp["process_id"].nunique()) if not comp.empty else 0,
        "counted": len(comp),
        "excluded": {},
        "warnings": {},
    }
    return ReportBundle("naive", shop_day.reset_index(drop=True), gross_adds, audit)
