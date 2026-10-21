"""Aggregate counted gross adds into the report metrics.

``combined`` is renewals + migrations. Because companion migrations are excluded upstream,
it equals the number of distinct contracts sold, which is what the business means by
"combined". ``digital_share`` is digital / combined and is undefined (NaN) on zero volume.
"""

from __future__ import annotations

import pandas as pd

from renewals.world.model import MIGRATION, RENEWAL

METRIC_COLUMNS = ["renewals", "migrations", "combined", "digital", "digital_share"]


def aggregate(facts: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sum gross adds by ``keys`` (any subset of day / shop_id / agent_id / channel / stream)."""
    if facts.empty:
        return pd.DataFrame(columns=[*keys, *METRIC_COLUMNS])
    f = facts.assign(
        renewals=(facts["stream"] == RENEWAL).astype(int),
        migrations=(facts["stream"] == MIGRATION).astype(int),
        combined=1,
        digital=facts["is_digital"].astype(int),
    )
    if keys:
        out = f.groupby(keys, as_index=False, sort=True)[
            ["renewals", "migrations", "combined", "digital"]
        ].sum()
    else:
        out = f[["renewals", "migrations", "combined", "digital"]].sum().to_frame().T
    out["digital_share"] = out["digital"] / out["combined"].where(out["combined"] > 0)
    return out.reset_index(drop=True)


def shop_day(facts: pd.DataFrame) -> pd.DataFrame:
    return aggregate(facts, ["day", "shop_id"])


def agent_day(facts: pd.DataFrame) -> pd.DataFrame:
    return aggregate(facts, ["day", "shop_id", "agent_id"])
