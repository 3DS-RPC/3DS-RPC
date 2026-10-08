import sys, pickle
import logging
import requests
import json
from typing import Optional

from api.love2 import *
from api.private import CLIENT_ID, CLIENT_SECRET, HOST
from api.networks import NetworkType

from sqlalchemy import create_engine, select, update, delete, and_
from sqlalchemy.orm import Session
from database import get_db_url, DiscordFriends, Friend
from database import Discord as DiscordTable
from dataclasses import dataclass
from functools import lru_cache
from requests.exceptions import HTTPError

API_ENDPOINT: str = 'https://discord.com/api/v10'

# How often to run a scan pass when idle, plus Discord API limits.
LOOP_INTERVAL = 2
REQUEST_TIMEOUT = 30
MAX_RATE_LIMIT_RETRIES = 5   # how many times to retry a 429 using Retry-After
MANUAL_RATE_LIMIT = 30       # seconds between manual presence updates/resets
PRESENCE_MIN_INTERVAL = 60   # seconds before re-issuing a presence update
TOKEN_LIFETIME = 604800      # Discord OAuth2 access token lifetime (7 days)
TOKEN_REFRESH_BUFFER = 1800  # refresh the token 30 minutes before expiry
RESET_RETRY_COOLDOWN = 300   # cap reset attempts for users whose reset keeps failing

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Tracks the last reset_presence attempt per user id so users whose resets keep
# failing aren't hammered with requests on every single loop pass.
_last_reset_attempts: dict = {}

# Reused HTTP session for Discord API calls.
http = requests.Session()


def _retry_after_seconds(response) -> float:
	"""How long Discord wants us to wait before retrying after a 429."""
	retry_after = response.headers.get('Retry-After')
	if retry_after is None:
		try:
			retry_after = str(response.json()['retry_after'])
		except (ValueError, KeyError):
			retry_after = response.headers.get('X-RateLimit-Reset-After')
	return float(retry_after or 0)


def _respect_rate_limit(response) -> None:
	"""Sleep until the current rate-limit bucket resets if we've exhausted it."""
	remaining = response.headers.get('X-RateLimit-Remaining')
	if remaining is None or int(remaining) > 0:
		return

	reset_after = response.headers.get('X-RateLimit-Reset-After')
	if reset_after is None:
		reset = response.headers.get('X-RateLimit-Reset')
		if reset is None:
			return
		reset_after = max(0.0, float(reset) - time.time())
	log.warning('[RATE LIMIT EXHAUSTED] waiting %ss', reset_after)
	time.sleep(float(reset_after))


def discord_post(url: str, bearer_token: str, data: dict, content_type: str = 'application/json'):
	"""POST to the Discord API, honoring its rate-limit response headers."""
	headers = {
		'Content-Type': content_type,
	}
	if bearer_token:
		headers['Authorization'] = 'Bearer %s' % bearer_token
	payload = json.dumps(data) if content_type == 'application/json' else data

	response = None
	for attempt in range(MAX_RATE_LIMIT_RETRIES):
		response = http.post(url, data=payload, headers=headers, timeout=REQUEST_TIMEOUT)
		if response.status_code == 429:
			retry_after = _retry_after_seconds(response)
			log.warning(
				'[RATE LIMITED] %s (attempt %s/%s), waiting %ss',
				url, attempt + 1, MAX_RATE_LIMIT_RETRIES, retry_after
			)
			time.sleep(retry_after)
			continue
		# Pace proactively if we've exhausted this bucket.
		_respect_rate_limit(response)
		return response

	response.raise_for_status()
	return response

with open('./cache/databases.dat', 'rb') as file:
	t = pickle.loads(file.read())
	titleDatabase = t[0]
	titlesToUID = t[1]

@lru_cache(maxsize=None)
def cached_get_title(title_id: str) -> dict:
	"""Cached wrapper around getTitle, which does expensive linear scans
	over the title database on every call. Results are shared and must be
	treated as read-only (callers already share them in the original code)."""
	return getTitle(title_id, titlesToUID, titleDatabase)

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
		if time.time() - self.current_user.last_accessed <= MANUAL_RATE_LIMIT:
			log.info('[MANUAL RATE LIMITED] %s', self.current_user.id)
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
		if self.current_user.rpc_session_token:
			data['token'] = self.current_user.rpc_session_token

		r = discord_post('%s/users/@me/headless-sessions' % API_ENDPOINT, self.current_user.bearer_token, data)
		r.raise_for_status()

		response = r.json()
		DiscordSession().create(self.current_user.refresh_token, response['token'])


	def reset_presence(self):
		if not self.current_user.rpc_session_token:
			log.info('[NO SESSION TO RESET] %s', self.current_user.id)
			return False
		if time.time() - self.current_user.last_accessed <= MANUAL_RATE_LIMIT:
			log.info('[MANUAL RATE LIMITED] %s', self.current_user.id)
			return False

		# Discord doesn't always properly terminate activity sessions anymore,
		# so we explicitly clear them first to avoid ghost/stuck presence before disconnecting
		data = {
			'activities': [],
			'token': self.current_user.rpc_session_token,
		}
		r = discord_post('%s/users/@me/headless-sessions' % API_ENDPOINT, self.current_user.bearer_token, data)

		try:
			r.raise_for_status()
		except HTTPError as e:
			# If we encounter 400, we assume that this session has already expired.
			# Let's go ahead and reset the session anyway.
			if e.response.status_code == 400:
				DiscordSession().retire(self.current_user.refresh_token)
			else:
				raise e

		# Then delete the session
		data = {
			'token': self.current_user.rpc_session_token,
		}
		r = discord_post('%s/users/@me/headless-sessions/delete' % API_ENDPOINT, self.current_user.bearer_token, data)

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
		log.info('[REFRESH BEARER %s]', self.current_user.id)
		current_refresh_token = self.current_user.refresh_token
		data = {
			'client_id': '%s' % CLIENT_ID,
			'client_secret': '%s' % CLIENT_SECRET,
			'grant_type': 'refresh_token',
			'refresh_token': current_refresh_token,
		}
		json_response = discord_post('%s/oauth2/token' % API_ENDPOINT, '', data, 'application/x-www-form-urlencoded')
		json_response.raise_for_status()
		response = json_response.json()

		# Rotate the tokens on the ORM object itself; the flush on commit will
		# persist them and keep the identity map in sync automatically.
		self.current_user.refresh_token = response['refresh_token']
		self.current_user.bearer_token = response['access_token']
		self.current_user.generation_date = time.time()
		session.commit()


	def delete_discord_user(self):
		user_id = self.current_user.id
		log.warning('[DELETING %s]', user_id)
		session.execute(delete(DiscordTable).where(DiscordTable.id == user_id))
		session.execute(delete(DiscordFriends).where(DiscordFriends.id == user_id))
		session.commit()


def run_loop_pass():
	# End the previous loop's transaction and drop the session's identity map
	# so the SELECTs below read freshly-committed rows (e.g. users/consoles added
	# or toggled by the web frontend while this process was already running)
	# instead of stale, cached objects from an earlier loop.
	session.rollback()
	session.expire_all()

	# First, refresh all OAuth2 bearer tokens if necessary.
	all_users = session.scalars(select(DiscordTable)).all()
	for oauth_user in all_users:
		# We only need to refresh 30 minutes before the token expires.
		if time.time() - oauth_user.generation_date < TOKEN_LIFETIME - TOKEN_REFRESH_BUFFER:
			continue

		# A 400/401/403 here means the refresh token is now invalid,
		# likely due to the user removing access via Discord,
		# so we remove the account. Transient 5xx/429 responses are
		# skipped and retried on the next loop instead.
		api_client = APIClient(oauth_user)
		try:
			api_client.refresh_bearer()
		except HTTPError as e:
			# Only a 400/401/403 means the refresh token itself is invalid
			# (e.g. the user revoked access), so only then should we delete the
			# account. Transient 5xx or 429 responses should just skip and
			# retry next loop instead of wiping the user's data.
			if e.response.status_code in (400, 401, 403):
				api_client.delete_discord_user()
			else:
				log.warning('[REFRESH FAILURE] %s', e)

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
		log.info('[INACTIVES] Handling %s', len(inactive_users))

	for inactive_user in inactive_users:
		api_client = APIClient(inactive_user)
		try:
			log.info('[INACTIVES] Resetting %s', inactive_user.id)
			api_client.reset_presence()
		except HTTPError as e:
			log.warning('[INACTIVE RESET FAILURE] %s', e)
			# api_client.delete_discord_user()

	# Finally, we'll refresh presences for all remaining users.
	# Fetch active Discord connections together with their account and friend
	# rows in a single query instead of issuing per-friend SELECTs (N+1).
	discord_rows = session.execute(
		select(DiscordFriends, DiscordTable, Friend)
			.join(DiscordTable, DiscordTable.id == DiscordFriends.id)
			.join(
				Friend,
				and_(
					Friend.friend_code == DiscordFriends.friend_code,
					Friend.network == DiscordFriends.network,
				)
			)
			.where(DiscordFriends.active)
			.where(DiscordTable.rpc_enabled)
	).all()

	if len(discord_rows) < 1:
		time.sleep(LOOP_INTERVAL)
		return

	for discord_friend, discord_user, friend_data in discord_rows:
		# If we've updated this user within the past minute, there's no need to update again.
		if time.time() - discord_user.last_accessed < PRESENCE_MIN_INTERVAL:
			continue

		api_client = APIClient(discord_user)

		if not friend_data.online:
			# If the user is offline, and they lack an RPC session,
			# there's nothing for us to do.
			if not discord_user.rpc_session_token:
				continue

			# Remove our presence for this now-offline user.
			try:
				log.info('[FRIENDS] Resetting presence for %s on %s', friend_data.friend_code, friend_data.network.lower_name())
				api_client.reset_presence()
			except HTTPError as e:
				log.warning('[FRIEND RESET FAILURE] %s', e)
				# api_client.delete_discord_user()
			continue

		log.info('[FRIENDS] Creating RPC for Discord ID %s - %s on %s]', discord_friend.id, discord_friend.friend_code, discord_friend.network.lower_name())
		try:
			principal_id = friend_code_to_principal_id(friend_data.friend_code)
		except FriendCodeValidityError as e:
			log.warning('[FRIENDS] Skipping invalid friend code %s on %s: %s', friend_data.friend_code, friend_data.network.lower_name(), e)
			continue
		mii = friend_data.mii
		if mii:
			mii = MiiData().mii_studio_url(mii)

		try:
			friend_code = str(principal_id_to_friend_code(principal_id)).zfill(12)
			title_data = cached_get_title(friend_data.title_id)

			discord_user_data = UserData(
				friend_code=friend_code,
				online=friend_data.online,
				game=title_data,
				game_description=friend_data.game_description,
				username=friend_data.username,
				mii_urls=mii
			)

			api_client.update_presence(discord_user_data, discord_friend.network)
		except HTTPError as e:
			log.warning('[FRIEND PRESENCE FAILURE] %s', e)
			# api_client.delete_discord_user()

	# Pace the loop before the next scan pass.
	time.sleep(LOOP_INTERVAL)


if __name__ == '__main__':
	while True:
		try:
			run_loop_pass()
		except Exception:
			log.exception('Unhandled error in main loop; retrying in %ss', LOOP_INTERVAL)
			session.rollback()
			time.sleep(LOOP_INTERVAL)
