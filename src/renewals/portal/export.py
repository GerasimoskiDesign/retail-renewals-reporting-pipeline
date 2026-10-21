"""Write portal reports straight to the download folder layout, without a browser.

Used by the test-suite (fast, no Chromium needed) and by ``download --offline`` for quick
experiments. The files are byte-for-byte what the portal serves, so the transform cannot
tell the difference.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path

from renewals.downloader.plan import plan_downloads
from renewals.portal.reports import build_report, report_to_xlsx
from renewals.world.generator import World


def export_reports(
    world: World, days: Iterable[dt.date], root: Path, force: bool = False
) -> list[Path]:
    written = []
    for task in plan_downloads(days, root, force=force):
        if task.skip:
            continue
        task.target.parent.mkdir(parents=True, exist_ok=True)
        df = build_report(world, task.spec, task.day)
        task.target.write_bytes(report_to_xlsx(task.spec, task.day, df))
        written.append(task.target)
    return written
