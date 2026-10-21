"""Transform -> validate -> publish for one reporting month.

Publishing is all-or-nothing: outputs are written to a staging folder and only swapped into
place after validation passes, so a failed run never leaves a half-updated month behind.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from renewals.downloader.dates import date_range, month_start
from renewals.downloader.naming import month_folder
from renewals.transform import metrics
from renewals.transform.excel import report_file_name, write_daily_report
from renewals.transform.rules import TransformResult, build_facts
from renewals.transform.sources import load_sources
from renewals.validation.checks import ReportBundle, ValidationReport, validate
from renewals.validation.naive import naive_bundle

log = logging.getLogger(__name__)


@dataclass
class MonthOutcome:
    month: dt.date
    as_of: dt.date
    mode: str
    validation: ValidationReport
    result: TransformResult | None
    bundle: ReportBundle
    published: list[Path]


def month_days(month: dt.date, as_of: dt.date) -> list[dt.date]:
    """Day 1 of the month up to ``as_of`` (or month end). Always the full prefix, never just
    the new days: late completions and cancellations change earlier days."""
    first = month_start(month)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return date_range(first, min(last, as_of))


def load_shops(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Shop master {path} missing. Run `python -m renewals generate`.")
    return pd.read_csv(path, dtype=str)


def processed_dir(output_dir: Path, month: dt.date) -> Path:
    return output_dir / "processed" / f"{month:%Y-%m}"


def reports_dir(output_dir: Path, month: dt.date) -> Path:
    return output_dir / "reports" / month_folder(month)


def compute_month(
    downloads_root: Path, month: dt.date, as_of: dt.date, mode: str = "fixed"
) -> tuple[ReportBundle, TransformResult | None]:
    days = month_days(month, as_of)
    src = load_sources(downloads_root, days)
    if mode == "naive":
        return naive_bundle(src, month), None
    result = build_facts(src, month, as_of)
    bundle = ReportBundle(
        "fixed",
        metrics.shop_day(result.facts),
        result.facts[["process_id", "contract_id", "stream"]],
        result.audit,
    )
    return bundle, result


def _swap_in(staging: Path, final: Path) -> None:
    if final.exists():
        shutil.rmtree(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(final)


def publish_month(result: TransformResult, shops: pd.DataFrame, output_dir: Path) -> list[Path]:
    """Write Parquet + one Excel report per day, then swap them into ``output_dir``.

    Parallelisable: each day's workbook is independent and could be written by a process
    pool. Sequential is ~1 s per month here, so the extra moving parts are not worth it.
    """
    staging = output_dir / ".staging"
    if staging.exists():
        shutil.rmtree(staging)
    proc = staging / "processed"
    rep = staging / "reports"
    proc.mkdir(parents=True)
    rep.mkdir(parents=True)

    facts = result.facts
    facts.to_parquet(proc / "facts.parquet", index=False)
    metrics.shop_day(facts).to_parquet(proc / "shop_day.parquet", index=False)
    metrics.agent_day(facts).to_parquet(proc / "agent_day.parquet", index=False)
    result.exclusions.to_parquet(proc / "exclusions.parquet", index=False)
    (proc / "audit.json").write_text(
        json.dumps(result.audit, indent=2, sort_keys=True), encoding="utf-8"
    )
    for day in month_days(result.month, result.as_of):
        write_daily_report(
            facts, shops, day, result.as_of, result.exclusions, rep / report_file_name(day)
        )

    _swap_in(proc, processed_dir(output_dir, result.month))
    _swap_in(rep, reports_dir(output_dir, result.month))
    shutil.rmtree(staging, ignore_errors=True)
    published = sorted(reports_dir(output_dir, result.month).glob("*.xlsx"))
    log.info("Published %d daily reports for %s", len(published), f"{result.month:%Y-%m}")
    return published


def write_naive(bundle: ReportBundle, output_dir: Path, month: dt.date) -> Path:
    target = output_dir / "naive" / f"{month:%Y-%m}"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "shop_day.parquet"
    bundle.shop_day.to_parquet(path, index=False)
    return path


def run_month(
    downloads_root: Path,
    output_dir: Path,
    shops: pd.DataFrame,
    month: dt.date,
    as_of: dt.date,
    mode: str = "fixed",
    raise_on_failure: bool = True,
) -> MonthOutcome:
    """Recompute ``month`` from day 1 to ``as_of``, validate, and publish if valid."""
    bundle, result = compute_month(downloads_root, month, as_of, mode)
    report = validate(bundle)
    published: list[Path] = []
    if mode == "naive":
        published = [write_naive(bundle, output_dir, month)]
    if raise_on_failure:
        report.raise_for_failures()
    if mode == "fixed" and report.passed and result is not None:
        published = publish_month(result, shops, output_dir)
    return MonthOutcome(month_start(month), as_of, mode, report, result, bundle, published)
