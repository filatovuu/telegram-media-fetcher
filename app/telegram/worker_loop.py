from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import Optional

from telegram.ext import Application
from telegram.constants import ParseMode

from app.downloader import download_urls, ensure_session_dir
from app.services import Services

from .send import send_file
from .ui import italic, safe_edit_italic
from .worker import progress_updater, update_waiting_queue_positions

logger = logging.getLogger(__name__)


def _cleanup_session_dir(*, session_dir: Path, download_root: Path) -> None:
    try:
        root = download_root.resolve()
        sd = session_dir.resolve()

        # Safety: never delete outside the configured download_root.
        if not sd.is_relative_to(root):
            logger.warning("Refusing to delete outside download_root: %s", sd)
            return

        # Safety: only remove our own session dirs.
        if not sd.name.startswith("session-"):
            logger.warning("Refusing to delete non-session dir: %s", sd)
            return

        shutil.rmtree(sd)
        logger.info("Cleaned up session dir: %s", sd)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning("Failed to cleanup session dir: %s", session_dir, exc_info=True)


def ensure_worker_running(application: Application) -> None:
    task = application.bot_data.get("worker_task")
    if task is not None and getattr(task, "done", None) and not task.done():
        return

    new_task = application.create_task(worker_loop(application))
    application.bot_data["worker_task"] = new_task
    logger.info("Worker task (re)started")


async def worker_loop(application: Application) -> None:
    services: Services = application.bot_data["services"]
    cfg = services.config

    logger.info("Worker loop started")

    try:
        while True:
            job = await services.queue.get()
            await update_waiting_queue_positions(application, services.queue)

            session_dir: Optional[Path] = None
            job_success = False

            try:
                await application.bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.progress_message_id,
                    text=italic("Starting download... 0%"),
                    parse_mode=ParseMode.HTML,
                )

                logger.info(
                    "Start job chat_id=%s urls=%s max_height=%s request_url=%s",
                    job.chat_id,
                    len(job.urls),
                    job.max_height,
                    job.request_url,
                )

                session_dir = ensure_session_dir(cfg.download_root)
                logger.info("Session dir: %s", session_dir)

                loop = asyncio.get_running_loop()
                progress_updates: asyncio.Queue[tuple[str, Optional[float]]] = asyncio.Queue()

                def progress_cb(phase: str, percent: Optional[float]) -> None:
                    loop.call_soon_threadsafe(progress_updates.put_nowait, (phase, percent))

                progress_task = application.create_task(
                    progress_updater(
                        application=application,
                        chat_id=job.chat_id,
                        message_id=job.progress_message_id,
                        progress_updates=progress_updates,
                        min_interval=cfg.progress_min_interval_sec,
                        stall_interval=cfg.progress_stall_interval_sec,
                    )
                )

                try:
                    files = await asyncio.to_thread(
                        download_urls,
                        job.urls,
                        output_dir=session_dir,
                        max_height=job.max_height,
                        playlist_items=job.playlist_items,
                        progress_cb=progress_cb,
                        ytdlp_js_runtime=cfg.ytdlp_js_runtime,
                        ytdlp_remote_components=cfg.ytdlp_remote_components,
                    )
                finally:
                    progress_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await progress_task

                if not files:
                    logger.warning("Job finished but no files found. session_dir=%s", session_dir)
                    await application.bot.edit_message_text(
                        chat_id=job.chat_id,
                        message_id=job.progress_message_id,
                        text="Download finished, but the file was not found.",
                    )
                    continue

                await safe_edit_italic(
                    application,
                    chat_id=job.chat_id,
                    message_id=job.progress_message_id,
                    text="Download completed... 100%",
                )

                for idx, p in enumerate(files, start=1):
                    prefix = "Uploading file to Telegram"
                    if len(files) > 1:
                        prefix = f"Uploading file to Telegram ({idx}/{len(files)})"

                    await safe_edit_italic(
                        application,
                        chat_id=job.chat_id,
                        message_id=job.progress_message_id,
                        text=f"{prefix}...",
                    )

                    await send_file(
                        application,
                        job.chat_id,
                        p,
                        local_mode=cfg.bot_local_mode,
                        local_path_from=cfg.bot_api_local_path_from,
                        local_path_to=cfg.bot_api_local_path_to,
                    )

                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        logger.warning("Failed to delete sent file: %s", p, exc_info=True)

                job_success = True

                await application.bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.progress_message_id,
                    text="Done.",
                )

                logger.info("Job done chat_id=%s files=%s", job.chat_id, len(files))

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Job failed: %s", exc)
                await application.bot.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.progress_message_id,
                    text=(
                        "This service cannot download from this URL:\n"
                        f"{job.request_url}\n\n"
                        "Please try a different link."
                    ),
                )
            finally:
                if job_success and session_dir is not None:
                    _cleanup_session_dir(session_dir=session_dir, download_root=cfg.download_root)

    except asyncio.CancelledError:
        logger.info("Worker loop cancelled")
        application.bot_data["worker_task"] = None
        raise
