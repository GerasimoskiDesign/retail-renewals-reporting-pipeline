# Data-quality traps in the synthetic world

The generator (`src/renewals/world/generator.py`, seed 42) injects four traps on purpose.
Each one is a real pattern from multi-system sales reporting: every source report is
correct on its own, and the problem only shows up when the reports are combined.
The table shows how often each trap fires in September 2026 (the demo month) and which
rule handles it.

| # | Trap | Where it shows up | Count (Sep 2026) | Handled by |
|---|------|-------------------|------------------|------------|
| 1 | Same contract in two reports | A renewal with a tariff change also creates a *companion* migration process for the same contract, so the contract is in both **Contract Renewals** and **Tariff Migrations**. Every digital sale is also in both a completion report and **Digital Orders**. | 1,017 companion migrations | `companion_migration` exclusion; channel taken from Digital Orders by process id, never counted again |
| 2 | Click & collect counted twice | A click & collect order is in **Digital Orders** (channel "Click & Collect") *and* in **Store Completions** as a "Pickup" row, because the device is handed over in the shop. The completion reports call it `STORE`. | every click & collect sale | Channel rule: one process = one sale; Pickup rows only confirm the hand-over agent |
| 3 | Started last month, completed this month | Completion lag is 0-6 days, so processes started in late August complete in early September and appear in September's completion reports. | 267 | `prior_month_start` exclusion (sales are attributed to the start day) |
| 4 | Cancelled, then restarted | About 4% of completed sales are cancelled 1-5 days later; 60% of those are restarted as a new process for the same contract. Both completions appear in the completion reports. For 15% of restarts the CRM never logged the cancellation, only the restart. | 448 cancelled, 58 superseded | `cancelled` (CRM CANCELLED event) and `superseded_by_restart` (CRM RESTARTED event points at the old process) |

## Campaign week (14-20 September 2026)

An "order online, collect in store" campaign runs that week. Starts per day rise about 3x
compared with normal weekdays: in-store x1.6, self-service and express digital x4,
click & collect x9. Pickup points (small shops with little walk-in trade) receive most of
the extra click & collect volume.

This is where the naive logic falls over. Trap 2 adds every click & collect sale to the
digital count twice, so at a shop where pickups outnumber in-store sales, digital ends up
larger than the total. In the demo month the naive logic gives 111 shop/days with a
digital share above 100%. Most of them fall in the campaign week and the days just after
it, when the campaign's pickups complete. The fixed logic has none.

## Other realistic noise (not traps, but handled)

* **Web Orders day to next-day range.** The portal only exports this report for a
  from/to range, and the business always pulled "day to next day", so consecutive files
  overlap by a full day. Rows are deduplicated by order id (`web_order_overlap_rows` in
  the audit).
* **Abandoned orders.** About 3% of processes never complete. They appear in Web Orders
  but in no completion report, and they are never counted.
* **Shops are closed on Sunday.** There are no in-store sales or pickups on Sundays, so
  the fixed digital share is 100% on Sundays. That is correct, not a bug.
* **BI exports have three title rows above the header; the CRM export does not.**
  Each source also spells channels and streams differently.

Regenerate the world with `python -m renewals generate`. Counts change if you pass a
different `--seed`.
