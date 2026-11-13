"""Linux: journald и, отдельно, файлы, которые в журнал не попадают."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from shutil import which

from src.config import Settings
from src.errors import CollectorUnavailable
from src.models.record import LogRecord, clip_message, normalize_level

logger = logging.getLogger(__name__)


def parse_journal_entry(raw: dict, *, host: str, max_chars: int) -> LogRecord | None:
    message = _journal_message(raw.get("MESSAGE"))
    message = clip_message(message, max_chars)
    if not message.strip():
        return None
    unit = _as_str(raw.get("_SYSTEMD_UNIT"))
    identifier = _as_str(raw.get("SYSLOG_IDENTIFIER"))
    channel = unit or identifier or "journal"
    process = _as_str(raw.get("_COMM")) or identifier
    pid = _as_int(raw.get("_PID"))
    uid = _as_int(raw.get("_UID"))
    gid = _as_int(raw.get("_GID"))
    executable = _as_str(raw.get("_EXE"))
    cmdline = clip_message(_as_str(raw.get("_CMDLINE") or "") or "", max_chars) or None
    facility = _as_str(raw.get("SYSLOG_FACILITY"))
    container = _as_str(raw.get("CONTAINER_NAME") or raw.get("_SYSTEMD_CGROUP"))
    fields: dict[str, str | int | float | bool | None] = {}
    if identifier:
        fields["identifier"] = identifier
    if raw.get("PRIORITY") is not None:
        fields["priority"] = str(raw["PRIORITY"])
    boot_id = _as_str(raw.get("_BOOT_ID"))
    if boot_id:
        fields["boot_id"] = boot_id
    machine_id = _as_str(raw.get("_MACHINE_ID"))
    if machine_id:
        fields["machine_id"] = machine_id
    transport = _as_str(raw.get("_TRANSPORT"))
    if transport:
        fields["transport"] = transport
    code_file = _as_str(raw.get("CODE_FILE"))
    if code_file:
        fields["code_file"] = code_file
    code_line = _as_str(raw.get("CODE_LINE"))
    if code_line:
        fields["code_line"] = code_line
    cursor = _as_str(raw.get("__CURSOR"))
    if cursor:
        fields["cursor"] = cursor
    tags = ["linux", "journal"]
    if unit:
        tags.append("systemd-unit")
    if container:
        tags.append("container")
    return LogRecord(
        ts=_timestamp(raw.get("__REALTIME_TIMESTAMP")),
        host=_as_str(raw.get("_HOSTNAME")) or host,
        os="linux",
        source="journal",
        channel=channel,
        level=normalize_level(raw.get("PRIORITY")),
        message=message,
        process=process,
        pid=pid,
        uid=uid,
        gid=gid,
        executable=executable,
        cmdline=cmdline,
        facility=facility,
        unit=unit,
        container=container,
        tags=tags,
        fields=fields,
    )


def _journal_message(value: object) -> str:
    if isinstance(value, list):
        numbers = [item for item in value if isinstance(item, int)]
        return bytes(numbers).decode("utf-8", "replace")
    if value is None:
        return ""
    return str(value)


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    return None


def _timestamp(value: object) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)) / 1_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


class JournalCollector:
    name = "linux-journal"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._host = settings.resolved_hostname()

    def stream(self) -> AsyncIterator[LogRecord]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[LogRecord]:
        if which("journalctl") is None:
            raise CollectorUnavailable("journalctl не найден в PATH")
        backfill = "0" if self._settings.READ_FROM == "end" else str(self._settings.JOURNAL_BACKFILL)
        process = await asyncio.create_subprocess_exec(
            "journalctl",
            "--no-pager",
            "-o",
            "json",
            "-n",
            backfill,
            "-f",
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
                    raise CollectorUnavailable(f"journalctl завершился с кодом {code}")
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
            logger.debug("journalctl прислал не JSON: %s", text[:200])
            return None
        if not isinstance(raw, dict):
            return None
        return parse_journal_entry(raw, host=self._host, max_chars=self._settings.MAX_MESSAGE_CHARS)


async def _drain_stderr(stream: asyncio.StreamReader) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        logger.info("journalctl: %s", line.decode("utf-8", "replace").rstrip())


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()
