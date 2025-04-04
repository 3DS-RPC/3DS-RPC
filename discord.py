import sys, pickle
from typing import Optional

from api.love2 import *
from api.private import CLIENT_ID, CLIENT_SECRET, HOST
from api.networks import NetworkType

from sqlalchemy import create_engine, select, update, delete
from sqlalchemy.orm import Session
from database import get_db_url, DiscordFriends, Friend
from database import Discord as DiscordTable
from dataclasses import dataclass
from requests.exceptions import HTTPError

API_ENDPOINT: str = 'https://discord.com/api/v10'

with open('./cache/databases.dat', 'rb') as file:
	t = pickle.loads(file.read())
	titleDatabase = t[0]
	titlesToUID = t[1]

engine = create_engine(get_db_url())

session = Session(engine)

@dataclass
class UserData:
	"""Represents information about the current Discord user's game."""
	friend_code: str
	online: bool
	game: dict
	game_description: str
	username: str
	mii_urls: Optional[dict]
	last_accessed: int


class DiscordSession:
	def retire(self, refresh_token: str):
		session.execute(
			update(DiscordTable)
			.where(DiscordTable.refresh_token == refresh_token)
			.values(
				rpc_session_token=None,
				last_accessed=time.time()
			)
		)
		session.commit()

	def create(self, refresh_token: str, session_token: Optional[str]):
		session.execute(
			update(DiscordTable)
			.where(DiscordTable.refresh_token == refresh_token)
			.values(
				rpc_session_token=session_token,
				last_accessed=time.time()
			)
		)
		session.commit()


class APIClient:
	current_user: DiscordTable

	def __init__(self, current_user: DiscordTable):
		self.current_user = current_user


	def update_presence(self, user_data: UserData, network: NetworkType):
		last_accessed = user_data.last_accessed
		if time.time() - last_accessed <= 30:
			print('[MANUAL RATE LIMITED]')
			return False

		game = user_data.game

		# This ends up in an array of activities - see `data` below.
		activity_data = {
			'type': 0,
			'application_id': CLIENT_ID,
			'assets': {},
			'name': game['name'] + ' (3DS)',
			'platform': 'desktop'
		}

		if game['icon_url']:
			activity_data['assets']['large_image'] = game['icon_url'].replace('/cdn/', HOST + '/cdn/')
			activity_data['assets']['large_text'] = game['name']
		if user_data.game_description:
			activity_data['details'] = user_data.game_description

		# Only add a profile button if the user has enabled it.
		if user_data.username and self.current_user.show_profile_button:
			profile_url = HOST + '/user/' + user_data.friend_code + '/?network=' + network.lower_name()
			activity_data['buttons'] = [{
				'label': 'Profile',
				'url': profile_url
			}]

		# Similarly, only show the user's Mii if enabled.
		if user_data.username and game['icon_url'] and self.current_user.show_small_image:
			# Format as a human-readable friend code (XXXX-XXXX-XXXX).
			user_friend_code = '-'.join(user_data.friend_code[i:i+4] for i in range(0, 12, 4))
			user_network_name = network.lower_name().capitalize()
			small_text_detail = f"{user_friend_code} on {user_network_name}"

			activity_data['assets']['small_image'] = user_data.mii_urls['face']
			activity_data['assets']['small_text'] = small_text_detail

		# Quickly sanitize our activity data by truncating
		# any text exceeding the maximum field limit, 128 characters.
		for key_name in list(activity_data):
			# However, don't modify image assets as they can go over 128.
			if 'image' in key_name:
				continue

			if isinstance(activity_data[key_name], str):
				if len(activity_data[key_name]) > 128:
					activity_data[key_name] = activity_data[key_name][:128]

		data = {'activities': [activity_data]}
		if discord_user.rpc_session_token:
			data['token'] = discord_user.rpc_session_token

		headers = {
			'Authorization': 'Bearer %s' % self.current_user.bearer_token,
			'Content-Type': 'application/json',
		}

		r = requests.post('%s/users/@me/headless-sessions' % API_ENDPOINT, data=json.dumps(data), headers=headers)
		r.raise_for_status()

		response = r.json()
		DiscordSession().create(self.current_user.refresh_token, response['token'])


	def reset_presence(self):
		if not self.current_user.rpc_session_token:
			print('[NO SESSION TO RESET]')
			return False
		elif time.time() - self.current_user.last_accessed <= 30:
			print('[MANUAL RATE LIMITED]')
			return False

		headers = {
			'Authorization': 'Bearer %s' % self.current_user.bearer_token,
			'Content-Type': 'application/json',
		}
		data = {
			'token': self.current_user.rpc_session_token,
		}
		r = requests.post('%s/users/@me/headless-sessions/delete' % API_ENDPOINT, data=json.dumps(data), headers=headers)

		try:
			r.raise_for_status()
		except HTTPError as e:
			# If we encounter 400, we assume that this session has already expired.
			# Let's go ahead and reset the session anyway.
			if e.response.status_code == 400:
				DiscordSession().retire(self.current_user.refresh_token)
			else:
				raise e


	def refresh_bearer(self):
		print('[REFRESH BEARER %s]' % self.current_user.id)
		current_refresh_token = self.current_user.refresh_token
		data = {
			'client_id': '%s' % CLIENT_ID,
			'client_secret': '%s' % CLIENT_SECRET,
			'grant_type': 'refresh_token',
			'refresh_token': current_refresh_token,
		}
		headers = {
			'Content-Type': 'application/x-www-form-urlencoded',
		}
		json_response = requests.post('%s/oauth2/token' % API_ENDPOINT, data=data, headers=headers)
		json_response.raise_for_status()
		response = json_response.json()

		session.execute(
			update(DiscordTable)
			.where(DiscordTable.refresh_token == current_refresh_token)
			.values(
				refresh_token=response['refresh_token'],
				bearer_token=response['access_token'],
				generation_date=time.time()
			)
		)
		session.commit()


	def delete_discord_user(self):
		user_id = self.current_user.id
		print('[DELETING %s]' % user_id)
		session.execute(delete(DiscordTable).where(DiscordTable.id == user_id))
		session.execute(delete(DiscordFriends).where(DiscordFriends.id == user_id))
		session.commit()


delay = 2

while True:
	# First, refresh all OAuth2 bearer tokens if necessary.
	all_users = session.scalars(select(DiscordTable)).all()
	for oauth_user in all_users:
		# We only need to refresh 30 minutes before the token expires.
		if time.time() - oauth_user.generation_date < 604800 - 1800:
			continue

		# Any HTTP error expected here is a 403.
		# This would mean that the refresh token is now invalid,
		# likely due to the user removing access via Discord.
		api_client = APIClient(oauth_user)
		try:
			api_client.refresh_bearer()
			time.sleep(delay * 2)
		except HTTPError:
			api_client.delete_discord_user()

	# Inactive users have removed our bot: the backend removed them
	# from both `friends` and `discord_friends`, but they still
	# have an account (i.e. they exist with credentials in `discord`).
	#
	# Find these users with ongoing sessions and reset their presence.
	inactive_query = (
		select(DiscordTable)
			.outerjoin(DiscordFriends, DiscordFriends.id == DiscordTable.id)
			.filter(DiscordFriends.id == None)
			.filter(DiscordTable.rpc_session_token != None)
	)
	inactive_users = session.scalars(inactive_query).all()

	if len(inactive_users) > 0:
		print('[INACTIVES] Handling %s' % len(inactive_users))

	for inactive_user in inactive_users:
		api_client = APIClient(inactive_user)
		try:
			print('[INACTIVES] Resetting %s' % inactive_user.id)
			api_client.reset_presence()
			time.sleep(delay)
		except HTTPError as e:
			print(f"[INACTIVE RESET FAILURE] {e}")
			# api_client.delete_discord_user()

	time.sleep(delay)

	# Finally, we'll refresh presences for all remaining users.
	discord_friends = session.scalars(select(DiscordFriends).where(DiscordFriends.active)).all()

	if len(discord_friends) < 1:
		time.sleep(delay)
		continue

	for discord_friend in discord_friends:
		# If we've updated this user within the past minute, there's no need to update again.
		discord_user = session.scalar(select(DiscordTable).where(DiscordTable.id == discord_friend.id))
		if time.time() - discord_user.last_accessed < 60:
			continue

		# If this user has no friend data, we cannot process them.
		friend_data: Friend = session.scalar(
			select(Friend)
			.where(Friend.friend_code == discord_friend.friend_code)
			.where(Friend.network == discord_friend.network)
		)
		if not friend_data:
			continue

		api_client = APIClient(discord_user)

		if not friend_data.online:
			# If the user is offline, and they lack an RPC session,
			# there's nothing for us to do.
			if not discord_user.rpc_session_token:
				continue

			# Remove our presence for this now-offline user.
			try:
				print('[FRIENDS] Resetting presence for %s on %s' % (friend_data.friend_code, friend_data.network.lower_name()))
				api_client.reset_presence()
				time.sleep(delay)
			except HTTPError as e:
				print(f"[FRIEND RESET FAILURE] {e}")
				# api_client.delete_discord_user()
			continue

		print('[FRIENDS] Creating RPC for Discord ID %s - %s on %s]' % (discord_friend.id, discord_friend.friend_code, discord_friend.network.lower_name()))
		principal_id = friend_code_to_principal_id(friend_data.friend_code)
		mii = friend_data.mii
		if mii:
			mii = MiiData().mii_studio_url(mii)

		try:
			friend_code = str(principal_id_to_friend_code(principal_id)).zfill(12)
			title_data = getTitle(friend_data.title_id, titlesToUID, titleDatabase)

			discord_user_data = UserData(
				friend_code=friend_code,
				online=friend_data.online,
				game=title_data,
				game_description=friend_data.game_description,
				username=friend_data.username,
				mii_urls=mii,
				last_accessed=friend_data.last_accessed
			)

			api_client.update_presence(discord_user_data, discord_friend.network)
			time.sleep(delay)
		except HTTPError as e:
			print(f"[FRIEND PRESENCE FAILURE] {e}")
			# api_client.delete_discord_user()
		time.sleep(delay)

	# Sleep for 5x our delay.
	time.sleep(delay * 5)
