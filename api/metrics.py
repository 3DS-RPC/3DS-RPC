from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func

from api.networks import NetworkType
from database import Discord, DiscordFriends, Friend

metrics_bp = Blueprint('metrics', __name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'metrics_keys.json')


def load_keys() -> list[dict]:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return []


def validate_key(key: str) -> bool:
    return any(k['key'].upper() == key.upper() for k in load_keys())


@dataclass
class BackendMetrics:
    """Container for backend processing metrics."""
    backend_start_time: float = time.time()
    total_users_processed: int = 0
    total_loop_time: float = 0.0
    last_loop_start_time: float = 0.0
    last_loop_end_time: float = 0.0
    current_loop_queue: int = 0
    last_loop_queue: int = 0
    _loop_count: int = 0

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.backend_start_time

    @property
    def average_loop_time(self) -> float:
        return self.total_loop_time / self._loop_count if self._loop_count > 0 else 0.0

    @property
    def last_loop_duration(self) -> Optional[float]:
        if self.last_loop_start_time > 0:
            return self.last_loop_end_time - self.last_loop_start_time
        return None


backend_metrics = BackendMetrics()


def record_loop_start(queue_size: int = 0) -> None:
    """Call at the start of a processing loop with the queue size."""
    backend_metrics.last_loop_start_time = time.time()
    backend_metrics.current_loop_queue = queue_size


def record_loop_end(users_processed: int) -> None:
    """Call at the end of a processing loop with the number of users processed."""
    backend_metrics.last_loop_end_time = time.time()
    backend_metrics._loop_count += 1
    backend_metrics.total_users_processed += users_processed
    backend_metrics.total_loop_time += (
        backend_metrics.last_loop_end_time - backend_metrics.last_loop_start_time
    )
    backend_metrics.last_loop_queue = backend_metrics.current_loop_queue
    backend_metrics.current_loop_queue = 0


def get_backend_metrics() -> dict:
    """Return backend metrics as a dictionary."""
    return {
        'uptime_seconds': backend_metrics.uptime_seconds,
        'total_users_processed': backend_metrics.total_users_processed,
        'total_loop_time_seconds': backend_metrics.total_loop_time,
        'average_loop_time_seconds': backend_metrics.average_loop_time,
        'last_loop_duration_seconds': backend_metrics.last_loop_duration,
        'current_loop_queue': backend_metrics.current_loop_queue,
        'last_loop_queue': backend_metrics.last_loop_queue
    }


def get_network_stats() -> dict:
    """Get online/offline counts per network."""
    from server import db

    # Single query with conditional aggregation
    result = db.session.query(
        Friend.network,
        func.count().label('total'),
        func.sum(case((Friend.online == True, 1), else_=0)).label('online')
    ).group_by(Friend.network).all()

    stats: dict[NetworkType, dict] = {}
    for row in result:
        total = row.total or 0
        online = row.online or 0
        stats[row.network] = {
            'total': total,
            'online': online,
            'offline': total - online
        }

    return {
        'nintendo': stats.get(NetworkType.NINTENDO, {'total': 0, 'online': 0, 'offline': 0}),
        'pretendo': stats.get(NetworkType.PRETENDO, {'total': 0, 'online': 0, 'offline': 0})
    }


@metrics_bp.route('/api/metrics/', methods=['GET'])
def get_metrics():
    api_key = request.headers.get('X-API-KEY') or request.args.get('api_key')
    if not api_key or not validate_key(api_key):
        return {'error': 'API key required'}, 401

    from server import db

    # Single query for friend stats
    friend_stats = db.session.query(
        func.count().label('total'),
        func.sum(case((Friend.online.is_(True), 1), else_=0)).label('online')
    ).first()

    total_friends = friend_stats.total if friend_stats else 0
    online_friends = friend_stats.online if friend_stats else 0
    offline_friends = total_friends - online_friends

    # Single query for Discord stats
    discord_stats = db.session.query(
        func.count().label('total_users'),
        func.sum(case((DiscordFriends.active.is_(True), 1), else_=0)).label('active')
    ).first()

    total_discord_users = discord_stats.total_users if discord_stats else 0
    active_discord_connections = discord_stats.active if discord_stats else 0

    return jsonify({
        'total_friends': total_friends,
        'online_friends': online_friends,
        'offline_friends': offline_friends,
        'total_discord_users': total_discord_users,
        'active_discord_connections': active_discord_connections,
        'networks': get_network_stats(),
        'backend': get_backend_metrics(),
        'timestamp': time.time()
    }), 200
