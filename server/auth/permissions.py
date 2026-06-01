"""Permission and flag checks for accounts."""

from typing import Optional


def has_flag(user_data: Optional[dict], flag: str) -> bool:
    if not user_data:
        return False
    return flag in (user_data.get("flags") or {})


def is_admin(user_data: Optional[dict]) -> bool:
    return has_flag(user_data, "Admin")
