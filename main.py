from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from telegram.ext import Application
from telegram.request import HTTPXRequest

from app.bot import Services, build_handlers
from app.config import load_config, load_logging_config
from app.queue import DownloadQueue
from app.state import InMemoryState


logger = logging.getLogger(__name__)


def _setup_logging(project_root: Path) -> None:
    cfg = load_logging_config(project_root)
    level = getattr(logging, cfg.log_level, logging.INFO)

    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _post_init(app: Application) -> None:
    # Worker is started lazily on first interaction (when the app is running)
    # to avoid PTB warnings about tasks created before start.
    app.bot_data.setdefault("worker_task", None)


async def _post_shutdown(app: Application) -> None:
    logger.info("Application shutdown: stopping worker")
    task = app.bot_data.get("worker_task")
    if task is None:
        return
    if getattr(task, "done", None) and task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    logger.info("Worker task stopped")


def main() -> None:
    project_root = Path(__file__).resolve().parent
    _setup_logging(project_root)
    cfg = load_config(project_root, load_env=False)

    queue = DownloadQueue()
    state = InMemoryState(ttl_sec=cfg.selection_ttl_sec)
    services = Services(config=cfg, queue=queue, state=state)

    request = HTTPXRequest(
        connect_timeout=cfg.bot_http_connect_timeout_sec,
        read_timeout=cfg.bot_http_read_timeout_sec,
        write_timeout=cfg.bot_http_write_timeout_sec,
        pool_timeout=cfg.bot_http_pool_timeout_sec,
    )

    application = (
        Application.builder()
        .token(cfg.bot_token)
        .base_url(cfg.bot_api_base_url)
        .base_file_url(cfg.bot_api_file_url)
        .local_mode(cfg.bot_local_mode)
        .request(request)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    application.bot_data["services"] = services

    for h in build_handlers(services):
        application.add_handler(h)

    application.run_polling(close_loop=True)


if __name__ == "__main__":
    main()
