from typing import Optional

HEALTH_OK = 'ok'
HEALTH_DEGRADED = 'degraded'
HEALTH_ERROR = 'error'

HEALTH_REASONS: dict[str, str] = {
    'comment_space': 'Profile skipped due to ending in space',
    'info_read_failed': 'Could not read profile info',
    'status_read_failed': 'Could not read status',
}


def health_reason_label(code: Optional[str]) -> str:
    """Resolve a stored health reason code to its static human-readable label."""
    if not code:
        return ''
    return HEALTH_REASONS.get(code, code)