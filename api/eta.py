"""Rough next-poll estimates for the consoles page."""

from __future__ import annotations

import threading
import time

from api import scheduler
from api.metrics import get_backend_metrics
from api.networks import NetworkType
from api.settings import env_float

DEFAULT_CYCLE_SECONDS = 10.0
MIN_CYCLE_SECONDS = 10.0
MAX_CYCLE_SECONDS = 300.0
MAX_CYCLES = 300

ETA_REFRESH_INTERVAL_SECONDS = 10 * 60

_cache: dict[NetworkType, dict[str, float]] = {}
_cache_generated_at = 0.0
_cache_lock = threading.Lock()


def estimate_next_polls(session, network, friend_codes=None, now=None) -> dict[str, float]:
    """Returns an estimated absolute poll time per friend code.

    Replays the scheduler with the current roster and schedule. Assumes online
    state and the remote window stay stable.
    """
    if friend_codes is not None and not friend_codes:
        return {}

    metrics = get_backend_metrics(network) or {}
    if metrics.get('network_status') == 'down':
        return {}

    now = int(time.time()) if now is None else int(now)
    try:
        entries, _ = scheduler.load_entries(session, network)
        remote = scheduler.load_remote(session, network)
    except Exception:
        return {}
    if not entries:
        return {}

    targets = set(friend_codes) if friend_codes is not None else None
    cycle_seconds = _cycle_seconds(metrics)
    return _simulate(entries, remote, now, scheduler.Config.from_env(), cycle_seconds, targets)


def refresh_cache(session, now=None) -> None:
    """Rebuilds the cached ETA map for both networks."""
    global _cache_generated_at

    now = int(time.time()) if now is None else int(now)
    refreshed: dict[NetworkType, dict[str, float]] = {}

    for network in (NetworkType.NINTENDO, NetworkType.PRETENDO):
        metrics = get_backend_metrics(network) or {}
        if metrics.get('network_status') == 'down':
            refreshed[network] = {}
            continue
        try:
            entries, _ = scheduler.load_entries(session, network)
            remote = scheduler.load_remote(session, network)
        except Exception:
            continue
        if not entries:
            refreshed[network] = {}
            continue
        cycle_seconds = _cycle_seconds(metrics)
        refreshed[network] = _simulate(entries, remote, now, scheduler.Config.from_env(), cycle_seconds, None)

    with _cache_lock:
        _cache.update(refreshed)
        _cache_generated_at = now


def get_cached_etas(network, now=None) -> dict[str, float]:
    current = time.time() if now is None else now
    with _cache_lock:
        if not _cache_generated_at:
            return {}
        if current - _cache_generated_at > ETA_REFRESH_INTERVAL_SECONDS * 2:
            return {}
        return dict(_cache.get(network, {}))


def get_cache_generated_at() -> float:
    with _cache_lock:
        return _cache_generated_at


def format_eta(eta: float | None, now: float | None = None) -> str:
    if not eta:
        return ''
    remaining = max(0, eta - (time.time() if now is None else now))
    if remaining < 60:
        return 'Next update: now'
    minutes = round(remaining / 60)
    if minutes < 60:
        return f'Next update in ~{minutes} min'
    return f'Next update in ~{round(minutes / 60, 1):g} h'


def _cycle_seconds(metrics: dict) -> float:
    observed = metrics.get('last_loop_duration_seconds') or metrics.get('average_loop_time_seconds') or 0
    cycle = max(MIN_CYCLE_SECONDS, float(observed or DEFAULT_CYCLE_SECONDS))
    cycle += env_float("SCHEDULER_LOOP_DELAY", 2.0, 0.2)
    return min(MAX_CYCLE_SECONDS, cycle)


def _simulate(entries, remote, now: int, config: scheduler.Config, cycle_seconds: float, targets: set[str] | None) -> dict[str, float]:
    etas: dict[str, float] = {}
    simulated = float(now)

    for _ in range(MAX_CYCLES):
        plan = scheduler.select(entries, remote, int(simulated), config)

        if not plan.poll:
            upcoming = []
            for entry in entries:
                lane = scheduler.lane(entry, int(simulated), config)
                interval = config.fast_interval if lane == 'fast' else config.slow_interval
                due = entry.last_polled_at + interval
                if due > simulated:
                    upcoming.append(due)
            if not upcoming:
                break
            simulated = min(upcoming)
            continue

        fast_codes = {entry.friend_code for entry in plan.fast}
        for entry in plan.poll:
            etas.setdefault(entry.friend_code, simulated)
            entry.last_polled_at = int(simulated)
            if entry.friend_code in fast_codes:
                entry.last_online = int(simulated)

        remote = {entry.friend_code: entry.pid for entry in plan.window}
        simulated += cycle_seconds

        if targets is not None and targets.issubset(etas):
            break
        if targets is None and len(etas) == len(entries):
            break

    return etas
