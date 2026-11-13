"""Буфер, который сбрасывается по таймеру или по числу записей."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from src.errors import PermanentSendError
from src.models.record import LogRecord

logger = logging.getLogger(__name__)

Sender = Callable[[list[LogRecord]], Awaitable[None]]


class LogBatcher:
    def __init__(
        self,
        *,
        max_logs: int,
        interval_seconds: float,
        max_buffer: int,
        retry_seconds: float,
        sender: Sender,
    ) -> None:
        self._max_logs = max_logs
        self._interval = interval_seconds
        self._max_buffer = max_buffer
        self._retry_seconds = retry_seconds
        self._sender = sender
        self._buffer: list[LogRecord] = []
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._retry_at = 0.0

    async def add(self, record: LogRecord) -> None:
        async with self._lock:
            self._append(record)
        await self._drain(size_only=True)

    async def flush(self, *, force: bool = False) -> None:
        await self._drain(size_only=False, force=force)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.flush()

    def _append(self, record: LogRecord) -> None:
        self._buffer.append(record)
        overflow = len(self._buffer) - self._max_buffer
        if overflow > 0:
            del self._buffer[:overflow]
            logger.warning("буфер переполнен, отброшено %s старых записей", overflow)

    def _take(self) -> list[LogRecord]:
        chunk = self._buffer[: self._max_logs]
        del self._buffer[: len(chunk)]
        return chunk

    async def _drain(self, *, size_only: bool, force: bool = False) -> None:
        async with self._send_lock:
            while True:
                async with self._lock:
                    if not force and time.monotonic() < self._retry_at:
                        return
                    ready = len(self._buffer) >= self._max_logs if size_only else bool(self._buffer)
                    if not ready:
                        return
                    chunk = self._take()
                try:
                    await self._sender(chunk)
                except PermanentSendError as exc:
                    logger.error("батч из %s записей отклонён и отброшен: %s", len(chunk), exc)
                    force = False
                    continue
                except Exception as exc:
                    async with self._lock:
                        self._buffer = chunk + self._buffer
                        overflow = len(self._buffer) - self._max_buffer
                        if overflow > 0:
                            del self._buffer[:overflow]
                            logger.warning("буфер переполнен, отброшено %s старых записей", overflow)
                        self._retry_at = time.monotonic() + self._retry_seconds
                    logger.warning(
                        "не удалось отправить батч (%s), повтор через %s с",
                        exc,
                        self._retry_seconds,
                    )
                    return
                force = False
