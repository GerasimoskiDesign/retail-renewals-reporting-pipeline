"""Command line interface: ``python -m renewals <command>``."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path

from renewals.settings import DEFAULT_SEED, Settings, load_settings

log = logging.getLogger("renewals")

# Demo users from config/users.yaml used for the README screenshots (dummy demo values).
SCREENSHOT_USERS = (
    ("regional.manager", "demo-regional", "dashboard_regional_manager.png"),
    ("employee.s05", "demo-employee", "dashboard_employee.png"),
)


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def _offline_downloader(settings: Settings):
    from renewals.downloader.bot import DownloadResult
    from renewals.downloader.plan import plan_downloads
    from renewals.portal.export import export_reports
    from renewals.world.generator import World

    world = World.load(settings.world_dir)

    def download(days: list[dt.date], root: Path) -> DownloadResult:
        skipped = [t.target for t in plan_downloads(days, root) if t.skip]
        written = export_reports(world, days, root)
        return DownloadResult(downloaded=written, skipped=skipped)

    return download


def cmd_generate(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.world.generator import generate_world
    from renewals.world.model import WorldConfig

    world = generate_world(WorldConfig(seed=args.seed))
    world.save(settings.world_dir)
    settings.reference_shops.parent.mkdir(parents=True, exist_ok=True)
    world.shops.to_csv(settings.reference_shops, index=False, lineterminator="\n")
    log.info("World written to %s, shop master to %s", settings.world_dir, settings.reference_shops)
    return 0


def cmd_mock_portal(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    from renewals.portal.app import cached_loader, create_app

    app = create_app(cached_loader(settings.world_dir), settings.bi, settings.crm)
    port = args.port or settings.mock_portal_port
    log.info("BI portal:  http://127.0.0.1:%d/bi/login", port)
    log.info("CRM portal: http://127.0.0.1:%d/crm/login", port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


def cmd_download(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.downloader.dates import date_range, report_days_for_run

    if args.run_date:
        days = report_days_for_run(args.run_date)
    elif args.date:
        days = [args.date]
    elif args.date_from and args.date_to:
        days = date_range(args.date_from, args.date_to)
    else:
        log.error("Give --run-date, --date, or --from and --to")
        return 1
    root = settings.download_root(args.real)
    log.info("Downloading %s into %s", ", ".join(f"{d:%a %d.%m.%Y}" for d in days), root)
    if args.offline:
        result = _offline_downloader(settings)(days, root)
    else:
        from renewals.downloader.bot import run_downloads

        result = run_downloads(settings, days, root, headless=args.headless, force=args.force)
    log.info(
        "Downloaded %d, skipped %d (already on disk), failed %d",
        len(result.downloaded),
        len(result.skipped),
        len(result.failed),
    )
    return 0 if not result.failed else 4


def cmd_transform(args: argparse.Namespace, settings: Settings, publish: bool = True) -> int:
    from renewals.pipeline import compute_month, load_shops, run_month
    from renewals.validation.checks import validate

    root = settings.download_root(args.real)
    if not publish:
        bundle, _ = compute_month(root, args.as_of, args.as_of, mode=args.mode)
        return 0 if validate(bundle).passed else 2
    outcome = run_month(
        root,
        settings.output_dir,
        load_shops(settings.reference_shops),
        args.as_of,
        args.as_of,
        mode=args.mode,
    )
    for path in outcome.published[-3:]:
        log.info("Wrote %s", path)
    return 0


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.orchestrator import run

    downloader = _offline_downloader(settings) if args.offline else None
    run(
        settings,
        args.as_of,
        real=args.real,
        downloader=downloader,
        headless=args.headless,
        force=args.force,
        start=args.start,
        with_mock_portal=args.with_mock_portal,
    )
    return 0


def cmd_dashboard(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.reporting.screenshots import streamlit_command

    cmd = streamlit_command(args.port)
    log.info("Dashboard on http://127.0.0.1:%d (Ctrl+C to stop)", args.port)
    return subprocess.call(cmd, cwd=settings.root)


def cmd_results(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.reporting.results import build_results

    build_results(settings, args.as_of, args.as_of, real=args.real)
    return 0


def cmd_screenshots(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.reporting.screenshots import capture

    shots = [(u, p, settings.docs_img_dir / f) for u, p, f in SCREENSHOT_USERS]
    capture(shots, settings.root)
    return 0


def cmd_hash_password(args: argparse.Namespace, settings: Settings) -> int:
    from renewals.dashboard.auth import hash_password

    sys.stdout.write(hash_password(args.plain) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m renewals", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="build the seeded synthetic world")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.set_defaults(func=cmd_generate)

    m = sub.add_parser("mock-portal", help="serve the mock BI + CRM portals on localhost")
    m.add_argument("--port", type=int, default=None)
    m.set_defaults(func=cmd_mock_portal)

    def dl_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--real",
            action="store_true",
            help="write to REAL_DOWNLOAD_DIR instead of the local test folder",
        )
        sp.add_argument("--headless", action="store_true", help="run Chromium headless")
        sp.add_argument("--force", action="store_true", help="re-download existing files")
        sp.add_argument(
            "--offline",
            action="store_true",
            help="skip the browser: write the portal files straight from the "
            "synthetic world (quick local runs)",
        )

    d = sub.add_parser("download", help="download the six reports with Playwright")
    d.add_argument(
        "--run-date", type=_date, help="scheduled-run date: yesterday, or Fri+Sat+Sun on a Monday"
    )
    d.add_argument("--date", type=_date, help="one report day")
    d.add_argument("--from", dest="date_from", type=_date, help="first report day")
    d.add_argument("--to", dest="date_to", type=_date, help="last report day")
    dl_flags(d)
    d.set_defaults(func=cmd_download)

    for name, publish in (("transform", True), ("validate", False)):
        t = sub.add_parser(
            name,
            help="recompute the month of --as-of from day 1 and publish"
            if publish
            else "compute and validate only (no outputs)",
        )
        t.add_argument("--as-of", type=_date, required=True)
        t.add_argument(
            "--mode",
            choices=["fixed", "naive"],
            default="fixed",
            help="naive = reproduce the old manual counting logic",
        )
        t.add_argument("--real", action="store_true", help="read from REAL_DOWNLOAD_DIR")
        t.set_defaults(func=lambda a, s, pub=publish: cmd_transform(a, s, publish=pub))

    r = sub.add_parser(
        "run", help="orchestrate: download missing -> transform -> validate -> publish"
    )
    r.add_argument("--as-of", type=_date, required=True, help="last report day to include")
    r.add_argument(
        "--start",
        type=_date,
        default=None,
        help="first day if nothing is published yet (default: 1st of the month)",
    )
    r.add_argument(
        "--with-mock-portal",
        action="store_true",
        help="start the mock portals in the background for this run",
    )
    dl_flags(r)
    r.set_defaults(func=cmd_run)

    ds = sub.add_parser("dashboard", help="start the Streamlit dashboard")
    ds.add_argument("--port", type=int, default=8501)
    ds.set_defaults(func=cmd_dashboard)

    rs = sub.add_parser("results", help="README charts + docs/results.md for a month")
    rs.add_argument("--as-of", type=_date, required=True)
    rs.add_argument("--real", action="store_true")
    rs.set_defaults(func=cmd_results)

    sc = sub.add_parser("screenshots", help="capture dashboard screenshots into docs/img")
    sc.set_defaults(func=cmd_screenshots)

    hp = sub.add_parser("hash-password", help="bcrypt-hash a password for config/users.yaml")
    hp.add_argument("plain")
    hp.set_defaults(func=cmd_hash_password)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    settings = load_settings()

    from renewals.orchestrator import DownloadFailedError
    from renewals.transform.sources import MissingSourceError
    from renewals.validation.checks import ValidationError

    try:
        return int(args.func(args, settings) or 0)
    except ValidationError:
        log.error("Validation failed - nothing was published.")
        return 2
    except MissingSourceError as exc:
        log.error("%s", exc)
        return 3
    except DownloadFailedError as exc:
        log.error("%s", exc)
        return 4
