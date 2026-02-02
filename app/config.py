from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def load_dotenv(dotenv_path: Path) -> None:
    """Minimal .env loader (no external deps).

    - Lines: KEY=VALUE
    - Ignores empty lines and comments (#)
    - Does not override existing environment variables
    """

    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value)
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = value


def _env(name: str, *, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float value for env var {name}: {raw!r}") from exc


def _env_int(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid int value for env var {name}: {raw!r}") from exc


@dataclass(frozen=True)
class YtDlpConfig:
    ytdlp_js_runtime: Optional[str]
    ytdlp_remote_components: Optional[str]


@dataclass(frozen=True)
class LoggingConfig:
    log_level: str


def load_logging_config(project_root: Path, *, load_env: bool = True) -> LoggingConfig:
    """Load logging-related settings.

    Kept separate from `load_config()` so logging can be configured before
    validating bot-specific required env vars.
    """

    if load_env:
        load_dotenv(project_root / ".env")

    raw = os.getenv("LOG_LEVEL", "INFO")
    level = (raw or "INFO").strip().upper()
    if not level:
        level = "INFO"
    return LoggingConfig(log_level=level)


def load_ytdlp_config(project_root: Path, *, load_env: bool = True) -> YtDlpConfig:
    """Load yt-dlp-specific settings.

    This is intentionally separate from `load_config()` so CLI tools can use it
    without requiring bot-specific environment variables.
    """

    if load_env:
        load_dotenv(project_root / ".env")

    ytdlp_js_runtime_raw = os.getenv("YTDLP_JS_RUNTIME")
    ytdlp_js_runtime = ytdlp_js_runtime_raw.strip() if ytdlp_js_runtime_raw else None

    # Important: if the variable is present but empty, keep it as "" to allow
    # explicit disabling (the downloader interprets empty as "disable").
    ytdlp_remote_components_raw = os.getenv("YTDLP_REMOTE_COMPONENTS")
    ytdlp_remote_components = (
        ytdlp_remote_components_raw.strip()
        if ytdlp_remote_components_raw is not None
        else None
    )

    return YtDlpConfig(
        ytdlp_js_runtime=ytdlp_js_runtime,
        ytdlp_remote_components=ytdlp_remote_components,
    )


@dataclass(frozen=True)
class Config:
    bot_token: str
    bot_api_base_url: str
    bot_api_file_url: str
    bot_local_mode: bool
    bot_api_local_path_from: Optional[Path]
    bot_api_local_path_to: Optional[Path]

    bot_http_connect_timeout_sec: float
    bot_http_read_timeout_sec: float
    bot_http_write_timeout_sec: float
    bot_http_pool_timeout_sec: float
    download_root: Path
    progress_min_interval_sec: float
    progress_stall_interval_sec: float
    playlist_page_size: int
    selection_ttl_sec: float

    # yt-dlp advanced options (optional)
    # None means: use built-in defaults.
    # Empty string for remote components means: disable remote components.
    ytdlp_js_runtime: Optional[str]
    ytdlp_remote_components: Optional[str]


def load_config(project_root: Path, *, load_env: bool = True) -> Config:
    if load_env:
        load_dotenv(project_root / ".env")

    ytdlp_cfg = load_ytdlp_config(project_root, load_env=False)

    download_root = Path(_env("DOWNLOAD_ROOT", default=str(project_root / "downloads")))

    local_from_raw = os.getenv("BOT_API_LOCAL_PATH_FROM")
    local_to_raw = os.getenv("BOT_API_LOCAL_PATH_TO")

    local_from = Path(local_from_raw).expanduser().resolve() if local_from_raw else None
    local_to = Path(local_to_raw) if local_to_raw else None


    return Config(
        bot_token=_env("BOT_TOKEN"),
        bot_api_base_url=_env("BOT_API_BASE_URL", default="https://api.telegram.org/bot"),
        bot_api_file_url=_env("BOT_API_FILE_URL", default="https://api.telegram.org/file/bot"),
        bot_local_mode=_env_bool("BOT_LOCAL_MODE", default=False),
        bot_api_local_path_from=local_from,
        bot_api_local_path_to=local_to,

        # Self-hosted Bot API server may take a long time to respond for sendDocument,
        # especially in --local mode when it uploads large files to Telegram DC.
        bot_http_connect_timeout_sec=_env_float("BOT_HTTP_CONNECT_TIMEOUT_SEC", default=10.0),
        bot_http_read_timeout_sec=_env_float("BOT_HTTP_READ_TIMEOUT_SEC", default=600.0),
        bot_http_write_timeout_sec=_env_float("BOT_HTTP_WRITE_TIMEOUT_SEC", default=600.0),
        bot_http_pool_timeout_sec=_env_float("BOT_HTTP_POOL_TIMEOUT_SEC", default=10.0),

        download_root=download_root.expanduser().resolve(),
        progress_min_interval_sec=_env_float("PROGRESS_MIN_INTERVAL_SEC", default=1.0),
        progress_stall_interval_sec=_env_float("PROGRESS_STALL_INTERVAL_SEC", default=10.0),
        playlist_page_size=_env_int("PLAYLIST_PAGE_SIZE", default=10),

        # Selection sessions are stored in memory; TTL prevents unbounded growth.
        selection_ttl_sec=_env_float("SELECTION_TTL_SEC", default=24 * 60 * 60),

        ytdlp_js_runtime=ytdlp_cfg.ytdlp_js_runtime,
        ytdlp_remote_components=ytdlp_cfg.ytdlp_remote_components,
    )
