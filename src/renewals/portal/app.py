"""FastAPI mock of the two source portals (BI with 5 reports, CRM with 1 report).

The portals are intentionally browser-only (login form, date picker, download button)
because that is what forced the original process to be manual: there was no API to call.
Both portals are mounted in one ASGI app under ``/bi`` and ``/crm`` with separate users and
separate session cookies, so the downloader treats them as two independent sites.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import uuid
from collections.abc import Callable

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from renewals.portal.reports import REPORTS, WEB_ORDERS, build_report, report_to_xlsx
from renewals.settings import PortalCredentials
from renewals.world.generator import World

log = logging.getLogger(__name__)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SESSION_COOKIE = "portal_session"


def crm_file_name(day: dt.date) -> str:
    """The CRM export names files itself; the downloader keeps this name unchanged."""
    return f"CRM_Process_Events_{day:%Y%m%d}.xlsx"


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        "<style>body{font-family:sans-serif;margin:2rem}label{display:block;margin:.5rem 0}"
        "</style></head><body>"
        f"<h1>{html.escape(title)}</h1>{body}</body></html>"
    )


def _login_form(error: str = "") -> str:
    err = f"<p id='error' style='color:#b00'>{html.escape(error)}</p>" if error else ""
    return (
        f"{err}<form method='post' action='login'>"
        "<label>Username <input id='username' name='username' autocomplete='off'></label>"
        "<label>Password <input id='password' name='password' type='password'></label>"
        "<button id='login' type='submit'>Sign in</button></form>"
    )


def _portal(kind: str, creds: PortalCredentials, world_loader: Callable[[], World]) -> FastAPI:
    app = FastAPI(title=f"Mock {kind.upper()} portal", docs_url=None, redoc_url=None)
    sessions: set[str] = set()
    landing = "reports" if kind == "bi" else "export"

    def logged_in(request: Request) -> bool:
        return request.cookies.get(SESSION_COOKIE) in sessions

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> HTMLResponse:
        return _page(f"{kind.upper()} portal - sign in", _login_form())

    @app.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form("")) -> Response:
        if username != creds.user or password != creds.password:
            return _page(f"{kind.upper()} portal - sign in", _login_form("Invalid credentials"))
        token = uuid.uuid4().hex
        sessions.add(token)
        resp = RedirectResponse(url=landing, status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, path=request.scope["root_path"])
        return resp

    if kind == "bi":

        @app.get("/reports", response_class=HTMLResponse)
        def reports_page(request: Request) -> Response:
            if not logged_in(request):
                return RedirectResponse(url="login", status_code=303)
            options = "".join(
                f"<option value='{r.key}'>{html.escape(r.title)}</option>"
                for r in REPORTS
                if r.portal == "bi"
            )
            body = (
                "<form id='report-form' method='get' action='download'>"
                f"<label>Report <select id='report' name='report'>{options}</select></label>"
                "<label>Date from <input id='date-from' name='date_from' type='date' required>"
                "</label><label>Date to (Web Orders only) "
                "<input id='date-to' name='date_to' type='date'></label>"
                "<button id='download' type='submit'>Download</button></form>"
            )
            return _page("BI portal - daily reports", body)

    else:

        @app.get("/export", response_class=HTMLResponse)
        def export_page(request: Request) -> Response:
            if not logged_in(request):
                return RedirectResponse(url="login", status_code=303)
            body = (
                "<form id='export-form' method='get' action='download'>"
                "<input type='hidden' name='report' value='crm_process_events'>"
                "<label>Event date <input id='event-date' name='date_from' type='date' required>"
                "</label><button id='export' type='submit'>Export to Excel</button></form>"
            )
            return _page("CRM - process events export", body)

    @app.get("/download")
    def download(request: Request, report: str, date_from: dt.date, date_to: str = "") -> Response:
        # An empty "Date to" field is submitted as an empty string, not omitted.
        if not logged_in(request):
            return Response("Not signed in", status_code=401)
        spec = next((r for r in REPORTS if r.key == report and r.portal == kind), None)
        if spec is None:
            return Response(f"Unknown report {report!r}", status_code=404)
        if spec is WEB_ORDERS:
            expected = (date_from + dt.timedelta(days=1)).isoformat()
            if date_to.strip() != expected:
                return Response("Web Orders requires a day -> next-day range", status_code=400)
        df = build_report(world_loader(), spec, date_from)
        content = report_to_xlsx(spec, date_from, df)
        if kind == "crm":
            filename = crm_file_name(date_from)
        else:
            filename = f"{spec.key}_{date_from:%Y%m%d}.xlsx"
        log.info("Serving %s for %s (%d rows)", spec.title, date_from, len(df))
        return Response(
            content,
            media_type=XLSX_MIME,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


def create_app(
    world_loader: Callable[[], World],
    bi: PortalCredentials,
    crm: PortalCredentials,
) -> FastAPI:
    """Build the combined ASGI app with both portals mounted."""
    root = FastAPI(title="Mock source portals", docs_url=None, redoc_url=None)
    root.mount("/bi", _portal("bi", bi, world_loader))
    root.mount("/crm", _portal("crm", crm, world_loader))

    @root.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return _page(
            "Mock source portals",
            "<ul><li><a href='bi/login'>BI portal</a></li>"
            "<li><a href='crm/login'>CRM portal</a></li></ul>",
        )

    return root


def cached_loader(world_dir) -> Callable[[], World]:
    """Load the world from disk once, on first request."""
    cache: dict[str, World] = {}

    def load() -> World:
        if "w" not in cache:
            cache["w"] = World.load(world_dir)
        return cache["w"]

    return load
