# Created by Deltaion Lee (MCMi460) on Github
# Based from NintendoClients' `examples/3ds/friends.py`
import datetime
import traceback
from typing import List, Optional
from datetime import datetime as dt

from nintendo import nasc
from nintendo.nex import backend, friends, settings
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session
import anyio, sys, argparse, time

from database import start_db_time, get_db_url, Friend, DiscordFriends

# Time in seconds before a user is considered "offline"
OFFLINE_THRESHOLD = 30 * 60  # 30 minutes
OFFLINE_CHECK_INTERVAL = 10  # Check offline users every 10 loops

# Delay table with descriptive names for each delay purpose
DELAY_TABLE = {
    "INITIAL": 2,         # Initial delay after startup (comment update)
    "REMOVE_FRIEND": 2,   # Delay between removing friends in batch
    "ADD_FRIEND": 2,      # Delay between adding friends
    "SYNC_FRIENDS": 2,    # Delay after sync operation
    "GET_PRESENCE": 2,    # Delay before querying presence
    "UPDATE_INFO": 2,     # Delay before getting persistent info
    "MINIMUM_LOOP": 2,    # Minimum delay at end of each loop cycle
}

# How many consecutive full-loop checks a friend may be absent from the remote friendlist
# before we consider them to have unfriended us. Protects against interrupted syncs incorrectly dropping friends (which would show
# up as "Not tracked" on the consoles page).
MISSING_STRIKE_LIMIT = 10

# Consecutive loops each friend has been absent from the remote friendlist.
_missing_strikes: dict[tuple, int] = {}

# Newly registered consoles are inserted with `online=False`, so without this
# they'd be classified as "offline" and only enter the rotation every
# OFFLINE_CHECK_INTERVAL loops.
NEW_USER_PRIORITY_WINDOW = 30 * 60  # 30 minutes since account_creation

# Whether we've already wiped the remote friendlist for this backend run.
_startup_wipe_done: bool = False

from api.private import NINTENDO_NEX_PASSWORD, NINTENDO_SERIAL_NUMBER, NINTENDO_MAC_ADDRESS, NINTENDO_DEVICE_CERT, NINTENDO_DEVICE_NAME, NINTENDO_REGION, NINTENDO_LANGUAGE, PRETENDO_NEX_PASSWORD, NINTENDO_PID, NINTENDO_PID_HMAC, PRETENDO_SERIAL_NUMBER, PRETENDO_MAC_ADDRESS, PRETENDO_DEVICE_CERT, PRETENDO_DEVICE_NAME, PRETENDO_REGION, PRETENDO_LANGUAGE, PRETENDO_PID, PRETENDO_PID_HMAC
from api import *
from api.love2 import *
from api.networks import NetworkType, InvalidNetworkError

import logging
logging.basicConfig(level=logging.INFO)

DEBUG = True
if not DEBUG:
    logging.getLogger('nintendo').setLevel(logging.WARNING)
    logging.getLogger('anynet').setLevel(logging.WARNING)

scrape_only: bool = False

network: NetworkType = NetworkType.NINTENDO

from api.metrics import record_loop_start, record_loop_end, get_backend_metrics, init_db, reset_metrics, update_backend_heartbeat, set_backend_status
from api.networks import NetworkType

# When a game server (e.g. Pretendo) is down, its NASC endpoint returns an HTML or bare error page instead of a form-encoded response.
import nintendo.nasc as _nasc
from anynet import http as _http


class NASCUnavailableError(Exception):
	"""Raised when the NASC server returns a non-form (malformed) response."""

	def __init__(self, status_code: int, body: str):
		self.status_code = status_code
		self.body = body
		super().__init__(
			f"NASC server returned an invalid response (HTTP {status_code}): {body[:200]!r}"
		)


async def _tolerant_nasc_request(self, req):
	req.form = _nasc.encode_form(req.form)
	response = await _http.request(self.url, req, self.context)
	form = _http.formdecode(response.text)

	if form.get("returncd") is None:
		raise NASCUnavailableError(response.status_code, response.text)
	
	# Drop empty fields (formdecode yields None for fields without a value).
	form = {key: value for key, value in form.items() if value is not None}

	response.form = _nasc.decode_form(form)
	return_code = response.form["returncd"].decode()

	if return_code == "null" or int(return_code) != 1:
		raise _nasc.NASCError(response.status_code, response.form)
	
	return response


_nasc.NASCClient.request = _tolerant_nasc_request


def is_network_outage(exc: Exception) -> bool:
	"""True if the exception indicates the game server is down or unreachable."""
	if isinstance(exc, NASCUnavailableError):
		return True
	if isinstance(exc, _nasc.NASCError):
		return exc.return_code == 110
	return isinstance(exc, (OSError, TimeoutError))


class QueriedFriend:
	""" A QueriedFriend holds the friend code, PID, and last access time for a given Friend. """

	# The friend code of this user, as a string.
	friend_code: str

	# The principal ID (a.k.a. PID) of this user.
	pid: int

	# The last access date of this user, per database.
	last_accessed: int
	
	# When the account was created, per database.
	account_creation: int
	
	# Whether the user is currently online
	online: bool
	
	# When the user was last seen online
	last_online: int

	# The friend's username, per database (None until their profile is scraped).
	username: Optional[str]

	def __init__(self, given_friend: Friend):
		self.friend_code = given_friend.friend_code
		self.pid = friend_code_to_principal_id(given_friend.friend_code)
		self.last_accessed = given_friend.last_accessed
		self.account_creation = given_friend.account_creation
		self.online = given_friend.online
		self.last_online = given_friend.last_online
		self.username = given_friend.username


async def main():
	engine = create_engine(get_db_url())
	session = Session(engine)
	
	# Create a simple DB wrapper for metrics module
	class MetricsDB:
		@staticmethod
		def session():
			return session
	metrics_db = MetricsDB()
	
	# Initialize metrics with database
	init_db(metrics_db)
	
	# Reset metrics on startup
	reset_metrics(network)

	# Consecutive loop cycles the game server has been unreachable. Used to
	# keep outage messages concise and calibrate backoff.
	consecutive_outages = 0

	while True:
		time.sleep(1)
		timestamp = dt.now().strftime('%Y-%m-%d %H:%M:%S')

		# End the previous loop's transaction and drop the session's identity map
		# so the SELECT below reads freshly-committed rows (e.g. users added by the
		# web frontend while this backend was already running) instead of stale,
		# cached objects from an earlier loop.
		session.rollback()
		session.expire_all()

		queried_friends = session.scalars(select(Friend).where(Friend.network == network)).all()
		if not queried_friends:
			record_loop_start(0, network)
			record_loop_end(0, network)
			print(f'[{timestamp}] Loop {get_backend_metrics(network)["loop_counter"]}: No friends to process')
			continue

		record_loop_start(len(queried_friends), network)

		all_friends: list[QueriedFriend] = []
		invalid_codes: list[str] = []
		for queried_friend in queried_friends:
			try:
				all_friends.append(QueriedFriend(queried_friend))
			except FriendCodeValidityError as e:
				invalid_codes.append(queried_friend.friend_code)
				print(f'[{timestamp}] Skipping invalid friend code {queried_friend.friend_code} on {network.lower_name()}: {e}')

		if invalid_codes:
			for fc in invalid_codes:
				session.execute(delete(Friend).where(Friend.friend_code == fc).where(Friend.network == network))
				session.execute(delete(DiscordFriends).where(
					DiscordFriends.friend_code == fc,
					DiscordFriends.network == network)
				)
			session.commit()
			print(f'[{timestamp}] Dropped {len(invalid_codes)} invalid friend code(s) from {network.lower_name()}')
		current_time = time.time()
		
		# Split friends into online and offline queues
		online_queue = []
		offline_queue = []
		
		for friend in all_friends:
			if friend.online and (current_time - friend.last_online <= OFFLINE_THRESHOLD):
				online_queue.append(friend)
			elif current_time - friend.account_creation <= NEW_USER_PRIORITY_WINDOW:
				online_queue.append(friend)
			else:
				offline_queue.append(friend)
		
		# Determine which queue to process based on loop counter
		current_metrics = get_backend_metrics(network)
		is_full_loop = current_metrics["loop_counter"] % OFFLINE_CHECK_INTERVAL == 0
		if is_full_loop:
			current_rotation = all_friends
			print(f'[{timestamp}] Loop {current_metrics["loop_counter"]}: Checking all {len(all_friends)} users (online: {len(online_queue)}, offline: {len(offline_queue)})')
		else:
			current_rotation = online_queue
			print(f'[{timestamp}] Loop {current_metrics["loop_counter"]}: Checking {len(online_queue)} online users (offline: {len(offline_queue)})')
		
		users_processed_this_loop = len(current_rotation)

		if not current_rotation:
			record_loop_end(0, network)
			continue

		outage_detected = False

		for i in range(0, len(current_rotation), 100):
			batch = current_rotation[i:i+100]

			try:
				client = nasc.NASCClient()

				# TODO: This should be separate between networks.
				# E.g. if the friend code was is banned on one network,
				# you'd still be able to keep the friend code for the other network.
				match network:
					case NetworkType.NINTENDO:
						client.set_locale(NINTENDO_REGION, NINTENDO_LANGUAGE)
						client.set_url("nasc.nintendowifi.net")
						PID = NINTENDO_PID
						NEX_PASSWORD = NINTENDO_NEX_PASSWORD
						
						client.set_device(NINTENDO_SERIAL_NUMBER, NINTENDO_MAC_ADDRESS, NINTENDO_DEVICE_CERT, NINTENDO_DEVICE_NAME)
						client.set_user(PID, NINTENDO_PID_HMAC)
					case NetworkType.PRETENDO:
						client.set_locale(PRETENDO_REGION, PRETENDO_LANGUAGE)
						client.set_url("nasc.pretendo.cc")
						client.context.set_authority(None)
						
						PID = PRETENDO_PID
						NEX_PASSWORD = PRETENDO_NEX_PASSWORD
						
						client.set_device(PRETENDO_SERIAL_NUMBER, PRETENDO_MAC_ADDRESS, PRETENDO_DEVICE_CERT, PRETENDO_DEVICE_NAME)
						client.set_user(PID, PRETENDO_PID_HMAC)
					case _:
						raise InvalidNetworkError(f"Network type {network} is not configured for querying")
					
				client.set_title(0x0004013000003202, 20)
				response = await client.login(0x3200)

				s = settings.load('friends')
				s.configure("ridfebb9", 20000)

				async with backend.connect(s, response.host, response.port) as be:
					async with be.login(str(PID), NEX_PASSWORD) as client:
						friends_client = friends.FriendsClientV1(client)

						# Begin our main loop!
						if is_full_loop:
							await full_queue_sync(friends_client, session, batch)
						else:
							await quick_queue(friends_client, session, batch)
						update_backend_heartbeat(network)

				consecutive_outages = 0
				set_backend_status(network, 'up')

			except Exception as e:
				if is_network_outage(e):
					outage_detected = True
					consecutive_outages += 1
					if consecutive_outages == 1:
						set_backend_status(network, 'down')
					print(f'[{timestamp}] {network.lower_name()} is unreachable (attempt {consecutive_outages}): {e}')
				else:
					consecutive_outages = 0
					print('An error occurred!\n%s' % e)
					print(traceback.format_exc())
				update_backend_heartbeat(network)
				await anyio.sleep(DELAY_TABLE["SYNC_FRIENDS"])

		if scrape_only:
			print('Done scraping.')
			break

		record_loop_end(users_processed_this_loop, network)
		timestamp = dt.now().strftime('%Y-%m-%d %H:%M:%S')
		duration = get_backend_metrics(network)["last_loop_duration_seconds"] or 0
		queue_batch_delay = min(300, max(10, users_processed_this_loop))

		# The delay scales with queue size to prevent overwhelming Pretendo:
		#   - Minimum delay: 60 seconds (prevents loops from running too fast for small queues)
		#   - Maximum delay: 300 seconds / 5 minutes (prevents excessive waiting for very large queues)
		if outage_detected:
			print(f"[{timestamp}] {network.lower_name()} is down; retrying in {queue_batch_delay}s")
		else:
			print(f"[{timestamp}] Processed {users_processed_this_loop} users in {duration:.2f}s, applying delay of {queue_batch_delay}s")
		await anyio.sleep(queue_batch_delay)


async def wipe_friends_list(friends_client: friends.FriendsClientV1) -> None:
	"""Remove every friend from the bot's remote friendlist.

	Runs once when the backend starts. A previous run may have been stopped
	mid-sync, leaving stale friends behind that the per-batch sync would have to
	grind through under the batch timeout; clearing them up-front prevents the
	remote friendlist from possibly filling up across restarts.
	"""
	print('Wiping the remote friendlist...')
	with anyio.move_on_after(600) as timeout_scope:
		for attempt in range(3):
			try:
				removables = await friends_client.get_all_friends()
			except Exception as e:
				print(f'Failed to list friends while wiping (attempt {attempt + 1}): {e}')
				await anyio.sleep(DELAY_TABLE["REMOVE_FRIEND"])
				continue

			if not removables:
				print('Remote friendlist already empty')
				return

			removed_count: int = 0
			for friend in removables:
				await anyio.sleep(DELAY_TABLE["REMOVE_FRIEND"])
				try:
					await friends_client.remove_friend_by_principal_id(friend.pid)
					removed_count += 1
				except Exception as e:
					print(f'Failed to remove friend {friend.pid} while wiping: {e}')

			print(f'Wiped {removed_count}/{len(removables)} friends')
			if removed_count == len(removables):
				return
	if timeout_scope.cancelled_caught:
		print('Wipe timed out; remote friendlist may still contain stale friends')


def update_strike(friend_code: str, present: bool, full_loop: bool, add_failed: bool) -> bool:
	"""Update a friend's strike counter from the latest friendlist poll."""
	strike_key = (network, friend_code)
	if present:
		_missing_strikes.pop(strike_key, None)
		return False
	if not full_loop:
		return False
	if add_failed:
		_missing_strikes.pop(strike_key, None)
		return False
	strikes = _missing_strikes.get(strike_key, 0) + 1
	if strikes < MISSING_STRIKE_LIMIT:
		_missing_strikes[strike_key] = strikes
		print(f'{friend_code} absent from friendlist ({strikes}/{MISSING_STRIKE_LIMIT})')
		return False
	_missing_strikes.pop(strike_key, None)
	return True


async def full_queue_sync(friends_client: friends.FriendsClientV1, session: Session, current_rotation: list[QueriedFriend]) -> None:
	"""Full-loop batch processing.

	Every OFFLINE_CHECK_INTERVAL loops we process the whole roster. This must
	reconcile the remote friendlist (add/remove) so presence and profile data
	stay accurate for every tracked friend.
	"""
	# Budget ~6s per user so the full remove+add sync of a Pretendo batch can
	# complete (the intentional delays alone account for ~4s/user). If the
	# timeout fired mid-sync, the un-added tail of the batch was wrongly treated
	# as "unfriended". Minimum 2 minutes, maximum 20 minutes to prevent hangs.
	timeout = min(1200, max(120, 6 * len(current_rotation)))

	# On the first batch after a restart, wipe the entire remote friendlist so a
	# previously interrupted run can't leave stale friends behind and slowly fill
	# up the list across restarts.
	global _startup_wipe_done
	if not _startup_wipe_done:
		_startup_wipe_done = True
		await wipe_friends_list(friends_client)

	with anyio.move_on_after(timeout) as timeout_scope:
		# If we recently started, update our comment.
		if get_backend_metrics(network)["uptime_seconds"] < 30:
			await anyio.sleep(DELAY_TABLE["INITIAL"])
			await friends_client.update_comment('3dsrpc.com')

		print(f'Processing {len(current_rotation)} users with {timeout / 60:.1f} minutes timeout')

		add_errors, current_friends_list = await reconcile_friends(friends_client, current_rotation)

	if timeout_scope.cancelled_caught:
		print(f'Batch timed out after {timeout} seconds')
	
	await refresh_friend_states(friends_client, session, current_rotation, add_errors, full_loop=True, current_friends_list=current_friends_list)


async def quick_queue(friends_client: friends.FriendsClientV1, session: Session, current_rotation: list[QueriedFriend]) -> None:
	"""Quick-loop batch processing for the online-only rotation.

	Compares the remote friendlist against the pids we want to track. If they
	match 100%, we can skip the add/remove calls entirely and go straight to
	presence. Only full loops count friend-strikes.
	"""
	print(f'Processing {len(current_rotation)} users')

	# Reconcile the remote friendlist against what we want to track. When our
	# tracked set fits within the 100-friend cap and hasn't changed, the
	# friendlist already matches, so we skip all add/remove work and go straight
	# to presence. This avoids clearing and re-adding the same accounts every
	# loop (which is especially expensive on Pretendo).
	add_errors, current_friends_list = await reconcile_friends(friends_client, current_rotation)

	await refresh_friend_states(friends_client, session, current_rotation, add_errors, full_loop=False, current_friends_list=current_friends_list)


async def reconcile_friends(friends_client: friends.FriendsClientV1, current_rotation: list[QueriedFriend]) -> tuple[List[tuple], Optional[list[friends.FriendRelationship]]]:
	"""Synchronize the remote friendlist against our current roster.

	By bulk syncing friends, we can remove all existing friends, and then add
	our new friends with only one call. Although both Nintendo and Pretendo
	currently support the bulk `sync_friends` RPC call, Pretendo's
	implementation is not optimized, and overloads their servers.
	"""
	all_friend_pids: List[int] = [f.pid for f in current_rotation]
	add_errors: List[tuple] = []

	current_friends_list = await friends_client.get_all_friends()
	existing_pids = {friend.pid for friend in current_friends_list}
	desired_pids = set(all_friend_pids)

	if existing_pids == desired_pids:
		# Friendlist already matches our tracked set, so no add/remove work is
		# needed and the fetched list can be reused downstream.
		return add_errors, current_friends_list

	if network == NetworkType.PRETENDO:
		# Remove friends we no longer track, add friends that are missing.
		removed_count: int = 0
		for friend in current_friends_list:
			if friend.pid in desired_pids:
				continue
			await anyio.sleep(DELAY_TABLE["REMOVE_FRIEND"])
			try:
				await friends_client.remove_friend_by_principal_id(friend.pid)
				removed_count += 1
			except Exception as e:
				print(f'Failed to remove friend {friend.pid}: {e}')

		if removed_count:
			print(f'Removed {removed_count} departed friend(s)')

		added_count: int = 0
		for friend_pid in all_friend_pids:
			if friend_pid in existing_pids:
				continue
			await anyio.sleep(DELAY_TABLE["ADD_FRIEND"])
			try:
				await friends_client.add_friend_by_principal_id(0, friend_pid)
				added_count += 1
			except Exception as e:
				add_errors.append((friend_pid, e))
				print(f'Failed to add friend {friend_pid}: {e}')

		if added_count or add_errors:
			print(f'Added {added_count}/{len(all_friend_pids)} friends ({len(add_errors)} errors)')
	else:
		# We expect the remote NEX implementation to remove all existing
		# relationships, and replace them with the 100 PIDs specified.
		# This path is currently only for Nintendo.
		try:
			await friends_client.sync_friend(0, all_friend_pids, [])
		except Exception as e:
			print(f'Failed to sync friends: {e}')
		await anyio.sleep(DELAY_TABLE["SYNC_FRIENDS"])

	return add_errors, None


async def refresh_friend_states(friends_client: friends.FriendsClientV1, session: Session, current_rotation: list[QueriedFriend], add_errors: List[tuple], full_loop: bool, current_friends_list: Optional[list[friends.FriendRelationship]] = None) -> None:
	"""Query the friendlist after sync, then update presence and profiles.

	An empty friendlist is almost certainly an outage or a failed sync, not a
	mass unfriend. A later successful poll resets the counters.
	"""
	await anyio.sleep(DELAY_TABLE["MINIMUM_LOOP"])

	# Query all successful friends. A pre-fetched list is reused when the
	# reconcile left the friendlist untouched, avoiding a redundant RPC.
	if current_friends_list is None:
		current_friends_list = await friends_client.get_all_friends()
	current_friend_pids: List[int] = [f.pid for f in current_friends_list]

	# An empty friendlist is almost certainly an outage or a failed sync, not a
	# mass unfriend. A later successful poll resets the counters.
	if not current_friend_pids:
		return

	# Determine which remote friends are confirmed present, and which have been
	# absent long enough to count as having unfriended us.
	added_friends, unfriended_codes = detect_unfriended(session, current_rotation, current_friend_pids, add_errors, full_loop)
	if unfriended_codes:
		print(f'Stopped tracking {len(unfriended_codes)} unfriended users')

	if len(added_friends) == 0:
		# All of our friends removed us, so there's no more work to be done.
		return

	await update_presences(friends_client, session, current_friend_pids)
	await update_profiles(friends_client, session, added_friends, current_friends_list)


def detect_unfriended(session: Session, current_rotation: list[QueriedFriend], current_friend_pids: List[int], add_errors: List[tuple], full_loop: bool) -> tuple[list[QueriedFriend], List[str]]:
	added_friends: List[QueriedFriend] = []
	unfriended_codes: List[str] = []
	failed_add_pids: set[int] = {pid for pid, _ in add_errors}

	for current_friend in current_rotation:
		present = current_friend.pid in current_friend_pids
		if present:
			added_friends.append(current_friend)
		if update_strike(current_friend.friend_code, present, full_loop, current_friend.pid in failed_add_pids):
			unfriended_codes.append(current_friend.friend_code)

	# Stop tracking friends who removed us. We never delete the user's console
	# (`discord_friends`), that is user-managed and may only be removed via the
	# website's Delete button. We just unselect it so the bot stops using it, while the user keeps it listed.
	if unfriended_codes:
		for fc in unfriended_codes:
			session.execute(delete(Friend).where(Friend.friend_code == fc).where(Friend.network == network))
			session.execute(update(DiscordFriends).where(
				DiscordFriends.friend_code == fc,
				DiscordFriends.network == network)
				.values(active=False)
			)
		session.commit()

	return added_friends, unfriended_codes


async def update_presences(friends_client: friends.FriendsClientV1, session: Session, current_friend_pids: List[int]) -> None:
	await anyio.sleep(DELAY_TABLE["GET_PRESENCE"])

	tracked_presences = await friends_client.get_friend_presence(current_friend_pids)
	online_user_pids: List[int] = []

	for game in tracked_presences:
		# Set all to offline if scraping
		if scrape_only:
			break

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
				title_id=game.presence.game_key.title_id,
				upd_id=game.presence.game_key.title_version,
				joinable=joinable,
				game_description=game_description,
				last_online=time.time()
			)
		)

	# Otherwise, if we have no presence data, this user must be offline.
	for offline_user in [h for h in current_friend_pids if not h in online_user_pids]:
		friend_code: str = str(principal_id_to_friend_code(offline_user)).zfill(12)
		session.execute(
			update(Friend)
			.where(Friend.friend_code == friend_code)
			.where(Friend.network == network)
			.values(
				online=False,
				title_id=0,
				upd_id=0
			)
		)
	session.commit()


async def update_profiles(friends_client: friends.FriendsClientV1, session: Session, added_friends: list[QueriedFriend], current_friends_list: list[friends.FriendRelationship]) -> None:
	"""Scrape persistent info (comment, Mii, username, favorite game) for added friends.

	As this is a time-heavy task, only update if necessary. A friend with no
	username yet has never had their profile scraped, so fetch it on the first
	loop they're processed instead of waiting for the `last_accessed` throttle
	(only the backend's own scrape refreshes `last_accessed`; profile
	views/polls must not).
	"""
	pending_updates: List[dict] = []
	for current_friend in added_friends:
		work: bool = False
		if time.time() - current_friend.last_accessed >= 600 or scrape_only or current_friend.username is None:
			work = True

		if not work:
			continue

		await anyio.sleep(DELAY_TABLE["UPDATE_INFO"])

		try:
			current_info = await friends_client.get_friend_persistent_info([current_friend.pid,])
		except Exception as e:
			print(f'Failed to get persistent info for {current_friend.friend_code}: {e}')
			continue
		comment: str = current_info[0].message
		favorite_game: int = 0
		username: str = ''
		face: str = ''
		if not comment.endswith(' '):
			# TODO(MCMi460): I just do not understand what I'm doing wrong with get_friend_mii_list.
			# The docs do not specify much about usage or parameters.
			# And no matter how many trials I do with varying inputs, nothing works - they all return Core::BufferOverflow.
			# I will not give up, but until I figure it out, the slower method (get_friend_mii)
			# will have to do.
			#
			# Get user's mii + username from mii

			# TODO(spotlightishere): This is a mess. Why does `friend_code = 0` prevent a conversion error?
			queried_relationship = [r for r in current_friends_list if r.pid == current_friend.pid][0]
			queried_relationship.friend_code = 0

			user_mii: list[friends.FriendMii] = await friends_client.get_friend_mii([queried_relationship,])
			username = user_mii[0].mii.name
			mii_data = user_mii[0].mii.mii_data
			obj = MiiData()
			obj.decode(obj.convert(io.BytesIO(mii_data)))
			face = obj.mii_studio()['data']

			# Get user's favorite game
			favorite_game = current_info[0].game_key.title_id
		else:
			comment = ''

		pending_updates.append({
			'friend_code': current_friend.friend_code,
			'username': username,
			'message': comment,
			'mii': face,
			'favorite_game': favorite_game
		})

	# Batch commit all updates
	for upd in pending_updates:
		session.execute(
			update(Friend)
			.where(Friend.friend_code == upd['friend_code'])
			.where(Friend.network == network)
			.values(
				username=upd['username'],
				message=upd['message'],
				mii=upd['mii'],
				favorite_game=upd['favorite_game'],
				last_accessed=time.time()
			)
		)
	session.commit()


if __name__ == '__main__':
	try:
		parser = argparse.ArgumentParser()
		parser.add_argument('-n', '--network', choices=[member.lower_name() for member in NetworkType], required=True)
		args = parser.parse_args()

		network = NetworkType[args.network.upper()]

		start_db_time(datetime.datetime.now(), network)
		anyio.run(main)
	except (KeyboardInterrupt, Exception) as e:
		if network is not None:
			start_db_time(None, network)
		print(e)