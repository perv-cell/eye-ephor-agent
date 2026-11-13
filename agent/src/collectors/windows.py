"""Windows Event Log через PowerShell.

Нативный EvtSubscribe можно подставить позже тем же контрактом stream().
Сейчас коллектор живой только на Windows и не тянет pywin32.
"""

import asyncio
import json
import logging
import os
import platform
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from src.config import Settings
from src.errors import CollectorUnavailable
from src.models.record import LogRecord, clip_message, normalize_level

logger = logging.getLogger(__name__)

_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$poll = 2
if ($env:EYE_POLL_SECONDS) {
  $parsed = 0
  [void][int]::TryParse($env:EYE_POLL_SECONDS, [ref]$parsed)
  if ($parsed -gt 0) { $poll = $parsed }
}
$fromStart = $env:EYE_READ_FROM -eq 'start'
$explicit = @()
if ($env:EYE_CHANNELS) {
  $explicit = $env:EYE_CHANNELS -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

function Get-Channels {
  if ($explicit.Count -gt 0) { return $explicit }
  return @(Get-WinEvent -ListLog * | Where-Object { $_.IsEnabled -eq $true } | Select-Object -ExpandProperty LogName)
}

function Write-Event($event, $channel) {
  $message = [string]$event.Message
  if ($null -eq $message) { $message = '' }
  if ($message.Length -gt 8000) { $message = $message.Substring(0, 8000) }
  $pidValue = $null
  if ($event.ProcessId) { $pidValue = [int]$event.ProcessId }
  $obj = [ordered]@{
    ts = $event.TimeCreated.ToUniversalTime().ToString('o')
    channel = [string]$channel
    level = [string]$event.LevelDisplayName
    event_id = [int]$event.Id
    provider = [string]$event.ProviderName
    pid = $pidValue
    record_id = [long]$event.RecordId
    message = $message
  }
  Write-Output ($obj | ConvertTo-Json -Compress)
}

$channels = @(Get-Channels)
$seen = @{}
foreach ($name in $channels) {
  if ($fromStart) {
    $backfill = @(Get-WinEvent -LogName $name -MaxEvents 200 | Sort-Object RecordId)
    $seen[$name] = [long]0
    foreach ($event in $backfill) {
      Write-Event $event $name
      $seen[$name] = [long]$event.RecordId
    }
  } else {
    $latest = Get-WinEvent -LogName $name -MaxEvents 1
    if ($latest) { $seen[$name] = [long]$latest.RecordId } else { $seen[$name] = [long]0 }
  }
}

while ($true) {
  foreach ($name in $channels) {
    if (-not $seen.ContainsKey($name)) { $seen[$name] = [long]0 }
    $events = @(Get-WinEvent -LogName $name -MaxEvents 100 | Sort-Object RecordId)
    foreach ($event in $events) {
      $recordId = [long]$event.RecordId
      if ($recordId -gt $seen[$name]) {
        Write-Event $event $name
        $seen[$name] = $recordId
      }
    }
  }
  Start-Sleep -Seconds $poll
}
"""


def parse_windows_event(raw: dict, *, host: str, max_chars: int) -> LogRecord | None:
    message = clip_message(str(raw.get("message") or ""), max_chars)
    if not message.strip():
        return None
    channel = str(raw.get("channel") or "Application")
    pid = raw.get("pid")
    pid_value = int(pid) if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()) else None
    fields: dict[str, str | int | float | bool | None] = {}
    if raw.get("event_id") is not None:
        fields["event_id"] = int(raw["event_id"])
    if raw.get("provider"):
        fields["provider"] = str(raw["provider"])
    if raw.get("record_id") is not None:
        fields["record_id"] = int(raw["record_id"])
    provider = str(raw["provider"]) if raw.get("provider") else None
    return LogRecord(
        ts=_parse_time(raw.get("ts")),
        host=host,
        os="windows",
        source="windows-event",
        channel=channel,
        level=normalize_level(raw.get("level")),
        message=message,
        process=provider,
        pid=pid_value,
        unit=channel,
        tags=["windows", "eventlog", channel.lower()],
        fields=fields,
    )


def _parse_time(value: object) -> datetime:
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


class WindowsEventCollector:
    name = "windows-events"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._host = settings.resolved_hostname()

    def stream(self) -> AsyncIterator[LogRecord]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[LogRecord]:
        if platform.system() != "Windows":
            raise CollectorUnavailable("журнал событий Windows читается только на Windows")
        env = os.environ.copy()
        env["EYE_POLL_SECONDS"] = str(max(1, int(self._settings.WINDOWS_POLL_SECONDS)))
        env["EYE_READ_FROM"] = self._settings.READ_FROM
        env["EYE_CHANNELS"] = ",".join(self._settings.windows_channels())
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
        process.stdin.write(_PS_SCRIPT.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    code = await process.wait()
                    raise CollectorUnavailable(f"powershell завершился с кодом {code}")
                record = self._parse(line)
                if record is not None:
                    yield record
        finally:
            stderr_task.cancel()
            await _stop_process(process)
            await asyncio.gather(stderr_task, return_exceptions=True)

    def _parse(self, line: bytes) -> LogRecord | None:
        text = line.decode("utf-8", "replace").lstrip("\ufeff").strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("powershell прислал не JSON: %s", text[:200])
            return None
        if not isinstance(raw, dict):
            return None
        return parse_windows_event(raw, host=self._host, max_chars=self._settings.MAX_MESSAGE_CHARS)


async def _drain_stderr(stream: asyncio.StreamReader) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        logger.info("windows-events: %s", line.decode("utf-8", "replace").rstrip())


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()
