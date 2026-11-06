"""Worker configuration assembled from the environment."""

from __future__ import annotations

from dataclasses import dataclass, field

from api import scheduler
from api.settings import env_float
from worker.failures import Backoff
from worker.pace import PaceConfig


@dataclass
class WorkerConfig:
    loop_delay: float = 2.0
    startup_delay: float = 2.0
    scheduler_config: scheduler.Config = field(default_factory=scheduler.Config)
    pace_config: PaceConfig = field(default_factory=PaceConfig)
    backoff_config: Backoff = field(default_factory=Backoff)

    @classmethod
    def from_env(cls) -> WorkerConfig:
        return cls(
            loop_delay=env_float("SCHEDULER_LOOP_DELAY", 2.0, 0.2),
            startup_delay=env_float("SCHEDULER_STARTUP_DELAY", 2.0, 0.0),
            scheduler_config=scheduler.Config.from_env(),
            pace_config=PaceConfig.from_env(),
            backoff_config=Backoff.from_env(),
        )
