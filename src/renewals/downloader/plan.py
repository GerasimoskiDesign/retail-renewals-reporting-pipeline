"""Download planning (pure, browser-free) and retry with backoff."""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from renewals.downloader.naming import report_path
from renewals.portal.reports import REPORTS, ReportSpec

log = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class DownloadTask:
    spec: ReportSpec
    day: dt.date
    target: Path
    skip: bool


def plan_downloads(
    days: Iterable[dt.date],
    root: Path,
    force: bool = False,
    reports: Iterable[ReportSpec] = REPORTS,
) -> list[DownloadTask]:
    """One task per (day, report). Existing files are skipped unless ``force``.

    Idempotency matters because the job is re-run after partial failures; re-downloading
    everything would hammer the portals and silently replace files someone already opened.
    """
    specs = list(reports)
    return [
        DownloadTask(spec, day, target, skip=target.exists() and not force)
        for day in sorted(set(days))
        for spec in specs
        for target in [report_path(root, spec, day)]
    ]


def retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Call ``fn`` until it succeeds, sleeping ``base_delay * 2**n`` between attempts.

    Portal exports time out under load at month start; exponential backoff gives the report
    server room instead of retrying into the same overload.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - any failure is retried, last one re-raised
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            log.warning(
                "Attempt %d/%d failed (%s); retrying in %.1fs", attempt, attempts, exc, delay
            )
            if on_retry:
                on_retry(attempt, exc)
            sleep(delay)
    raise AssertionError("unreachable")
