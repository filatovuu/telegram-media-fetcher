from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.downloader import ProbeResult, find_ffmpeg, probe_url
from app.queue import DownloadJob
from app.services import Services
from app.state import PendingSelection

from .keyboards import playlist_page_keyboard, quality_keyboard
from .ui import italic
from .worker_loop import ensure_worker_running

logger = logging.getLogger(__name__)


def _cannot_download_text(url: str) -> str:
    return (
        "This service cannot download from this URL:\n"
        f"{url}\n\n"
        "Please try a different link."
    )


def _require_ffmpeg_text() -> str:
    return (
        "ffmpeg was not found on the server, but downloading video+audio requires merging tracks.\n\n"
        "How to fix:\n"
        "- Install dependencies: pip install -r requirements.txt\n"
        "- Or install system ffmpeg (macOS): brew install ffmpeg"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text("Send me a link to a video or audio and I'll download it and send you the file.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "Commands:\n"
        "/start - start\n"
        "/help - help\n\n"
        "Just send a URL. If the link contains multiple files or multiple quality options, "
        "the bot will show selection buttons."
    )


async def on_text_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services: Services = context.application.bot_data["services"]
    cfg = services.config

    if update.message is None or update.message.text is None:
        return

    url = update.message.text.strip()

    logger.info(
        "Incoming URL chat_id=%s user_id=%s url=%s",
        update.message.chat_id,
        update.message.from_user.id,
        url,
    )

    if not find_ffmpeg():
        await update.message.reply_text(_require_ffmpeg_text())
        return

    status_msg = await update.message.reply_text(italic("Checking the link..."), parse_mode=ParseMode.HTML)

    probe: ProbeResult = await asyncio.to_thread(
        probe_url,
        url,
        ytdlp_js_runtime=cfg.ytdlp_js_runtime,
        ytdlp_remote_components=cfg.ytdlp_remote_components,
    )

    logger.info(
        "Probe result supported=%s playlist_entries=%s heights=%s",
        probe.supported,
        len(probe.playlist_entries),
        probe.heights[:6],
    )

    if not probe.supported:
        await status_msg.edit_text(_cannot_download_text(url))
        return

    pending = PendingSelection(
        chat_id=update.message.chat_id,
        user_id=update.message.from_user.id,
        url=url,
        playlist_entries=probe.playlist_entries,
        selected_indices=set(),
        heights=probe.heights,
        selected_height=None,
    )

    token = services.state.create(pending)

    if len(probe.playlist_entries) > 1:
        text = (
            f"Multiple files were found at the link: {len(probe.playlist_entries)}.\n"
            "Select one file to download."
        )
        await status_msg.edit_text(
            italic(text),
            reply_markup=playlist_page_keyboard(
                token,
                pending,
                page=0,
                page_size=services.config.playlist_page_size,
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    if len(probe.heights) >= 2:
        await status_msg.edit_text(
            italic("Multiple quality options are available. Choose: "),
            reply_markup=quality_keyboard(token, probe.heights),
            parse_mode=ParseMode.HTML,
        )
        return

    pending.selected_indices = {1} if probe.playlist_entries else set()
    await enqueue_from_pending(context, token, progress_message_id=status_msg.message_id)


def _validate_selection_owner(update: Update, pending: PendingSelection) -> bool:
    query = update.callback_query
    if query is None:
        return False

    # Reject clicks by other users (important for group chats)
    if query.from_user and query.from_user.id != pending.user_id:
        return False

    if query.message is None or query.message.chat_id != pending.chat_id:
        return False

    return True


async def on_playlist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services: Services = context.application.bot_data["services"]

    query = update.callback_query
    if query is None or query.data is None:
        return

    await query.answer()

    _, token, action = query.data.split(":", 2)
    pending = services.state.get(token)
    if pending is None:
        await query.edit_message_text("This selection session has expired. Please send the link again.")
        return

    if not _validate_selection_owner(update, pending):
        await query.answer("This selection is not for you.", show_alert=True)
        return

    if action == "noop":
        return

    # Backward compatibility: old buttons from previous versions
    if action in {"all", "none"}:
        await query.answer("You can only select one file now", show_alert=True)
        return

    if action == "done":
        if not pending.selected_indices:
            await query.answer("Select a file first", show_alert=True)
            return
        if len(pending.heights) >= 2:
            await query.edit_message_text(
                italic("Choose download quality:"),
                reply_markup=quality_keyboard(token, pending.heights),
                parse_mode=ParseMode.HTML,
            )
            return
        await enqueue_from_pending(context, token, progress_message_id=query.message.message_id)
        return

    if action.startswith("p"):
        page = int(action[1:])
        await query.edit_message_reply_markup(
            reply_markup=playlist_page_keyboard(
                token,
                pending,
                page=page,
                page_size=services.config.playlist_page_size,
            )
        )
        return

    if action.startswith("t"):
        idx = int(action[1:])
        pending.selected_indices = {idx}

        if len(pending.heights) >= 2:
            await query.edit_message_text(
                italic("Choose download quality:"),
                reply_markup=quality_keyboard(token, pending.heights),
                parse_mode=ParseMode.HTML,
            )
            return

        await enqueue_from_pending(context, token, progress_message_id=query.message.message_id)
        return


async def on_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services: Services = context.application.bot_data["services"]

    query = update.callback_query
    if query is None or query.data is None:
        return

    await query.answer()

    _, token, action = query.data.split(":", 2)
    pending = services.state.get(token)
    if pending is None:
        await query.edit_message_text("This selection session has expired. Please send the link again.")
        return

    if not _validate_selection_owner(update, pending):
        await query.answer("This selection is not for you.", show_alert=True)
        return

    if action == "best":
        pending.selected_height = None
        await query.answer("Selected: best", show_alert=False)
        await enqueue_from_pending(context, token, progress_message_id=query.message.message_id)
        return

    if action.startswith("h"):
        pending.selected_height = int(action[1:])
        await query.answer(f"Selected: up to {pending.selected_height}p", show_alert=False)
        await enqueue_from_pending(context, token, progress_message_id=query.message.message_id)
        return


async def enqueue_from_pending(
    context: ContextTypes.DEFAULT_TYPE,
    token: str,
    *,
    progress_message_id: int,
) -> None:
    services: Services = context.application.bot_data["services"]
    pending = services.state.pop(token)
    if pending is None:
        return

    ensure_worker_running(context.application)

    playlist_items: Optional[str] = None
    if pending.playlist_entries:
        chosen_idx = next(iter(sorted(pending.selected_indices)), None)
        if chosen_idx is None:
            chosen_idx = 1

        playlist_items = str(chosen_idx)
        urls = [pending.url]
    else:
        urls = [pending.url]

    job = DownloadJob(
        chat_id=pending.chat_id,
        request_url=pending.url,
        urls=urls,
        max_height=pending.selected_height,
        playlist_items=playlist_items,
        progress_message_id=progress_message_id,
    )

    pos = await services.queue.enqueue(job)

    logger.info(
        "Enqueued job chat_id=%s urls=%s max_height=%s queue_pos=%s",
        job.chat_id,
        len(job.urls),
        job.max_height,
        pos,
    )

    await context.bot.edit_message_text(
        chat_id=job.chat_id,
        message_id=job.progress_message_id,
        text=italic(f"Added to the queue. Position: {pos}.\nPlease wait..."),
        parse_mode=ParseMode.HTML,
    )


def build_handlers(services: Services):
    _ = services
    return [
        CommandHandler("start", cmd_start),
        CommandHandler("help", cmd_help),
        CallbackQueryHandler(on_playlist_callback, pattern=r"^pl:"),
        CallbackQueryHandler(on_quality_callback, pattern=r"^q:"),
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_url),
    ]
