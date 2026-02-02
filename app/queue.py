from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DownloadJob:
    chat_id: int
    request_url: str
    urls: list[str]
    max_height: Optional[int]
    playlist_items: Optional[str]
    progress_message_id: int


class DownloadQueue:
    def __init__(self) -> None:
        self._items: list[DownloadJob] = []
        self._cond = asyncio.Condition()

    def qsize(self) -> int:
        return len(self._items)

    async def enqueue(self, job: DownloadJob) -> int:
        """Enqueue and return 1-based position in queue."""
        async with self._cond:
            self._items.append(job)
            pos = len(self._items)
            self._cond.notify(1)
            return pos

    async def get(self) -> DownloadJob:
        async with self._cond:
            while not self._items:
                await self._cond.wait()
            return self._items.pop(0)

    async def snapshot(self) -> list[DownloadJob]:
        """Return a stable snapshot of currently waiting jobs."""
        async with self._cond:
            return list(self._items)
