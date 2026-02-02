from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from telegram.ext import Application

logger = logging.getLogger(__name__)


def _probe_video_dims(path: Path) -> Optional[tuple[int, int]]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,sample_aspect_ratio:stream_tags=rotate",
                "-of",
                "json",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams or not isinstance(streams[0], dict):
            return None

        s = streams[0]
        w = s.get("width")
        h = s.get("height")
        sar = s.get("sample_aspect_ratio")
        rotate = None
        tags = s.get("tags")
        if isinstance(tags, dict):
            rotate = tags.get("rotate")

        if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
            return None

        if isinstance(sar, str) or isinstance(rotate, str):
            logger.info(
                "Video meta: w=%s h=%s sar=%s rotate=%s file=%s",
                w,
                h,
                sar,
                rotate,
                path.name,
            )

        if isinstance(rotate, str) and rotate.strip() in {"90", "270", "-90"}:
            w, h = h, w

        return w, h
    except Exception:
        logger.debug("ffprobe video dims failed for %s", path, exc_info=True)
        return None


async def send_file(
    application: Application,
    chat_id: int,
    path: Path,
    *,
    local_mode: bool,
    local_path_from: Optional[Path],
    local_path_to: Optional[Path],
) -> None:
    abs_path = path.resolve()

    logger.info("Sending file chat_id=%s path=%s local_mode=%s", chat_id, abs_path, local_mode)

    ext = abs_path.suffix.lower()
    is_video = ext in {".mp4", ".mkv", ".webm"}
    is_audio = ext in {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}

    if local_mode:
        mapped_path = abs_path
        if local_path_from and local_path_to:
            try:
                rel = abs_path.relative_to(local_path_from)
                mapped_path = local_path_to / rel
            except Exception:
                logger.debug(
                    "Failed to map local path abs_path=%s local_from=%s local_to=%s",
                    abs_path,
                    local_path_from,
                    local_path_to,
                    exc_info=True,
                )
                mapped_path = abs_path

        uri = "file://" + quote(str(mapped_path), safe="/")
        logger.info("Sending via local file uri=%s", uri)

        if is_video:
            dims = _probe_video_dims(abs_path)
            extra: dict[str, int] = {}
            if dims is not None:
                extra["width"], extra["height"] = dims
            await application.bot.send_video(
                chat_id=chat_id,
                video=uri,
                caption=path.name,
                supports_streaming=True,
                **extra,
            )
            return

        if is_audio:
            await application.bot.send_audio(chat_id=chat_id, audio=uri, caption=path.name)
            return

        await application.bot.send_document(chat_id=chat_id, document=uri, caption=path.name)
        return

    with abs_path.open("rb") as f:
        if is_video:
            dims = _probe_video_dims(abs_path)
            extra: dict[str, int] = {}
            if dims is not None:
                extra["width"], extra["height"] = dims
            await application.bot.send_video(
                chat_id=chat_id,
                video=f,
                caption=path.name,
                supports_streaming=True,
                **extra,
            )
            return

        if is_audio:
            await application.bot.send_audio(chat_id=chat_id, audio=f, caption=path.name)
            return

        await application.bot.send_document(chat_id=chat_id, document=f, caption=path.name)
