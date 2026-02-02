from __future__ import annotations

import secrets
from dataclasses import dataclass
from dataclasses import field
import time
from typing import Optional

from .downloader import PlaylistEntry


def new_token() -> str:
    # callback_data must be <= 64 bytes; keep it short.
    return secrets.token_hex(8)


@dataclass
class PendingSelection:
    chat_id: int
    user_id: int
    url: str

    playlist_entries: list[PlaylistEntry]
    selected_indices: set[int]

    heights: list[int]
    selected_height: Optional[int]

    created_at: float = field(default_factory=time.time)


class InMemoryState:
    def __init__(self, *, ttl_sec: float = 24 * 60 * 60) -> None:
        self.pending: dict[str, PendingSelection] = {}
        self._ttl_sec = float(ttl_sec)

    def create(self, pending: PendingSelection) -> str:
        token = new_token()
        self.pending[token] = pending
        return token

    def get(self, token: str) -> Optional[PendingSelection]:
        pending = self.pending.get(token)
        if pending is None:
            return None
        if (time.time() - pending.created_at) > self._ttl_sec:
            self.pending.pop(token, None)
            return None
        return pending

    def pop(self, token: str) -> Optional[PendingSelection]:
        pending = self.pending.get(token)
        if pending is None:
            return None
        if (time.time() - pending.created_at) > self._ttl_sec:
            self.pending.pop(token, None)
            return None
        return self.pending.pop(token, None)
