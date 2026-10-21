"""Streamlit dashboard. Launch with ``python -m renewals dashboard``.

This file is presentation only. Every number comes from :class:`DataAccess`, which applies
the user's permissions before anything reaches the page.
"""

from __future__ import annotations

import math

import altair as alt
import pandas as pd
import streamlit as st

from renewals.dashboard.access import AccessDenied, DataAccess, Filters, load_facts
from renewals.dashboard.auth import EMPLOYEE, authenticate, load_users
from renewals.pipeline import load_shops
from renewals.settings import load_settings
from renewals.world.model import CHANNEL_LABELS, CHANNELS, STREAM_LABELS, STREAMS

ROLE_LABELS = {
    "regional_manager": "Regional Manager",
    "shop_manager": "Shop Manager",
    "employee": "Employee",
}
FIXED_BLUE = "#2a78d6"
DIGITAL_ORANGE = "#eb6834"
SHARE_FORMAT = {"Digital share": st.column_config.NumberColumn(format="%.1f%%")}


@st.cache_resource
def _data() -> tuple[DataAccess, dict]:
    settings = load_settings()
    access = DataAccess(load_facts(settings.output_dir), load_shops(settings.reference_shops))
    return access, load_users(settings.users_file)


def _login(users: dict) -> None:
    st.title("Renewals dashboard")
    st.caption("Synthetic data. Demo users are listed in the README.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            user = authenticate(users, username, password)
            if user:
                st.session_state["user"] = user
                st.rerun()
            st.error("Unknown user or wrong password")


def _pct(x: float) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x:.1%}"


def _table(df: pd.DataFrame, extra: list[str]) -> pd.DataFrame:
    cols = [*extra, "renewals", "migrations", "combined", "digital", "digital_share"]
    out = df[cols].rename(
        columns={
            "shop_id": "Shop",
            "shop_name": "Shop name",
            "region_name": "Region",
            "agent_id": "Agent",
            "renewals": "Renewals",
            "migrations": "Migrations",
            "combined": "Combined",
            "digital": "Digital",
            "digital_share": "Digital share",
        }
    )
    out["Digital share"] = (out["Digital share"] * 100).round(1)
    return out


def main() -> None:
    st.set_page_config(page_title="Renewals dashboard", layout="wide")
    access, users = _data()
    user = st.session_state.get("user")
    if user is None:
        _login(users)
        return

    shops = access.shops_for(user)
    days = access.trend(user, Filters())["day"]
    with st.sidebar:
        st.markdown(f"**{user.display_name}**  \n{ROLE_LABELS[user.role]}")
        if st.button("Sign out"):
            st.session_state.pop("user")
            st.rerun()
        lo, hi = days.min().date(), days.max().date()
        picked = st.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi)
        date_from, date_to = picked if isinstance(picked, tuple) and len(picked) == 2 else (lo, hi)
        channels = st.multiselect("Channel", CHANNELS, format_func=CHANNEL_LABELS.get)
        streams = st.multiselect("Stream", STREAMS, format_func=STREAM_LABELS.get)
        shop_pick: list[str] = []
        if user.role != EMPLOYEE:
            shop_pick = st.multiselect(
                "Shops",
                shops["shop_id"].tolist(),
                format_func=lambda s: f"{s} " + shops.set_index("shop_id").at[s, "shop_name"],
            )
    filters = Filters(date_from, date_to, tuple(channels), tuple(streams), tuple(shop_pick))

    st.title("Renewals dashboard")
    scope = (
        "all regions"
        if user.role == "regional_manager"
        else shops["region_name"].iloc[0]
        if user.role == "shop_manager"
        else f"{shops['shop_id'].iloc[0]} {shops['shop_name'].iloc[0]}"
    )
    st.caption(f"Synthetic data - scope: {scope} - {date_from:%d.%m.%Y} to {date_to:%d.%m.%Y}")

    k = access.kpis(user, filters)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Combined gross adds", f"{k['combined']:,}")
    c2.metric("Contract renewals", f"{k['renewals']:,}")
    c3.metric("Tariff migrations", f"{k['migrations']:,}")
    c4.metric("Digital share", _pct(k["digital_share"]))

    trend = access.trend(user, filters)
    if not trend.empty:
        long = trend.melt("day", ["combined", "digital"], var_name="series", value_name="count")
        long["series"] = long["series"].map({"combined": "Combined", "digital": "Digital"})
        chart = (
            alt.Chart(long)
            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=30))
            .encode(
                x=alt.X("day:T", title=None),
                y=alt.Y("count:Q", title="Gross adds per day"),
                color=alt.Color(
                    "series:N",
                    title=None,
                    scale=alt.Scale(
                        domain=["Combined", "Digital"], range=[FIXED_BLUE, DIGITAL_ORANGE]
                    ),
                ),
                tooltip=[alt.Tooltip("day:T", format="%d.%m.%Y"), "series:N", "count:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(chart, width="stretch")

    if user.role != EMPLOYEE:
        st.subheader("Shop leaderboard")
        board = access.shop_leaderboard(user, filters)
        st.dataframe(
            _table(board, ["region_name", "shop_id", "shop_name"]),
            hide_index=True,
            column_config=SHARE_FORMAT,
            width="stretch",
        )
        st.subheader("Agent detail")
    else:
        st.subheader("My sales")
    try:
        agents = access.agent_detail(user, filters)
        st.dataframe(
            _table(agents, ["shop_id", "agent_id"]),
            hide_index=True,
            column_config=SHARE_FORMAT,
            width="stretch",
        )
    except AccessDenied as exc:
        st.info(str(exc))


main()
