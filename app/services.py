from __future__ import annotations

from dataclasses import dataclass

from app.config import Config
from app.queue import DownloadQueue
from app.state import InMemoryState


@dataclass(frozen=True)
class Services:
    config: Config
    queue: DownloadQueue
    state: InMemoryState
