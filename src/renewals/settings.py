"""Project paths and environment-driven settings.

Credentials and URLs come only from environment variables (optionally loaded from a
local ``.env`` file via python-dotenv). The defaults below mirror ``.env.example`` so the
demo works out of the box, but they are dummy values for the local mock portals only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SEED = 42


@dataclass(frozen=True)
class PortalCredentials:
    """Login data for one source portal."""

    base_url: str
    user: str
    password: str


@dataclass(frozen=True)
class Settings:
    """Resolved settings for one process run."""

    root: Path
    bi: PortalCredentials
    crm: PortalCredentials
    mock_portal_port: int
    real_download_dir: Path

    @property
    def world_dir(self) -> Path:
        return self.root / "data" / "generated"

    @property
    def reference_shops(self) -> Path:
        """Shop master data (region, shop name). Small, deterministic, committed."""
        return self.root / "data" / "reference" / "shops.csv"

    @property
    def test_download_dir(self) -> Path:
        """Default download target. Kept local so experiments never touch the shared folder."""
        return self.root / "downloads"

    def download_root(self, real: bool) -> Path:
        return self.real_download_dir if real else self.test_download_dir

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def users_file(self) -> Path:
        return self.root / "config" / "users.yaml"

    @property
    def docs_img_dir(self) -> Path:
        return self.root / "docs" / "img"


def load_settings(root: Path | None = None) -> Settings:
    """Load settings from the environment, reading ``<root>/.env`` if present."""
    root = (root or PROJECT_ROOT).resolve()
    load_dotenv(root / ".env", override=False)
    port = int(os.getenv("MOCK_PORTAL_PORT", "8701"))
    real_dir = Path(os.getenv("REAL_DOWNLOAD_DIR", "./share/downloads"))
    if not real_dir.is_absolute():
        real_dir = root / real_dir
    return Settings(
        root=root,
        bi=PortalCredentials(
            base_url=os.getenv("BI_PORTAL_URL", f"http://127.0.0.1:{port}/bi"),
            user=os.getenv("BI_PORTAL_USER", "bi.demo"),
            password=os.getenv("BI_PORTAL_PASSWORD", "change-me-bi"),
        ),
        crm=PortalCredentials(
            base_url=os.getenv("CRM_PORTAL_URL", f"http://127.0.0.1:{port}/crm"),
            user=os.getenv("CRM_PORTAL_USER", "crm.demo"),
            password=os.getenv("CRM_PORTAL_PASSWORD", "change-me-crm"),
        ),
        mock_portal_port=port,
        real_download_dir=real_dir,
    )
