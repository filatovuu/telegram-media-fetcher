# Downloader Bot (Telegram + yt-dlp)

Download video/audio from a URL on your server and deliver the resulting file to the user in Telegram.

## TL;DR

- Send a URL to the bot.
- The bot downloads the content **on the server where it runs** (via `yt-dlp`).
- The bot sends the resulting file back to you in Telegram.
- You can keep the file in Telegram cache and watch/listen **offline** (e.g., on a plane or with poor connectivity).

## Purpose

This project is a Telegram bot (plus a small CLI tool) for personal “download → keep in Telegram → watch/listen later” workflows:

1) User sends a URL to the bot.
2) The server running this project downloads the content.
3) The server sends the final file to the user via Telegram.
4) The user can keep the file in Telegram cache for offline viewing.

## Features

- URL-driven downloads using `yt-dlp`.
- Telegram bot based on `python-telegram-bot` (v20+).
- Queueing: links are processed sequentially.
- Interactive selection:
	- Playlist/item selection (paged inline keyboard).
	- Quality selection (best / height caps) when available.
- Progress updates via message edits (throttled).
- Optional self-hosted Telegram Bot API server (`telegram-bot-api`, `--local`) for efficient large file uploads.
- CLI mode for local/manual downloads.

## Limitations / Important Notes

- The download queue and selection sessions are **in-memory**. Restarting the bot clears them.
- The bot uses **polling** (not webhooks).
- Downloads are processed **one at a time** (single worker loop).
- Telegram and/or your Bot API server can impose file size and rate limits. For large files, a self-hosted Bot API server with `--local` is recommended.
- YouTube downloads may require a JS runtime (`deno` or `node`) for recent `yt-dlp` versions.

## Architecture (Short)

High-level pipeline:

1) Telegram update handlers parse messages/callbacks.
2) A download job is created and enqueued.
3) A worker loop takes jobs from the queue.
4) The downloader runs `yt-dlp` and post-processing.
5) The sender delivers the file via Telegram (optionally via `file://...` in local mode).
6) Temporary session directory is cleaned up on success.

Code map (starting points):

- Bot entry + wiring: `main.py`, `app/bot.py`, `app/__main__.py`
- Handlers/UI: `app/telegram/handlers.py`, `app/telegram/keyboards.py`, `app/telegram/ui.py`
- Worker + queue: `app/telegram/worker_loop.py`, `app/queue.py`
- Downloading: `app/downloader.py`
- Sending files: `app/telegram/send.py`
- Configuration: `app/config.py`, `.env.example`

## Tech Stack / Libraries

Required dependencies:

- `yt-dlp` — https://github.com/yt-dlp/yt-dlp/
- `python-telegram-bot` — https://github.com/python-telegram-bot/python-telegram-bot

Other key dependencies (see `requirements.txt`):

- `imageio-ffmpeg` (bundles ffmpeg binaries for many environments; a system `ffmpeg` may still be useful)

System tools (recommended / sometimes required):

- `ffmpeg` (merge/transcode/extract audio)
- `deno` or `node` (JS runtime for `yt-dlp` on some sites like YouTube)

## Installation & Run

You can run the project either with Docker (recommended) or directly with Python.

### Requirements

- Python 3.10+ recommended
- (Optional) Docker + Docker Compose (for the full stack)
- `ffmpeg` available (system or via `imageio-ffmpeg`)
- (Optional) `deno`/`node` for YouTube

### Quick Start (Docker Compose — recommended)

This starts **two containers**:

- `telegram-bot-api` — self-hosted Telegram Bot API server (tdlib) with `--local`
- `bot` — your Python bot

1) Create `.env`:

```bash
cp .env.example .env
```

2) Fill required values in `.env`:

- `BOT_TOKEN` — from @BotFather
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` — from https://my.telegram.org

3) Start:

```bash
docker compose up --build
```

Stop:

```bash
docker compose down
```

Follow logs:

```bash
docker compose logs -f
```

### Run Locally (Python)

1) Create venv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Create `.env`:

```bash
cp .env.example .env
```

3) Choose how your bot talks to Telegram:

- Option A (simpler): use official API (`api.telegram.org`) by setting:
	- `BOT_API_BASE_URL=https://api.telegram.org/bot`
	- `BOT_API_FILE_URL=https://api.telegram.org/file/bot`
	- `BOT_LOCAL_MODE=false`

- Option B (recommended for large files): self-hosted Bot API server with `--local`:
	- `BOT_API_BASE_URL=http://localhost:8081/bot`
	- `BOT_API_FILE_URL=http://localhost:8081/file/bot`
	- `BOT_LOCAL_MODE=true`

4) Start the bot:

```bash
python main.py
```

Or:

```bash
python -m app
```

Enable verbose logs:

```bash
LOG_LEVEL=DEBUG python main.py
```

## Configuration (.env)

The project uses a minimal built-in `.env` loader (no extra dependency). Use `.env.example` as the authoritative reference.

### Required

- `BOT_TOKEN` — Telegram bot token (BotFather).

### Bot API URLs

- `BOT_API_BASE_URL` — Bot API base URL (default: `https://api.telegram.org/bot`)
- `BOT_API_FILE_URL` — Bot API file URL (default: `https://api.telegram.org/file/bot`)

Self-hosted example:

```dotenv
BOT_API_BASE_URL=http://localhost:8081/bot
BOT_API_FILE_URL=http://localhost:8081/file/bot
```

### Local Mode (`--local`)

- `BOT_LOCAL_MODE` — `true` to allow `file://...` uploads when using a self-hosted Bot API server started with `--local`.

Path mapping (useful when Bot API server runs in Docker but the bot runs on the host):

- `BOT_API_LOCAL_PATH_FROM` — absolute path on the bot side (host)
- `BOT_API_LOCAL_PATH_TO` — path inside the Bot API container

Example:

```dotenv
BOT_API_LOCAL_PATH_FROM=/ABS/PATH/TO/Downloader_bot/downloads
BOT_API_LOCAL_PATH_TO=/data/downloads
```

### HTTP timeouts (Bot → Bot API)

Self-hosted servers may take a long time to respond when sending large files:

- `BOT_HTTP_CONNECT_TIMEOUT_SEC` (default: `10.0`)
- `BOT_HTTP_READ_TIMEOUT_SEC` (default: `600.0`)
- `BOT_HTTP_WRITE_TIMEOUT_SEC` (default: `600.0`)
- `BOT_HTTP_POOL_TIMEOUT_SEC` (default: `10.0`)

### Downloads / Progress / UI

- `DOWNLOAD_ROOT` (default: `./downloads`) — where session directories are created
- `PROGRESS_MIN_INTERVAL_SEC` (default: `1.0`) — throttles progress message edits
- `PROGRESS_STALL_INTERVAL_SEC` (default: `10.0`) — periodic “still working” updates
- `PLAYLIST_PAGE_SIZE` (default: `10`) — inline keyboard paging size
- `SELECTION_TTL_SEC` (default: `86400`) — how long selection sessions are kept in memory

### yt-dlp advanced options

- `YTDLP_JS_RUNTIME` — pin JS runtime name (e.g. `deno` or `node`)
- `YTDLP_REMOTE_COMPONENTS` — remote components configuration (default `ejs:github`, empty disables)

### Docker Compose (Bot API server)

These are used by `docker-compose.yml` to wire ports/volumes:

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (required for the `telegram-bot-api` container)
- `BOTAPI_HTTP_PORT` (default `8081`)
- `BOTAPI_DATA_DIR` (default `./botapi_data`)
- `BOTAPI_DOWNLOADS_MOUNT` (default `./downloads`)
- `BOTAPI_DOWNLOADS_CONTAINER_PATH` (default `/data/downloads`)

## Telegram API Setup

### Create a Bot Token (BotFather)

1) Open Telegram and start a chat with @BotFather.
2) Run `/newbot`.
3) Copy the token into `.env` as `BOT_TOKEN`.

### Get `api_id` and `api_hash` (for self-hosted Bot API server)

1) Go to https://my.telegram.org
2) Sign in with your phone number.
3) Create an application to obtain `api_id` and `api_hash`.
4) Put them into `.env` as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`.

### Switching between official API and self-hosted server

If the bot was previously used against `api.telegram.org`, Telegram may require `logOut` before switching to a new Bot API server instance.

## Docker & Containers

### Containers (what each one does)

- `telegram-bot-api` container (tdlib Telegram Bot API server)
	- Exposes a Bot API-compatible HTTP endpoint for the bot.
	- Runs with `--local` so it can upload from local disk (`file://...`) and handle larger files.
	- Persists its own state under `botapi_data/`.
	- Shares the `downloads/` volume with the bot so it can read downloaded files directly.

- `bot` container (this Python app)
	- Polls updates from Telegram (via `BOT_API_BASE_URL`).
	- Downloads media with `yt-dlp` into `downloads/`.
	- Sends the resulting files back to the user through the Bot API.

### Why self-host `telegram-bot-api` (and why `--local`)

This project’s “download on a server → deliver into Telegram” workflow benefits from a local Bot API server because it can:

- Upload much larger files (up to 2000 MB) compared to the hosted Bot API limits.
- Upload via `file://...` (the Bot API server reads from disk), which avoids proxying big uploads through your Python process.
- Download files without the hosted `getFile` size cap.

Official references:

- Local server features/limits: https://core.telegram.org/bots/api#using-a-local-bot-api-server
- tdlib server docs (`--local`, switching notes): https://github.com/tdlib/telegram-bot-api

### Size limits (hosted vs local)

Hosted Bot API (`https://api.telegram.org`):

- Upload by multipart/form-data: up to **10 MB** for photos, **50 MB** for other files (video/audio/documents).
- Upload by URL: up to **5 MB** for photos, **20 MB** for other types.
- Download via `getFile`: maximum file size to download is **20 MB**.

Local `telegram-bot-api` with `--local`:

- Upload files up to **2000 MB**.
- Download files without a size limit.
- Upload using local path and the `file://` URI scheme.

Sources:

- Sending files (hosted limits): https://core.telegram.org/bots/api#sending-files
- Local server limits/features: https://core.telegram.org/bots/api#using-a-local-bot-api-server

### Images

The default Compose setup builds two images:

- `Dockerfile.telegram-bot-api`: builds `telegram-bot-api` (tdlib server)
- `Dockerfile.bot`: builds the Python Telegram bot

Data & volumes:

- `botapi_data/` is mounted into `/var/lib/telegram-bot-api` (server state)
- `downloads/` is mounted into both containers for `--local` mode uploads

Common commands:

```bash
# start everything
docker compose up --build

# start only Bot API server
docker compose up --build telegram-bot-api

# stop
docker compose down
```

## Logs & Debugging

- Docker logs: `docker compose logs -f` (or `docker compose logs -f bot`)
- Local run: logs go to stdout

Useful knobs:

- `LOG_LEVEL=DEBUG` for verbose troubleshooting
- Increase `BOT_HTTP_READ_TIMEOUT_SEC` / `BOT_HTTP_WRITE_TIMEOUT_SEC` if you see `telegram.error.TimedOut` during uploads

## Using the Bot (User Flow)

1) Start the bot.
2) Send a URL in a chat.
3) If the link contains multiple items (playlist), pick an item.
4) If multiple qualities are available, pick “Best” or a height cap.
5) Wait for download and delivery.

Tip: if you plan to watch offline, keep the delivered file in Telegram cache (see below).

## Telegram Cache (Offline Viewing Recommendation)

Telegram’s cache size may be limited by default.

Recommendation: increase Telegram cache size so your delivered files are kept longer for offline viewing.

Typical path (may vary by platform):

1) Telegram → Settings
2) Storage and Data
3) Storage Usage
4) Increase cache size

## CLI Usage

Interactive mode:

```bash
python downloader_cli.py
```

Specify a URL and output directory:

```bash
python downloader_cli.py "https://example.com/video" --output ./downloads
```

Quality selection:

```bash
python downloader_cli.py "URL" --choose-quality
```

## License

This project is released under **The Unlicense**: https://unlicense.org

Important:

- This project uses third-party code and libraries (e.g., `yt-dlp`, `python-telegram-bot`, and others).
- Those dependencies may be distributed under different licenses.
- You are responsible for reviewing and complying with all applicable dependency licenses.

## Reporting Issues / Bug Reports

Issues are welcome, but please understand:

- There is **no guarantee** any issue will be fixed.
- The project is maintained on a best-effort basis.

When creating an issue, you MUST include:

1) A link to the page/resource you tried to download
2) The full error log output (attach as text, not screenshots)
3) A clear description of the problem
4) Expected result
5) Actual result

## Disclaimer

You are responsible for how you use this software.

- Respect the terms of service of websites you interact with.
- Only download content you have the right to download.
- This project is provided “as is”, without warranty of any kind.

