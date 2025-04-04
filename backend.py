# Created by Deltaion Lee (MCMi460) on Github
# Based from NintendoClients' `examples/3ds/friends.py`
import datetime

from nintendo import nasc
from nintendo.nex import backend, friends, settings
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session
import anyio, sys, argparse

from database import start_db_time, get_db_url, Friend, DiscordFriends

from api.private import NINTENDO_NEX_PASSWORD, NINTENDO_SERIAL_NUMBER, NINTENDO_MAC_ADDRESS, NINTENDO_DEVICE_CERT, NINTENDO_DEVICE_NAME, NINTENDO_REGION, NINTENDO_LANGUAGE, PRETENDO_NEX_PASSWORD, NINTENDO_PID, NINTENDO_PID_HMAC, PRETENDO_SERIAL_NUMBER, PRETENDO_MAC_ADDRESS, PRETENDO_DEVICE_CERT, PRETENDO_DEVICE_NAME, PRETENDO_REGION, PRETENDO_LANGUAGE, PRETENDO_PID, PRETENDO_PID_HMAC
from api import *
from api.love2 import *
from api.networks import NetworkType, InvalidNetworkError

import logging
logging.basicConfig(level=logging.INFO)

delay = 2
backend_start_time = time.time()
scrape_only = False

network: NetworkType = NetworkType.NINTENDO

class QueriedFriend:
	""" A QueriedFriend holds the friend code, PID, and last access time for a given Friend. """

	# The friend code of this user, as a string.
	friend_code: str

	# The principal ID (a.k.a. PID) of this user.
	pid: int

	# The last access date of this user, per database.
	last_accessed: int

	def __init__(self, given_friend: Friend):
		self.friend_code = given_friend.friend_code
		self.pid = friend_code_to_principal_id(given_friend.friend_code)
		self.last_accessed = given_friend.last_accessed


async def main():
	engine = create_engine(get_db_url())
	session = Session(engine)

	while True:
		time.sleep(1)
		print('Grabbing new friends...')

		queried_friends = session.scalars(select(Friend).where(Friend.network == network)).all()
		if not queried_friends:
			continue

		all_friends: [QueriedFriend] = list(map(QueriedFriend, queried_friends))

		for i in range(0, len(all_friends), 100):
			current_rotation = all_friends[i:i+100]

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
						await main_friends_loop(friends_client, session, current_rotation)

			except Exception as e:
				print('An error occurred!\n%s' % e)
				print(traceback.format_exc())
				time.sleep(2)

		if scrape_only:
			print('Done scraping.')
			break


async def main_friends_loop(friends_client: friends.FriendsClientV1, session: Session, current_rotation: list[QueriedFriend]):
	# If we recently started, update our comment, and remove existing friends.
	if time.time() - backend_start_time < 30:
		time.sleep(delay)
		await friends_client.update_comment('3dsrpc.com')

	# Synchronize our current roster of friends.
	#
	# We expect the remote NEX implementation to remove all existing
	# relationships, and replace them with the 100 PIDs specified.
	# As of writing, both Nintendo and Pretendo support this.
	all_friend_pids: list[int] = [ f.pid for f in current_rotation ]
	await friends_client.sync_friend(0, all_friend_pids, [])

	time.sleep(delay)

	# Query all successful friends.
	current_friends_list: [friends.FriendRelationship] = await friends_client.get_all_friends()
	current_friend_pids: [int] = [ f.pid for f in current_friends_list ]

	# Determine which remote friends failed to add, and thus have unfriended us.
	added_friends: [QueriedFriend] = []
	for current_friend in current_rotation:
		current_pid = current_friend.pid

		if current_pid in current_friend_pids:
			added_friends.append(current_friend)
			continue

		# This user must have removed us.
		# Remove this friend code from both our tracked network friends and Discord friend codes.
		session.execute(delete(Friend).where(Friend.friend_code == current_friend.friend_code).where(Friend.network == network))
		session.execute(delete(DiscordFriends).where(
			DiscordFriends.friend_code == current_friend.friend_code,
			DiscordFriends.network == network)
		)
		session.commit()

	if len(added_friends) == 0:
		# All of our friends removed us, so there's no more work to be done.
		return

	time.sleep(delay)

	# Query the presences of all of our added friends.
	# Only online users will have their presence returned.
	tracked_presences: [friends.FriendPresence] = await friends_client.get_friend_presence(current_friend_pids)
	online_user_pids: [int] = []

	for game in tracked_presences:
		# Set all to offline if scraping
		if scrape_only:
			break

		online_user_pids.append(game.pid)
		game_description = game.presence.game_mode_description
		if not game_description:
			game_description = ''
		joinable = bool(game.presence.join_availability_flag)

		friend_code = str(principal_id_to_friend_code(game.pid)).zfill(12)
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
		session.commit()

	# Otherwise, if we have no presence data, this user must be offline.
	for offline_user in [ h for h in current_friend_pids if not h in online_user_pids ]:
		friend_code = str(principal_id_to_friend_code(offline_user)).zfill(12)
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
	for current_friend in added_friends:
		# As this is a time-heavy task, only update if necessary.
		work = False
		if time.time() - current_friend.last_accessed <= 600000 or scrape_only:
			work = True

		if not work:
			continue

		time.sleep(delay)

		try:
			current_info = await friends_client.get_friend_persistent_info([current_friend.pid,])
		except:
			continue
		comment = current_info[0].message
		favorite_game = 0
		username = ''
		face = ''
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

			user_mii: [friends.FriendMii] = await friends_client.get_friend_mii([queried_relationship,])
			username = user_mii[0].mii.name
			mii_data = user_mii[0].mii.mii_data
			obj = MiiData()
			obj.decode(obj.convert(io.BytesIO(mii_data)))
			face = obj.mii_studio()['data']

			# Get user's favorite game
			favorite_game = current_info[0].game_key.title_id
		else:
			comment = ''

		session.execute(
			update(Friend)
			.where(Friend.friend_code == current_friend.friend_code)
			.where(Friend.network == network)
			.values(
				username=username,
				message=comment,
				mii=face,
				favorite_game=favorite_game
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