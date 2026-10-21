"""Which report days a run should fetch.

Reports are always for *yesterday*. Nobody runs the job on weekends, so the Monday run must
pick up Friday, Saturday and Sunday. They are fetched as three separate single-day reports
(not one Fri-Sun range) because the downstream report is per day and the portals' range
exports aggregate differently from their daily exports.
"""

from __future__ import annotations

import datetime as dt

MONDAY = 0


def report_days_for_run(run_date: dt.date) -> list[dt.date]:
    """Days to download when the scheduled job runs on ``run_date``."""
    if run_date.weekday() == MONDAY:
        return [run_date - dt.timedelta(days=n) for n in (3, 2, 1)]
    return [run_date - dt.timedelta(days=1)]


def date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    """Inclusive list of days from ``start`` to ``end``."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return [start + dt.timedelta(days=n) for n in range((end - start).days + 1)]


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)
