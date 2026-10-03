import datetime
import hashlib
import logging
import os
import time

import requests
from flask import has_request_context, request

import api.private as private

DISCORD_WEBHOOK_URL: str = getattr(private, 'DISCORD_WEBHOOK_URL', '')


def redact_sensitive(text: str) -> str:
    for name, value in vars(private).items():
        if isinstance(value, bytes):
            value = value.hex()
        if not isinstance(value, str):
            continue
        if len(value) < 8:
            continue
        text = text.replace(value, f'<REDACTED:{name}>')
    return text


class DiscordWebhookHandler(logging.Handler):
    """Posts ERROR+ log records to a Discord webhook as an embed.

    Identical messages are rate-limited so a repeating error (e.g. a downed
    game server) can't spam the channel: each unique message is posted at most
    once per COOLDOWN seconds.
    """

    COOLDOWN: float = 5 * 60

    def __init__(self, webhook_url: str = DISCORD_WEBHOOK_URL, level: int = logging.ERROR):
        super().__init__(level=level)
        self.webhook_url = webhook_url
        self._last_sent: dict[str, float] = {}

    def _dedupe(self, message: str) -> bool:
        """Return True if this message should be posted (not a recent duplicate)."""
        now = time.time()
        key = hashlib.sha256(message.encode()).hexdigest()
        last = self._last_sent.get(key)
        if last is not None and now - last < self.COOLDOWN:
            return False
        self._last_sent[key] = now
        # Drop expired entries to keep the cache bounded.
        if len(self._last_sent) > 100:
            self._last_sent = {k: v for k, v in self._last_sent.items() if now - v < self.COOLDOWN}
        return True

    def emit(self, record: logging.LogRecord) -> None:
        if not self.webhook_url:
            return
        try:
            message = redact_sensitive(self.format(record))
            if not self._dedupe(message):
                return
            if len(message) > 4000:
                message = message[-4000:] + '\n...(truncated)'
            fields = []
            if has_request_context():
                fields.append({'name': 'Method', 'value': request.method, 'inline': True})
                fields.append({'name': 'Path', 'value': request.path, 'inline': True})
                if request.query_string:
                    fields.append({'name': 'Query', 'value': request.query_string.decode('utf-8', 'replace')[:1000], 'inline': True})
                user_agent = request.headers.get('User-Agent')
                if user_agent:
                    fields.append({'name': 'User-Agent', 'value': user_agent[:1000], 'inline': True})
            payload = {
                'embeds': [{
                    'title': '3DS-RPC %s' % record.levelname,
                    'color': 0xFF0000 if record.levelno >= logging.ERROR else 0xFFA500,
                    'description': '```py\n%s\n```' % message,
                    'fields': fields,
                    'timestamp': datetime.datetime.utcnow().isoformat(),
                }]
            }
            requests.post(self.webhook_url, json=payload, timeout=10)
        except Exception:
            self.handleError(record)


def setup_error_webhook(logger: logging.Logger) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    logger.addHandler(DiscordWebhookHandler())