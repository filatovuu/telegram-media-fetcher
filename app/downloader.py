from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

try:
    import imageio_ffmpeg  # type: ignore
except Exception:  # pragma: no cover
    imageio_ffmpeg = None

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


logger = logging.getLogger(__name__)


def _js_runtimes(preferred: Optional[str]) -> Optional[dict[str, dict[str, Any]]]:
    """Return yt-dlp js_runtimes dict.

    yt-dlp expects a dict like {"deno": {}, "node": {}}.
    We include runtimes that are actually present on PATH.
    """

    if preferred:
        preferred = preferred.strip().lower()
        if shutil.which(preferred):
            return {preferred: {}}
        # If user asked for it but it's missing, keep default behavior and let yt-dlp warn.
        return None

    runtimes: dict[str, dict[str, Any]] = {}
    for name in ("deno", "node"):
        if shutil.which(name):
            runtimes[name] = {}

    return runtimes or None


def _remote_components(raw: Optional[str]) -> Optional[set[str]]:
    """Remote components for yt-dlp (EJS challenge solver).

    Typical value: ejs:github
    """

    # Preserve the previous behavior:
    # - If env var is missing / not provided, default to ejs:github
    # - If provided but empty, disable remote components
    if raw is None:
        raw = "ejs:github"
    raw = raw.strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return set(parts) if parts else None


def ytdlp_advanced_options(
    *,
    ytdlp_js_runtime: Optional[str] = None,
    ytdlp_remote_components: Optional[str] = None,
) -> dict[str, Any]:
    js_runtime = ytdlp_js_runtime.strip().lower() if ytdlp_js_runtime else None
    remote_raw = ytdlp_remote_components.strip() if ytdlp_remote_components is not None else None
    return {
        "js_runtimes": _js_runtimes(js_runtime),
        "remote_components": _remote_components(remote_raw),
    }


def _has_video_stream(file_path: Path, *, ffmpeg_path: str) -> bool:
    """Best-effort check that container has a video stream."""

    try:
        proc = subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-i",
                str(file_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except Exception:
        return True  # If we can't check, don't block sending.

    text_out = (proc.stderr or "") + (proc.stdout or "")
    return "Video:" in text_out


def _find_ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def _probe_streams(
    file_path: Path,
    *,
    ffprobe_path: Optional[str],
    ffmpeg_path: str,
) -> dict[str, Optional[str] | bool]:
    """Return best-effort stream info.

    Keys: has_video, has_audio, vcodec, acodec, sar, dar, rotate
    """

    if ffprobe_path:
        try:
            proc = subprocess.run(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(file_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            data = json.loads(proc.stdout or "{}")
            streams = data.get("streams") or []
            vcodec: Optional[str] = None
            acodec: Optional[str] = None
            sar: Optional[str] = None
            dar: Optional[str] = None
            rotate: Optional[str] = None
            has_video = False
            has_audio = False
            for s in streams:
                if not isinstance(s, dict):
                    continue
                codec_type = s.get("codec_type")
                codec_name = s.get("codec_name")
                if codec_type == "video" and isinstance(codec_name, str) and codec_name:
                    has_video = True
                    if vcodec is None:
                        vcodec = codec_name.lower()
                    if sar is None:
                        sample_aspect_ratio = s.get("sample_aspect_ratio")
                        if isinstance(sample_aspect_ratio, str) and sample_aspect_ratio:
                            sar = sample_aspect_ratio
                    if dar is None:
                        display_aspect_ratio = s.get("display_aspect_ratio")
                        if isinstance(display_aspect_ratio, str) and display_aspect_ratio:
                            dar = display_aspect_ratio
                    if rotate is None:
                        tags = s.get("tags")
                        if isinstance(tags, dict):
                            r = tags.get("rotate")
                            if isinstance(r, str) and r:
                                rotate = r
                if codec_type == "audio" and isinstance(codec_name, str) and codec_name:
                    has_audio = True
                    if acodec is None:
                        acodec = codec_name.lower()

            return {
                "has_video": has_video,
                "has_audio": has_audio,
                "vcodec": vcodec,
                "acodec": acodec,
                "sar": sar,
                "dar": dar,
                "rotate": rotate,
            }
        except Exception:
            logger.debug("ffprobe stream probe failed for %s", file_path, exc_info=True)

    # Fallback: only presence check.
    has_video = _has_video_stream(file_path, ffmpeg_path=ffmpeg_path)
    return {
        "has_video": has_video,
        "has_audio": None,
        "vcodec": None,
        "acodec": None,
        "sar": None,
        "dar": None,
        "rotate": None,
    }


def _fix_h264_sar_to_1_1(input_path: Path, *, ffmpeg_path: str) -> Path:
    """Rewrite H.264 bitstream metadata to SAR=1:1 without re-encoding.

    Some sources set a weird SAR that makes Telegram display a square video.
    This uses ffmpeg's h264_metadata bitstream filter.
    """

    out_path = input_path.with_name(f"{input_path.stem} [sar].mp4")
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-c",
        "copy",
        "-bsf:v",
        "h264_metadata=sample_aspect_ratio=1/1",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return out_path


def _transcode_to_telegram_mp4(input_path: Path, *, ffmpeg_path: str) -> Path:
    out_path = input_path.with_name(f"{input_path.stem} [tg].mp4")

    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return out_path


def _extract_audio_m4a(input_path: Path, *, ffmpeg_path: str) -> Path:
    out_path = input_path.with_name(f"{input_path.stem}.m4a")
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(out_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return out_path


class YtDlpLogger:
    def debug(self, msg: str) -> None:
        logger.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        logger.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        logger.error("yt-dlp: %s", msg)


def find_ffmpeg() -> Optional[str]:
    system = shutil.which("ffmpeg")
    if system:
        return system
    if imageio_ffmpeg is not None:
        try:
            return str(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            logger.debug("imageio-ffmpeg lookup failed", exc_info=True)
            return None
    return None


def new_session_id() -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    rnd = uuid.uuid4().hex[:8]
    return f"session-{ts}-{rnd}"


def ensure_session_dir(download_root: Path) -> Path:
    download_root.mkdir(parents=True, exist_ok=True)
    session_dir = download_root / new_session_id()
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def is_valid_url(url: str) -> bool:
    url = url.strip()
    return url.startswith("http://") or url.startswith("https://")


@dataclass(frozen=True)
class PlaylistEntry:
    index: int
    title: str
    url: str
    duration_sec: Optional[int]


@dataclass(frozen=True)
class ProbeResult:
    supported: bool
    reason: Optional[str]
    playlist_entries: list[PlaylistEntry]
    heights: list[int]


def entry_to_url(entry: Dict[str, Any]) -> Optional[str]:
    webpage_url = entry.get("webpage_url")
    if isinstance(webpage_url, str) and webpage_url.startswith("http"):
        return webpage_url

    url = entry.get("url")
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("http"):
        return url

    ie_key = entry.get("ie_key")
    if isinstance(ie_key, str) and ie_key:
        return f"{ie_key}:{url}"

    return url


def available_heights(info: Dict[str, Any]) -> list[int]:
    formats = info.get("formats") or []
    heights: set[int] = set()
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        height = fmt.get("height")
        vcodec = fmt.get("vcodec")
        if isinstance(height, int) and height > 0 and vcodec and vcodec != "none":
            heights.add(height)
    return sorted(heights, reverse=True)


def probe_url(
    url: str,
    *,
    ytdlp_js_runtime: Optional[str] = None,
    ytdlp_remote_components: Optional[str] = None,
) -> ProbeResult:
    """Validate yt-dlp support and extract playlist entries and quality options.

    This is a blocking function; run it in a thread from asyncio code.
    """

    if not is_valid_url(url):
        return ProbeResult(False, "Invalid link. Expected an http/https URL.", [], [])

    try:
        ffmpeg_path = find_ffmpeg()

        advanced = ytdlp_advanced_options(
            ytdlp_js_runtime=ytdlp_js_runtime,
            ytdlp_remote_components=ytdlp_remote_components,
        )
        js_runtimes = advanced["js_runtimes"]
        remote_components = advanced["remote_components"]

        # 1) Lightweight playlist probe
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "logger": YtDlpLogger(),
                "ffmpeg_location": ffmpeg_path,
                "js_runtimes": js_runtimes,
                "remote_components": remote_components,
                "extract_flat": True,
                "skip_download": True,
                "noplaylist": False,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)

        playlist_entries: list[PlaylistEntry] = []
        if isinstance(info, dict) and info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
            for idx, entry in enumerate(entries, start=1):
                entry_url = entry_to_url(entry)
                if not entry_url:
                    continue
                title = entry.get("title")
                if not isinstance(title, str) or not title.strip():
                    title = str(entry.get("id") or "(untitled)")
                duration = entry.get("duration")
                duration_sec = int(duration) if isinstance(duration, (int, float)) else None
                playlist_entries.append(
                    PlaylistEntry(index=idx, title=title.strip(), url=entry_url, duration_sec=duration_sec)
                )

        # 2) Quality probe for the *main* URL (or first entry)
        quality_target = playlist_entries[0].url if playlist_entries else url
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "logger": YtDlpLogger(),
                "ffmpeg_location": ffmpeg_path,
                "js_runtimes": js_runtimes,
                "remote_components": remote_components,
                "noplaylist": True,
            }
        ) as ydl:
            info2 = ydl.extract_info(quality_target, download=False)

        heights = available_heights(info2 if isinstance(info2, dict) else {})

        return ProbeResult(True, None, playlist_entries, heights)

    except DownloadError as exc:
        msg = str(exc).strip() or "This URL is not supported by yt-dlp."
        return ProbeResult(False, msg, [], [])
    except Exception as exc:
        return ProbeResult(False, str(exc).strip() or repr(exc), [], [])


ProgressCallback = Callable[[str, Optional[float]], None]
RawProgressHook = Callable[[Dict[str, Any]], None]


def download_urls(
    urls: Iterable[str],
    *,
    output_dir: Path,
    max_height: Optional[int],
    playlist_items: Optional[str],
    progress_cb: Optional[ProgressCallback],
    ytdlp_js_runtime: Optional[str] = None,
    ytdlp_remote_components: Optional[str] = None,
    raw_progress_hook: Optional[RawProgressHook] = None,
    telegram_compatibility: bool = True,
    allow_playlist: bool = False,
) -> list[Path]:
    """Blocking download. Returns list of produced media files."""

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg not found")

    ffprobe_path = _find_ffprobe()

    urls = list(urls)

    advanced = ytdlp_advanced_options(
        ytdlp_js_runtime=ytdlp_js_runtime,
        ytdlp_remote_components=ytdlp_remote_components,
    )
    js_runtimes = advanced["js_runtimes"]
    remote_components = advanced["remote_components"]
    if js_runtimes is not None:
        logger.info("yt-dlp js runtimes enabled: %s", ",".join(js_runtimes.keys()))
    if remote_components is not None:
        logger.info("yt-dlp remote components enabled: %s", ",".join(sorted(remote_components)))

    logger.info(
        "download_urls urls=%s output_dir=%s max_height=%s ffmpeg=%s",
        len(urls),
        output_dir,
        max_height,
        ffmpeg_path,
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(output_dir / "%(title).200s [%(id)s].%(ext)s")

    if telegram_compatibility:
        # Prefer Telegram-playable codecs: H.264 (avc1) + AAC (m4a).
        # Fallbacks:
        # - If no compatible video formats exist, try other video+audio.
        # - If the source only provides audio, allow audio-only (bestaudio).
        if max_height:
            format_selector = (
                f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/"
                f"bv*[height<={max_height}][vcodec!=none]+ba/"
                f"best[height<={max_height}][vcodec!=none]/"
                f"best[vcodec!=none]/"
                f"bestaudio/best"
            )
        else:
            format_selector = "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*[vcodec!=none]+ba/best[vcodec!=none]/bestaudio/best"
    else:
        # CLI-style selector: best video+audio, optionally capped by height.
        if max_height:
            format_selector = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/best"
        else:
            format_selector = "bv*+ba/best"

    last_percent = {"v": -1}
    processing_announced = {"v": False}
    file_downloaded: dict[str, float] = {}
    file_total: dict[str, float] = {}

    def _emit_processing() -> None:
        if progress_cb is None:
            return
        if processing_announced["v"]:
            return
        processing_announced["v"] = True
        progress_cb("processing", None)

    def _emit_download_percent(p: float) -> None:
        if progress_cb is None:
            return
        pi = int(max(0.0, min(100.0, p)))
        if pi == last_percent["v"]:
            return
        last_percent["v"] = pi
        progress_cb("download", float(pi))

    def hook(progress: Dict[str, Any]) -> None:
        if raw_progress_hook is not None:
            try:
                raw_progress_hook(progress)
            except Exception:
                # CLI progress is best-effort; never fail downloads because of it.
                logger.debug("raw_progress_hook failed", exc_info=True)

        status = progress.get("status")
        if status in {"postprocessing", "processing"}:
            _emit_processing()
            return

        filename = progress.get("filename")
        key = filename if isinstance(filename, str) and filename else "__unknown__"

        if status == "finished":
            # Mark this file as fully downloaded so aggregate percent reaches 100.
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            if isinstance(total, (int, float)) and total > 0:
                file_total[key] = float(total)
                file_downloaded[key] = float(total)
            # Don't force 100% here; aggregate will update on subsequent events.
            return

        if status != "downloading":
            return

        downloaded = progress.get("downloaded_bytes")
        total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
        if isinstance(downloaded, (int, float)) and downloaded >= 0:
            file_downloaded[key] = float(downloaded)
        if isinstance(total, (int, float)) and total > 0:
            file_total[key] = float(total)

        # Aggregate across multiple downloads (video+audio) so percent doesn't reset.
        totals = [t for t in file_total.values() if t > 0]
        if totals:
            sum_total = float(sum(totals))
            sum_done = 0.0
            for k, t in file_total.items():
                if t <= 0:
                    continue
                d = file_downloaded.get(k, 0.0)
                if d < 0:
                    d = 0.0
                if d > t:
                    d = t
                sum_done += d

            _emit_download_percent(100.0 * sum_done / sum_total)
            return

        # Fallback if totals are unknown: keep the old behavior, but never reset backwards.
        percent_str = progress.get("_percent_str")
        if isinstance(percent_str, str):
            try:
                p = float(percent_str.strip().strip("%"))
            except Exception:
                return
            if p < float(last_percent["v"]):
                return
            _emit_download_percent(p)

    ydl_opts: Dict[str, Any] = {
        "outtmpl": outtmpl,
        "format": format_selector,
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_path,
        "js_runtimes": js_runtimes,
        "remote_components": remote_components,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "logger": YtDlpLogger(),
        "ignoreerrors": False,
        "retries": 3,
        # Default behavior: one input URL -> one output file.
        # For the bot, avoid downloading entire playlists by default.
        "noplaylist": (playlist_items is None) and (not allow_playlist),
    }

    if playlist_items is not None:
        # Restrict playlist download to a specific item. yt-dlp expects a string like "3".
        ydl_opts["playlist_items"] = str(playlist_items)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download(urls)
    except DownloadError as exc:
        msg = str(exc)
        if "requested format is not available" not in msg.lower():
            raise

        # Some sites (generic/html5 extractor) provide a very limited set of formats.
        # Retry once with a fully permissive selector.
        logger.warning("yt-dlp: format not available, retrying with permissive selector")
        ydl_opts2 = dict(ydl_opts)
        if not telegram_compatibility and max_height:
            # Preserve CLI behavior: if height-limited format fails, retry without a limit.
            ydl_opts2["format"] = "bv*+ba/best"
        else:
            ydl_opts2["format"] = "best/bestaudio/bestvideo"
        with YoutubeDL(ydl_opts2) as ydl:
            ydl.download(urls)

    files = _collect_media_files(output_dir)
    logger.info(
        "download finished. media_files=%s (%s)",
        len(files),
        ", ".join([f.name for f in files[:5]]),
    )

    if telegram_compatibility:
        # Post-process for Telegram compatibility.
        # Some sources (e.g. Instagram) can return HEVC/VP9 which plays as 'audio only'
        # on some clients. Also, some downloads may end up as audio-only in an mp4.
        video_exts = {".mp4", ".mkv", ".webm"}
        fixed_any = False
        for p in list(files):
            if p.suffix.lower() not in video_exts:
                continue

            info = _probe_streams(p, ffprobe_path=ffprobe_path, ffmpeg_path=ffmpeg_path)
            has_video = bool(info.get("has_video"))
            has_audio = bool(info.get("has_audio")) if info.get("has_audio") is not None else True
            vcodec = info.get("vcodec")
            sar = info.get("sar")

            if not has_video and has_audio:
                logger.warning("No video stream detected, extracting audio: %s", p.name)
                fixed_any = True
                _emit_processing()
                try:
                    out_audio = _extract_audio_m4a(p, ffmpeg_path=ffmpeg_path)
                    with contextlib.suppress(Exception):
                        p.unlink(missing_ok=True)
                    files.append(out_audio)
                except Exception:
                    # Keep original if extraction failed.
                    pass
                continue

            if has_video and isinstance(vcodec, str) and vcodec and vcodec != "h264":
                logger.warning("Non-H.264 video codec detected (%s), transcoding: %s", vcodec, p.name)
                fixed_any = True
                _emit_processing()
                try:
                    out_mp4 = _transcode_to_telegram_mp4(p, ffmpeg_path=ffmpeg_path)
                    with contextlib.suppress(Exception):
                        p.unlink(missing_ok=True)
                    files.append(out_mp4)
                except Exception:
                    # Keep original if transcode failed.
                    pass

            # Telegram sometimes renders a square if SAR is strange (e.g. 16:9 on a 9:16 frame).
            # If we can detect it, fix it without re-encoding.
            if (
                has_video
                and isinstance(vcodec, str)
                and vcodec == "h264"
                and isinstance(sar, str)
                and sar
                and sar != "1:1"
            ):
                logger.warning("Non-1:1 SAR detected (%s), fixing metadata: %s", sar, p.name)
                fixed_any = True
                _emit_processing()
                try:
                    out_fixed = _fix_h264_sar_to_1_1(p, ffmpeg_path=ffmpeg_path)
                    with contextlib.suppress(Exception):
                        p.unlink(missing_ok=True)
                    files.append(out_fixed)
                except Exception:
                    # Keep original if fix failed.
                    pass

        if fixed_any:
            # Refresh list, prefer newly produced files.
            files = _collect_media_files(output_dir)

    return files


_MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}


def _collect_media_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for p in folder.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in _MEDIA_EXTS:
            continue
        if p.name.endswith(".part"):
            continue
        files.append(p)

    # Prefer newest first; if equal, prefer larger.
    files.sort(key=lambda x: (x.stat().st_mtime, x.stat().st_size), reverse=True)
    return files
