from __future__ import annotations

from typing import Any

from app.services import Services
from app.telegram.handlers import build_handlers as _build_handlers
from app.telegram.worker_loop import worker_loop as _worker_loop


def build_handlers(services: Services):
    return _build_handlers(services)


async def worker_loop(application: Any) -> None:
    await _worker_loop(application)
