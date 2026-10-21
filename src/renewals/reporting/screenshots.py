"""Start the dashboard headless and capture README screenshots with Playwright."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from renewals.portal.server import find_free_port

log = logging.getLogger(__name__)

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


def streamlit_command(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP),
        "--server.headless",
        "true",
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--browser.gatherUsageStats",
        "false",
        "--client.toolbarMode",
        "viewer",
        "--theme.base",
        "light",
    ]


def _wait_healthy(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as r:  # noqa: S310
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Dashboard did not become healthy")


def capture(shots: list[tuple[str, str, Path]], cwd: Path) -> list[Path]:
    """``shots`` = [(username, password, output_png), ...]."""
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        streamlit_command(port), cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    written = []
    try:
        _wait_healthy(url)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for username, password, out in shots:
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                page.goto(url)
                page.locator("input[aria-label='Username']").fill(username)
                page.locator("input[type='password']").fill(password)
                page.get_by_role("button", name="Sign in").click()
                page.get_by_text("Combined gross adds").wait_for(timeout=30_000)
                page.wait_for_timeout(2500)  # let charts finish rendering
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), full_page=True)
                log.info("Saved %s", out)
                written.append(out)
                page.close()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    return written
