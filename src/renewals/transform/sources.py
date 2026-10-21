"""Read downloaded report files and normalise them to one vocabulary.

Every source spells channels, streams and empty values differently. Normalising at the
edge means the business rules in :mod:`renewals.transform.rules` only ever see one set of
codes (those in :mod:`renewals.world.model`).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from renewals.downloader.naming import report_path
from renewals.portal.reports import (
    BI_DIGITAL_CHANNEL,
    BI_STREAM,
    BI_TITLE_ROWS,
    CONTRACT_RENEWALS,
    CRM_EVENTS,
    DIGITAL_ORDERS,
    REPORTS,
    STORE_COMPLETIONS,
    TARIFF_MIGRATIONS,
    WEB_ORDERS,
    ReportSpec,
)
from renewals.world.model import MIGRATION, RENEWAL

log = logging.getLogger(__name__)

STREAM_CODES = {v: k for k, v in BI_STREAM.items()}
DIGITAL_CODES = {v: k for k, v in BI_DIGITAL_CHANNEL.items()}


class MissingSourceError(FileNotFoundError):
    """Raised when a report file the transform needs is not on disk."""


@dataclass
class SourceData:
    """All normalised source rows for a set of report days."""

    completions: pd.DataFrame  # Contract Renewals + Tariff Migrations
    digital_orders: pd.DataFrame
    store_completions: pd.DataFrame
    web_orders: pd.DataFrame
    crm_events: pd.DataFrame


def _clean_str(s: pd.Series) -> pd.Series:
    """Strip whitespace and turn empty strings into missing values."""
    out = s.astype("string").str.strip()
    return out.mask(out == "", pd.NA)


def read_report(path: Path, spec: ReportSpec) -> pd.DataFrame:
    header = BI_TITLE_ROWS if spec.portal == "bi" else 0
    df = pd.read_excel(path, header=header, engine="openpyxl", dtype=object)
    return df.dropna(how="all")


def _completions(df: pd.DataFrame, stream: str, day: dt.date, src: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "process_id": _clean_str(df["Process ID"]),
            "contract_id": _clean_str(df["Contract ID"]),
            "stream": stream,
            "shop_id": _clean_str(df["Shop Code"]),
            "agent_id": _clean_str(df["Agent"]),
            "sales_channel_code": _clean_str(df["Sales Channel"]).str.upper(),
            "started_at": pd.to_datetime(df["Process Start"]),
            "completed_at": pd.to_datetime(df["Process End"]),
            "report_day": day,
            "source_file": src,
        }
    )


def _digital_orders(df: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": _clean_str(df["Order ID"]),
            "process_id": _clean_str(df["Process Ref"]),
            "contract_id": _clean_str(df["Contract ID"]),
            "stream": _clean_str(df["Product Stream"]).map(STREAM_CODES),
            "channel": _clean_str(df["Digital Channel"]).map(DIGITAL_CODES),
            "shop_id": _clean_str(df["Shop Code"]),
            "completed_at": pd.to_datetime(df["Completed On"]),
            "report_day": day,
        }
    )


def _store_completions(df: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "shop_id": _clean_str(df["Shop Code"]),
            "agent_id": _clean_str(df["Agent"]),
            "contract_id": _clean_str(df["Contract ID"]),
            "process_id": _clean_str(df["Process Ref"]),
            "stream": _clean_str(df["Stream"]).map(STREAM_CODES),
            "completion_type": _clean_str(df["Completion Type"]).str.lower(),
            "completed_at": pd.to_datetime(df["Completed On"]),
            "report_day": day,
        }
    )


def _web_orders(df: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": _clean_str(df["Order ID"]),
            "process_id": _clean_str(df["Process Ref"]),
            "contract_id": _clean_str(df["Contract ID"]),
            "stream": _clean_str(df["Product Stream"]).map(STREAM_CODES),
            "channel": _clean_str(df["Digital Channel"]).map(DIGITAL_CODES),
            "shop_id": _clean_str(df["Shop Code"]),
            "placed_at": pd.to_datetime(df["Order Placed"]),
            "report_day": day,
        }
    )


def _crm_events(df: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "process_id": _clean_str(df["process_id"]),
            "contract_id": _clean_str(df["contract_id"]),
            "event_type": _clean_str(df["event_type"]).str.upper(),
            "event_ts": pd.to_datetime(df["event_ts"]),
            "restart_of": _clean_str(df["restart_of"]),
            "report_day": day,
        }
    )


def _concat(frames: list[pd.DataFrame], columns: list[str]) -> pd.DataFrame:
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def load_sources(root: Path, days: list[dt.date]) -> SourceData:
    """Load and normalise all six reports for ``days``. Fails loudly on any missing file."""
    missing = [
        report_path(root, spec, day)
        for day in days
        for spec in REPORTS
        if not report_path(root, spec, day).exists()
    ]
    if missing:
        listed = "\n  ".join(str(p) for p in missing[:10])
        raise MissingSourceError(f"{len(missing)} report file(s) missing, e.g.:\n  {listed}")

    parts: dict[str, list[pd.DataFrame]] = {spec.key: [] for spec in REPORTS}
    for day in days:
        for spec in REPORTS:
            path = report_path(root, spec, day)
            raw = read_report(path, spec)
            if spec is CONTRACT_RENEWALS:
                part = _completions(raw, RENEWAL, day, path.name)
            elif spec is TARIFF_MIGRATIONS:
                part = _completions(raw, MIGRATION, day, path.name)
            elif spec is DIGITAL_ORDERS:
                part = _digital_orders(raw, day)
            elif spec is STORE_COMPLETIONS:
                part = _store_completions(raw, day)
            elif spec is WEB_ORDERS:
                part = _web_orders(raw, day)
            else:
                part = _crm_events(raw, day)
            parts[spec.key].append(part)

    data = SourceData(
        completions=_concat(
            parts[CONTRACT_RENEWALS.key] + parts[TARIFF_MIGRATIONS.key], COMPLETION_COLUMNS
        ),
        digital_orders=_concat(parts[DIGITAL_ORDERS.key], DIGITAL_COLUMNS),
        store_completions=_concat(parts[STORE_COMPLETIONS.key], STORE_COLUMNS),
        web_orders=_concat(parts[WEB_ORDERS.key], WEB_COLUMNS),
        crm_events=_concat(parts[CRM_EVENTS.key], CRM_COLUMNS),
    )
    log.info(
        "Loaded %d days: %d completion rows, %d digital orders, %d store completions, "
        "%d web orders, %d CRM events",
        len(days),
        len(data.completions),
        len(data.digital_orders),
        len(data.store_completions),
        len(data.web_orders),
        len(data.crm_events),
    )
    return data


COMPLETION_COLUMNS = [
    "process_id",
    "contract_id",
    "stream",
    "shop_id",
    "agent_id",
    "sales_channel_code",
    "started_at",
    "completed_at",
    "report_day",
    "source_file",
]
DIGITAL_COLUMNS = [
    "order_id",
    "process_id",
    "contract_id",
    "stream",
    "channel",
    "shop_id",
    "completed_at",
    "report_day",
]
STORE_COLUMNS = [
    "shop_id",
    "agent_id",
    "contract_id",
    "process_id",
    "stream",
    "completion_type",
    "completed_at",
    "report_day",
]
WEB_COLUMNS = [
    "order_id",
    "process_id",
    "contract_id",
    "stream",
    "channel",
    "shop_id",
    "placed_at",
    "report_day",
]
CRM_COLUMNS = ["process_id", "contract_id", "event_type", "event_ts", "restart_of", "report_day"]
