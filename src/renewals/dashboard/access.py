"""Permission-enforcing data access for the dashboard.

Permissions live here, not in the UI. The Streamlit page only ever receives data that has
already been filtered for the signed-in user, so a UI bug (a widget that forgets to hide a
column, a crafted query parameter) cannot leak another shop's or another agent's numbers.

=================  ================  =====================  ===================
Role               Shops visible     Agent-level detail     Shop leaderboard
=================  ================  =====================  ===================
Regional manager   all               all agents             yes (all shops)
Shop manager       own region        agents in own region   yes (own region)
Employee           own shop          own agent row only     no
=================  ================  =====================  ===================
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from renewals.dashboard.auth import EMPLOYEE, REGIONAL_MANAGER, SHOP_MANAGER, User
from renewals.transform.metrics import aggregate


class AccessDenied(PermissionError):
    """The user asked for data outside their permission."""


@dataclass(frozen=True)
class Filters:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    channels: tuple[str, ...] = field(default_factory=tuple)  # empty = all
    streams: tuple[str, ...] = field(default_factory=tuple)  # empty = all
    shop_ids: tuple[str, ...] = field(default_factory=tuple)  # empty = all permitted


def load_facts(output_dir: Path) -> pd.DataFrame:
    """All published months of counted gross adds."""
    files = sorted((output_dir / "processed").glob("*/facts.parquet"))
    if not files:
        raise FileNotFoundError(
            f"No processed data in {output_dir}. Run `python -m renewals run --as-of ...` first."
        )
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


class DataAccess:
    """The only way the dashboard reads data."""

    def __init__(self, facts: pd.DataFrame, shops: pd.DataFrame) -> None:
        self._shops = shops[["shop_id", "shop_name", "region_id", "region_name"]].copy()
        self._facts = facts.merge(self._shops[["shop_id", "region_id"]], on="shop_id", how="left")

    # -- permission primitives -------------------------------------------------------
    def permitted_shops(self, user: User) -> list[str]:
        s = self._shops
        if user.role == REGIONAL_MANAGER:
            ids = s["shop_id"]
        elif user.role == SHOP_MANAGER:
            ids = s.loc[s["region_id"] == user.region_id, "shop_id"]
        elif user.role == EMPLOYEE:
            ids = s.loc[s["shop_id"] == user.shop_id, "shop_id"]
        else:  # pragma: no cover - User validates roles
            raise AccessDenied(f"unknown role {user.role}")
        return sorted(ids.tolist())

    def shops_for(self, user: User) -> pd.DataFrame:
        return self._shops[self._shops["shop_id"].isin(self.permitted_shops(user))].reset_index(
            drop=True
        )

    def _scoped(self, user: User, filters: Filters) -> pd.DataFrame:
        allowed = set(self.permitted_shops(user))
        if filters.shop_ids:
            outside = set(filters.shop_ids) - allowed
            if outside:
                raise AccessDenied(f"{user.username} may not view shops {sorted(outside)}")
            allowed = set(filters.shop_ids)
        f = self._facts[self._facts["shop_id"].isin(allowed)]
        if filters.date_from:
            f = f[f["day"] >= pd.Timestamp(filters.date_from)]
        if filters.date_to:
            f = f[f["day"] <= pd.Timestamp(filters.date_to)]
        if filters.channels:
            f = f[f["channel"].isin(filters.channels)]
        if filters.streams:
            f = f[f["stream"].isin(filters.streams)]
        return f

    # -- views ---------------------------------------------------------------------
    def kpis(self, user: User, filters: Filters) -> dict[str, float]:
        tot = aggregate(self._scoped(user, filters), [])
        if tot.empty:
            return {
                "renewals": 0,
                "migrations": 0,
                "combined": 0,
                "digital": 0,
                "digital_share": float("nan"),
            }
        row = tot.iloc[0]
        return {
            k: (float(row[k]) if k == "digital_share" else int(row[k]))
            for k in ("renewals", "migrations", "combined", "digital", "digital_share")
        }

    def trend(self, user: User, filters: Filters) -> pd.DataFrame:
        return aggregate(self._scoped(user, filters), ["day"])

    def shop_leaderboard(self, user: User, filters: Filters) -> pd.DataFrame:
        if user.role == EMPLOYEE:
            raise AccessDenied("Employees do not see the shop leaderboard")
        board = aggregate(self._scoped(user, filters), ["shop_id"])
        board = self._shops.merge(board, on="shop_id", how="inner")
        return board.sort_values(["combined", "shop_id"], ascending=[False, True]).reset_index(
            drop=True
        )

    def agent_detail(self, user: User, filters: Filters) -> pd.DataFrame:
        f = self._scoped(user, filters)
        if user.role == EMPLOYEE:
            if not user.agent_id:
                raise AccessDenied("No agent linked to this employee")
            f = f[f["agent_id"] == user.agent_id]
        detail = aggregate(f, ["shop_id", "agent_id"])
        return detail.sort_values(["combined", "agent_id"], ascending=[False, True]).reset_index(
            drop=True
        )
