"""Invariants every published report must satisfy. Any violation stops publishing.

These checks exist because people act on these numbers: a silently wrong report is worse
than a late one. The checks are deliberately independent of how the numbers were produced,
so the same checks run against the fixed pipeline and the naive row-counting baseline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger(__name__)


class ValidationError(RuntimeError):
    """Raised when a report violates an invariant. Carries the full report."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(report.summary())


@dataclass
class ReportBundle:
    """What the checks look at, whichever logic produced it."""

    mode: str
    shop_day: pd.DataFrame  # day, shop_id, renewals, migrations, combined, digital
    gross_adds: pd.DataFrame  # one row per counted gross add: process_id, contract_id, stream
    audit: dict


@dataclass
class CheckResult:
    name: str
    passed: bool
    violations: int
    detail: str
    sample: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class ValidationReport:
    mode: str
    results: list[CheckResult]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        lines = [f"Validation ({self.mode}): {'PASSED' if self.passed else 'FAILED'}"]
        for r in self.results:
            status = "ok  " if r.passed else "FAIL"
            lines.append(f"  [{status}] {r.name}: {r.detail}")
        return "\n".join(lines)

    def raise_for_failures(self) -> None:
        if not self.passed:
            raise ValidationError(self)


def check_digital_share(shop_day: pd.DataFrame) -> CheckResult:
    """Digital sales are a subset of all sales, so digital <= total for every shop/day."""
    bad = shop_day[shop_day["digital"] > shop_day["combined"]]
    worst = (bad["digital"] / bad["combined"]).max() if not bad.empty else None
    detail = (
        "digital <= total on every shop/day"
        if bad.empty
        else f"{len(bad)} shop/days with digital share > 100% (worst {worst:.0%})"
    )
    return CheckResult("digital_share_le_100", bad.empty, len(bad), detail, bad.head(20))


def check_no_double_count(gross_adds: pd.DataFrame) -> CheckResult:
    """A contract is one customer decision: it may appear at most once in gross adds."""
    counts = gross_adds.groupby("contract_id").size()
    dup = counts[counts > 1]
    detail = (
        "every contract counted once"
        if dup.empty
        else f"{len(dup)} contracts counted more than once ({int(dup.sum() - len(dup))} extra)"
    )
    sample = gross_adds[gross_adds["contract_id"].isin(dup.index[:20])]
    return CheckResult("no_contract_counted_twice", dup.empty, len(dup), detail, sample)


def check_reconciliation(bundle: ReportBundle) -> CheckResult:
    """Every source row is either counted, a duplicate row, or excluded for a stated reason,
    and the published totals add up to the counted rows."""
    a = bundle.audit
    excluded = sum(a.get("excluded", {}).values())
    accounted = a["counted"] + a["duplicate_rows"] + excluded
    published = int(bundle.shop_day["combined"].sum()) if not bundle.shop_day.empty else 0
    problems = []
    if accounted != a["source_rows"]:
        problems.append(f"source rows {a['source_rows']} != accounted {accounted}")
    if published != a["counted"]:
        problems.append(f"published total {published} != counted {a['counted']}")
    if len(bundle.gross_adds) != a["counted"]:
        problems.append(f"gross-add rows {len(bundle.gross_adds)} != counted {a['counted']}")
    detail = (
        f"{a['source_rows']} source rows = {a['counted']} counted + {a['duplicate_rows']} "
        f"duplicate rows + {excluded} excluded"
        if not problems
        else "; ".join(problems)
    )
    return CheckResult("totals_reconcile", not problems, len(problems), detail)


def validate(bundle: ReportBundle) -> ValidationReport:
    report = ValidationReport(
        bundle.mode,
        [
            check_digital_share(bundle.shop_day),
            check_no_double_count(bundle.gross_adds),
            check_reconciliation(bundle),
        ],
    )
    (log.info if report.passed else log.error)("%s", report.summary())
    return report
