from __future__ import annotations

import asyncio
import time
from typing import Optional

from telegram.ext import Application

from app.queue import DownloadJob, DownloadQueue
from app.telegram.ui import safe_edit_italic


async def update_waiting_queue_positions(application: Application, queue: DownloadQueue) -> None:
    """Update the displayed queue position for all waiting jobs."""

    waiting: list[DownloadJob] = await queue.snapshot()
    for pos, job in enumerate(waiting, start=1):
        await safe_edit_italic(
            application,
            chat_id=job.chat_id,
            message_id=job.progress_message_id,
            text=f"In queue. Position: {pos}.\nPlease wait...",
        )


async def progress_updater(
    *,
    application: Application,
    chat_id: int,
    message_id: int,
    progress_updates: "asyncio.Queue[tuple[str, Optional[float]]]",
    min_interval: float,
    stall_interval: float,
) -> None:
    last_sent_percent = -1
    last_sent_time = 0.0
    start_time = time.monotonic()

    phase: str = "download"

    last_progress_time = start_time
    last_seen_percent = 0

    while True:
        try:
            new_phase, percent = await asyncio.wait_for(progress_updates.get(), timeout=stall_interval)
            now = time.monotonic()

            if isinstance(new_phase, str) and new_phase:
                phase = new_phase

            if phase != "download":
                if now - last_sent_time < min_interval:
                    continue

                last_progress_time = now
                last_sent_time = now
                await safe_edit_italic(
                    application,
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Processing...",
                )
                continue

            if percent is None:
                continue

            percent_int = int(percent)

            if percent_int == last_sent_percent:
                continue
            if now - last_sent_time < min_interval:
                continue

            last_seen_percent = percent_int
            last_progress_time = now
            last_sent_percent = percent_int
            last_sent_time = now

            await safe_edit_italic(
                application,
                chat_id=chat_id,
                message_id=message_id,
                text=f"Downloading... {percent_int}%",
            )

        except asyncio.TimeoutError:
            now = time.monotonic()
            if now - last_sent_time < min_interval:
                continue

            stalled = int(now - last_progress_time)
            if phase != "download":
                stalled_min = stalled // 60
                stalled_sec = stalled % 60
                text = f"Processing... (no progress for {stalled_min}:{stalled_sec:02d})"
            else:
                if last_seen_percent <= 0:
                    text = "Downloading..."
                else:
                    stalled_min = stalled // 60
                    stalled_sec = stalled % 60
                    text = f"Downloading... {last_seen_percent}% (no progress for {stalled_min}:{stalled_sec:02d})"

            last_sent_time = now
            await safe_edit_italic(
                application,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
            )
