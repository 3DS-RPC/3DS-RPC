import datetime
import logging
import os

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
    """Posts ERROR+ log records to a Discord webhook as an embed."""

    def __init__(self, webhook_url: str = DISCORD_WEBHOOK_URL, level: int = logging.ERROR):
        super().__init__(level=level)
        self.webhook_url = webhook_url

    def emit(self, record: logging.LogRecord) -> None:
        if not self.webhook_url:
            return
        try:
            message = redact_sensitive(self.format(record))
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