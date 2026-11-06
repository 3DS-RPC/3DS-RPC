"""Environment parsing, worker settings, and network timeouts."""

from __future__ import annotations

import os


def env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    return max(minimum, value) if minimum is not None else value


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    return max(minimum, value) if minimum is not None else value


# NASC authentication request.
NASC_LOGIN_SECONDS = 30

# Non-critical startup banner update.
COMMENT_SECONDS = 10

# Full remote friend-list read.
FRIEND_LIST_SECONDS = 60

# One friend add/remove.
FRIEND_OP_SECONDS = 30

# One presence batch.
PRESENCE_SECONDS = 60

# One persistent-info or Mii batch.
PROFILE_BATCH_SECONDS = 60

# How long a console must wait between manual Mii/profile refreshes.
REFRESH_COOLDOWN_SECONDS = 3600


def refresh_available(last_refresh: int, refresh_requested: bool, now: float) -> bool:
    return not refresh_requested and now >= (last_refresh or 0) + REFRESH_COOLDOWN_SECONDS


def refresh_remaining(last_refresh: int, now: float) -> int:
    return max(0, int((last_refresh or 0) + REFRESH_COOLDOWN_SECONDS - now))
