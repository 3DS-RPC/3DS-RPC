from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass

from flask import Blueprint, jsonify
from sqlalchemy import func, update

from api.networks import NetworkType
from database import Friend

metrics_bp = Blueprint('metrics', __name__)

log = logging.getLogger(__name__)

db = None

# How stale the last backend heartbeat may be before we consider a network down.
HEARTBEAT_THRESHOLD = 10 * 60  # 10 minutes
_PROGRESS_PERSIST_INTERVAL = 1.0
_loop_progress_state: dict = {}
_last_progress_persist: dict = {}

# Heartbeats are cheap but frequent; write at most once per interval.
HEARTBEAT_WRITE_INTERVAL = 60.0
_last_heartbeat_write: dict = {}


def init_db(database_instance):
    """Initialize the metrics module with the SQLAlchemy db instance."""
    global db
    db = database_instance


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
    fast_loop_current: int = 0
    fast_loop_total: int = 0
    fast_loop_skipped: int = 0
    fast_loop_failed: int = 0
    fast_loop_last_update: float = 0.0
    slow_loop_current: int = 0
    slow_loop_total: int = 0
    slow_loop_skipped: int = 0
    slow_loop_failed: int = 0
    slow_loop_last_update: float = 0.0
    loop_mode: str = ''

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.backend_start_time

    @property
    def average_loop_time(self) -> float:
        return self.total_loop_time / self.loop_counter if self.loop_counter > 0 else 0.0

    @property
    def last_loop_duration(self) -> float | None:
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
            fast_loop_current=0,
            fast_loop_total=0,
            fast_loop_skipped=0,
            fast_loop_failed=0,
            fast_loop_last_update=0.0,
            slow_loop_current=0,
            slow_loop_total=0,
            slow_loop_skipped=0,
            slow_loop_failed=0,
            slow_loop_last_update=0.0,
            backend_start_time=time.time(),
            loop_mode=''
        ))
        session.commit()
    _last_heartbeat_write.pop(network, None)


def update_backend_heartbeat(network: NetworkType | None = None, force: bool = False) -> None:
    """Update the heartbeat timestamp, at most once per interval."""
    from database import BackendMetrics as DBBackendMetrics
    if db is None or network is None:
        return
    now = time.time()
    if not force and now - _last_heartbeat_write.get(network, 0.0) < HEARTBEAT_WRITE_INTERVAL:
        return
    _ensure_metrics_record(network)
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record:
            record.last_loop_end_time = now
            session.commit()
    _last_heartbeat_write[network] = now


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
                fast_loop_current=0,
                fast_loop_total=0,
                fast_loop_skipped=0,
                fast_loop_failed=0,
                fast_loop_last_update=0.0,
                slow_loop_current=0,
                slow_loop_total=0,
                slow_loop_skipped=0,
                slow_loop_failed=0,
                slow_loop_last_update=0.0,
                backend_start_time=time.time(),
                loop_mode=''
            ))
            session.commit()


_backend_metrics: dict[NetworkType, BackendMetrics] = {}


def get_backend_metrics_instance(network: NetworkType | None = None) -> BackendMetrics:
    """Get or create backend metrics instance for a network."""
    if network is None:
        return BackendMetrics()
    if network not in _backend_metrics:
        _backend_metrics[network] = BackendMetrics()
    return _backend_metrics[network]


backend_metrics = BackendMetrics()


def _local_date(timestamp: float) -> datetime.date:
    """Returns the calendar date for a timestamp in the server's local timezone."""
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).astimezone().date()


def record_loop_start(queue_size: int = 0, network: NetworkType | None = None) -> None:
    """Call at the start of a processing loop with the queue size."""
    from database import BackendMetrics as DBBackendMetrics
    if network is None or db is None:
        return
    
    _ensure_metrics_record(network)
    
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record:
            now = time.time()
            started = record.last_loop_start_time
            if started and _local_date(started) != _local_date(now):
                record.loop_counter = 0
                record.total_loop_time = 0.0
            record.last_loop_start_time = now
            record.current_loop_queue = queue_size
            # Count the loop as it starts, not when it finishes. A full sweep
            # can take hours under per-batch pacing, so counting on completion
            # leaves the counter (and the status API) frozen at 0.
            record.loop_counter += 1
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
            now = time.time()
            loop_duration = now - record.last_loop_start_time
            record.last_loop_end_time = now
            record.last_loop_duration = loop_duration
            record.total_users_processed = users_processed
            record.total_loop_time += loop_duration
            record.last_loop_queue = record.current_loop_queue
            record.current_loop_queue = 0
            record.loop_mode = ''
            session.commit()


def _queue_progress(current: int, total: int, last_update: float, active: bool = False, skipped: int = 0, failed: int = 0) -> dict:
    """Progress through one queue rotation as a serializable dict."""
    completed = current + skipped + failed
    percent = (completed / total * 100) if total > 0 else 0
    return {
        'processed': current,
        'skipped': skipped,
        'failed': failed,
        'completed': completed,
        'total': total,
        'last_update': last_update or 0,
        'percent': round(min(100, max(0, percent)), 1),
        'active': active,
    }


def _progress_dict(record) -> dict:
    """Progress through the fast and slow queues."""
    active_mode = record.loop_mode or ''
    return {
        'fast': _queue_progress(record.fast_loop_current, record.fast_loop_total, record.fast_loop_last_update, active_mode in ('fast', 'quick', 'mixed'), record.fast_loop_skipped, record.fast_loop_failed),
        'slow': _queue_progress(record.slow_loop_current, record.slow_loop_total, record.slow_loop_last_update, active_mode in ('slow', 'full', 'mixed'), record.slow_loop_skipped, record.slow_loop_failed),
    }


def _progress_dict_instance(metrics: BackendMetrics) -> dict:
    active_mode = metrics.loop_mode or ''
    return {
        'fast': _queue_progress(metrics.fast_loop_current, metrics.fast_loop_total, metrics.fast_loop_last_update, active_mode in ('fast', 'quick', 'mixed'), metrics.fast_loop_skipped, metrics.fast_loop_failed),
        'slow': _queue_progress(metrics.slow_loop_current, metrics.slow_loop_total, metrics.slow_loop_last_update, active_mode in ('slow', 'full', 'mixed'), metrics.slow_loop_skipped, metrics.slow_loop_failed),
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
            'loop_mode': metrics.loop_mode or None,
            'current_loop_elapsed_seconds': None,
            'progress': _progress_dict_instance(metrics)
        }
    with db.session() as session:
        record = session.query(DBBackendMetrics).filter_by(network=network).first()
        if record and record.backend_start_time > 0:
            now = time.time()
            uptime = now - record.backend_start_time
            in_progress = bool(record.loop_mode)
            # `loop_counter` counts loops as they start, so the in-progress
            # loop hasn't contributed to `total_loop_time` yet. Divide by the
            # number of loops that have actually finished.
            completed_loops = record.loop_counter - 1 if in_progress else record.loop_counter
            average_loop_time = record.total_loop_time / completed_loops if completed_loops > 0 else 0.0
            elapsed = now - record.last_loop_start_time if in_progress and record.last_loop_start_time > 0 else None
            return {
                'uptime_seconds': uptime,
                'last_seen': record.last_loop_end_time if record.last_loop_end_time > 0 else record.backend_start_time,
                'total_users_processed': record.total_users_processed,
                'total_loop_time_seconds': record.total_loop_time,
                'average_loop_time_seconds': average_loop_time,
                'last_loop_duration_seconds': record.last_loop_duration if record.last_loop_duration > 0 else None,
                'current_loop_queue': record.current_loop_queue,
                'last_loop_queue': record.last_loop_queue,
                'loop_counter': record.loop_counter,
                'network_status': record.network_status,
                'loop_mode': record.loop_mode or None,
                'current_loop_elapsed_seconds': elapsed,
                'progress': _progress_dict(record)
            }
        return None


def _shared_session():
    """Returns the shared session for both Flask and the backend wrapper."""
    session = db.session() if callable(db.session) else db.session
    return session


_PROGRESS_LANES = ('fast', 'slow')


def _new_lane(total: int) -> dict:
    return {'current': 0, 'total': total, 'skipped': 0, 'failed': 0}


def _lane_complete(lane: dict) -> bool:
    return lane['current'] + lane['skipped'] + lane['failed'] >= lane['total']


def _persist_loop_progress(lane_name: str, lane: dict, network: NetworkType, commit: bool = False, loop_mode: str | None = None) -> None:
    """Write one lane's progress for a network to the database."""
    from database import BackendMetrics as DBBackendMetrics
    session = _shared_session()
    now = time.time()
    values = {
        f'{lane_name}_loop_current': lane['current'],
        f'{lane_name}_loop_total': lane['total'],
        f'{lane_name}_loop_skipped': lane['skipped'],
        f'{lane_name}_loop_failed': lane['failed'],
        f'{lane_name}_loop_last_update': now,
    }
    if loop_mode is not None:
        values['loop_mode'] = loop_mode
    session.execute(
        update(DBBackendMetrics)
        .where(DBBackendMetrics.network == network)
        .values(**values)
    )
    if commit:
        session.commit()
    _last_progress_persist[(network, lane_name)] = now


def begin_cycle_progress(fast_total: int, slow_total: int, network: NetworkType | None = None) -> None:
    """Reset progress for both lanes once this cycle's poll set is known."""
    if network is None:
        return
    lanes = {
        'fast': _new_lane(fast_total),
        'slow': _new_lane(slow_total),
    }
    if fast_total and slow_total:
        mode = 'mixed'
    elif fast_total:
        mode = 'fast'
    else:
        mode = 'slow'
    _loop_progress_state[network] = {'lanes': lanes, 'mode': mode}
    _persist_loop_progress('fast', lanes['fast'], network)
    _persist_loop_progress('slow', lanes['slow'], network, commit=True, loop_mode=mode)


def _select_lane(state: dict, mode: str | None) -> tuple[str, dict] | None:
    lanes = state['lanes']
    if mode is None:
        mode = 'fast' if not _lane_complete(lanes['fast']) else 'slow'
    lane = lanes.get(mode)
    return (mode, lane) if lane is not None else None


def _bump_progress(network: NetworkType | None, field: str, amount: int, mode: str | None) -> None:
    if network is None or amount <= 0:
        return
    state = _loop_progress_state.get(network)
    if state is None:
        return
    selected = _select_lane(state, mode)
    if selected is None:
        return
    mode, lane = selected
    if field == 'current':
        lane['current'] = min(lane['total'] - lane['skipped'] - lane['failed'], lane['current'] + amount)
    else:
        remaining = max(0, lane['total'] - lane['current'] - lane['failed'] if field == 'skipped' else lane['total'] - lane['current'] - lane['skipped'])
        lane[field] = min(remaining, lane[field] + amount)

    now = time.time()
    if _lane_complete(lane) or now - _last_progress_persist.get((network, mode), 0.0) >= _PROGRESS_PERSIST_INTERVAL:
        _persist_loop_progress(mode, lane, network)


def advance_loop_progress(network: NetworkType | None = None, amount: int = 1, mode: str | None = None) -> None:
    """Advance one lane's processed count."""
    _bump_progress(network, 'current', amount, mode)


def skip_loop_progress(network: NetworkType | None = None, amount: int = 1, mode: str | None = None) -> None:
    """Count users that could not be polled, e.g. a failed friend add."""
    _bump_progress(network, 'skipped', amount, mode)


def fail_loop_progress(network: NetworkType | None = None, amount: int = 1, mode: str | None = None) -> None:
    """Count users whose poll failed without aborting the cycle."""
    _bump_progress(network, 'failed', amount, mode)


def flush_loop_progress(network: NetworkType | None = None) -> None:
    """Persist both lanes' exact progress before the caller commits."""
    if network is None:
        return
    state = _loop_progress_state.get(network)
    if state is None:
        return
    for lane_name in _PROGRESS_LANES:
        _persist_loop_progress(lane_name, state['lanes'][lane_name], network)


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
            'loop_mode': (metrics or {}).get('loop_mode'),
            'current_loop_elapsed_seconds': (metrics or {}).get('current_loop_elapsed_seconds'),
            'tracked': tracked,
            'progress': (metrics or {}).get('progress', {
                'fast': _queue_progress(0, 0, 0),
                'slow': _queue_progress(0, 0, 0),
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
# A snapshot older than this is considered stale: the background refresher is
# not keeping up (e.g. repeated DB errors), so requests should try to rebuild
# it themselves instead of serving an arbitrarily old result forever.
PUBLIC_STATUS_STALE_AFTER = PUBLIC_STATUS_REFRESH_INTERVAL * 3
_public_status_cache: dict | None = None
_public_status_built_at: float = 0.0
_public_status_refresh_attempt_at: float = 0.0
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

    global _public_status_cache, _public_status_built_at
    with _status_cache_lock:
        _public_status_cache = public
        _public_status_built_at = time.time()
        _network_metrics_cache.clear()
        _network_metrics_cache.update(networks)


def _ensure_status_cache_fresh() -> None:
    """Build the snapshot if missing, or rebuild it once it has gone stale."""
    global _public_status_refresh_attempt_at
    cached = _public_status_cache
    if cached is None:
        # First build: let failures surface (there is no previous value to serve).
        refresh_status_cache()
        return
    now = time.time()
    if now - _public_status_built_at > PUBLIC_STATUS_STALE_AFTER and now - _public_status_refresh_attempt_at >= PUBLIC_STATUS_REFRESH_INTERVAL:
        # Don't let every request hammer the DB while refreshes are failing.
        _public_status_refresh_attempt_at = now
        try:
            refresh_status_cache()
        except Exception:
            log.exception('Failed to refresh status cache; serving last good snapshot')


def get_public_status() -> dict:
    """Return the cached public status snapshot with its freshness annotated."""
    _ensure_status_cache_fresh()
    cached = _public_status_cache
    if cached is None:
        raise RuntimeError('Status cache is unavailable')

    # Shallow copy: callers/templates must not mutate the shared snapshot, and
    # the freshness fields must reflect the current age, not the build time.
    result = dict(cached)
    age = round(max(0.0, time.time() - _public_status_built_at), 1)
    result['cache_age_seconds'] = age
    result['stale'] = age > PUBLIC_STATUS_STALE_AFTER
    return result


def get_cached_network_metrics(network: NetworkType) -> dict | None:
    """Return the cached backend metric subset the nav/status pill needs."""
    _ensure_status_cache_fresh()
    return _network_metrics_cache.get(network)


@metrics_bp.route('/api/status/', methods=['GET'])
def api_status():
    """Public status snapshot (no API key required) for the status page."""
    return jsonify(get_public_status()), 200
