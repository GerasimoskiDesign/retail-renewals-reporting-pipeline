"""Demo users with bcrypt-hashed credentials, loaded from ``config/users.yaml``.

Only hashes are stored. The demo credentials are listed in the README because this is a
public demo; in production the user file would come from the directory service instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import bcrypt
import yaml

REGIONAL_MANAGER = "regional_manager"
SHOP_MANAGER = "shop_manager"
EMPLOYEE = "employee"
ROLES = (REGIONAL_MANAGER, SHOP_MANAGER, EMPLOYEE)


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: str
    region_id: str | None = None
    shop_id: str | None = None
    agent_id: str | None = None
    password_hash: str = ""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"Unknown role {self.role!r} for {self.username}")
        if self.role == SHOP_MANAGER and not self.region_id:
            raise ValueError(f"Shop manager {self.username} needs a region_id")
        if self.role == EMPLOYEE and not self.shop_id:
            raise ValueError(f"Employee {self.username} needs a shop_id")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def load_users(path: Path) -> dict[str, User]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    users = {}
    for entry in raw.get("users", []):
        user = User(
            username=entry["username"],
            display_name=entry.get("display_name", entry["username"]),
            role=entry["role"],
            region_id=entry.get("region_id"),
            shop_id=entry.get("shop_id"),
            agent_id=entry.get("agent_id"),
            password_hash=entry["password_hash"],
        )
        users[user.username] = user
    return users


def authenticate(users: dict[str, User], username: str, plain: str) -> User | None:
    """Return the user if the credentials match, else ``None``.

    A dummy hash check runs for unknown usernames so response time does not reveal which
    usernames exist.
    """
    user = users.get(username.strip())
    candidate = user.password_hash if user else hash_password("timing-equaliser")
    ok = bcrypt.checkpw(plain.encode("utf-8"), candidate.encode("ascii"))
    return user if (user and ok) else None
