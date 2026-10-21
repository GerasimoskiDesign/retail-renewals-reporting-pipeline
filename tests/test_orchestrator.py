import datetime as dt
from pathlib import Path

import pytest
from conftest import make_settings

from renewals.downloader.bot import DownloadResult
from renewals.downloader.plan import plan_downloads
from renewals.orchestrator import (
    DownloadFailedError,
    days_to_process,
    last_published_date,
    run,
)
from renewals.portal.export import export_reports

D = dt.date


def test_last_published_date_reads_output_file_names(tmp_path: Path):
    assert last_published_date(tmp_path) is None
    folder = tmp_path / "reports" / "September 2026"
    folder.mkdir(parents=True)
    for name in (
        "Renewals Report 01.09.2026.xlsx",
        "Renewals Report 12.09.2026.xlsx",
        "Renewals Report 30.09.2026 (copy).xlsx",
        "notes.xlsx",
    ):
        (folder / name).write_bytes(b"")
    assert last_published_date(tmp_path) == D(2026, 9, 12)


def test_days_to_process():
    assert days_to_process(None, D(2026, 9, 3)) == [D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3)]
    assert days_to_process(None, D(2026, 9, 3), start=D(2026, 9, 3)) == [D(2026, 9, 3)]
    assert days_to_process(D(2026, 9, 3), D(2026, 9, 5)) == [D(2026, 9, 4), D(2026, 9, 5)]
    assert days_to_process(D(2026, 9, 5), D(2026, 9, 5)) == []


class OfflineDownloader:
    """Stands in for the Playwright bot; records which days it was asked for."""

    def __init__(self, world):
        self.world = world
        self.calls: list[list[dt.date]] = []

    def __call__(self, days, root):
        self.calls.append(list(days))
        skipped = [t.target for t in plan_downloads(days, root) if t.skip]
        return DownloadResult(downloaded=export_reports(self.world, days, root), skipped=skipped)


def test_run_downloads_missing_days_and_publishes_incrementally(world, tmp_path: Path):
    settings = make_settings(tmp_path, world)
    dl = OfflineDownloader(world)

    first = run(settings, D(2026, 9, 3), downloader=dl)
    assert first.last_published is None
    assert len(first.months[0].published) == 3
    assert last_published_date(settings.output_dir) == D(2026, 9, 3)

    again = run(settings, D(2026, 9, 3), downloader=dl)
    assert again.months == [] and len(dl.calls) == 1  # nothing to do, no download

    later = run(settings, D(2026, 9, 5), downloader=dl)
    assert later.days_needed == [D(2026, 9, 4), D(2026, 9, 5)]
    # the whole month prefix is requested (recompute from day 1), existing files skip
    assert dl.calls[-1] == [D(2026, 9, d) for d in range(1, 6)]
    assert len(later.months[0].published) == 5
    names = [t[0] for t in later.timings]
    assert names[0] == "find last published" and names[1] == "download"


def test_run_across_month_boundary_recomputes_both_months(world, tmp_path: Path):
    settings = make_settings(tmp_path, world)
    summary = run(
        settings, D(2026, 10, 2), downloader=OfflineDownloader(world), start=D(2026, 9, 29)
    )
    assert [m.month for m in summary.months] == [D(2026, 9, 1), D(2026, 10, 1)]


def test_failed_download_publishes_nothing(world, tmp_path: Path):
    settings = make_settings(tmp_path, world)

    def broken(days, root):
        return DownloadResult(failed=[(root / "x.xlsx", "timeout")])

    with pytest.raises(DownloadFailedError):
        run(settings, D(2026, 9, 3), downloader=broken)
    assert last_published_date(settings.output_dir) is None
