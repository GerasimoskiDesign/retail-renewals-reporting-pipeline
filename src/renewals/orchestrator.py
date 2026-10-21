"""``python -m renewals run --as-of YYYY-MM-DD``: one command for the daily job.

1. Find the last published report day from the output file names.
2. Download everything missing up to ``--as-of`` (whole month prefix; existing files skip).
3. Recompute every affected month from day 1, validate, publish.
4. Log a timed summary.

Steps that could run in parallel are marked ``PARALLEL:``; the default is sequential because
the whole run is minutes long and sequential logs are far easier to support.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from renewals.downloader.bot import DownloadResult
from renewals.downloader.dates import date_range, month_start
from renewals.pipeline import MonthOutcome, load_shops, month_days, run_month
from renewals.settings import Settings

log = logging.getLogger(__name__)

_REPORT_NAME = re.compile(r"^Renewals Report (\d{2})\.(\d{2})\.(\d{4})\.xlsx$")

Downloader = Callable[[list[dt.date], Path], DownloadResult]


class DownloadFailedError(RuntimeError):
    pass


@dataclass
class RunSummary:
    as_of: dt.date
    last_published: dt.date | None
    days_needed: list[dt.date]
    months: list[MonthOutcome] = field(default_factory=list)
    timings: list[tuple[str, float, str]] = field(default_factory=list)

    def log(self) -> None:
        total = sum(t for _, t, _ in self.timings)
        lines = [f"Run summary (as of {self.as_of}):"]
        lines += [f"  {name:<36} {secs:7.2f}s  {detail}" for name, secs, detail in self.timings]
        lines.append(f"  {'total':<36} {total:7.2f}s")
        log.info("\n".join(lines))


def last_published_date(output_dir: Path) -> dt.date | None:
    """Latest day with a published ``Renewals Report DD.MM.YYYY.xlsx``."""
    dates = []
    for path in (output_dir / "reports").glob("*/*.xlsx"):
        m = _REPORT_NAME.match(path.name)
        if m:
            dates.append(dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    return max(dates) if dates else None


def days_to_process(
    last_published: dt.date | None, as_of: dt.date, start: dt.date | None = None
) -> list[dt.date]:
    """New report days after the last published one, up to and including ``as_of``."""
    if last_published is None:
        begin = start or month_start(as_of)
    else:
        begin = last_published + dt.timedelta(days=1)
    return date_range(begin, as_of) if begin <= as_of else []


def playwright_downloader(settings: Settings, headless: bool, force: bool) -> Downloader:
    from renewals.downloader.bot import run_downloads

    def download(days: list[dt.date], root: Path) -> DownloadResult:
        return run_downloads(settings, days, root, headless=headless, force=force)

    return download


def _mock_portal(settings: Settings):
    from renewals.portal.app import cached_loader, create_app
    from renewals.portal.server import BackgroundServer

    app = create_app(cached_loader(settings.world_dir), settings.bi, settings.crm)
    return BackgroundServer(app, settings.mock_portal_port)


def run(
    settings: Settings,
    as_of: dt.date,
    real: bool = False,
    downloader: Downloader | None = None,
    headless: bool = True,
    force: bool = False,
    start: dt.date | None = None,
    with_mock_portal: bool = False,
) -> RunSummary:
    downloads_root = settings.download_root(real)
    output_dir = settings.output_dir

    t0 = time.perf_counter()
    last = last_published_date(output_dir)
    new_days = days_to_process(last, as_of, start)
    summary = RunSummary(as_of, last, new_days)
    summary.timings.append(
        (
            "find last published",
            time.perf_counter() - t0,
            f"last={last or 'none'}, new days={len(new_days)}",
        )
    )
    if not new_days and not force:
        log.info("Already published up to %s; nothing to do", last)
        summary.log()
        return summary
    if force and not new_days:
        new_days = [as_of]

    months = sorted({month_start(d) for d in new_days})
    needed = sorted({d for m in months for d in month_days(m, as_of)})

    t0 = time.perf_counter()
    downloader = downloader or playwright_downloader(settings, headless, force)
    # PARALLEL: the BI and CRM portal sessions are independent and could download concurrently.
    with _mock_portal(settings) if with_mock_portal else nullcontext():
        result = downloader(needed, downloads_root)
    summary.timings.append(
        (
            "download",
            time.perf_counter() - t0,
            f"{len(result.downloaded)} downloaded, {len(result.skipped)} skipped, "
            f"{len(result.failed)} failed",
        )
    )
    if result.failed:
        summary.log()
        raise DownloadFailedError(f"{len(result.failed)} downloads failed; nothing published")

    shops = load_shops(settings.reference_shops)
    # PARALLEL: months are independent (only relevant when a run spans a month boundary).
    for month in months:
        t0 = time.perf_counter()
        outcome = run_month(downloads_root, output_dir, shops, month, as_of)
        summary.months.append(outcome)
        audit = outcome.bundle.audit
        summary.timings.append(
            (
                f"transform+validate+publish {month:%Y-%m}",
                time.perf_counter() - t0,
                f"{audit['counted']} gross adds, {len(outcome.published)} reports",
            )
        )
    summary.log()
    return summary
