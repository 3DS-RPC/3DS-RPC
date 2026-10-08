from __future__ import annotations
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func, update

from api.networks import NetworkType
from database import Discord, DiscordFriends, Friend

metrics_bp = Blueprint('metrics', __name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'metrics_keys.json')

db = None

# How stale the last backend heartbeat may be before we consider a network down.
HEARTBEAT_THRESHOLD = 10 * 60  # 10 minutes
_PROGRESS_PERSIST_INTERVAL = 1.0
_loop_progress_state: dict = {}
_last_progress_persist: dict = {}


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
    network_status: str = 'up'
    full_loop_current: int = 0
    full_loop_total: int = 0
    full_loop_last_update: float = 0.0
    quick_loop_current: int = 0
    quick_loop_total: int = 0
    quick_loop_last_update: float = 0.0

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


def reset_metrics(network: NetworkType) -> None:
    """Reset metrics for a network (call when backend restarts)."""
    from database import BackendMetrics as DBBackendMetrics
    if db is None:
        return
    with db.session() as session:
        existing = session.query(DBBackendMetrics).filter_by(network=network).first()
        if existing:
            session.delete(existing)
        session.add(DBBackendMetrics(
            network=network,
            loop_counter=0,
            total_users_processed=0,
            total_loop_time=0.0,
            last_loop_start_time=0.0,
            last_loop_end_time=0.0,
            last_loop_duration=0.0,
            current_loop_queue=0,
            last_loop_queue=0,
            full_loop_current=0,
            full_loop_total=0,
            full_loop_last_update=0.0,
            quick_loop_current=0,
            quick_loop_total=0,
            quick_loop_last_update=0.0,
            backend_start_time=time.time()
        ))
        session.commit()


def update_backend_heartbeat(network: NetworkType) -> None:
    """Update the last_loop_end_time heartbeat so the frontend doesn't mark us offline."""
    from database import BackendMetrics as DBBackendMetrics
    if db is None:
        return
    _ensure_metrics_record(network)
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record:
            record.last_loop_end_time = time.time()
            session.commit()


def set_backend_status(network: NetworkType, status: str) -> None:
    """Mark the backend for a network as 'up' or 'down' (e.g. the game server is unreachable)."""
    from database import BackendMetrics as DBBackendMetrics
    if db is None:
        return
    _ensure_metrics_record(network)
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record and record.network_status != status:
            record.network_status = status
            session.commit()


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
                full_loop_current=0,
                full_loop_total=0,
                full_loop_last_update=0.0,
                quick_loop_current=0,
                quick_loop_total=0,
                quick_loop_last_update=0.0,
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
            record.last_loop_duration = loop_duration
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


def _queue_progress(current: int, total: int, last_update: float) -> dict:
    """Progress through one queue rotation as a serializable dict."""
    percent = (current / total * 100) if total > 0 else 0
    return {
        'processed': current,
        'total': total,
        'last_update': last_update or 0,
        'percent': round(min(100, max(0, percent)), 1),
    }


def _progress_dict(record) -> dict:
    """Progress through both queue rotations (full sweep + quick sweep)."""
    return {
        'full': _queue_progress(record.full_loop_current, record.full_loop_total, record.full_loop_last_update),
        'quick': _queue_progress(record.quick_loop_current, record.quick_loop_total, record.quick_loop_last_update),
    }


def _progress_dict_instance(metrics: BackendMetrics) -> dict:
    return {
        'full': _queue_progress(metrics.full_loop_current, metrics.full_loop_total, metrics.full_loop_last_update),
        'quick': _queue_progress(metrics.quick_loop_current, metrics.quick_loop_total, metrics.quick_loop_last_update),
    }


def get_backend_metrics(network: NetworkType | None = None) -> dict | None:
    """Return backend metrics as a dictionary, or None if no valid record exists."""
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
            'loop_counter': metrics.loop_counter,
            'network_status': metrics.network_status,
            'progress': _progress_dict_instance(metrics)
        }
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record and record.backend_start_time > 0:
            uptime = time.time() - record.backend_start_time
            return {
                'uptime_seconds': uptime,
                'last_seen': record.last_loop_end_time if record.last_loop_end_time > 0 else record.backend_start_time,
                'total_users_processed': record.total_users_processed,
                'total_loop_time_seconds': record.total_loop_time,
                'average_loop_time_seconds': record.total_loop_time / record.loop_counter if record.loop_counter > 0 else 0.0,
                'last_loop_duration_seconds': record.last_loop_duration if record.last_loop_duration > 0 else None,
                'current_loop_queue': record.current_loop_queue,
                'last_loop_queue': record.last_loop_queue,
                'loop_counter': record.loop_counter,
                'network_status': record.network_status,
                'progress': _progress_dict(record)
            }
        return None


def _shared_session():
    """Return the metrics module's shared database session.

    Works both under Flask-SQLAlchemy (`db.session` is a callable scoped
    session) and the backend's thin wrapper (`db.session()` returns the one
    session the backend reuses all loop long).
    """
    session = db.session() if callable(db.session) else db.session
    return session


_PROGRESS_MODES = ('full', 'quick')


def _progress_columns(mode: str, current: int, total: int, now: float) -> dict:
    """Column names + values for the queue being processed, by mode."""
    return {
        f'{mode}_loop_current': current,
        f'{mode}_loop_total': total,
        f'{mode}_loop_last_update': now,
    }


def _persist_loop_progress(mode: str, current: int, total: int, network: NetworkType, commit: bool = False) -> None:
    """Write progress through one queue rotation for a network to the database."""
    
    from database import BackendMetrics as DBBackendMetrics
    session = _shared_session()
    session.execute(
        update(DBBackendMetrics)
        .where(DBBackendMetrics.network == network)
        .values(**_progress_columns(mode, current, total, time.time()))
    )
    if commit:
        session.commit()
    _last_progress_persist[(network, mode)] = time.time()


def begin_loop_progress(total: int, network: NetworkType | None = None, mode: str = 'quick') -> None:
    """Reset progress for a queue rotation once its roster is known.

    Called between loops, before any batch work starts, so committing here is
    safe (there is no pending transaction yet).
    """
    if network is None or mode not in _PROGRESS_MODES:
        return
    _loop_progress_state[network] = {'current': 0, 'total': total, 'mode': mode}
    _persist_loop_progress(mode, 0, total, network, commit=True)


def advance_loop_progress(network: NetworkType | None = None, amount: int = 1) -> None:
    """Advance the active queue rotation's progress as consoles are processed.

    Persists on completion, otherwise throttled to reduce writes. The write
    lives in the backend's in-flight transaction and becomes visible when that
    transaction commits (per-batch), so no `commit` is issued here.
    """
    if network is None:
        return
    state = _loop_progress_state.get(network)
    if state is None:
        return
    state['current'] = min(state['total'], state['current'] + amount)
    now = time.time()
    if state['current'] >= state['total'] or now - _last_progress_persist.get((network, state['mode']), 0.0) >= _PROGRESS_PERSIST_INTERVAL:
        _persist_loop_progress(state['mode'], state['current'], state['total'], network)


def is_backend_online(metrics: dict | None) -> bool:
    """True if the backend is up and recently heartbeated for a network."""
    if not metrics or metrics.get('network_status') == 'down':
        return False
    last_seen = metrics.get('last_seen', 0) or 0
    return time.time() - last_seen < HEARTBEAT_THRESHOLD


def build_public_status() -> dict:
    """Sanitized, login-free snapshot used by the status page and /api/status."""
    def summarize(network: NetworkType, label: str) -> dict:
        metrics = get_backend_metrics(network)
        tracked = db.session.query(
            func.count()
        ).filter(Friend.network == network).scalar() or 0
        return {
            'name': label,
            'online': is_backend_online(metrics),
            'network_status': (metrics or {}).get('network_status', 'up'),
            'uptime_seconds': (metrics or {}).get('uptime_seconds', 0),
            'last_seen': (metrics or {}).get('last_seen', 0),
            'last_loop_duration_seconds': (metrics or {}).get('last_loop_duration_seconds'),
            'average_loop_time_seconds': (metrics or {}).get('average_loop_time_seconds', 0),
            'loop_counter': (metrics or {}).get('loop_counter', 0),
            'tracked': tracked,
            'progress': (metrics or {}).get('progress', {
                'full': _queue_progress(0, 0, 0),
                'quick': _queue_progress(0, 0, 0),
            }),
        }

    nintendo = summarize(NetworkType.NINTENDO, 'Nintendo')
    pretendo = summarize(NetworkType.PRETENDO, 'Pretendo')

    if nintendo['online'] and pretendo['online']:
        status = 'Operational'
    elif nintendo['online'] or pretendo['online']:
        status = 'Semi-Operational'
    else:
        status = 'Offline'

    return {
        'status': status,
        'timestamp': time.time(),
        'networks': {
            'nintendo': nintendo,
            'pretendo': pretendo,
        },
        'heartbeat_threshold_seconds': HEARTBEAT_THRESHOLD,
    }


# The status page (and the nav every page renders) only need a periodic snapshot.
# Building it runs several DB queries, so a background task refreshes it and the
# request handlers serve these cached copies without ever touching the database.
PUBLIC_STATUS_REFRESH_INTERVAL = 5  # seconds
_public_status_cache: dict | None = None
_network_metrics_cache: dict = {}
_status_cache_lock = threading.Lock()


def refresh_status_cache() -> None:
    """Rebuild the cached public status and per-network metric snapshots.

    Must be called within a Flask application context (it queries the DB).
    """
    public = build_public_status()
    networks = {}
    for key, network in (('nintendo', NetworkType.NINTENDO), ('pretendo', NetworkType.PRETENDO)):
        entry = public['networks'].get(key, {})
        networks[network] = {
            'uptime_seconds': entry.get('uptime_seconds', 0),
            'last_seen': entry.get('last_seen', 0),
            'network_status': entry.get('network_status', 'up'),
        }

    global _public_status_cache
    with _status_cache_lock:
        _public_status_cache = public
        _network_metrics_cache.clear()
        _network_metrics_cache.update(networks)


def get_public_status() -> dict:
    """Return the cached public status snapshot, building it once if needed."""
    cached = _public_status_cache
    if cached is None:
        refresh_status_cache()
        cached = _public_status_cache
    return cached


def get_cached_network_metrics(network: NetworkType) -> dict | None:
    """Return the cached backend metric subset the nav/status pill needs."""
    if _public_status_cache is None:
        refresh_status_cache()
    return _network_metrics_cache.get(network)


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


@metrics_bp.route('/api/status/', methods=['GET'])
def api_status():
    """Public status snapshot (no API key required) for the status page."""
    return jsonify(get_public_status()), 200
