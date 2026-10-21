"""End to end: real mock portal on a free port, real Chromium driven by Playwright."""

import dataclasses
import datetime as dt

import pytest
from conftest import make_settings

from renewals.downloader.bot import run_downloads
from renewals.downloader.dates import report_days_for_run
from renewals.downloader.naming import report_path
from renewals.portal.app import create_app
from renewals.portal.reports import REPORTS
from renewals.portal.server import BackgroundServer, find_free_port
from renewals.transform.rules import build_facts
from renewals.transform.sources import load_sources

pytestmark = pytest.mark.e2e

MONDAY = dt.date(2026, 9, 14)


@pytest.fixture()
def portal(world, tmp_path):
    port = find_free_port()
    settings = make_settings(tmp_path, world, port=port)
    app = create_app(lambda: world, settings.bi, settings.crm)
    with BackgroundServer(app, port):
        yield settings


def test_monday_run_downloads_three_days_then_is_idempotent(portal, world):
    settings = portal
    days = report_days_for_run(MONDAY)
    root = settings.download_root(real=False)

    first = run_downloads(settings, days, root, headless=True, base_delay=0.1)
    assert first.failed == []
    assert len(first.downloaded) == 3 * len(REPORTS)
    for day in days:
        for spec in REPORTS:
            assert report_path(root, spec, day).exists()
    assert (root / "September 2026" / "11.09.2026" / "Tariff Migrations 11.xlsx").exists()
    assert (root / "September 2026" / "13.09.2026" / "CRM_Process_Events_20260913.xlsx").exists()
    assert not list(root.rglob("*.part"))

    second = run_downloads(settings, days, root, headless=True)
    assert second.downloaded == [] and len(second.skipped) == 3 * len(REPORTS)

    # what the browser fetched is usable by the transform
    src = load_sources(root, days)
    res = build_facts(src, dt.date(2026, 9, 1), days[-1])
    assert res.audit["counted"] > 0
    assert res.audit["warnings"]["online_without_digital_order"] == 0


def test_real_flag_writes_to_the_real_folder(portal):
    settings = portal
    day = [dt.date(2026, 9, 15)]
    run_downloads(settings, day, settings.download_root(real=True), headless=True)
    assert report_path(settings.real_download_dir, REPORTS[0], day[0]).exists()
    assert not settings.test_download_dir.exists()


def test_bad_credentials_are_reported_not_hidden(portal):
    settings = dataclasses.replace(portal, crm=dataclasses.replace(portal.crm, password="wrong"))
    root = settings.download_root(real=False)
    result = run_downloads(
        settings, [dt.date(2026, 9, 15)], root, headless=True, attempts=2, base_delay=0.1
    )
    failed = [p.name for p, _ in result.failed]
    assert failed == ["CRM_Process_Events_20260915.xlsx"]
    assert len(result.downloaded) == len(REPORTS) - 1
