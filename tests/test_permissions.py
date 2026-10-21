"""Full role x view permission matrix, enforced in the data-access layer."""

import datetime as dt

import pandas as pd
import pytest
from conftest import REPO

from renewals.dashboard.access import AccessDenied, DataAccess, Filters
from renewals.dashboard.auth import User, authenticate, hash_password, load_users

USERS = {
    "regional": User("rm", "RM", "regional_manager"),
    "north": User("north", "North", "shop_manager", region_id="R1"),
    "south": User("south", "South", "shop_manager", region_id="R2"),
    "employee": User("emp", "Emp", "employee", region_id="R1", shop_id="S05", agent_id="A057"),
    "employee_no_agent": User("emp2", "Emp2", "employee", shop_id="S05"),
}


@pytest.fixture(scope="module")
def facts(world):
    """Two sales for every agent of every shop, plus one self-service sale per shop."""
    rows = []
    n = 0
    for a in world.agents.itertuples():
        for stream, channel in (("renewal", "in_store"), ("migration", "click_collect")):
            n += 1
            rows.append(
                (
                    f"P{n}",
                    f"C{n}",
                    stream,
                    channel,
                    channel != "in_store",
                    a.shop_id,
                    a.agent_id,
                    pd.Timestamp(2026, 9, 1 + n % 5),
                )
            )
    for s in world.shops.itertuples():
        n += 1
        rows.append(
            (
                f"P{n}",
                f"C{n}",
                "renewal",
                "self_service",
                True,
                s.shop_id,
                "DIGITAL",
                pd.Timestamp(2026, 9, 2),
            )
        )
    return pd.DataFrame(
        rows,
        columns=[
            "process_id",
            "contract_id",
            "stream",
            "channel",
            "is_digital",
            "shop_id",
            "agent_id",
            "day",
        ],
    )


@pytest.fixture(scope="module")
def access(facts, world):
    return DataAccess(facts, world.shops)


def region_shops(world, region):
    return sorted(world.shops.loc[world.shops["region_id"] == region, "shop_id"])


def test_permitted_shops_per_role(access, world):
    assert access.permitted_shops(USERS["regional"]) == sorted(world.shops["shop_id"])
    assert access.permitted_shops(USERS["north"]) == region_shops(world, "R1")
    assert access.permitted_shops(USERS["south"]) == region_shops(world, "R2")
    assert access.permitted_shops(USERS["employee"]) == ["S05"]


@pytest.mark.parametrize("key", ["regional", "north", "south", "employee"])
def test_kpis_only_count_permitted_shops(access, facts, key):
    allowed = access.permitted_shops(USERS[key])
    expected = facts[facts["shop_id"].isin(allowed)]
    k = access.kpis(USERS[key], Filters())
    assert k["combined"] == len(expected)
    assert k["digital"] == int(expected["is_digital"].sum())


@pytest.mark.parametrize(
    "key, allowed",
    [("regional", True), ("north", True), ("south", True), ("employee", False)],
)
def test_shop_leaderboard_matrix(access, key, allowed):
    user = USERS[key]
    if allowed:
        board = access.shop_leaderboard(user, Filters())
        assert set(board["shop_id"]) == set(access.permitted_shops(user))
        assert board["combined"].is_monotonic_decreasing
    else:
        with pytest.raises(AccessDenied):
            access.shop_leaderboard(user, Filters())


def test_agent_detail_scope(access, world):
    agents_of = world.agents.groupby("shop_id")["agent_id"].apply(set)
    full = access.agent_detail(USERS["regional"], Filters())
    assert set(full["agent_id"]) == set(world.agents["agent_id"]) | {"DIGITAL"}

    north = access.agent_detail(USERS["north"], Filters())
    assert set(north["shop_id"]) == set(region_shops(world, "R1"))
    south_agents = set().union(*[agents_of[s] for s in region_shops(world, "R2")])
    assert not (set(north["agent_id"]) & south_agents)

    own = access.agent_detail(USERS["employee"], Filters())
    assert own["agent_id"].tolist() == ["A057"]
    assert own["shop_id"].tolist() == ["S05"]

    with pytest.raises(AccessDenied):
        access.agent_detail(USERS["employee_no_agent"], Filters())


@pytest.mark.parametrize("key", ["north", "employee"])
def test_requesting_a_shop_outside_permission_is_denied(access, world, key):
    outside = region_shops(world, "R2")[0]
    with pytest.raises(AccessDenied):
        access.kpis(USERS[key], Filters(shop_ids=(outside,)))
    with pytest.raises(AccessDenied):
        access.trend(USERS[key], Filters(shop_ids=(outside,)))


def test_manager_can_narrow_to_a_shop_in_own_region(access, facts, world):
    shop = region_shops(world, "R1")[1]
    k = access.kpis(USERS["north"], Filters(shop_ids=(shop,)))
    assert k["combined"] == int((facts["shop_id"] == shop).sum())


def test_channel_stream_and_date_filters(access, facts):
    u = USERS["regional"]
    k = access.kpis(u, Filters(channels=("self_service",)))
    assert k["combined"] == k["digital"] == int((facts["channel"] == "self_service").sum())
    k = access.kpis(u, Filters(streams=("migration",)))
    assert k["renewals"] == 0 and k["migrations"] == int((facts["stream"] == "migration").sum())
    day = dt.date(2026, 9, 2)
    k = access.kpis(u, Filters(date_from=day, date_to=day))
    assert k["combined"] == int((facts["day"] == pd.Timestamp(day)).sum())


# -- authentication -----------------------------------------------------------------------

DEMO = {
    "regional.manager": "demo-regional",
    "manager.north": "demo-north",
    "manager.south": "demo-south",
    "employee.s05": "demo-employee",
}


def test_demo_users_authenticate_with_readme_credentials():
    users = load_users(REPO / "config" / "users.yaml")
    assert set(users) == set(DEMO)
    for name, pw in DEMO.items():
        assert authenticate(users, name, pw) is users[name]
    assert users["employee.s05"].agent_id == "A057"


def test_wrong_password_and_unknown_user_are_rejected():
    users = load_users(REPO / "config" / "users.yaml")
    assert authenticate(users, "regional.manager", "demo-north") is None
    assert authenticate(users, "nobody", "demo-regional") is None


def test_only_hashes_are_stored():
    text = (REPO / "config" / "users.yaml").read_text()
    for pw in DEMO.values():
        assert pw not in text
    assert hash_password("x").startswith("$2b$")


def test_invalid_user_definitions_are_rejected():
    with pytest.raises(ValueError):
        User("x", "x", "superuser")
    with pytest.raises(ValueError):
        User("x", "x", "shop_manager")
    with pytest.raises(ValueError):
        User("x", "x", "employee")
