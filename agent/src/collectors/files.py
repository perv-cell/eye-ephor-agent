"""Поиск и слежение за текстовыми файлами логов.

Коллектор общий для Linux, macOS и Windows: путь и список имён
приходят снаружи, формат строки приводится к LogRecord.
"""

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

from src.config import Settings
from src.models.record import LogRecord, clip_message, normalize_level
from src.runtime import current_os_name

logger = logging.getLogger(__name__)

SKIP_DIR_NAMES = {"journal", "private", "lost+found"}
SKIP_FILE_NAMES = {"wtmp", "btmp", "lastlog", "faillog", "tallylog", "readme"}
SYSLOG_FILE_NAMES = {
    "syslog",
    "messages",
    "auth.log",
    "kern.log",
    "daemon.log",
    "debug",
    "user.log",
    "mail.log",
    "mail.err",
    "mail.warn",
    "secure",
    "cron",
    "boot.log",
    "dmesg",
    "system.log",
}
TEXT_SUFFIXES = {".log", ".out", ".err"}
ARCHIVE_SUFFIXES = {".gz", ".xz", ".bz2", ".zst", ".zip", ".lz4"}
_ROTATED = re.compile(r"\.\d+$")


def looks_like_text(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(1024)
    except OSError:
        return False
    return b"\x00" not in chunk


def _accept(path: Path, *, include_syslog: bool) -> bool:
    name = path.name
    lowered = name.lower()
    if lowered in SKIP_FILE_NAMES or name.startswith("."):
        return False
    if any(lowered.endswith(suffix) for suffix in ARCHIVE_SUFFIXES):
        return False
    if _ROTATED.search(name):
        return False
    is_syslog = lowered in SYSLOG_FILE_NAMES
    is_text_name = path.suffix.lower() in TEXT_SUFFIXES
    if is_syslog and not include_syslog:
        return False
    if not is_syslog and not is_text_name:
        return False
    if not path.is_file():
        return False
    return looks_like_text(path)


def discover_log_files(
    root: Path,
    *,
    include_syslog: bool,
    extra: list[Path],
    limit: int,
    scan_root: bool = True,
) -> list[Path]:
    """Все читаемые текстовые логи под root плюс явно указанные файлы."""
    found: list[Path] = []
    if scan_root and root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name.lower() not in SKIP_DIR_NAMES and not name.startswith(".")
            ]
            for name in filenames:
                path = Path(dirpath) / name
                if _accept(path, include_syslog=include_syslog):
                    found.append(path)
    for path in extra:
        if path.is_file() and looks_like_text(path):
            found.append(path)
    unique = sorted(set(found), key=lambda item: str(item))
    if len(unique) > limit:
        logger.warning("найдено %s файлов логов, слежу за первыми %s", len(unique), limit)
        return unique[:limit]
    return unique


def parse_file_line(
    path: Path,
    line: str,
    *,
    host: str,
    os_name: str,
    max_chars: int,
) -> LogRecord | None:
    message = clip_message(line, max_chars)
    if not message.strip():
        return None
    name = path.name.lower()
    tags = ["file", os_name]
    if name in SYSLOG_FILE_NAMES:
        tags.append("syslog")
    return LogRecord(
        ts=_now(),
        host=host,
        os=os_name,
        source="file",
        channel=str(path),
        level=_guess_level(message),
        message=message,
        tags=tags,
        fields={
            "path": str(path),
            "filename": path.name,
            "parent": str(path.parent),
        },
    )


def _guess_level(message: str) -> str:
    padded = f" {message.upper()} "
    for token, level in (
        (" EMERGENCY ", "emergency"),
        (" FATAL ", "critical"),
        (" CRITICAL ", "critical"),
        (" ERROR ", "error"),
        (" ERR ", "error"),
        (" WARN ", "warning"),
        (" WARNING ", "warning"),
        (" DEBUG ", "debug"),
    ):
        if token in padded:
            return normalize_level(level)
    return "info"


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class FileTailCollector:
    """Следит за файлами и подхватывает новые при следующем обходе каталога."""

    name = "files"

    def __init__(
        self,
        settings: Settings,
        *,
        root: str,
        include_syslog: bool,
        scan_root: bool = True,
    ) -> None:
        self._settings = settings
        self._root = Path(root)
        self._include_syslog = include_syslog
        self._scan_root = scan_root
        self._host = settings.resolved_hostname()
        self._os_name = current_os_name()

    def stream(self) -> AsyncIterator[LogRecord]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[LogRecord]:
        queue: asyncio.Queue[LogRecord] = asyncio.Queue(maxsize=self._settings.MAX_BUFFER_LOGS)
        stop = asyncio.Event()
        supervisor = asyncio.create_task(self._supervise(queue, stop))
        try:
            while True:
                yield await queue.get()
        finally:
            stop.set()
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)

    async def _supervise(self, queue: asyncio.Queue[LogRecord], stop: asyncio.Event) -> None:
        tasks: dict[str, asyncio.Task[None]] = {}
        known: set[str] = set()
        try:
            while not stop.is_set():
                paths = discover_log_files(
                    self._root,
                    include_syslog=self._include_syslog,
                    extra=[Path(item) for item in self._settings.extra_paths()],
                    limit=self._settings.MAX_TAIL_FILES,
                    scan_root=self._scan_root,
                )
                current = {str(path) for path in paths}
                if current != known:
                    logger.info("файловый коллектор следит за %s файлами в %s", len(paths), self._root)
                    known = current
                for path in paths:
                    key = str(path)
                    task = tasks.get(key)
                    if task is None or task.done():
                        tasks[key] = asyncio.create_task(self._tail(path, queue, stop))
                await asyncio.sleep(self._settings.FILE_RESCAN_SECONDS)
        finally:
            for task in tasks.values():
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _tail(self, path: Path, queue: asyncio.Queue[LogRecord], stop: asyncio.Event) -> None:
        from_end = self._settings.READ_FROM == "end"
        if _tail_available():
            await self._tail_with_command(path, queue, stop, from_end=from_end)
            return
        await self._tail_in_python(path, queue, stop, from_end=from_end)

    async def _tail_with_command(
        self,
        path: Path,
        queue: asyncio.Queue[LogRecord],
        stop: asyncio.Event,
        *,
        from_end: bool,
    ) -> None:
        count = "0" if from_end else "+1"
        process = await asyncio.create_subprocess_exec(
            "tail",
            "-n",
            count,
            "-F",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert process.stdout is not None
            while not stop.is_set():
                line = await process.stdout.readline()
                if not line:
                    break
                record = parse_file_line(
                    path,
                    line.decode("utf-8", "replace"),
                    host=self._host,
                    os_name=self._os_name,
                    max_chars=self._settings.MAX_MESSAGE_CHARS,
                )
                if record is not None:
                    await queue.put(record)
        finally:
            await _stop_process(process)

    async def _tail_in_python(
        self,
        path: Path,
        queue: asyncio.Queue[LogRecord],
        stop: asyncio.Event,
        *,
        from_end: bool,
    ) -> None:
        def open_file() -> object:
            handle = path.open("r", encoding="utf-8", errors="replace")
            if from_end:
                handle.seek(0, os.SEEK_END)
            return handle

        handle = await asyncio.to_thread(open_file)
        try:
            while not stop.is_set():
                line = await asyncio.to_thread(handle.readline)  # type: ignore[attr-defined]
                if not line:
                    await asyncio.sleep(0.4)
                    continue
                record = parse_file_line(
                    path,
                    line,
                    host=self._host,
                    os_name=self._os_name,
                    max_chars=self._settings.MAX_MESSAGE_CHARS,
                )
                if record is not None:
                    await queue.put(record)
        finally:
            await asyncio.to_thread(handle.close)  # type: ignore[attr-defined]


def _tail_available() -> bool:
    from shutil import which

    return which("tail") is not None


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()
