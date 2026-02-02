from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.state import PendingSelection


def _fmt_duration(sec: Optional[int]) -> str:
    if not sec or sec <= 0:
        return ""
    minutes = sec // 60
    seconds = sec % 60
    return f" ({minutes}:{seconds:02d})"


def playlist_page_keyboard(
    token: str,
    pending: PendingSelection,
    *,
    page: int,
    page_size: int,
) -> InlineKeyboardMarkup:
    total = len(pending.playlist_entries)
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))

    start = page * page_size
    end = min(total, start + page_size)

    rows: list[list[InlineKeyboardButton]] = []

    for entry in pending.playlist_entries[start:end]:
        checked = "🔘" if entry.index in pending.selected_indices else "⚪"
        text = f"{checked} {entry.index}. {entry.title[:40]}{_fmt_duration(entry.duration_sec)}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"pl:{token}:t{entry.index}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pl:{token}:p{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{page_count}", callback_data=f"pl:{token}:noop"))
    if page < page_count - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pl:{token}:p{page+1}"))
    rows.append(nav)

    return InlineKeyboardMarkup(rows)


def quality_keyboard(token: str, heights: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    rows.append([InlineKeyboardButton("Best", callback_data=f"q:{token}:best")])

    for h in heights[:6]:
        rows.append([InlineKeyboardButton(f"Up to {h}p", callback_data=f"q:{token}:h{h}")])

    return InlineKeyboardMarkup(rows)
