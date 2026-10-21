"""Where downloaded files live and what they are called.

Layout: ``<root>/<Month YYYY>/<DD.MM.YYYY>/<Report Title> <day>.xlsx``, for example
``downloads/September 2026/02.09.2026/Tariff Migrations 2.xlsx``.

The day number is appended because the people who used the folder opened several days side
by side in Excel, and identical file names in different folders made the windows
indistinguishable. The CRM export is the exception: it names its own files
(``CRM_Process_Events_YYYYMMDD.xlsx``) and downstream tooling already matched that pattern,
so the downloader keeps the server-suggested name unchanged.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from renewals.portal.app import crm_file_name
from renewals.portal.reports import REPORTS, ReportSpec

# Fixed English names: strftime("%B") would follow the OS locale.
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_DAY_FOLDER = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def month_folder(day: dt.date) -> str:
    return f"{MONTH_NAMES[day.month - 1]} {day.year}"


def day_folder(day: dt.date) -> str:
    return f"{day:%d.%m.%Y}"


def report_file_name(spec: ReportSpec, day: dt.date) -> str:
    if spec.portal == "crm":
        return crm_file_name(day)
    return f"{spec.title} {day.day}.xlsx"


def report_path(root: Path, spec: ReportSpec, day: dt.date) -> Path:
    return root / month_folder(day) / day_folder(day) / report_file_name(spec, day)


def parse_day_folder(name: str) -> dt.date | None:
    m = _DAY_FOLDER.match(name)
    if not m:
        return None
    return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def downloaded_days(root: Path) -> list[dt.date]:
    """Days for which all six reports are present on disk."""
    days = []
    if not root.exists():
        return days
    for folder in root.glob("* */*"):
        day = parse_day_folder(folder.name)
        if day and all(report_path(root, spec, day).exists() for spec in REPORTS):
            days.append(day)
    return sorted(days)
