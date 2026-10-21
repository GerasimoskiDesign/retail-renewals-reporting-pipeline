import datetime as dt
from pathlib import Path

import pytest

from renewals.downloader.dates import date_range, report_days_for_run
from renewals.downloader.naming import (
    day_folder,
    downloaded_days,
    month_folder,
    parse_day_folder,
    report_file_name,
    report_path,
)
from renewals.portal.reports import (
    CONTRACT_RENEWALS,
    CRM_EVENTS,
    REPORTS,
    TARIFF_MIGRATIONS,
    WEB_ORDERS,
)

D = dt.date


# -- Monday logic -------------------------------------------------------------------------


def test_monday_run_fetches_friday_saturday_sunday_as_separate_days():
    assert report_days_for_run(D(2026, 9, 14)) == [D(2026, 9, 11), D(2026, 9, 12), D(2026, 9, 13)]


@pytest.mark.parametrize(
    "run_date, expected",
    [
        (D(2026, 9, 15), [D(2026, 9, 14)]),  # Tuesday -> Monday
        (D(2026, 9, 18), [D(2026, 9, 17)]),  # Friday -> Thursday
        (D(2026, 9, 19), [D(2026, 9, 18)]),  # Saturday (manual run) -> Friday
        (D(2026, 9, 20), [D(2026, 9, 19)]),  # Sunday (manual run) -> Saturday
    ],
)
def test_other_weekdays_fetch_yesterday_only(run_date, expected):
    assert report_days_for_run(run_date) == expected


def test_monday_logic_crosses_month_boundary():
    # Monday 1 June 2026 must fetch 29, 30, 31 May.
    assert report_days_for_run(D(2026, 6, 1)) == [D(2026, 5, 29), D(2026, 5, 30), D(2026, 5, 31)]


def test_date_range_is_inclusive_and_rejects_reversed_ranges():
    assert date_range(D(2026, 9, 29), D(2026, 10, 1)) == [
        D(2026, 9, 29),
        D(2026, 9, 30),
        D(2026, 10, 1),
    ]
    assert date_range(D(2026, 9, 1), D(2026, 9, 1)) == [D(2026, 9, 1)]
    with pytest.raises(ValueError):
        date_range(D(2026, 9, 2), D(2026, 9, 1))


# -- file naming --------------------------------------------------------------------------


def test_bi_file_gets_day_number_appended_without_zero_padding(tmp_path: Path):
    path = report_path(tmp_path, TARIFF_MIGRATIONS, D(2026, 9, 2))
    assert path.relative_to(tmp_path).as_posix() == (
        "September 2026/02.09.2026/Tariff Migrations 2.xlsx"
    )
    assert report_file_name(CONTRACT_RENEWALS, D(2026, 9, 30)) == "Contract Renewals 30.xlsx"


def test_web_orders_file_is_named_after_its_from_day():
    assert report_file_name(WEB_ORDERS, D(2026, 9, 14)) == "Web Orders 14.xlsx"


def test_crm_file_keeps_its_own_naming_pattern():
    assert report_file_name(CRM_EVENTS, D(2026, 9, 2)) == "CRM_Process_Events_20260902.xlsx"


def test_month_folder_is_english_and_locale_independent():
    assert [month_folder(D(2026, m, 1)) for m in (1, 5, 12)] == [
        "January 2026",
        "May 2026",
        "December 2026",
    ]
    assert day_folder(D(2026, 1, 5)) == "05.01.2026"


def test_parse_day_folder_round_trips_and_rejects_noise():
    assert parse_day_folder("14.09.2026") == D(2026, 9, 14)
    assert parse_day_folder("2026-09-14") is None
    assert parse_day_folder("notes") is None


def test_downloaded_days_counts_only_complete_days(tmp_path: Path):
    complete, partial = D(2026, 9, 1), D(2026, 9, 2)
    for spec in REPORTS:
        p = report_path(tmp_path, spec, complete)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    p = report_path(tmp_path, REPORTS[0], partial)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    assert downloaded_days(tmp_path) == [complete]
