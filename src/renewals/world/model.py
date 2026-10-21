"""Shared vocabulary for the synthetic retail world.

Keeping channel and stream codes in one place matters because three different source
reports spell them differently; every module maps *to* these codes, never between reports.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

IN_STORE = "in_store"
SELF_SERVICE = "self_service"
EXPRESS = "express"
CLICK_COLLECT = "click_collect"

CHANNELS: tuple[str, ...] = (IN_STORE, SELF_SERVICE, EXPRESS, CLICK_COLLECT)
DIGITAL_CHANNELS: frozenset[str] = frozenset({SELF_SERVICE, EXPRESS, CLICK_COLLECT})

RENEWAL = "renewal"
MIGRATION = "migration"
STREAMS: tuple[str, ...] = (RENEWAL, MIGRATION)

CHANNEL_LABELS = {
    IN_STORE: "In-store",
    SELF_SERVICE: "Self-service digital",
    EXPRESS: "Express digital",
    CLICK_COLLECT: "Click & collect",
}
STREAM_LABELS = {RENEWAL: "Contract renewals", MIGRATION: "Tariff migrations"}

PHONE_PREFIX = "+999"  # fake prefix: can never collide with a real number


@dataclass(frozen=True)
class WorldConfig:
    """Parameters of one synthetic world. Everything random derives from ``seed``."""

    seed: int = 42
    start: dt.date = dt.date(2026, 8, 24)
    end: dt.date = dt.date(2026, 9, 30)
    campaign_start: dt.date = dt.date(2026, 9, 14)
    campaign_end: dt.date = dt.date(2026, 9, 20)

    def in_campaign(self, day: dt.date) -> bool:
        return self.campaign_start <= day <= self.campaign_end
