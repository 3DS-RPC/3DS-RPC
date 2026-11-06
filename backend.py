# Created by Deltaion Lee (MCMi460) on Github
# Based from NintendoClients' `examples/3ds/friends.py`
import argparse
import logging
import time
import traceback
from datetime import UTC
from datetime import datetime as dt

import anyio
from nintendo.nex import friends
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from api import scheduler
from api.love import principal_id_to_friend_code
from api.metrics import (
	advance_loop_progress,
	begin_cycle_progress,
	flush_loop_progress,
	get_backend_metrics,
	init_db,
	record_loop_end,
	record_loop_start,
	reset_metrics,
	set_backend_status,
	skip_loop_progress,
	update_backend_heartbeat,
)
from api.networks import NetworkType
from api.settings import COMMENT_SECONDS, FRIEND_LIST_SECONDS, FRIEND_OP_SECONDS
from database import DiscordFriends, get_db_url, start_db_time
from worker.config import WorkerConfig
from worker.failures import FailureClass, classify_error, describe_failure
from worker.pace import Pacer
from worker.presence import (
	apply_profile_updates,
	fetch_profile_updates,
	update_presences,
)
from worker.session import FriendsConnection

logging.basicConfig(level=logging.INFO)

_startup_comment_done: bool = False

network: NetworkType = NetworkType.NINTENDO


async def main():
	engine = create_engine(get_db_url())
	session = Session(engine)

	class MetricsDB:
		@staticmethod
		def session():
			return session
	metrics_db = MetricsDB()

	init_db(metrics_db)
	reset_metrics(network)

	# Consecutive outage cycles, for log messages.
	consecutive_outages = 0

	config = WorkerConfig.from_env()
	connection = FriendsConnection(network)
	last_idle_log = 0.0

	while True:
		timestamp = dt.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M:%S')

		# Drop the previous transaction and identity map so fresh rows are visible.
		session.rollback()
		session.expire_all()

		entries, invalid_codes = scheduler.load_entries(session, network)
		if invalid_codes:
			invalid_set = set(invalid_codes)
			for fc in invalid_codes:
				scheduler.forget_friend(session, network, fc)
				session.execute(delete(DiscordFriends).where(
					DiscordFriends.friend_code == fc,
					DiscordFriends.network == network)
				)
				print(f'[{timestamp}] Skipping invalid friend code {fc} on {network.lower_name()}')
			session.commit()
			entries = [entry for entry in entries if entry.friend_code not in invalid_set]
			print(f'[{timestamp}] Dropped {len(invalid_codes)} invalid friend code(s) from {network.lower_name()}')

		now = int(time.time())
		remote = scheduler.load_remote(session, network)
		plan = scheduler.select(entries, remote, now, config.scheduler_config)

		if not entries and not remote:
			update_backend_heartbeat(network)
			now_ts = time.time()
			if now_ts - last_idle_log >= 300:
				last_idle_log = now_ts
				print(f'[{timestamp}] No friends to process')
			await anyio.sleep(config.loop_delay)
			continue

		if plan.is_empty:
			# Nothing due and the window already matches: no server contact needed.
			update_backend_heartbeat(network)
			now_ts = time.time()
			if now_ts - last_idle_log >= 300:
				last_idle_log = now_ts
				fast_in_window = sum(
					1 for entry in plan.window
					if scheduler.lane(entry, now, config.scheduler_config) == 'fast'
				)
				print(f'[{timestamp}] Nothing due ({len(plan.window)} in window: '
					f'{fast_in_window} fast / {len(plan.window) - fast_in_window} slow)')
			await anyio.sleep(config.loop_delay)
			continue

		record_loop_start(len(plan.poll), network)

		if plan.poll:
			begin_cycle_progress(len(plan.fast), len(plan.slow), network)

		users_processed = 0
		try:
			if connection.is_stale:
				await connection.close()
			if not connection.is_connected:
				await connection.connect()
			assert connection.client is not None
			users_processed = await run_cycle(
				connection.client,
				session,
				network,
				plan,
				now,
				config.scheduler_config,
				config.pace_config,
				config.startup_delay,
			)
			consecutive_outages = 0
			config.backoff_config.reset()
			set_backend_status(network, 'up')
			update_backend_heartbeat(network)
		except Exception as e:
			await connection.close()
			session.rollback()
			record_loop_end(0, network)

			failure = classify_error(e)
			if failure is FailureClass.FATAL:
				print(f'[{timestamp}] {network.lower_name()} hit a fatal error: {describe_failure(e)}')
				print(traceback.format_exc())
				raise

			if failure is FailureClass.OUTAGE:
				consecutive_outages += 1
				set_backend_status(network, 'down')
				label = f'{network.lower_name()} is unreachable (attempt {consecutive_outages})'
			else:
				consecutive_outages = 0
				label = f'{network.lower_name()} {failure.value} failure'
				print(f'An error occurred!\n{e}')
				print(traceback.format_exc())

			delay = config.backoff_config.next_delay()
			print(f'[{timestamp}] {label}; retrying in {delay:.1f}s: {describe_failure(e)}')
			update_backend_heartbeat(network)
			await anyio.sleep(delay)
			continue

		record_loop_end(users_processed, network)
		timestamp = dt.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M:%S')
		duration = (get_backend_metrics(network) or {}).get("last_loop_duration_seconds") or 0
		print(f"[{timestamp}] Processed {users_processed} users in {duration:.2f}s ({len(plan.window)} in window, {len(plan.fast)} fast / {len(plan.slow)} slow due)")
		await anyio.sleep(config.loop_delay)


async def run_cycle(friends_client: friends.FriendsClientV1, session: Session, network: NetworkType, plan: scheduler.Plan, now: int, config: scheduler.Config, pacer: Pacer, startup_delay: float) -> int:
	"""Applies one window plan: reconcile, poll what is due, persist state."""
	global _startup_comment_done
	if not _startup_comment_done:
		await anyio.sleep(startup_delay)
		try:
			with anyio.fail_after(COMMENT_SECONDS):
				await friends_client.update_comment('3dsrpc.com')
			_startup_comment_done = True
		except Exception as e:
			print(f'Failed to update comment: {e}')

	with anyio.fail_after(FRIEND_LIST_SECONDS):
		actual = await friends_client.get_all_friends()
	remote_read_complete = bool(actual)
	add_errors, current_friends = await reconcile_friends(friends_client, network, plan.window, actual, pacer)
	present_pids = {friend.pid for friend in current_friends}
	poll_time = int(time.time())
	processed = 0

	if plan.poll:
		fast_pids = {entry.pid for entry in plan.fast}
		lanes = {
			entry.pid: ('fast' if entry.pid in fast_pids else 'slow')
			for entry in plan.poll
		}
		failed_add_pids = {pid for pid, _ in add_errors}
		polled_present = [entry for entry in plan.poll if entry.pid in present_pids]
		confirmed_absent = [
			entry for entry in plan.poll
			if entry.pid not in present_pids and entry.pid not in failed_add_pids
		]
		skipped = [entry for entry in plan.poll if entry.pid in failed_add_pids]

		# Profile RPCs run before writes so the cycle can commit once without a SQLite lock.
		profile_updates = await fetch_profile_updates(friends_client, polled_present, current_friends)

		if polled_present:
			await update_presences(
				friends_client,
				session,
				network,
				[entry.pid for entry in polled_present],
				rotation_pids={entry.pid for entry in polled_present},
				lanes=lanes,
			)

		# Count polled/absent users; add failures are skipped.
		for lane_name in ('fast', 'slow'):
			if remote_read_complete:
				count = sum(1 for entry in confirmed_absent if lanes.get(entry.pid) == lane_name)
				if count:
					advance_loop_progress(network, count, mode=lane_name)
			count = sum(1 for entry in skipped if lanes.get(entry.pid) == lane_name)
			if count:
				skip_loop_progress(network, count, mode=lane_name)

		apply_profile_updates(session, network, profile_updates)
		processed = len(polled_present) + len(confirmed_absent) if remote_read_complete else 0
		if skipped:
			print(f'Skipped {len(skipped)} user(s): friend add failed')
		scheduler.record_poll_results(
			session,
			network,
			plan.poll,
			present_pids,
			failed_add_pids,
			now,
			poll_time,
			config,
			remote_read_complete,
		)
		flush_loop_progress(network)

	if not remote_read_complete:
		print('Remote friend list read was empty; keeping the previous window and due times')
		session.commit()
		return processed

	# Persist the actual remote list for the next plan.
	remote = build_remote_map(plan.window, current_friends)
	scheduler.save_remote(session, network, remote, poll_time)
	session.commit()

	return processed


def build_remote_map(window, current_friends) -> dict[str, int]:
	"""Maps friend codes to PIDs for the remote list."""
	desired_codes = {entry.pid: entry.friend_code for entry in window}
	remote = {}
	for friend in current_friends:
		friend_code = desired_codes.get(friend.pid)
		if friend_code is None:
			try:
				friend_code = str(principal_id_to_friend_code(friend.pid)).zfill(12)
			except Exception:
				continue
		remote[friend_code] = friend.pid
	return remote


async def _pace(pacer: Pacer) -> None:
	wait = pacer.reserve(time.monotonic())
	if wait > 0:
		await anyio.sleep(wait)


async def reconcile_friends(friends_client: friends.FriendsClientV1, network: NetworkType, desired: list[scheduler.Entry], actual: list[friends.FriendRelationship], pacer: Pacer) -> tuple[list[tuple], list[friends.FriendRelationship]]:
	"""Applies the delta to the remote friend list."""
	desired_pids = [entry.pid for entry in desired]
	desired_set = set(desired_pids)
	existing_pids = {friend.pid for friend in actual}

	if existing_pids == desired_set:
		return [], actual

	if network == NetworkType.PRETENDO:
		return await _reconcile_pretendo(friends_client, desired, actual, desired_set, existing_pids, pacer)
	return await _reconcile_nintendo(friends_client, desired, desired_pids)


async def _reconcile_pretendo(friends_client, desired, actual, desired_set, existing_pids, pacer) -> tuple[list[tuple], list[friends.FriendRelationship]]:
	add_errors: list[tuple] = []

	removed = 0
	for friend in actual:
		if friend.pid in desired_set:
			continue
		await _pace(pacer)
		try:
			with anyio.fail_after(FRIEND_OP_SECONDS):
				await friends_client.remove_friend_by_principal_id(friend.pid)
			pacer.on_success()
			removed += 1
		except Exception as e:
			pacer.on_pressure()
			print(f'Failed to remove friend {friend.pid}: {e}')
	if removed:
		print(f'Removed {removed} departed friend(s)')

	added = 0
	for entry in desired:
		if entry.pid in existing_pids:
			continue
		await _pace(pacer)
		try:
			with anyio.fail_after(FRIEND_OP_SECONDS):
				await friends_client.add_friend_by_principal_id(0, entry.pid)
			pacer.on_success()
			added += 1
		except Exception as e:
			pacer.on_pressure()
			add_errors.append((entry.pid, e))
			print(f'Failed to add friend {entry.friend_code}: {e}')
	if added or add_errors:
		print(f'Added {added}/{len(desired)} friends ({len(add_errors)} errors)')

	if removed or added:
		with anyio.fail_after(FRIEND_LIST_SECONDS):
			actual = await friends_client.get_all_friends()

	return add_errors, actual


async def _reconcile_nintendo(friends_client, desired, desired_pids) -> tuple[list[tuple], list[friends.FriendRelationship]]:
	sync_failed = False
	try:
		with anyio.fail_after(FRIEND_OP_SECONDS):
			await friends_client.sync_friend(0, desired_pids, [])
	except Exception as e:
		sync_failed = True
		print(f'Failed to sync friends: {e}')

	with anyio.fail_after(FRIEND_LIST_SECONDS):
		actual = await friends_client.get_all_friends()
	add_errors: list[tuple] = []
	if sync_failed:
		present = {friend.pid for friend in actual}
		add_errors = [(entry.pid, None) for entry in desired if entry.pid not in present]
	return add_errors, actual


if __name__ == '__main__':
	try:
		parser = argparse.ArgumentParser()
		parser.add_argument('-n', '--network', choices=[member.lower_name() for member in NetworkType], required=True)
		args = parser.parse_args()

		network = NetworkType[args.network.upper()]

		start_db_time(dt.now(UTC), network)
		anyio.run(main)
	except (KeyboardInterrupt, Exception) as e:
		if network is not None:
			start_db_time(None, network)
		print(e)
