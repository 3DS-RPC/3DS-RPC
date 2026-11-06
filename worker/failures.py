"""Failure classification and retry backoff."""

from __future__ import annotations

import random
from enum import Enum

from nintendo.nasc import NASCError
from nintendo.nex.common import RMCError

from api.networks import InvalidNetworkError
from api.settings import env_float


class FailureClass(Enum):
    FATAL = "fatal"
    AUTH = "auth"
    TRANSIENT = "transient"
    OUTAGE = "outage"

    @property
    def retryable(self) -> bool:
        return self is not FailureClass.FATAL

    @property
    def severity(self) -> int:
        return {
            FailureClass.TRANSIENT: 1,
            FailureClass.AUTH: 2,
            FailureClass.OUTAGE: 3,
            FailureClass.FATAL: 4,
        }[self]


class NASCUnavailableError(Exception):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"NASC server returned an invalid response (HTTP {status_code}): {body[:200]!r}"
        )


_FATAL_NASC_CODES = frozenset({121, 122, 125})

_AUTH_RMC_NAMES = frozenset({
    "Core::AccessDenied",
    "RendezVous::NotAuthenticated",
    "RendezVous::InvalidUsername",
    "RendezVous::InvalidPassword",
    "RendezVous::AccountDisabled",
    "RendezVous::AccountExpired",
    "RendezVous::InvalidPID",
    "RendezVous::ConcurrentLoginDenied",
    "Authentication::NASAuthenticateError",
    "Authentication::TokenExpired",
    "Authentication::ValidationFailed",
    "Authentication::PrincipalIdUnmatched",
})

_OUTAGE_RMC_NAMES = frozenset({
    "RendezVous::DatabaseTemporarilyUnavailable",
    "RendezVous::GameServerMaintenance",
    "Authentication::UnderMaintenance",
    "Authentication::OutOfService",
    "Authentication::ServiceNoLongerAvailable",
    "Authentication::HttpConnectionError",
    "Authentication::HttpDNSError",
    "Transport::ServiceUnavailable",
    "Transport::ConnectionFailure",
    "Transport::ConnectionReset",
    "Transport::TemporaryServerError",
    "Transport::Timeout",
})


def classify_error(exc: BaseException) -> FailureClass:
    return _classify(exc, depth=0)


def _classify(exc: BaseException, depth: int) -> FailureClass:
    if isinstance(exc, InvalidNetworkError):
        return FailureClass.FATAL

    if isinstance(exc, NASCUnavailableError):
        return FailureClass.OUTAGE

    if isinstance(exc, NASCError):
        if getattr(exc, "return_code", None) in _FATAL_NASC_CODES:
            return FailureClass.FATAL
        return FailureClass.OUTAGE

    if isinstance(exc, RMCError):
        name = _rmc_name(exc)
        if name in _AUTH_RMC_NAMES:
            return FailureClass.AUTH
        if name in _OUTAGE_RMC_NAMES:
            return FailureClass.OUTAGE
        return FailureClass.TRANSIENT

    if isinstance(exc, BaseExceptionGroup):
        classes = [_classify(child, depth + 1) for child in exc.exceptions]
        return max(classes, key=lambda item: item.severity) if classes else FailureClass.TRANSIENT

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return FailureClass.OUTAGE

    if isinstance(exc, RuntimeError) and "prudp connection failed" in str(exc).lower():
        return FailureClass.OUTAGE

    if depth < 4:
        nested = exc.__cause__ or exc.__context__
        if nested is not None:
            return _classify(nested, depth + 1)

    return FailureClass.TRANSIENT


def describe_failure(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return describe_failure(exc.exceptions[0])
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        return describe_failure(cause)
    return str(exc) or type(exc).__name__


def _rmc_name(exc: RMCError) -> str:
    try:
        return exc.name()
    except Exception:
        return ""


class Backoff:
    def __init__(self, initial: float = 1.0, maximum: float = 60.0):
        self.initial = max(0.05, initial)
        self.maximum = max(self.initial, maximum)
        self.next = self.initial

    @classmethod
    def from_env(cls) -> Backoff:
        return cls(
            initial=env_float("SCHEDULER_BACKOFF_INITIAL", 1.0, 0.05),
            maximum=env_float("SCHEDULER_BACKOFF_MAX", 60.0, 0.05),
        )

    def reset(self) -> None:
        self.next = self.initial

    def next_delay(self) -> float:
        delay = self.next
        jitter = random.uniform(0.0, delay * 0.1)
        self.next = min(self.maximum, self.next * 2)
        return delay + jitter
