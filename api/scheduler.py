"""Sliding-window queue scheduler."""

from dataclasses import dataclass, field

from sqlalchemy import and_, delete, insert, update
from sqlalchemy import select as sqlalchemy_select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from api.love import FriendCodeValidityError, friend_code_to_principal_id
from api.settings import env_int
from database import DiscordFriends, Friend, FriendSchedule, RemoteWindow

DEFAULT_MISS_LIMIT = 10


@dataclass
class Config:
    window: int = 100
    fast_slots: int = 90
    fast_interval: int = 2 * 60
    slow_interval: int = 30 * 60
    new_window: int = 30 * 60
    online_window: int = 30 * 60
    warm_window: int = 7 * 24 * 60 * 60
    slow_starvation_limit: int = 24 * 60 * 60

    @classmethod
    def from_env(cls) -> "Config":
        """Reads scheduler overrides from the environment."""
        config = cls(
            window=env_int("SCHEDULER_WINDOW", cls.window),
            fast_slots=env_int("SCHEDULER_FAST_SLOTS", cls.fast_slots),
            fast_interval=env_int("SCHEDULER_FAST_INTERVAL", cls.fast_interval, 0),
            slow_interval=env_int("SCHEDULER_SLOW_INTERVAL", cls.slow_interval, 0),
            new_window=env_int("SCHEDULER_NEW_WINDOW", cls.new_window, 0),
            online_window=env_int("SCHEDULER_ONLINE_WINDOW", cls.online_window, 0),
            warm_window=env_int("SCHEDULER_WARM_WINDOW", cls.warm_window, 0),
            slow_starvation_limit=env_int("SCHEDULER_SLOW_STARVATION_LIMIT", cls.slow_starvation_limit, 0),
        )
        config.slow_interval = max(config.slow_interval, config.fast_interval)
        return config


@dataclass
class Entry:
    friend_code: str
    pid: int
    online: bool
    last_online: int
    account_creation: int
    refresh_requested: bool
    last_polled_at: int = 0
    consecutive_misses: int = 0
    last_accessed: int = 0
    last_refresh: int = 0
    profile_retry_at: int = 0
    username: str | None = None


@dataclass
class Plan:
    window: list[Entry] = field(default_factory=list)
    poll: list[Entry] = field(default_factory=list)
    fast: list[Entry] = field(default_factory=list)
    slow: list[Entry] = field(default_factory=list)
    add: list[int] = field(default_factory=list)
    remove: list[int] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.poll or self.add or self.remove)


def lane(entry: Entry, now: int, config: Config) -> str:
    online = entry.online and now - entry.last_online <= config.online_window
    new = now - entry.account_creation <= config.new_window
    return "fast" if (online or new) else "slow"


def is_due(entry: Entry, lane_name: str, now: int, config: Config) -> bool:
    interval = config.fast_interval if lane_name == "fast" else config.slow_interval
    return now - entry.last_polled_at >= interval


def _sort_key(entry: Entry, lane_name: str, index: int, now: int, config: Config):
    if lane_name == "fast":
        priority = 0 if now - entry.account_creation <= config.new_window else 1
        return (priority, entry.last_polled_at, entry.friend_code, index)

    # Overdue slow users first, then recently active ones, oldest poll first.
    overdue = entry.last_polled_at == 0 or now - entry.last_polled_at >= config.slow_starvation_limit
    activity = max(entry.last_online, entry.last_accessed)
    warm = activity > 0 and now - activity <= config.warm_window
    return (
        0 if overdue else 1,
        0 if warm else 1,
        entry.last_polled_at,
        entry.friend_code,
        index,
    )


def select(entries: list[Entry], remote: dict[str, int], now: int, config: Config | None = None) -> Plan:
    """Chooses the remote window and the due subset to poll."""
    config = config or Config()
    plan = Plan()

    tracked = {entry.friend_code for entry in entries}
    fast = []
    slow = []
    for index, entry in enumerate(entries):
        (fast if lane(entry, now, config) == "fast" else slow).append(index)

    fast.sort(key=lambda index: _sort_key(entries[index], "fast", index, now, config))
    slow.sort(key=lambda index: _sort_key(entries[index], "slow", index, now, config))

    fast_capacity = min(config.fast_slots, config.window)
    selected: list[int] = []
    selected_codes: set[str] = set()

    for index in fast[:fast_capacity]:
        if len(selected) < config.window:
            selected.append(index)
            selected_codes.add(entries[index].friend_code)

    slow_slots = config.window - len(selected)
    for index in slow:
        if slow_slots == 0:
            break
        if is_due(entries[index], "slow", now, config):
            selected.append(index)
            selected_codes.add(entries[index].friend_code)
            slow_slots -= 1

    # Sticky: keep already-remote slow users while there are free slots.
    if slow_slots > 0:
        keep = [
            index
            for index in slow
            if entries[index].friend_code in remote
            and entries[index].friend_code not in selected_codes
        ]
        keep.sort(key=lambda index: entries[index].last_polled_at)
        for index in keep[:slow_slots]:
            selected.append(index)
            selected_codes.add(entries[index].friend_code)
            slow_slots -= 1

    selected.sort()
    for index in selected:
        entry = entries[index]
        plan.window.append(entry)
        lane_name = lane(entry, now, config)
        if is_due(entry, lane_name, now, config):
            plan.poll.append(entry)
            (plan.fast if lane_name == "fast" else plan.slow).append(entry)

    for entry in plan.window:
        if entry.friend_code not in remote:
            plan.add.append(entry.pid)
    for code, pid in remote.items():
        if code not in tracked or code not in selected_codes:
            plan.remove.append(pid)

    plan.add.sort()
    plan.remove.sort()
    return plan


def load_entries(session, network) -> tuple[list[Entry], list[str]]:
    """Loads tracked friends with their schedule state."""
    rows = session.execute(
        sqlalchemy_select(Friend, FriendSchedule)
        .outerjoin(
            FriendSchedule,
            and_(
                FriendSchedule.network == Friend.network,
                FriendSchedule.friend_code == Friend.friend_code,
            ),
        )
        .where(Friend.network == network)
        .order_by(Friend.friend_code)
    ).all()

    entries = []
    invalid = []
    for friend, schedule in rows:
        try:
            pid = friend_code_to_principal_id(friend.friend_code)
        except FriendCodeValidityError:
            invalid.append(friend.friend_code)
            continue

        # Seed from the old loop's last_updated so the first run doesn't re-poll everyone.
        last_polled = max(
            int(schedule.last_polled_at if schedule else 0),
            int(friend.last_updated or 0),
        )
        entries.append(
            Entry(
                friend_code=friend.friend_code,
                pid=pid,
                online=bool(friend.online),
                last_online=int(friend.last_online or 0),
                account_creation=int(friend.account_creation or 0),
                refresh_requested=bool(friend.refresh_requested),
                last_polled_at=last_polled,
                consecutive_misses=int(schedule.consecutive_misses if schedule else 0),
                last_accessed=int(friend.last_accessed or 0),
                last_refresh=int(friend.last_refresh or 0),
                profile_retry_at=int(friend.profile_retry_at or 0),
                username=friend.username,
            )
        )
    return entries, invalid


def load_remote(session, network) -> dict[str, int]:
    rows = session.execute(
        sqlalchemy_select(RemoteWindow.friend_code, RemoteWindow.pid).where(
            RemoteWindow.network == network
        )
    ).all()
    return {friend_code: int(pid) for friend_code, pid in rows}


def save_remote(session, network, remote: dict[str, int], now: int) -> None:
    session.execute(delete(RemoteWindow).where(RemoteWindow.network == network))
    if remote:
        session.execute(
            insert(RemoteWindow),
            [
                {
                    "network": network,
                    "friend_code": code,
                    "pid": pid,
                    "added_at": now,
                }
                for code, pid in remote.items()
            ],
        )


def _schedule_insert(session):
    if session.get_bind().dialect.name == "postgresql":
        return postgresql_insert(FriendSchedule)
    return sqlite_insert(FriendSchedule)


def save_polled(session, network, rows: list[tuple[str, int, int, int]], now: int) -> None:
    """rows: (friend_code, last_polled_at, next_due_at, consecutive_misses)."""
    if not rows:
        return

    # Skip codes purged earlier in the same cycle so the FK does not reject the batch.
    codes = [code for code, *_ in rows]
    existing = set(
        session.scalars(
            sqlalchemy_select(Friend.friend_code).where(Friend.friend_code.in_(codes))
        ).all()
    )
    rows = [row for row in rows if row[0] in existing]
    if not rows:
        return

    statement = _schedule_insert(session)
    statement = statement.on_conflict_do_update(
        index_elements=[FriendSchedule.network, FriendSchedule.friend_code],
        set_={
            "last_polled_at": statement.excluded.last_polled_at,
            "next_due_at": statement.excluded.next_due_at,
            "consecutive_misses": statement.excluded.consecutive_misses,
            "updated_at": statement.excluded.updated_at,
        },
    )
    session.execute(
        statement,
        [
            {
                "network": network,
                "friend_code": code,
                "last_polled_at": polled,
                "next_due_at": due,
                "consecutive_misses": misses,
                "updated_at": now,
            }
            for code, polled, due, misses in rows
        ],
    )


def forget_friend(session, network, friend_code: str) -> None:
    """Deletes a friend and its schedule/window rows (SQLite has no FK cascade)."""
    session.execute(
        delete(FriendSchedule)
        .where(FriendSchedule.network == network)
        .where(FriendSchedule.friend_code == friend_code)
    )
    session.execute(
        delete(RemoteWindow)
        .where(RemoteWindow.network == network)
        .where(RemoteWindow.friend_code == friend_code)
    )
    session.execute(
        delete(Friend)
        .where(Friend.friend_code == friend_code)
        .where(Friend.network == network)
    )


def record_poll_results(session, network, polled: list[Entry], present_pids: set[int], failed_add_pids: set[int], selection_time: int, poll_time: int, config: Config, remote_read_complete: bool = True) -> None:
    """Persists poll times, next due times, and purge decisions for one cycle."""
    rows = []
    purged = []

    if not remote_read_complete:
        # Empty/partial read: treat as unconfirmed; preserve due times and misses.
        for entry in polled:
            lane_name = lane(entry, selection_time, config)
            interval = config.fast_interval if lane_name == 'fast' else config.slow_interval
            rows.append((entry.friend_code, entry.last_polled_at, entry.last_polled_at + interval, entry.consecutive_misses))
        save_polled(session, network, rows, poll_time)
        return

    for entry in polled:
        lane_name = lane(entry, selection_time, config)
        add_failed = entry.pid in failed_add_pids
        misses, should_purge = next_misses(
            entry.consecutive_misses,
            present=entry.pid in present_pids,
            counted=lane_name == 'slow',
            add_failed=add_failed,
        )
        interval = config.fast_interval if lane_name == 'fast' else config.slow_interval
        if add_failed:
            # Not polled: keep the old due time so it retries next cycle.
            rows.append((entry.friend_code, entry.last_polled_at, entry.last_polled_at + interval, misses))
            continue
        rows.append((entry.friend_code, poll_time, poll_time + interval, misses))
        if should_purge:
            purged.append(entry.friend_code)
            print(f'Stopped tracking {entry.friend_code}: absent from the remote friendlist for {misses} slow sweeps')

    # Purge first so save_polled skips deleted rows.
    for friend_code in purged:
        forget_friend(session, network, friend_code)
        session.execute(update(DiscordFriends).where(
            DiscordFriends.friend_code == friend_code,
            DiscordFriends.network == network)
            .values(active=False)
        )

    # Keep the UI's "last updated" in sync with the schedule for every user we
    # actually reached, including confirmed-absent ones.
    polled_codes = [entry.friend_code for entry in polled if entry.pid not in failed_add_pids]
    if polled_codes:
        session.execute(
            update(Friend)
            .where(Friend.friend_code.in_(polled_codes))
            .where(Friend.network == network)
            .values(last_updated=poll_time)
        )

    save_polled(session, network, rows, poll_time)


def next_misses(previous: int, present: bool, counted: bool, add_failed: bool, limit: int = DEFAULT_MISS_LIMIT) -> tuple[int, bool]:
    """Only confirmed slow polls increment; fast polls keep the count and add failures reset it."""
    if present or add_failed:
        return 0, False
    if not counted:
        return previous, False
    value = previous + 1
    return value, value >= limit
