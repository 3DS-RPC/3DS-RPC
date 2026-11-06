"""Presence polling and profile scraping."""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field

import anyio
from sqlalchemy import update

from api.health import HEALTH_DEGRADED, HEALTH_ERROR, HEALTH_OK, health_reason_label
from api.love import principal_id_to_friend_code
from api.love2 import MiiData
from api.metrics import advance_loop_progress
from api.networks import NetworkType
from api.settings import (
    PRESENCE_SECONDS,
    PROFILE_BATCH_SECONDS,
    REFRESH_COOLDOWN_SECONDS,
)
from database import DiscordFriends, Friend
from worker.failures import FailureClass, classify_error

log = logging.getLogger(__name__)

PROFILE_BATCH_SIZE = 20
PROFILE_RETRY_COOLDOWN_SECONDS = 30 * 60


def set_console_health(session, friend_code: str, network: NetworkType, health: str, reason: str | None = None, log_issue: bool = True) -> None:
    """Persist a console's health marker."""
    session.execute(
        update(DiscordFriends)
        .where(DiscordFriends.friend_code == friend_code)
        .where(DiscordFriends.network == network)
        .values(
            health=health,
            health_reason=reason,
            health_updated=time.time()
        )
    )
    if log_issue and health != HEALTH_OK:
        log.warning('[HEALTH] %s on %s -> %s: %s', friend_code, network.lower_name(), health, health_reason_label(reason))


async def update_presences(friends_client, session, network: NetworkType, current_friend_pids: list[int], rotation_pids: set | None = None, lanes: dict[int, str] | None = None) -> None:
    """Reads presence and writes status fields. The caller owns the transaction."""
    try:
        with anyio.fail_after(PRESENCE_SECONDS):
            tracked_presences = await friends_client.get_friend_presence(current_friend_pids)
    except Exception:
        for pid in current_friend_pids:
            status_code = str(principal_id_to_friend_code(pid)).zfill(12)
            set_console_health(session, status_code, network, HEALTH_ERROR, 'status_read_failed', log_issue=False)
        try:
            session.commit()
        except Exception:
            session.rollback()
        log.warning('[HEALTH] status read failed on %s', network.lower_name())
        raise

    online_user_pids: list[int] = []
    expected_by_lane = {'fast': 0, 'slow': 0}
    if rotation_pids is not None:
        for pid in rotation_pids:
            expected_by_lane[lanes.get(pid, 'slow') if lanes else 'slow'] += 1
    else:
        expected_by_lane['slow'] = len(current_friend_pids)
    advanced_by_lane = {'fast': 0, 'slow': 0}

    def count_progress(pid: int) -> None:
        if rotation_pids is not None and pid not in rotation_pids:
            return
        lane_name = lanes.get(pid, 'slow') if lanes else 'slow'
        advance_loop_progress(network, mode=lane_name)
        advanced_by_lane[lane_name] += 1

    for game in tracked_presences:
        online_user_pids.append(game.pid)
        game_description: str = game.presence.game_mode_description
        if not game_description:
            game_description = ''
        joinable: bool = bool(game.presence.join_availability_flag)

        friend_code: str = str(principal_id_to_friend_code(game.pid)).zfill(12)
        session.execute(
            update(Friend)
            .where(Friend.friend_code == friend_code)
            .where(Friend.network == network)
            .values(
                online=True,
                title_id=str(game.presence.game_key.title_id),
                upd_id=str(game.presence.game_key.title_version),
                joinable=joinable,
                game_description=game_description,
                last_online=time.time(),
                last_updated=time.time()
            )
        )
        # The response can include friends outside the poll set.
        count_progress(game.pid)

    # Otherwise this user must be offline.
    for offline_user in [h for h in current_friend_pids if not h in online_user_pids]:
        friend_code: str = str(principal_id_to_friend_code(offline_user)).zfill(12)
        session.execute(
            update(Friend)
            .where(Friend.friend_code == friend_code)
            .where(Friend.network == network)
            .values(
                online=False,
                title_id='0',
                upd_id='0',
                joinable=None,
                game_description=None,
                last_updated=time.time()
            )
        )
        count_progress(offline_user)

    for lane_name in ('slow', 'fast'):
        remaining = expected_by_lane[lane_name] - advanced_by_lane[lane_name]
        if remaining > 0:
            advance_loop_progress(network, remaining, mode=lane_name)

    # Baseline health; profile-specific failures override it later.
    for pid in current_friend_pids:
        friend_code = str(principal_id_to_friend_code(pid)).zfill(12)
        set_console_health(session, friend_code, network, HEALTH_OK)


@dataclass
class ProfileUpdate:
    friend_code: str
    values: dict = field(default_factory=dict)
    health: str = HEALTH_OK
    reason: str | None = None
    refresh_eligible: bool = False
    retry_at: int = 0


def _chunks(items, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


async def _fetch_infos(friends_client, pids: list[int]):
    """Fetches persistent info, falling back to per-user calls on a batch error."""
    try:
        with anyio.fail_after(PROFILE_BATCH_SECONDS):
            return await friends_client.get_friend_persistent_info(pids)
    except Exception as exc:
        # Do not multiply an outage into many more timeout calls.
        if len(pids) <= 1 or classify_error(exc) is FailureClass.OUTAGE:
            raise
        infos = []
        for pid in pids:
            try:
                with anyio.fail_after(PROFILE_BATCH_SECONDS):
                    infos.extend(await friends_client.get_friend_persistent_info([pid]))
            except Exception:
                continue
        return infos


async def _fetch_miis(friends_client, relationships):
    """Fetches Mii data, falling back to per-user calls on a batch error."""
    try:
        with anyio.fail_after(PROFILE_BATCH_SECONDS):
            return await friends_client.get_friend_mii(relationships)
    except Exception as exc:
        if len(relationships) <= 1 or classify_error(exc) is FailureClass.OUTAGE:
            raise
        miis = []
        for relationship in relationships:
            try:
                with anyio.fail_after(PROFILE_BATCH_SECONDS):
                    miis.extend(await friends_client.get_friend_mii([relationship]))
            except Exception:
                continue
        return miis


async def fetch_profile_updates(friends_client, added_friends, current_friends_list) -> list[ProfileUpdate]:
    """Fetches profile data in batches without touching the database."""
    now = time.time()
    retry_at = int(now + PROFILE_RETRY_COOLDOWN_SECONDS)
    candidates = []
    for entry in added_friends:
        refresh_eligible = entry.refresh_requested and now - entry.last_refresh >= REFRESH_COOLDOWN_SECONDS
        if entry.profile_retry_at > now:
            continue
        if refresh_eligible or entry.username is None:
            candidates.append((entry, refresh_eligible))
    if not candidates:
        return []

    relationships = {friend.pid: friend for friend in current_friends_list}
    updates: list[ProfileUpdate] = []

    for batch in _chunks(candidates, PROFILE_BATCH_SIZE):
        try:
            infos = await _fetch_infos(friends_client, [entry.pid for entry, _ in batch])
        except Exception as e:
            for entry, _ in batch:
                print(f'Failed to get persistent info for {entry.friend_code}: {e}')
                updates.append(ProfileUpdate(
                    entry.friend_code, health=HEALTH_ERROR, reason='info_read_failed', retry_at=retry_at
                ))
            continue

        info_by_pid = {info.pid: info for info in infos if info.pid is not None}
        mii_batch = []

        for entry, refresh_eligible in batch:
            info = info_by_pid.get(entry.pid)
            if info is None:
                print(f'No persistent info returned for {entry.friend_code}')
                updates.append(ProfileUpdate(
                    entry.friend_code, health=HEALTH_ERROR, reason='info_read_failed', retry_at=retry_at
                ))
                continue

            message = info.message or ''
            values = {
                'message': message,
            }

            if message.endswith(' '):
                # 3DS convention: the user asked us to skip their profile.
                values['message'] = ''
                updates.append(ProfileUpdate(
                    entry.friend_code, values, HEALTH_DEGRADED, 'comment_space', refresh_eligible, 0
                ))
                continue

            values['favorite_game'] = info.game_key.title_id if info.game_key else 0
            relationship = relationships.get(entry.pid)
            if relationship is None:
                # Store what we have; profile fields wait for a later cycle.
                updates.append(ProfileUpdate(
                    entry.friend_code, values, refresh_eligible=refresh_eligible, retry_at=0
                ))
                continue

            # Hack: get_friend_mii rejects a real friend_code here.
            relationship.friend_code = 0
            mii_batch.append((entry, values, relationship, refresh_eligible))

        if not mii_batch:
            continue

        try:
            miis = await _fetch_miis(friends_client, [relationship for _, _, relationship, _ in mii_batch])
        except Exception as e:
            for entry, values, _, refresh_eligible in mii_batch:
                print(f'Failed to get Mii for {entry.friend_code}: {e}')
                updates.append(ProfileUpdate(
                    entry.friend_code, values, HEALTH_ERROR, 'mii_read_failed', refresh_eligible, retry_at
                ))
            continue

        mii_by_pid = {mii.pid: mii for mii in miis if mii.pid is not None}
        for entry, values, _, refresh_eligible in mii_batch:
            mii = mii_by_pid.get(entry.pid)
            if mii is None:
                print(f'No Mii returned for {entry.friend_code}')
                updates.append(ProfileUpdate(
                    entry.friend_code, values, HEALTH_ERROR, 'mii_read_failed', refresh_eligible, retry_at
                ))
                continue

            try:
                obj = MiiData()
                obj.decode(obj.convert(io.BytesIO(mii.mii.mii_data)))
                values['username'] = mii.mii.name
                values['mii'] = obj.mii_studio()['data']
            except Exception as e:
                print(f'Failed to decode Mii for {entry.friend_code}: {e}')
                updates.append(ProfileUpdate(
                    entry.friend_code, values, HEALTH_ERROR, 'mii_decode_failed', refresh_eligible, retry_at
                ))
                continue
            updates.append(ProfileUpdate(
                entry.friend_code, values, refresh_eligible=refresh_eligible, retry_at=0
            ))

    return updates


def apply_profile_updates(session, network: NetworkType, updates: list[ProfileUpdate]) -> None:
    """Writes profile values and health markers in the caller's transaction."""
    now = time.time()
    for update_item in updates:
        has_values = bool(update_item.values)
        values = dict(update_item.values)
        values['profile_retry_at'] = update_item.retry_at
        if has_values:
            values['last_accessed'] = now
            if update_item.refresh_eligible:
                values['refresh_requested'] = False
                values['last_refresh'] = now
        session.execute(
            update(Friend)
            .where(Friend.friend_code == update_item.friend_code)
            .where(Friend.network == network)
            .values(**values)
        )
        if update_item.health != HEALTH_OK:
            set_console_health(session, update_item.friend_code, network, update_item.health, update_item.reason)
