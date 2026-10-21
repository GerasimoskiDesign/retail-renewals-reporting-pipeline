"""Business rules: from raw completion rows to one counted gross add per sale.

The rules are applied in a fixed order and every excluded row keeps the *first* reason that
applied, so the reconciliation in the validation layer can account for every source row.

Counting unit
    A *gross add* is one completed process that survives all exclusions. It is attributed to
    the day the process **started** (the day the customer committed, which is what shop
    targets are set on), but only once it has **completed**. Because completions arrive up
    to six days after the start, a new day of data changes earlier days: the month is
    therefore recomputed from day 1 on every run.

Exclusion rules (in order)
    R1 ``duplicate_row``          the same process_id delivered more than once (re-downloads,
                                  overlapping files). Keep one row.
    R2 ``prior_month_start``      started before the reporting month. It belongs to a month
                                  that is already closed (trap 3).
    R3 ``cancelled``              CRM logged a cancellation on or before as-of (trap 4).
    R4 ``superseded_by_restart``  CRM logged a restart that replaces this process, even if
                                  the cancellation itself was never logged (trap 4).
    R5 ``companion_migration``    a migration on a contract that already has a counted renewal
                                  in the month. A renewal with a tariff change emits both
                                  processes, but it is one sale (trap 1).
    R6 ``duplicate_contract``     safety net: any remaining second process for the same
                                  contract and stream in the month; the latest completion wins.

Channel rule
    The BI completion reports only know STORE/ONLINE, and a click & collect order shows as
    STORE because the hand-over happens in the shop. The channel of record therefore comes
    from the Digital Orders report (keyed by process id); anything not in it is in-store.
    The Store Completions "Pickup" rows are used only to confirm the hand-over agent, never
    counted again (trap 2).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd

from renewals.transform.sources import SourceData
from renewals.world.model import CLICK_COLLECT, DIGITAL_CHANNELS, IN_STORE, MIGRATION, RENEWAL

log = logging.getLogger(__name__)

DIGITAL_AGENT = "DIGITAL"  # pseudo-agent for self-service / express sales (no human agent)

EXCLUSION_REASONS = (
    "prior_month_start",
    "cancelled",
    "superseded_by_restart",
    "companion_migration",
    "duplicate_contract",
)

FACT_COLUMNS = [
    "process_id",
    "contract_id",
    "stream",
    "channel",
    "is_digital",
    "shop_id",
    "agent_id",
    "day",
    "started_at",
    "completed_at",
]


@dataclass
class TransformResult:
    """Counted gross adds, the excluded rows with reasons, and an audit trail."""

    month: dt.date
    as_of: dt.date
    facts: pd.DataFrame
    exclusions: pd.DataFrame
    audit: dict = field(default_factory=dict)


def _month_bounds(month: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(month.replace(day=1))
    return start, start + pd.offsets.MonthBegin(1)


def assign_channels(completions: pd.DataFrame, digital_orders: pd.DataFrame) -> pd.Series:
    """Channel of record per completion row (see module docstring, "Channel rule")."""
    digital = (
        digital_orders.drop_duplicates("process_id").set_index("process_id")["channel"]
        if not digital_orders.empty
        else pd.Series(dtype="string")
    )
    channel = completions["process_id"].map(digital)
    return channel.fillna(IN_STORE).astype("string")


def build_facts(src: SourceData, month: dt.date, as_of: dt.date) -> TransformResult:
    """Apply the exclusion and channel rules for one reporting month."""
    comp = src.completions.copy()
    source_rows = len(comp)
    comp = comp.sort_values(["process_id", "report_day"], kind="stable")
    dup_mask = comp.duplicated("process_id", keep="first")
    duplicate_rows = int(dup_mask.sum())
    comp = comp[~dup_mask].reset_index(drop=True)

    comp["channel"] = assign_channels(comp, src.digital_orders)
    comp["reason"] = pd.Series(pd.NA, index=comp.index, dtype="string")

    def exclude(mask: pd.Series, reason: str) -> None:
        comp.loc[mask & comp["reason"].isna(), "reason"] = reason

    start, end = _month_bounds(month)
    exclude(comp["started_at"] < start, "prior_month_start")

    events = src.crm_events
    events = events[events["event_ts"].dt.date <= as_of] if not events.empty else events
    cancelled = set(events.loc[events["event_type"] == "CANCELLED", "process_id"])
    superseded = set(events.loc[events["event_type"] == "RESTARTED", "restart_of"].dropna())
    exclude(comp["process_id"].isin(cancelled), "cancelled")
    exclude(comp["process_id"].isin(superseded), "superseded_by_restart")

    renewal_contracts = set(
        comp.loc[comp["reason"].isna() & (comp["stream"] == RENEWAL), "contract_id"]
    )
    exclude(
        (comp["stream"] == MIGRATION) & comp["contract_id"].isin(renewal_contracts),
        "companion_migration",
    )

    alive = comp[comp["reason"].isna()].sort_values(
        ["contract_id", "stream", "completed_at", "process_id"], kind="stable"
    )
    dup_contract = alive.duplicated(["contract_id", "stream"], keep="last")
    exclude(comp.index.isin(alive.index[dup_contract]), "duplicate_contract")

    counted = comp[comp["reason"].isna()].copy()
    counted["is_digital"] = counted["channel"].isin(DIGITAL_CHANNELS)
    counted["agent_id"] = counted["agent_id"].astype("string")
    no_agent = counted["channel"].isin(DIGITAL_CHANNELS - {CLICK_COLLECT})
    counted.loc[no_agent, "agent_id"] = DIGITAL_AGENT
    counted["agent_id"] = counted["agent_id"].fillna(DIGITAL_AGENT)
    counted["day"] = counted["started_at"].dt.normalize()
    facts = (
        counted[FACT_COLUMNS]
        .sort_values(["day", "shop_id", "process_id"], kind="stable")
        .reset_index(drop=True)
    )

    exclusions = (
        comp.loc[
            comp["reason"].notna(),
            [
                "process_id",
                "contract_id",
                "stream",
                "shop_id",
                "channel",
                "started_at",
                "completed_at",
                "reason",
            ],
        ]
        .sort_values(["reason", "process_id"], kind="stable")
        .reset_index(drop=True)
    )
    audit = _audit(src, comp, facts, exclusions, source_rows, duplicate_rows, as_of, start, end)
    log.info(
        "Month %s as of %s: %d source rows -> %d counted, %d excluded, %d duplicate rows",
        f"{month:%Y-%m}",
        as_of,
        source_rows,
        len(facts),
        len(exclusions),
        duplicate_rows,
    )
    return TransformResult(month.replace(day=1), as_of, facts, exclusions, audit)


def _audit(
    src: SourceData,
    comp: pd.DataFrame,
    facts: pd.DataFrame,
    exclusions: pd.DataFrame,
    source_rows: int,
    duplicate_rows: int,
    as_of: dt.date,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    """Numbers the validation layer reconciles, plus cross-source consistency warnings."""
    by_reason = exclusions["reason"].value_counts().to_dict()
    online_not_digital = int(
        ((comp["sales_channel_code"] == "ONLINE") & (comp["channel"] == IN_STORE)).sum()
    )
    web = src.web_orders
    web_rows = len(web)
    web_unique = web.drop_duplicates("order_id") if not web.empty else web
    digital_facts = facts[facts["is_digital"]]
    placed = set(web_unique["process_id"]) if not web_unique.empty else set()
    store = src.store_completions
    pickups = store[store["completion_type"] == "pickup"] if not store.empty else store
    return {
        "source_rows": int(source_rows),
        "duplicate_rows": duplicate_rows,
        "unique_processes": int(len(comp)),
        "counted": int(len(facts)),
        "excluded": {r: int(by_reason.get(r, 0)) for r in EXCLUSION_REASONS},
        "warnings": {
            "online_without_digital_order": online_not_digital,
            "digital_without_web_order": int((~digital_facts["process_id"].isin(placed)).sum()),
            "web_order_overlap_rows": int(web_rows - len(web_unique)),
            "pickups_without_completion": int(
                (~pickups["process_id"].isin(set(comp["process_id"]))).sum()
            )
            if not pickups.empty
            else 0,
        },
        "window": {
            "start": str(start.date()),
            "end_exclusive": str(end.date()),
            "as_of": str(as_of),
        },
    }
