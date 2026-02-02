from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

if TYPE_CHECKING:
    from telegram.ext import Application


logger = logging.getLogger(__name__)


def italic(text: str) -> str:
    return f"<i>{html.escape(text)}</i>"


async def safe_edit_italic(
    application: "Application",
    *,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    try:
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=italic(text),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        # Ignore edit errors (rate limit / message not modified / message deleted).
        logger.debug("safe_edit_italic failed", exc_info=True)


async def safe_edit_plain(
    application: "Application",
    *,
    chat_id: int,
    message_id: int,
    text: str,
) -> None:
    try:
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )
    except Exception:
        # Ignore edit errors (rate limit / message not modified / message deleted).
        logger.debug("safe_edit_plain failed", exc_info=True)
