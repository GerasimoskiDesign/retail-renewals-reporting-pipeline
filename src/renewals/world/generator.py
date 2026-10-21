"""Seeded generator for the synthetic retail world.

The world is deliberately *not* clean. Four data-quality traps (documented in
``data/TRAPS.md``) are injected so the transform layer has real problems to solve and the
naive counting logic visibly breaks during the campaign week:

1. Companion migrations: a renewal with a tariff change also emits a migration process for
   the same contract, so the contract shows up in two source reports.
2. Click & collect orders appear as a digital order *and* as an in-store pickup completion.
3. Processes started in the previous month complete in this month.
4. Cancelled-then-restarted contracts complete twice and look like two gross adds.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from renewals.world.model import (
    CLICK_COLLECT,
    EXPRESS,
    IN_STORE,
    MIGRATION,
    PHONE_PREFIX,
    RENEWAL,
    SELF_SERVICE,
    WorldConfig,
)

log = logging.getLogger(__name__)

REGIONS = [
    ("R1", "Region North"),
    ("R2", "Region South"),
    ("R3", "Region East"),
    ("R4", "Region West"),
]
SHOP_TOWNS = [
    "Riverside",
    "Old Town",
    "Harbour",
    "Central Station",
    "Market Square",
    "Hillview",
    "Lakeside",
    "Bridge Street",
    "Millbrook",
    "Northgate",
    "Southpark",
    "Eastfield",
    "Westbury",
    "Kingsway",
    "Oakwood",
    "Elm Court",
    "Stonebridge",
    "Fairview",
    "Greenfield",
    "Ashford",
    "Brookside",
    "Cedar Mall",
    "Dockyard",
    "Parkside",
    "Queens Road",
    "Rosewood",
    "Silverlake",
    "Thornbury",
    "Valley Plaza",
    "Windmill",
]
# shop_type -> (count, agents per shop)
SHOP_TYPES = {"flagship": (4, 14), "standard": (18, 6), "pickup_point": (8, 3)}

# Expected daily process starts per shop on a normal weekday.
BASE_RATES = {
    "flagship": {IN_STORE: 14.0, SELF_SERVICE: 4.0, EXPRESS: 2.0, CLICK_COLLECT: 1.5},
    "standard": {IN_STORE: 6.0, SELF_SERVICE: 2.5, EXPRESS: 1.0, CLICK_COLLECT: 1.0},
    "pickup_point": {IN_STORE: 2.5, SELF_SERVICE: 0.8, EXPRESS: 0.4, CLICK_COLLECT: 0.5},
}
# The campaign is a digital "order online, collect in store" push: store traffic rises a
# little, digital a lot, click & collect most. Overall volume ends up roughly 3x.
CAMPAIGN_FACTOR = {IN_STORE: 1.6, SELF_SERVICE: 4.0, EXPRESS: 4.0, CLICK_COLLECT: 9.0}
# weekday() -> factor; shops are closed on Sunday, digital channels never close.
STORE_WEEKDAY = [1.0, 1.0, 1.0, 1.05, 1.2, 0.8, 0.0]
DIGITAL_WEEKDAY = [1.0, 1.0, 1.0, 1.0, 1.05, 0.95, 0.9]

# Completion lag in days (index = lag 0..6).
LAG_P = {
    IN_STORE: [0.72, 0.14, 0.07, 0.04, 0.02, 0.01, 0.0],
    SELF_SERVICE: [0.40, 0.25, 0.15, 0.10, 0.05, 0.03, 0.02],
    EXPRESS: [0.60, 0.25, 0.10, 0.05, 0.0, 0.0, 0.0],
    CLICK_COLLECT: [0.0, 0.30, 0.30, 0.20, 0.10, 0.06, 0.04],
}
RENEWAL_SHARE = 0.6
OPEN_RATE = 0.03  # never completed (abandoned orders)
CANCEL_RATE = 0.04
RESTART_RATE = 0.6  # share of cancellations that get restarted
CANCEL_UNLOGGED_RATE = 0.15  # restarts where the CRM never logged the cancellation
COMPANION_RATE = 0.15  # renewals that also emit a migration process

PROCESS_COLUMNS = [
    "process_id",
    "contract_id",
    "msisdn",
    "stream",
    "channel",
    "shop_id",
    "agent_id",
    "started_at",
    "completed_at",
    "status",
    "cancelled_at",
    "cancel_logged",
    "restart_of",
    "is_companion",
]


def _build_shops() -> pd.DataFrame:
    rows = []
    idx = 0
    for shop_type, (count, _) in SHOP_TYPES.items():
        for _ in range(count):
            region_id, region_name = REGIONS[idx % len(REGIONS)]
            rows.append(
                {
                    "shop_id": f"S{idx + 1:02d}",
                    "shop_name": SHOP_TOWNS[idx],
                    "region_id": region_id,
                    "region_name": region_name,
                    "shop_type": shop_type,
                }
            )
            idx += 1
    return pd.DataFrame(rows)


def _build_agents(shops: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n = 0
    for shop in shops.itertuples():
        for _ in range(SHOP_TYPES[shop.shop_type][1]):
            n += 1
            rows.append({"agent_id": f"A{n:03d}", "shop_id": shop.shop_id})
    return pd.DataFrame(rows)


def _ts(day: dt.date, minutes: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=int(minutes))


class _Builder:
    """Accumulates process rows; holds the RNG so call order fully determines output."""

    def __init__(self, cfg: WorldConfig, shops: pd.DataFrame, agents: pd.DataFrame) -> None:
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.shops = shops
        self.agents_by_shop = agents.groupby("shop_id")["agent_id"].apply(list).to_dict()
        self.rows: list[dict] = []
        self.next_pid = 1

    def pid(self) -> str:
        value = f"P{self.next_pid:07d}"
        self.next_pid += 1
        return value

    def agent(self, shop_id: str) -> str:
        pool = self.agents_by_shop[shop_id]
        return pool[int(self.rng.integers(len(pool)))]

    def start_time(self, day: dt.date, channel: str) -> dt.datetime:
        if channel == IN_STORE:
            return _ts(day, self.rng.integers(9 * 60, 19 * 60 + 30))
        return _ts(day, self.rng.integers(0, 24 * 60))

    def completion(self, channel: str, started: dt.datetime) -> dt.datetime:
        lag = int(self.rng.choice(7, p=LAG_P[channel]))
        day = started.date() + dt.timedelta(days=lag)
        if channel == CLICK_COLLECT and day.weekday() == 6:
            # Pickups need an open shop: Sunday pickups shift to Saturday or Monday.
            lag = lag - 1 if lag > 1 else lag + 1
            day = started.date() + dt.timedelta(days=lag)
        if lag == 0:
            done = started + dt.timedelta(minutes=int(self.rng.integers(10, 240)))
            if done.date() != started.date():
                done = dt.datetime.combine(started.date(), dt.time(23, 50))
            return done
        return _ts(day, self.rng.integers(8 * 60, 20 * 60))

    def add(self, **kw) -> dict:
        row = {
            "process_id": self.pid(),
            "contract_id": None,
            "msisdn": None,
            "completed_at": None,
            "status": "completed",
            "cancelled_at": None,
            "cancel_logged": False,
            "restart_of": None,
            "is_companion": False,
        }
        row.update(kw)
        self.rows.append(row)
        return row

    def run(self) -> pd.DataFrame:
        cfg = self.cfg
        day = cfg.start
        while day <= cfg.end:
            camp = cfg.in_campaign(day)
            for shop in self.shops.itertuples():
                for channel, base in BASE_RATES[shop.shop_type].items():
                    wk = STORE_WEEKDAY if channel == IN_STORE else DIGITAL_WEEKDAY
                    rate = base * wk[day.weekday()] * (CAMPAIGN_FACTOR[channel] if camp else 1.0)
                    for _ in range(int(self.rng.poisson(rate))):
                        self._new_sale(day, shop.shop_id, channel)
            day += dt.timedelta(days=1)
        df = pd.DataFrame(self.rows, columns=PROCESS_COLUMNS)
        self._assign_contracts(df)
        for col in ("started_at", "completed_at", "cancelled_at"):
            df[col] = pd.to_datetime(df[col])
        return df

    def _new_sale(self, day: dt.date, shop_id: str, channel: str) -> None:
        stream = RENEWAL if self.rng.random() < RENEWAL_SHARE else MIGRATION
        started = self.start_time(day, channel)
        agent = self.agent(shop_id) if channel in (IN_STORE, CLICK_COLLECT) else None
        contract_key = f"K{len(self.rows)}"  # placeholder, real ids assigned later
        if self.rng.random() < OPEN_RATE:
            self.add(
                contract_id=contract_key,
                stream=stream,
                channel=channel,
                shop_id=shop_id,
                agent_id=agent,
                started_at=started,
                status="open",
            )
            return
        completed = self.completion(channel, started)
        if channel == CLICK_COLLECT:
            agent = self.agent(shop_id)  # the agent who hands over the device gets credit
        row = self.add(
            contract_id=contract_key,
            stream=stream,
            channel=channel,
            shop_id=shop_id,
            agent_id=agent,
            started_at=started,
            completed_at=completed,
        )
        if stream == RENEWAL and self.rng.random() < COMPANION_RATE:
            self.add(
                contract_id=contract_key,
                stream=MIGRATION,
                channel=channel,
                shop_id=shop_id,
                agent_id=agent,
                started_at=started,
                completed_at=completed,
                is_companion=True,
            )
        if self.rng.random() < CANCEL_RATE:
            self._cancel_and_maybe_restart(row)

    def _cancel_and_maybe_restart(self, row: dict) -> None:
        cancelled = row["completed_at"] + dt.timedelta(
            days=int(self.rng.integers(1, 6)), hours=int(self.rng.integers(0, 8))
        )
        row["status"] = "cancelled"
        row["cancelled_at"] = cancelled
        row["cancel_logged"] = True
        if self.rng.random() >= RESTART_RATE:
            return
        if self.rng.random() < CANCEL_UNLOGGED_RATE:
            row["cancel_logged"] = False
        started = cancelled + dt.timedelta(hours=int(self.rng.integers(1, 30)))
        channel = row["channel"]
        if channel == IN_STORE and started.weekday() == 6:
            started += dt.timedelta(days=1)
        lag_days = int(self.rng.integers(0, 4))
        completed = started + dt.timedelta(days=lag_days, minutes=int(self.rng.integers(20, 300)))
        self.add(
            contract_id=row["contract_id"],
            stream=row["stream"],
            channel=channel,
            shop_id=row["shop_id"],
            agent_id=row["agent_id"],
            started_at=started,
            completed_at=completed,
            restart_of=row["process_id"],
        )

    def _assign_contracts(self, df: pd.DataFrame) -> None:
        keys = df["contract_id"].unique()
        numbers = self.rng.choice(90_000_000, size=len(keys), replace=False) + 10_000_000
        phones = self.rng.choice(90_000_000, size=len(keys), replace=False) + 10_000_000
        contract_map = {k: f"C{n}" for k, n in zip(keys, numbers, strict=True)}
        phone_map = {k: f"{PHONE_PREFIX}{p}" for k, p in zip(keys, phones, strict=True)}
        df["msisdn"] = df["contract_id"].map(phone_map)
        df["contract_id"] = df["contract_id"].map(contract_map)


def build_crm_events(processes: pd.DataFrame) -> pd.DataFrame:
    """Derive the CRM status-event log (cancellations and restarts) from processes."""
    cancelled = processes[(processes["status"] == "cancelled") & processes["cancel_logged"]]
    cancel_ev = pd.DataFrame(
        {
            "process_id": cancelled["process_id"],
            "contract_id": cancelled["contract_id"],
            "event_type": "CANCELLED",
            "event_ts": cancelled["cancelled_at"],
            "restart_of": None,
        }
    )
    restarts = processes[processes["restart_of"].notna()]
    restart_ev = pd.DataFrame(
        {
            "process_id": restarts["process_id"],
            "contract_id": restarts["contract_id"],
            "event_type": "RESTARTED",
            "event_ts": restarts["started_at"],
            "restart_of": restarts["restart_of"],
        }
    )
    events = pd.concat([cancel_ev, restart_ev], ignore_index=True)
    return events.sort_values(["event_ts", "process_id"]).reset_index(drop=True)


class World:
    """In-memory synthetic world."""

    def __init__(
        self,
        cfg: WorldConfig,
        shops: pd.DataFrame,
        agents: pd.DataFrame,
        processes: pd.DataFrame,
        events: pd.DataFrame,
    ) -> None:
        self.cfg = cfg
        self.shops = shops
        self.agents = agents
        self.processes = processes
        self.events = events

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.shops.to_parquet(folder / "shops.parquet", index=False)
        self.agents.to_parquet(folder / "agents.parquet", index=False)
        self.processes.to_parquet(folder / "processes.parquet", index=False)
        self.events.to_parquet(folder / "crm_events.parquet", index=False)
        meta = {k: str(v) for k, v in asdict(self.cfg).items()}
        (folder / "world.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> World:
        if not (folder / "processes.parquet").exists():
            raise FileNotFoundError(
                f"No synthetic world in {folder}. Run `python -m renewals generate` first."
            )
        meta = json.loads((folder / "world.json").read_text(encoding="utf-8"))
        cfg = WorldConfig(
            seed=int(meta["seed"]),
            start=dt.date.fromisoformat(meta["start"]),
            end=dt.date.fromisoformat(meta["end"]),
            campaign_start=dt.date.fromisoformat(meta["campaign_start"]),
            campaign_end=dt.date.fromisoformat(meta["campaign_end"]),
        )
        return cls(
            cfg,
            pd.read_parquet(folder / "shops.parquet"),
            pd.read_parquet(folder / "agents.parquet"),
            pd.read_parquet(folder / "processes.parquet"),
            pd.read_parquet(folder / "crm_events.parquet"),
        )


def generate_world(cfg: WorldConfig | None = None) -> World:
    """Build the full synthetic world deterministically from ``cfg.seed``."""
    cfg = cfg or WorldConfig()
    shops = _build_shops()
    agents = _build_agents(shops)
    processes = _Builder(cfg, shops, agents).run()
    events = build_crm_events(processes)
    log.info(
        "Generated world: %d shops, %d agents, %d processes, %d CRM events (seed=%d)",
        len(shops),
        len(agents),
        len(processes),
        len(events),
        cfg.seed,
    )
    return World(cfg, shops, agents, processes, events)
