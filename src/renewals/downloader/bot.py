"""Playwright bot that signs into both portals and downloads the daily reports."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from renewals.downloader.plan import DownloadTask, plan_downloads, retry
from renewals.settings import PortalCredentials, Settings

log = logging.getLogger(__name__)

TIMEOUT_MS = 20_000


@dataclass
class DownloadResult:
    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)


class PortalSession:
    """One signed-in browser context for one portal."""

    def __init__(self, context: BrowserContext, creds: PortalCredentials, kind: str) -> None:
        self.page: Page = context.new_page()
        self.page.set_default_timeout(TIMEOUT_MS)
        self.creds = creds
        self.kind = kind
        self.landing = f"{creds.base_url.rstrip('/')}/{'reports' if kind == 'bi' else 'export'}"

    def ensure_signed_in(self) -> None:
        """Open the report page, signing in first if the portal bounced us to the login form."""
        self.page.goto(self.landing)
        if "/login" in self.page.url:
            log.info("Signing in to %s portal", self.kind.upper())
            self.page.fill("#username", self.creds.user)
            self.page.fill("#password", self.creds.password)
            self.page.click("#login")
            self.page.wait_for_load_state()
            if "/login" in self.page.url:
                raise PermissionError(f"Login to {self.kind.upper()} portal failed")

    def download(self, task: DownloadTask) -> Path:
        self.ensure_signed_in()
        page = self.page
        if self.kind == "bi":
            page.select_option("#report", task.spec.key)
            page.fill("#date-from", task.day.isoformat())
            if task.spec.range_days:
                end = task.day + dt.timedelta(days=task.spec.range_days)
                page.fill("#date-to", end.isoformat())
            else:
                page.fill("#date-to", "")
            button = "#download"
        else:
            page.fill("#event-date", task.day.isoformat())
            button = "#export"
        with page.expect_download(timeout=TIMEOUT_MS) as info:
            page.click(button)
        download = info.value
        task.target.parent.mkdir(parents=True, exist_ok=True)
        target = task.target
        if self.kind == "crm" and download.suggested_filename != target.name:
            log.warning(
                "CRM suggested %s, expected %s; keeping the CRM name",
                download.suggested_filename,
                target.name,
            )
            target = target.with_name(download.suggested_filename)
        tmp = target.with_suffix(".part")
        download.save_as(tmp)
        tmp.replace(target)  # atomic: a half-written file never looks like a finished one
        return target


def run_downloads(
    settings: Settings,
    days: list[dt.date],
    root: Path,
    headless: bool = True,
    force: bool = False,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> DownloadResult:
    """Download all six reports for ``days`` into ``root``.

    Parallelisable: the BI and CRM portals are independent sites, so the two sessions could run
    in separate threads/browsers. Kept sequential because the BI portal throttles concurrent
    exports per user and the whole run takes minutes, not hours.
    """
    result = DownloadResult()
    tasks = plan_downloads(days, root, force=force)
    for t in tasks:
        if t.skip:
            result.skipped.append(t.target)
    todo = [t for t in tasks if not t.skip]
    log.info("Download plan: %d to fetch, %d already on disk", len(todo), len(result.skipped))
    if not todo:
        return result
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            sessions = {
                "bi": PortalSession(browser.new_context(accept_downloads=True), settings.bi, "bi"),
                "crm": PortalSession(
                    browser.new_context(accept_downloads=True), settings.crm, "crm"
                ),
            }
            for task in todo:
                session = sessions[task.spec.portal]
                try:
                    path = retry(
                        lambda s=session, t=task: s.download(t),
                        attempts=attempts,
                        base_delay=base_delay,
                    )
                    result.downloaded.append(path)
                    log.info("Downloaded %s", path.relative_to(root))
                except Exception as exc:  # noqa: BLE001 - recorded and reported, run continues
                    log.error("Giving up on %s: %s", task.target.name, exc)
                    result.failed.append((task.target, str(exc)))
        finally:
            browser.close()
    return result
