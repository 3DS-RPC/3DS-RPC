"""Token bucket used to pace friend add/remove operations."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from api.settings import env_float


@dataclass
class PaceConfig:
    rate: float = 1.0
    burst: float = 3.0
    min_rate: float = 0.1

    @classmethod
    def from_env(cls) -> PaceConfig:
        legacy_delay = os.environ.get("FRIEND_OP_DELAY") or os.environ.get("SCHEDULER_OP_DELAY")
        if os.environ.get("FRIEND_OP_RATE") is None and legacy_delay:
            try:
                delay = max(0.05, float(legacy_delay))
            except ValueError:
                delay = 1.0
            rate = 1.0 / delay
            burst = 1.0
        else:
            rate = env_float("FRIEND_OP_RATE", cls.rate, 0.01)
            burst = env_float("FRIEND_OP_BURST", cls.burst, 1.0)

        legacy_max = os.environ.get("FRIEND_OP_MAX_DELAY") or os.environ.get("SCHEDULER_OP_MAX_DELAY")
        if os.environ.get("FRIEND_OP_MIN_RATE") is None and legacy_max:
            try:
                min_rate = 1.0 / max(0.05, float(legacy_max))
            except ValueError:
                min_rate = cls.min_rate
        else:
            min_rate = env_float("FRIEND_OP_MIN_RATE", cls.min_rate, 0.001)

        return cls(rate=rate, burst=burst, min_rate=min(min_rate, rate))


class Pacer:
    def __init__(self, config: PaceConfig | None = None, now: float | None = None):
        self.config = config or PaceConfig()
        self.rate = max(self.config.rate, self.config.min_rate)
        self.capacity = max(1.0, self.config.burst)
        self.tokens = self.capacity
        self.last = time.monotonic() if now is None else now

    def _refill(self, now: float) -> None:
        if now > self.last:
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now

    def reserve(self, now: float) -> float:
        """Reserves one operation and returns how long to wait before it."""
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0

        wait = (1.0 - self.tokens) / self.rate
        self.tokens = 0.0
        self.last = now
        return wait

    def on_success(self) -> None:
        self.rate = min(self.config.rate, self.rate * 1.1)

    def on_pressure(self) -> None:
        self.rate = max(self.config.min_rate, self.rate * 0.5)
        self.tokens = 0.0
