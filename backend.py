# Created by Deltaion Lee (MCMi460) on Github
# Based from NintendoClients' `examples/3ds/friends.py`
import datetime
import traceback
from typing import List
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

delay: int = 2
scrape_only: bool = False

network: NetworkType = NetworkType.NINTENDO

from api.metrics import record_loop_start, record_loop_end, get_backend_metrics, init_db, reset_metrics
from api.networks import NetworkType

class QueriedFriend:
	""" A QueriedFriend holds the friend code, PID, and last access time for a given Friend. """

	# The friend code of this user, as a string.
	friend_code: str

	# The principal ID (a.k.a. PID) of this user.
	pid: int

	# The last access date of this user, per database.
	last_accessed: int
	
	# Whether the user is currently online
	online: bool
	
	# When the user was last seen online
	last_online: int

	def __init__(self, given_friend: Friend):
		self.friend_code = given_friend.friend_code
		self.pid = friend_code_to_principal_id(given_friend.friend_code)
		self.last_accessed = given_friend.last_accessed
		self.online = given_friend.online
		self.last_online = given_friend.last_online


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

	while True:
		time.sleep(1)
		timestamp = dt.now().strftime('%Y-%m-%d %H:%M:%S')
		
		queried_friends = session.scalars(select(Friend).where(Friend.network == network)).all()
		if not queried_friends:
			record_loop_start(0, network)
			record_loop_end(0, network)
			print(f'[{timestamp}] Loop {get_backend_metrics(network)["loop_counter"]}: No friends to process')
			continue

		record_loop_start(len(queried_friends), network)

		all_friends: list[QueriedFriend] = list(map(QueriedFriend, queried_friends))
		current_time = time.time()
		
		# Split friends into online and offline queues
		online_queue = []
		offline_queue = []
		
		for friend in all_friends:
			if friend.online and (current_time - friend.last_online <= OFFLINE_THRESHOLD):
				online_queue.append(friend)
			else:
				offline_queue.append(friend)
		
		# Determine which queue to process based on loop counter
		current_metrics = get_backend_metrics(network)
		if current_metrics["loop_counter"] % OFFLINE_CHECK_INTERVAL == 0:
			current_rotation = all_friends
			print(f'[{timestamp}] Loop {current_metrics["loop_counter"]}: Checking all {len(all_friends)} users (online: {len(online_queue)}, offline: {len(offline_queue)})')
		else:
			current_rotation = online_queue
			print(f'[{timestamp}] Loop {current_metrics["loop_counter"]}: Checking {len(online_queue)} online users (offline: {len(offline_queue)})')
		
		users_processed_this_loop = len(current_rotation)

		if not current_rotation:
			record_loop_end(0, network)
			continue

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
						await main_friends_loop(friends_client, session, batch)

			except Exception as e:
				print('An error occurred!\n%s' % e)
				print(traceback.format_exc())
				await anyio.sleep(2)

		if scrape_only:
			print('Done scraping.')
			break

		record_loop_end(users_processed_this_loop, network)
		timestamp = dt.now().strftime('%Y-%m-%d %H:%M:%S')
		duration = get_backend_metrics(network)["last_loop_duration_seconds"] or 0
		delay = min(300, max(60, users_processed_this_loop))

		# The delay scales with queue size to prevent overwhelming Pretendo:
		#   - Minimum delay: 60 seconds (prevents loops from running too fast for small queues)
		#   - Maximum delay: 300 seconds / 5 minutes (prevents excessive waiting for very large queues)
		print(f"[{timestamp}] Processed {users_processed_this_loop} users in {duration:.2f}s, applying delay of {delay}s")
		await anyio.sleep(delay)


async def main_friends_loop(friends_client: friends.FriendsClientV1, session: Session, current_rotation: list[QueriedFriend]):
	# TODO:(Phoenix): Assumes 3s per user (300s for 100 users). May need to increase to 6s/user (10 min per batch)
	# If timeout is exceeded, the batch ends early (may stop mid-batch) to prevent hangs
	timeout = 3 * len(current_rotation)
	with anyio.move_on_after(timeout) as timeout_scope:
		# If we recently started, update our comment, and remove existing friends.
		if get_backend_metrics(network)["uptime_seconds"] < 30:
			await anyio.sleep(delay)
			await friends_client.update_comment('3dsrpc.com')
			
		print(f'Processing {len(current_rotation)} users with {timeout / 60:.1f} minutes timeout')
		if get_backend_metrics(network)["uptime_seconds"] < 30:
			await anyio.sleep(delay)
			await friends_client.update_comment('3dsrpc.com')
		
		# Synchronize our current roster of friends.
		# By bulk syncing friends, we can remove all existing friends,
		# and then add our new friends with only one call.
		#
		# Although both Nintendo and Pretendo currently support
		# the bulk `sync_friends` RPC call, Pretendo's
		# implementation is not optimized, and overloads their servers.
		all_friend_pids: List[int] = [f.pid for f in current_rotation]
		if network == NetworkType.PRETENDO:
			# Clear our current, registered friends.
			removables = await friends_client.get_all_friends()
			removed_count: int = 0
			for friend in removables:
				await anyio.sleep(delay)
				try:
					await friends_client.remove_friend_by_principal_id(friend.pid)
					removed_count += 1
				except Exception as e:
					print(f'Failed to remove friend {friend.pid}: {e}')

			print(f'Removed {removed_count}/{len(removables)} friends')

			# Individually add all pending friend PIDs.
			added_count: int = 0
			add_errors: List[tuple] = []
			for friend_pid in all_friend_pids:
				await anyio.sleep(delay)
				try:
					await friends_client.add_friend_by_principal_id(0, friend_pid)
					added_count += 1
				except Exception as e:
					add_errors.append((friend_pid, e))
					print(f'Failed to add friend {friend_pid}: {e}')

			if add_errors:
				print(f'Added {added_count}/{len(all_friend_pids)} friends ({len(add_errors)} errors)')
		else:
			# We expect the remote NEX implementation to remove all existing
			# relationships, and replace them with the 100 PIDs specified.
			# This path is currently only for Nintendo.
			try:
				await friends_client.sync_friend(0, all_friend_pids, [])
			except Exception as e:
				print(f'Failed to sync friends: {e}')
			raise

	if timeout_scope.cancelled_caught:
		print(f'Batch timed out after {timeout} seconds')
	
	await anyio.sleep(delay)

	# Query all successful friends.
	current_friends_list = await friends_client.get_all_friends()
	current_friend_pids: List[int] = [f.pid for f in current_friends_list]

	# Determine which remote friends failed to add, and thus have unfriended us.
	added_friends: List[QueriedFriend] = []
	unfriended_codes: List[str] = []
	for current_friend in current_rotation:
		current_pid: int = current_friend.pid

		if current_pid in current_friend_pids:
			added_friends.append(current_friend)
			continue

		# This user must have removed us.
		unfriended_codes.append(current_friend.friend_code)

	# Batch delete unfriended users
	if unfriended_codes:
		for fc in unfriended_codes:
			session.execute(delete(Friend).where(Friend.friend_code == fc).where(Friend.network == network))
			session.execute(delete(DiscordFriends).where(
				DiscordFriends.friend_code == fc,
				DiscordFriends.network == network)
			)
		session.commit()
		print(f'Removed {len(unfriended_codes)} unfriended users')

	if len(added_friends) == 0:
		# All of our friends removed us, so there's no more work to be done.
		return

	await anyio.sleep(delay)

	# Query the presences of all of our added friends.
	# Only online users will have their presence returned.
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

	# Lastly, update all added friend comments, usernames, etc.
	pending_updates: List[dict] = []
	for current_friend in added_friends:
		# As this is a time-heavy task, only update if necessary.
		work: bool = False
		if time.time() - current_friend.last_accessed >= 600 or scrape_only:
			work = True

		if not work:
			continue

		await anyio.sleep(delay)

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
				favorite_game=upd['favorite_game']
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