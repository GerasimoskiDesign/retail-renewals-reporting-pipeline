from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pandas as pd
import pytest

from renewals.downloader.dates import date_range
from renewals.portal.export import export_reports
from renewals.settings import PortalCredentials, Settings
from renewals.transform.sources import (
    COMPLETION_COLUMNS,
    CRM_COLUMNS,
    DIGITAL_COLUMNS,
    STORE_COLUMNS,
    WEB_COLUMNS,
    SourceData,
    load_sources,
)
from renewals.world.generator import World, generate_world

MONTH = dt.date(2026, 9, 1)
AS_OF = dt.date(2026, 9, 30)
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def world() -> World:
    return generate_world()


@pytest.fixture(scope="session")
def month_downloads(world: World, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """All six reports for every day of September, exported straight from the world."""
    root = tmp_path_factory.mktemp("downloads")
    export_reports(world, date_range(MONTH, AS_OF), root)
    return root


@pytest.fixture(scope="session")
def month_sources(month_downloads: Path) -> SourceData:
    return load_sources(month_downloads, date_range(MONTH, AS_OF))


def make_settings(root: Path, world: World, port: int = 8701) -> Settings:
    """Settings rooted in a temp folder, with the world and shop master in place."""
    world.save(root / "data" / "generated")
    ref = root / "data" / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    world.shops.to_csv(ref / "shops.csv", index=False)
    (root / "config").mkdir(exist_ok=True)
    shutil.copy(REPO / "config" / "users.yaml", root / "config" / "users.yaml")
    base = f"http://127.0.0.1:{port}"
    return Settings(
        root=root,
        bi=PortalCredentials(f"{base}/bi", "bi.test", "bi-test-pass"),
        crm=PortalCredentials(f"{base}/crm", "crm.test", "crm-test-pass"),
        mock_portal_port=port,
        real_download_dir=root / "share",
    )


# -- tiny hand-built sources for rule tests ----------------------------------------------


def ts(day: int, hour: int = 10, month: int = 9) -> pd.Timestamp:
    return pd.Timestamp(2026, month, day, hour)


def completion(
    pid: str,
    contract: str,
    stream: str = "renewal",
    shop: str = "S01",
    agent: str | None = "A001",
    code: str = "STORE",
    started: pd.Timestamp = None,
    completed: pd.Timestamp = None,
    report_day: int | None = None,
) -> dict:
    started = started if started is not None else ts(3)
    completed = completed if completed is not None else started + pd.Timedelta(hours=2)
    return {
        "process_id": pid,
        "contract_id": contract,
        "stream": stream,
        "shop_id": shop,
        "agent_id": agent,
        "sales_channel_code": code,
        "started_at": started,
        "completed_at": completed,
        "report_day": dt.date(2026, 9, report_day) if report_day else completed.date(),
        "source_file": "test.xlsx",
    }


def digital(
    pid: str,
    contract: str,
    channel: str,
    stream: str = "renewal",
    shop: str = "S01",
    completed: pd.Timestamp = None,
) -> dict:
    completed = completed if completed is not None else ts(4)
    return {
        "order_id": "WO-" + pid[1:],
        "process_id": pid,
        "contract_id": contract,
        "stream": stream,
        "channel": channel,
        "shop_id": shop,
        "completed_at": completed,
        "report_day": completed.date(),
    }


def store(
    pid: str,
    contract: str,
    kind: str,
    agent: str = "A001",
    shop: str = "S01",
    stream: str = "renewal",
    completed: pd.Timestamp = None,
) -> dict:
    completed = completed if completed is not None else ts(4)
    return {
        "shop_id": shop,
        "agent_id": agent,
        "contract_id": contract,
        "process_id": pid,
        "stream": stream,
        "completion_type": kind,
        "completed_at": completed,
        "report_day": completed.date(),
    }


def web(pid: str, contract: str, channel: str, placed: pd.Timestamp, report_day: int) -> dict:
    return {
        "order_id": "WO-" + pid[1:],
        "process_id": pid,
        "contract_id": contract,
        "stream": "renewal",
        "channel": channel,
        "shop_id": "S01",
        "placed_at": placed,
        "report_day": dt.date(2026, 9, report_day),
    }


def event(
    pid: str, contract: str, kind: str, when: pd.Timestamp, restart_of: str | None = None
) -> dict:
    return {
        "process_id": pid,
        "contract_id": contract,
        "event_type": kind,
        "event_ts": when,
        "restart_of": restart_of,
        "report_day": when.date(),
    }


def sources(
    completions=(), digital_orders=(), store_completions=(), web_orders=(), crm_events=()
) -> SourceData:
    def frame(rows, cols):
        return pd.DataFrame(list(rows), columns=cols)

    return SourceData(
        completions=frame(completions, COMPLETION_COLUMNS),
        digital_orders=frame(digital_orders, DIGITAL_COLUMNS),
        store_completions=frame(store_completions, STORE_COLUMNS),
        web_orders=frame(web_orders, WEB_COLUMNS),
        crm_events=frame(crm_events, CRM_COLUMNS),
    )
