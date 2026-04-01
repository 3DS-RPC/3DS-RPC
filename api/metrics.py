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

db = None


def init_db(database_instance):
    """Initialize the metrics module with the SQLAlchemy db instance."""
    global db
    db = database_instance


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
    loop_counter: int = 0

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.backend_start_time

    @property
    def average_loop_time(self) -> float:
        return self.total_loop_time / self.loop_counter if self.loop_counter > 0 else 0.0

    @property
    def last_loop_duration(self) -> Optional[float]:
        if self.last_loop_start_time > 0:
            return self.last_loop_end_time - self.last_loop_start_time
        return None


def _ensure_metrics_record(network: NetworkType) -> None:
    """Ensure a metrics record exists for the given network."""
    from database import BackendMetrics as DBBackendMetrics
    if db is None:
        return
    with db.session() as session:
        existing = session.query(DBBackendMetrics).filter_by(network=network).first()
        if not existing:
            session.add(DBBackendMetrics(
                network=network,
                loop_counter=0,
                total_users_processed=0,
                total_loop_time=0.0,
                last_loop_start_time=0.0,
                last_loop_end_time=0.0,
                current_loop_queue=0,
                last_loop_queue=0,
                backend_start_time=time.time()
            ))
            session.commit()


_backend_metrics: dict[NetworkType, BackendMetrics] = {}


def get_backend_metrics_instance(network: NetworkType | None = None) -> BackendMetrics:
    """Get or create backend metrics instance for a network."""
    global _backend_metrics
    if network is None:
        return BackendMetrics()
    if network not in _backend_metrics:
        _backend_metrics[network] = BackendMetrics()
    return _backend_metrics[network]


backend_metrics = BackendMetrics()


def record_loop_start(queue_size: int = 0, network: NetworkType | None = None) -> None:
    """Call at the start of a processing loop with the queue size."""
    from database import BackendMetrics as DBBackendMetrics
    if network is None or db is None:
        return
    
    _ensure_metrics_record(network)
    
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record:
            record.last_loop_start_time = time.time()
            record.current_loop_queue = queue_size
            session.commit()


def record_loop_end(users_processed: int, network: NetworkType | None = None) -> None:
    """Call at the end of a processing loop with the number of users processed."""
    from database import BackendMetrics as DBBackendMetrics
    if network is None or db is None:
        return
    
    _ensure_metrics_record(network)
    
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record:
            loop_duration = time.time() - record.last_loop_start_time
            record.last_loop_end_time = time.time()
            record.loop_counter += 1
            record.total_users_processed = users_processed
            record.total_loop_time += loop_duration
            record.last_loop_queue = record.current_loop_queue
            record.current_loop_queue = 0
            
            # Reset counters every 10 loops
            if record.loop_counter >= 10:
                record.loop_counter = 0
                record.total_users_processed = 0
                record.total_loop_time = 0.0
            
            session.commit()


def get_backend_metrics(network: NetworkType | None = None) -> dict:
    """Return backend metrics as a dictionary."""
    from database import BackendMetrics as DBBackendMetrics
    if network is None or db is None:
        metrics = get_backend_metrics_instance()
        return {
            'uptime_seconds': metrics.uptime_seconds,
            'total_users_processed': metrics.total_users_processed,
            'total_loop_time_seconds': metrics.total_loop_time,
            'average_loop_time_seconds': metrics.average_loop_time,
            'last_loop_duration_seconds': metrics.last_loop_duration,
            'current_loop_queue': metrics.current_loop_queue,
            'last_loop_queue': metrics.last_loop_queue,
            'loop_counter': metrics.loop_counter
        }
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record:
            duration = record.last_loop_end_time - record.last_loop_start_time if record.last_loop_end_time > 0 else None
            return {
                'uptime_seconds': time.time() - record.backend_start_time,
                'total_users_processed': record.total_users_processed,
                'total_loop_time_seconds': record.total_loop_time,
                'average_loop_time_seconds': record.total_loop_time / record.loop_counter if record.loop_counter > 0 else 0.0,
                'last_loop_duration_seconds': max(0, duration) if duration is not None else None,
                'current_loop_queue': record.current_loop_queue,
                'last_loop_queue': record.last_loop_queue,
                'loop_counter': record.loop_counter
            }
        metrics = get_backend_metrics_instance()
        return {
            'uptime_seconds': metrics.uptime_seconds,
            'total_users_processed': metrics.total_users_processed,
            'total_loop_time_seconds': metrics.total_loop_time,
            'average_loop_time_seconds': metrics.average_loop_time,
            'last_loop_duration_seconds': metrics.last_loop_duration,
            'current_loop_queue': metrics.current_loop_queue,
            'last_loop_queue': metrics.last_loop_queue,
            'loop_counter': metrics.loop_counter
        }


def get_network_stats() -> dict:
    """Get online/offline counts per network."""
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

    nintendo_metrics = get_backend_metrics(NetworkType.NINTENDO)
    pretendo_metrics = get_backend_metrics(NetworkType.PRETENDO)

    return jsonify({
        'total_friends': total_friends,
        'online_friends': online_friends,
        'offline_friends': offline_friends,
        'total_discord_users': total_discord_users,
        'active_discord_connections': active_discord_connections,
        'networks': get_network_stats(),
        'backend': {
            'nintendo': nintendo_metrics,
            'pretendo': pretendo_metrics
        },
        'timestamp': time.time()
    }), 200