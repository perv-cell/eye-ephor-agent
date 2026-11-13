"""Общий контракт источника логов.

Новая ОС или новый формат — отдельный класс со `stream()`,
его регистрируют в `factory.build_collectors`.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from src.models.record import LogRecord


class LogCollector(Protocol):
    name: str

    def stream(self) -> AsyncIterator[LogRecord]:
        """Бесконечный поток новых записей. Конец потока значит, что источник умер."""
