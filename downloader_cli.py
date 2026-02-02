from __future__ import annotations

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Package 'yt-dlp' was not found. Install dependencies: pip install -r requirements.txt"
    ) from exc

# Reuse implementation shared with the bot.
from app.config import load_ytdlp_config
from app.downloader import (
    available_heights,
    download_urls,
    entry_to_url,
    find_ffmpeg,
    new_session_id,
    ytdlp_advanced_options,
)


# Default output path. You can change it here.
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "downloads"


class _SilentYtDlpLogger:
    def debug(self, msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        return

    def error(self, msg: str) -> None:
        # Suppress yt-dlp's default "ERROR: ..." output; we print our own message.
        return


def _report_cannot_download(url: str, exc: BaseException) -> None:
    print("\nThis service cannot download from this URL:")
    print(url)
    details = str(exc).strip()
    if details:
        print("\nReason:")
        print(details)


def _extract_first_entry(info: Dict[str, Any]) -> Dict[str, Any]:
    # If URL is a playlist/feed, yt-dlp may return a playlist dict.
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        for entry in entries:
            if isinstance(entry, dict):
                return entry
    return info


def _format_entry_label(entry: Dict[str, Any], index: int) -> str:
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        title = entry.get("id") or "(untitled)"
    duration = entry.get("duration")
    dur_str = ""
    if isinstance(duration, (int, float)) and duration > 0:
        minutes = int(duration) // 60
        seconds = int(duration) % 60
        dur_str = f" ({minutes}:{seconds:02d})"
    return f"{index:>3}) {str(title).strip()}{dur_str}"


def _parse_selection(selection: str, *, max_index: int) -> list[int]:
    # 1-based indices. Supports: "a", "1,3,5", "2-7".
    s = selection.strip().lower()
    if s in {"a", "all", "*"}:
        return list(range(1, max_index + 1))

    result: set[int] = set()
    parts = [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            left, right = [x.strip() for x in part.split("-", 1)]
            if not left.isdigit() or not right.isdigit():
                raise ValueError
            a = int(left)
            b = int(right)
            if a <= 0 or b <= 0:
                raise ValueError
            start = min(a, b)
            end = max(a, b)
            if end > max_index:
                raise ValueError
            for i in range(start, end + 1):
                result.add(i)
        else:
            if not part.isdigit():
                raise ValueError
            i = int(part)
            if i <= 0 or i > max_index:
                raise ValueError
            result.add(i)

    if not result:
        raise ValueError
    return sorted(result)


def _choose_playlist_entries(entries: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    total = len(entries)
    print(f"Multiple videos were found at the URL: {total}.")

    # Show a preview list; for large lists, show the first N.
    preview_count = min(total, 30)
    for idx in range(1, preview_count + 1):
        print(_format_entry_label(entries[idx - 1], idx))
    if total > preview_count:
        print(f"... and {total - preview_count} more (you can select by number).")

    print(
        "Choose which videos to download (e.g. 1,3,5 or 2-10).\n"
        "Enter 'a' to download all, or 'q' to cancel:"
    )

    while True:
        raw = input("> ").strip()
        if raw.lower() in {"q", "quit", "exit"}:
            raise KeyboardInterrupt
        try:
            indices = _parse_selection(raw, max_index=total)
            return [entries[i - 1] for i in indices]
        except Exception:
            print("Invalid input. Example: 1,3,5 or 2-10 or a")


def _prompt_quality_choice(heights: list[int]) -> Optional[int]:
    # Return max height (<= chosen) or None for "best".
    # Show a small, user-friendly menu.
    presets = []
    for h in heights:
        if h not in presets:
            presets.append(h)
        if len(presets) >= 6:
            break

    if len(presets) < 2:
        return None

    print("Available quality options:")
    print("  0) Best (recommended)")
    for idx, h in enumerate(presets, start=1):
        print(f"  {idx}) Up to {h}p")
    print("Choose an option (Enter = 0):")

    while True:
        choice = input("> ").strip()
        if choice == "":
            return None
        if choice.isdigit():
            num = int(choice)
            if num == 0:
                return None
            if 1 <= num <= len(presets):
                return presets[num - 1]
        print("Invalid choice. Enter a number from the list.")


@dataclass(frozen=True)
class ProgressState:
    last_line: str = ""


def _format_progress_line(progress: Dict[str, Any]) -> str:
    # yt-dlp provides convenient string fields: '_percent_str', '_speed_str', '_eta_str'
    percent = (progress.get("_percent_str") or "").strip() or "?%"
    speed = (progress.get("_speed_str") or "").strip() or "?"
    eta = (progress.get("_eta_str") or "").strip() or "?"

    # Also show the filename, if available.
    filename = progress.get("filename")
    if filename:
        name = Path(str(filename)).name
        return f"{percent} | {speed} | ETA {eta} | {name}"

    return f"{percent} | {speed} | ETA {eta}"


def _make_progress_hook(state: ProgressState):
    # state (dataclass frozen) immutable; store last_line in closure cell
    last_line_holder = {"line": state.last_line}

    def hook(progress: Dict[str, Any]) -> None:
        status = progress.get("status")

        if status == "downloading":
            line = _format_progress_line(progress)
            # Rewrite the same terminal line.
            # Add spaces to overwrite the tail of the previous line.
            pad = max(0, len(last_line_holder["line"]) - len(line))
            sys.stdout.write("\r" + line + (" " * pad))
            sys.stdout.flush()
            last_line_holder["line"] = line

        elif status == "finished":
            # Print a newline after progress so the next output starts on a new line.
            sys.stdout.write("\n")
            sys.stdout.flush()

    return hook


def download(
    urls: list[str],
    output_dir: Path,
    *,
    max_height: Optional[int],
    ytdlp_js_runtime: Optional[str],
    ytdlp_remote_components: Optional[str],
) -> None:
    # Keep CLI progress output format unchanged by reusing yt-dlp's raw progress dict.
    raw_hook = _make_progress_hook(ProgressState())

    download_urls(
        urls,
        output_dir=output_dir,
        max_height=max_height,
        playlist_items=None,
        progress_cb=None,
        ytdlp_js_runtime=ytdlp_js_runtime,
        ytdlp_remote_components=ytdlp_remote_components,
        raw_progress_hook=raw_hook,
        telegram_compatibility=False,
        allow_playlist=True,
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="downloader_cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
        Console video downloader (yt-dlp).

        If no URL is provided, the program will ask for it interactively.
        """,
    )
    parser.add_argument("url", nargs="?", help="Video URL or a page containing videos")
    parser.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory (default: ./downloads)",
    )
    parser.add_argument(
        "--choose-quality",
        action="store_true",
        help="Show a quality selection menu (if available) before downloading",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    # Load optional yt-dlp env settings from .env (if present) without requiring bot config.
    project_root = Path(__file__).resolve().parent
    ytdlp_cfg = load_ytdlp_config(project_root)
    advanced_opts = ytdlp_advanced_options(
        ytdlp_js_runtime=ytdlp_cfg.ytdlp_js_runtime,
        ytdlp_remote_components=ytdlp_cfg.ytdlp_remote_components,
    )

    url = (args.url or "").strip()
    if not url:
        print("Enter a video URL or a page URL:")
        url = input("> ").strip()

    if not url:
        print("Error: no URL provided.")
        return 2

    output_dir = Path(os.path.expanduser(args.output)).resolve()

    session_id = new_session_id()
    session_dir = output_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    if not find_ffmpeg():
        print(
            "Error: ffmpeg was not found, but downloading video with audio requires merging tracks.\n"
            "Install dependencies (including ffmpeg) and try again:\n"
            "  pip install -r requirements.txt\n\n"
            "If you prefer system ffmpeg, on macOS you can run:\n"
            "  brew install ffmpeg\n"
        )
        return 1

    max_height: Optional[int] = None
    if args.choose_quality:
        try:
            with YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    "logger": _SilentYtDlpLogger(),
                    **advanced_opts,
                }
            ) as ydl:
                info = ydl.extract_info(url, download=False)
            info = _extract_first_entry(info)
            heights = available_heights(info)
            max_height = _prompt_quality_choice(heights)
        except DownloadError as exc:
            _report_cannot_download(url, exc)
            return 1
        except KeyboardInterrupt:
            print("\nCanceled by user.")
            return 130
        except Exception:
            # If format extraction fails, continue with "best".
            max_height = None

    print(textwrap.dedent(
        f"""\
        Starting download…
        URL: {url}
        Folder: {session_dir}
        """
    ).rstrip())

    # If the URL contains multiple videos (playlist/feed/page), offer a selection
    # to avoid downloading everything.
    selected_urls: list[str] = [url]
    try:
        with YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "noplaylist": False,
                "logger": _SilentYtDlpLogger(),
                **advanced_opts,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)

        if isinstance(info, dict) and info.get("_type") == "playlist":
            raw_entries = info.get("entries") or []
            entries: list[Dict[str, Any]] = [e for e in raw_entries if isinstance(e, dict)]
            if len(entries) > 1:
                chosen = _choose_playlist_entries(entries)
                urls_from_entries = [u for u in (entry_to_url(e) for e in chosen) if u]
                if urls_from_entries:
                    selected_urls = urls_from_entries
    except DownloadError as exc:
        _report_cannot_download(url, exc)
        return 1
    except KeyboardInterrupt:
        print("\nCanceled by user.")
        return 130
    except Exception:
        # If we can't determine the list of videos, just download the original URL.
        selected_urls = [url]

    try:
        download(
            selected_urls,
            session_dir,
            max_height=max_height,
            ytdlp_js_runtime=ytdlp_cfg.ytdlp_js_runtime,
            ytdlp_remote_components=ytdlp_cfg.ytdlp_remote_components,
        )
    except DownloadError as exc:
        _report_cannot_download(url, exc)
        return 1
    except RuntimeError as exc:
        print("\nError:")
        print(str(exc).strip() or repr(exc))
        return 1
    except KeyboardInterrupt:
        print("\nCanceled by user.")
        return 130
    except Exception as exc:
        _report_cannot_download(url, exc)
        return 1

    print("Download finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
