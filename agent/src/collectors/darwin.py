"""macOS Unified Log через `log stream`."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

from src.config import Settings
from src.errors import CollectorUnavailable
from src.models.record import LogRecord, clip_message, normalize_level

logger = logging.getLogger(__name__)


def parse_unified_entry(raw: dict, *, host: str, max_chars: int) -> LogRecord | None:
    message = clip_message(str(raw.get("eventMessage") or raw.get("message") or ""), max_chars)
    if not message.strip():
        return None
    subsystem = str(raw.get("subsystem") or "")
    category = str(raw.get("category") or "")
    channel = subsystem or category or "unified"
    image = str(raw.get("processImagePath") or "")
    process = Path(image).name if image else None
    pid_raw = raw.get("processID")
    pid = int(pid_raw) if isinstance(pid_raw, int) or (isinstance(pid_raw, str) and str(pid_raw).isdigit()) else None
    fields: dict[str, str | int | float | bool | None] = {}
    if subsystem:
        fields["subsystem"] = subsystem
    if category:
        fields["category"] = category
    return LogRecord(
        ts=_parse_time(raw.get("timestamp")),
        host=host,
        os="macos",
        source="unified",
        channel=channel,
        level=normalize_level(raw.get("messageType")),
        message=message,
        process=process or None,
        pid=pid,
        executable=image or None,
        tags=["macos", "unified"],
        fields=fields,
    )


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.now(timezone.utc)
    text = value.strip()
    for candidate in (text, text.replace(" ", "T", 1).replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


class MacOSUnifiedCollector:
    name = "macos-unified"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._host = settings.resolved_hostname()

    def stream(self) -> AsyncIterator[LogRecord]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[LogRecord]:
        if which("log") is None:
            raise CollectorUnavailable("команда log не найдена в PATH")
        process = await asyncio.create_subprocess_exec(
            "log",
            "stream",
            "--style",
            "ndjson",
            "--level",
            self._settings.MACOS_LOG_LEVEL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
        try:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    code = await process.wait()
                    raise CollectorUnavailable(f"log stream завершился с кодом {code}")
                record = self._parse(line)
                if record is not None:
                    yield record
        finally:
            stderr_task.cancel()
            await _stop_process(process)
            await asyncio.gather(stderr_task, return_exceptions=True)

    def _parse(self, line: bytes) -> LogRecord | None:
        text = line.decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("log stream прислал не JSON: %s", text[:200])
            return None
        if not isinstance(raw, dict):
            return None
        return parse_unified_entry(raw, host=self._host, max_chars=self._settings.MAX_MESSAGE_CHARS)


async def _drain_stderr(stream: asyncio.StreamReader) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        logger.info("macos-unified: %s", line.decode("utf-8", "replace").rstrip())


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()
