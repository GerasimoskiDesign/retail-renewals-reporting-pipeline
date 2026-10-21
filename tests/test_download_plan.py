import datetime as dt
from pathlib import Path

import pytest

from renewals.downloader.naming import report_path
from renewals.downloader.plan import plan_downloads, retry
from renewals.portal.export import export_reports
from renewals.portal.reports import DIGITAL_ORDERS, REPORTS

DAY = dt.date(2026, 9, 14)


def test_plan_has_six_tasks_per_day_sorted_and_deduplicated(tmp_path: Path):
    tasks = plan_downloads([DAY, DAY - dt.timedelta(days=1), DAY], tmp_path)
    assert len(tasks) == 2 * len(REPORTS)
    assert [t.day for t in tasks][:: len(REPORTS)] == [DAY - dt.timedelta(days=1), DAY]


def test_existing_file_is_skipped_unless_forced(tmp_path: Path):
    existing = report_path(tmp_path, DIGITAL_ORDERS, DAY)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"already here")

    tasks = {t.spec.key: t for t in plan_downloads([DAY], tmp_path)}
    assert tasks[DIGITAL_ORDERS.key].skip is True
    assert sum(t.skip for t in tasks.values()) == 1

    forced = plan_downloads([DAY], tmp_path, force=True)
    assert not any(t.skip for t in forced)


def test_second_export_writes_nothing_and_keeps_files_untouched(world, tmp_path: Path):
    first = export_reports(world, [DAY], tmp_path)
    assert len(first) == len(REPORTS)
    stamps = {p: p.stat().st_mtime_ns for p in first}
    assert export_reports(world, [DAY], tmp_path) == []
    assert {p: p.stat().st_mtime_ns for p in first} == stamps


def test_retry_backs_off_exponentially_then_succeeds():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("portal busy")
        return "ok"

    assert retry(flaky, attempts=4, base_delay=1.5, sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert sleeps == [1.5, 3.0]


def test_retry_reraises_after_last_attempt():
    sleeps = []

    def always_fails():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        retry(always_fails, attempts=3, base_delay=1, sleep=sleeps.append)
    assert sleeps == [1, 2]
