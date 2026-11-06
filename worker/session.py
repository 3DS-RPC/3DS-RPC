"""NASC/NEX session lifecycle."""

from __future__ import annotations

import time
from contextlib import AsyncExitStack

import anyio
import nintendo.nasc as _nasc
from anynet import http as _http
from nintendo import nasc
from nintendo.nex import backend, friends, settings

from api import private
from api.networks import InvalidNetworkError, NetworkType
from api.settings import NASC_LOGIN_SECONDS
from worker.failures import NASCUnavailableError

SESSION_MAX_AGE = 10 * 60


async def _tolerant_nasc_request(self, req):
	"""NASC outage pages are HTML, not a form; surface them as an outage error."""
	req.form = _nasc.encode_form(req.form)
	response = await _http.request(self.url, req, self.context)
	form = _http.formdecode(response.text)

	if form.get("returncd") is None:
		raise NASCUnavailableError(response.status_code, response.text)

	form = {key: value for key, value in form.items() if value is not None}

	response.form = _nasc.decode_form(form)
	return_code = response.form["returncd"].decode()

	if return_code == "null" or int(return_code) != 1:
		raise _nasc.NASCError(response.status_code, response.form)

	return response


_nasc.NASCClient.request = _tolerant_nasc_request


class FriendsConnection:
	def __init__(self, network: NetworkType):
		self.network = network
		self.client: friends.FriendsClientV1 | None = None
		self.connected_at = 0.0
		self._stack = AsyncExitStack()

	@property
	def is_connected(self) -> bool:
		return self.client is not None

	@property
	def is_stale(self) -> bool:
		return self.is_connected and time.monotonic() - self.connected_at >= SESSION_MAX_AGE

	async def connect(self) -> None:
		await self.close()
		stack = AsyncExitStack()
		try:
			client = nasc.NASCClient()

			# TODO: credentials should be per-network, not per-process.
			match self.network:
				case NetworkType.NINTENDO:
					client.set_locale(private.NINTENDO_REGION, private.NINTENDO_LANGUAGE)
					client.set_url("nasc.nintendowifi.net")
					pid = private.NINTENDO_PID
					nex_password = private.NINTENDO_NEX_PASSWORD

					client.set_device(private.NINTENDO_SERIAL_NUMBER, private.NINTENDO_MAC_ADDRESS, private.NINTENDO_DEVICE_CERT, private.NINTENDO_DEVICE_NAME)
					client.set_user(pid, private.NINTENDO_PID_HMAC)
				case NetworkType.PRETENDO:
					client.set_locale(private.PRETENDO_REGION, private.PRETENDO_LANGUAGE)
					client.set_url("nasc.pretendo.cc")
					client.context.set_authority(None)

					pid = private.PRETENDO_PID
					nex_password = private.PRETENDO_NEX_PASSWORD

					client.set_device(private.PRETENDO_SERIAL_NUMBER, private.PRETENDO_MAC_ADDRESS, private.PRETENDO_DEVICE_CERT, private.PRETENDO_DEVICE_NAME)
					client.set_user(pid, private.PRETENDO_PID_HMAC)
				case _:
					raise InvalidNetworkError(f"Network type {self.network} is not configured for querying")

			client.set_title(0x0004013000003202, 20)
			with anyio.fail_after(NASC_LOGIN_SECONDS):
				response = await client.login(0x3200)

			# The NEX context managers own long-lived anyio scopes; do not wrap
			# them in a timeout scope that exits before they do.
			friends_settings = settings.load('friends')
			friends_settings.configure("ridfebb9", 20000)

			nex_backend = await stack.enter_async_context(backend.connect(friends_settings, response.host, response.port))
			nex_client = await stack.enter_async_context(nex_backend.login(str(pid), nex_password))
			self.client = friends.FriendsClientV1(nex_client)
			self._stack = stack
			self.connected_at = time.monotonic()
		except BaseException:
			try:
				await stack.aclose()
			except Exception as close_error:
				print(f'Failed to clean up after a failed connection: {close_error}')
			raise

	async def close(self) -> None:
		stack, self._stack = self._stack, AsyncExitStack()
		self.client = None
		self.connected_at = 0.0
		try:
			await stack.aclose()
		except Exception as e:
			print(f'Failed to close the friends connection cleanly: {e}')
