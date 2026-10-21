"""Each injected trap, isolated on a few hand-built rows."""

import datetime as dt

import pandas as pd
from conftest import completion, digital, event, sources, store, ts, web

from renewals.transform import metrics
from renewals.transform.rules import DIGITAL_AGENT, build_facts

MONTH, AS_OF = dt.date(2026, 9, 1), dt.date(2026, 9, 30)


def run(src):
    return build_facts(src, MONTH, AS_OF)


def reasons(result) -> dict[str, str]:
    return dict(zip(result.exclusions["process_id"], result.exclusions["reason"], strict=True))


def totals(result) -> dict:
    row = metrics.aggregate(result.facts, []).iloc[0]
    return {k: row[k] for k in ("renewals", "migrations", "combined", "digital")}


def test_trap1_companion_migration_is_one_sale_not_two():
    src = sources(
        completions=[
            completion("P1", "C1", "renewal"),
            completion("P2", "C1", "migration"),  # companion of the renewal
            completion("P3", "C2", "migration"),  # a genuine standalone migration
        ]
    )
    res = run(src)
    assert reasons(res) == {"P2": "companion_migration"}
    assert totals(res) == {"renewals": 1, "migrations": 1, "combined": 2, "digital": 0}
    assert res.facts["contract_id"].is_unique


def test_trap2_click_and_collect_counted_once_as_digital():
    # Completion report says STORE (hand-over in the shop), Digital Orders says Click &
    # Collect, Store Completions lists a Pickup. One sale, digital, credited to the agent.
    src = sources(
        completions=[
            completion("P1", "C1", code="STORE", agent="A007"),
            completion("P2", "C2", code="STORE", agent="A008"),
        ],
        digital_orders=[digital("P1", "C1", "click_collect")],
        store_completions=[
            store("P1", "C1", "pickup", agent="A007"),
            store("P2", "C2", "sale", agent="A008"),
        ],
    )
    res = run(src)
    facts = res.facts.set_index("process_id")
    assert facts.loc["P1", "channel"] == "click_collect"
    assert facts.loc["P1", "agent_id"] == "A007"
    assert facts.loc["P2", "channel"] == "in_store"
    assert totals(res) == {"renewals": 2, "migrations": 0, "combined": 2, "digital": 1}


def test_self_service_sales_have_no_human_agent():
    src = sources(
        completions=[completion("P1", "C1", code="ONLINE", agent=None)],
        digital_orders=[digital("P1", "C1", "self_service")],
    )
    res = run(src)
    assert res.facts.loc[0, "agent_id"] == DIGITAL_AGENT
    assert res.facts.loc[0, "is_digital"]


def test_trap3_prior_month_start_is_excluded_from_this_month():
    src = sources(
        completions=[
            completion("P1", "C1", started=ts(29, month=8), completed=ts(2)),
            completion("P2", "C2", started=ts(1, hour=0), completed=ts(2)),
        ]
    )
    res = run(src)
    assert reasons(res) == {"P1": "prior_month_start"}
    assert res.facts["process_id"].tolist() == ["P2"]


def test_trap4_cancelled_then_restarted_counts_once():
    src = sources(
        completions=[
            completion("P1", "C1", started=ts(3), completed=ts(3, 15)),
            completion("P9", "C1", started=ts(7), completed=ts(8)),  # the restart
        ],
        crm_events=[
            event("P1", "C1", "CANCELLED", ts(6)),
            event("P9", "C1", "RESTARTED", ts(7), restart_of="P1"),
        ],
    )
    res = run(src)
    assert reasons(res) == {"P1": "cancelled"}
    assert res.facts["process_id"].tolist() == ["P9"]


def test_trap4_restart_without_logged_cancellation_supersedes_original():
    src = sources(
        completions=[completion("P1", "C1", started=ts(3)), completion("P9", "C1", started=ts(7))],
        crm_events=[event("P9", "C1", "RESTARTED", ts(7), restart_of="P1")],
    )
    assert reasons(run(src)) == {"P1": "superseded_by_restart"}


def test_safety_net_keeps_latest_when_no_crm_event_explains_a_repeat():
    src = sources(
        completions=[
            completion("P1", "C1", started=ts(3), completed=ts(3, 14)),
            completion("P2", "C1", started=ts(5), completed=ts(5, 14)),
        ]
    )
    res = run(src)
    assert reasons(res) == {"P1": "duplicate_contract"}


def test_cancelled_contract_without_restart_is_not_a_sale():
    src = sources(
        completions=[completion("P1", "C1")], crm_events=[event("P1", "C1", "CANCELLED", ts(9))]
    )
    res = run(src)
    assert res.facts.empty and reasons(res) == {"P1": "cancelled"}


def test_cancellation_after_as_of_is_not_applied_yet():
    src = sources(
        completions=[completion("P1", "C1")], crm_events=[event("P1", "C1", "CANCELLED", ts(20))]
    )
    early = build_facts(src, MONTH, dt.date(2026, 9, 10))
    assert len(early.facts) == 1


def test_same_process_in_two_files_counted_once():
    row = completion("P1", "C1")
    src = sources(completions=[row, {**row, "source_file": "redownload.xlsx"}])
    res = run(src)
    assert len(res.facts) == 1 and res.audit["duplicate_rows"] == 1


def test_sale_is_attributed_to_its_start_day():
    src = sources(completions=[completion("P1", "C1", started=ts(3), completed=ts(8))])
    assert run(src).facts.loc[0, "day"] == pd.Timestamp(2026, 9, 3)


def test_overlapping_web_order_files_are_deduplicated_in_audit():
    w = web("P1", "C1", "self_service", ts(4), report_day=3)
    src = sources(
        completions=[completion("P1", "C1", code="ONLINE", agent=None, started=ts(4))],
        digital_orders=[digital("P1", "C1", "self_service")],
        web_orders=[w, {**w, "report_day": dt.date(2026, 9, 4)}],
    )
    audit = run(src).audit
    assert audit["warnings"]["web_order_overlap_rows"] == 1
    assert audit["warnings"]["digital_without_web_order"] == 0


def test_online_completion_missing_from_digital_orders_is_flagged():
    src = sources(completions=[completion("P1", "C1", code="ONLINE", agent=None)])
    assert run(src).audit["warnings"]["online_without_digital_order"] == 1
