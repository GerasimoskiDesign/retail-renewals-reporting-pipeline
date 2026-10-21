import datetime as dt

import pandas as pd
import pytest

from renewals.pipeline import compute_month, run_month
from renewals.transform import metrics
from renewals.transform.rules import build_facts
from renewals.validation.checks import (
    ReportBundle,
    ValidationError,
    check_digital_share,
    check_no_double_count,
    check_reconciliation,
    validate,
)
from renewals.validation.naive import naive_bundle

MONTH, AS_OF = dt.date(2026, 9, 1), dt.date(2026, 9, 30)
CAMPAIGN = (pd.Timestamp(2026, 9, 14), pd.Timestamp(2026, 9, 20))


def shop_day(rows):
    return pd.DataFrame(rows, columns=["day", "shop_id", "combined", "digital"])


# -- invariants on hand-made data ---------------------------------------------------------


def test_digital_share_check_fails_above_100_percent():
    ok = shop_day([(pd.Timestamp(2026, 9, 1), "S01", 5, 5)])
    bad = shop_day([(pd.Timestamp(2026, 9, 1), "S01", 4, 5)])
    assert check_digital_share(ok).passed
    res = check_digital_share(bad)
    assert not res.passed and res.violations == 1 and "125%" in res.detail


def test_double_count_check_flags_contract_in_two_streams():
    ga = pd.DataFrame(
        {
            "process_id": ["P1", "P2", "P3"],
            "contract_id": ["C1", "C1", "C2"],
            "stream": ["renewal", "migration", "renewal"],
        }
    )
    res = check_no_double_count(ga)
    assert not res.passed and res.violations == 1
    assert check_no_double_count(ga.iloc[1:]).passed


def test_reconciliation_detects_unaccounted_rows():
    sd = shop_day([(pd.Timestamp(2026, 9, 1), "S01", 8, 2)])
    ga = pd.DataFrame(
        {"process_id": list("abcdefgh"), "contract_id": list("abcdefgh"), "stream": ["renewal"] * 8}
    )
    audit = {"source_rows": 10, "duplicate_rows": 1, "counted": 8, "excluded": {"x": 1}}
    assert check_reconciliation(ReportBundle("t", sd, ga, audit)).passed
    audit_bad = {**audit, "source_rows": 11}
    assert not check_reconciliation(ReportBundle("t", sd, ga, audit_bad)).passed
    sd_bad = shop_day([(pd.Timestamp(2026, 9, 1), "S01", 7, 2)])
    assert not check_reconciliation(ReportBundle("t", sd_bad, ga, audit)).passed


def test_failed_report_raises_validation_error():
    bad = ReportBundle(
        "t",
        shop_day([(pd.Timestamp(2026, 9, 1), "S01", 1, 2)]),
        pd.DataFrame({"process_id": ["a"], "contract_id": ["a"], "stream": ["renewal"]}),
        {"source_rows": 1, "duplicate_rows": 0, "counted": 1, "excluded": {}},
    )
    report = validate(bad)
    with pytest.raises(ValidationError, match="digital_share_le_100"):
        report.raise_for_failures()


# -- naive vs fixed on the full synthetic month (the regression test) -------------------


@pytest.fixture(scope="module")
def naive(month_sources):
    return naive_bundle(month_sources, MONTH)


@pytest.fixture(scope="module")
def fixed(month_sources):
    res = build_facts(month_sources, MONTH, AS_OF)
    return ReportBundle(
        "fixed",
        metrics.shop_day(res.facts),
        res.facts[["process_id", "contract_id", "stream"]],
        res.audit,
    )


def test_fixed_logic_passes_every_invariant(fixed):
    report = validate(fixed)
    assert report.passed, report.summary()


def test_naive_logic_breaks_digital_share_in_campaign_week(naive):
    sd = naive.shop_day
    over = sd[sd["digital"] > sd["combined"]]
    in_campaign = over[over["day"].between(*CAMPAIGN)]
    assert len(in_campaign) >= 10
    assert in_campaign["digital_share"].max() > 1.5
    # the campaign week is where it blows up: far more breaches per day than normal weeks
    normal = over[over["day"] < CAMPAIGN[0]]
    assert len(in_campaign) / 7 > 3 * (len(normal) / 13)


def test_naive_network_share_exceeds_100_percent_on_some_day(naive):
    by_day = naive.shop_day.groupby("day")[["digital", "combined"]].sum()
    assert (by_day["digital"] > by_day["combined"]).any()


def test_naive_logic_double_counts_contracts_fixed_does_not(naive, fixed):
    assert not check_no_double_count(naive.gross_adds).passed
    assert check_no_double_count(fixed.gross_adds).passed


def test_naive_overstates_volume_by_the_documented_exclusions(naive, fixed):
    excluded = sum(fixed.audit["excluded"].values()) + fixed.audit["duplicate_rows"]
    assert naive.audit["counted"] - fixed.audit["counted"] == excluded


def test_naive_mode_fails_loudly_and_publishes_no_report(month_downloads, world, tmp_path):
    with pytest.raises(ValidationError):
        run_month(month_downloads, tmp_path, world.shops, MONTH, AS_OF, mode="naive")
    assert not (tmp_path / "reports").exists()


def test_compute_month_naive_and_fixed_read_the_same_files(month_downloads):
    naive_b, none = compute_month(month_downloads, MONTH, dt.date(2026, 9, 5), mode="naive")
    fixed_b, res = compute_month(month_downloads, MONTH, dt.date(2026, 9, 5))
    assert none is None and res is not None
    assert naive_b.audit["source_rows"] == fixed_b.audit["source_rows"]
