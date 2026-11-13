"""Какие коллекторы запускать на текущей ОС.

auto на Linux: journald, если есть journalctl, плюс файлы приложений
без классических syslog (они уже есть в журнале).
all включает и журнал, и syslog-файлы — дубли возможны.
"""

import platform
from shutil import which

from src.collectors.base import LogCollector
from src.collectors.darwin import MacOSUnifiedCollector
from src.collectors.files import FileTailCollector
from src.collectors.linux import JournalCollector
from src.collectors.windows import WindowsEventCollector
from src.config import Settings


def build_collectors(settings: Settings) -> list[LogCollector]:
    system = platform.system()
    if system == "Windows":
        return _windows(settings)
    if system == "Darwin":
        return _darwin(settings)
    return _linux(settings)


def _linux(settings: Settings) -> list[LogCollector]:
    mode = settings.LOG_SOURCES
    collectors: list[LogCollector] = []
    has_journal = which("journalctl") is not None
    want_journal = mode in {"auto", "journal", "all"}
    if want_journal and (has_journal or mode == "journal"):
        collectors.append(JournalCollector(settings))
    scan_root = mode in {"auto", "files", "all"}
    if scan_root or settings.extra_paths():
        include_syslog = mode in {"files", "all"} or (mode == "auto" and not has_journal)
        collectors.append(
            FileTailCollector(
                settings,
                root=settings.LINUX_LOG_ROOT,
                include_syslog=include_syslog,
                scan_root=scan_root,
            )
        )
    return collectors


def _darwin(settings: Settings) -> list[LogCollector]:
    mode = settings.LOG_SOURCES
    collectors: list[LogCollector] = []
    has_log = which("log") is not None
    want_unified = mode in {"auto", "journal", "all"}
    if want_unified and (has_log or mode == "journal"):
        collectors.append(MacOSUnifiedCollector(settings))
    scan_root = mode in {"auto", "files", "all"}
    if scan_root or settings.extra_paths():
        include_syslog = mode in {"files", "all"} or (mode == "auto" and not has_log)
        collectors.append(
            FileTailCollector(
                settings,
                root=settings.MACOS_LOG_ROOT,
                include_syslog=include_syslog,
                scan_root=scan_root,
            )
        )
    return collectors


def _windows(settings: Settings) -> list[LogCollector]:
    mode = settings.LOG_SOURCES
    collectors: list[LogCollector] = []
    if mode != "files":
        collectors.append(WindowsEventCollector(settings))
    scan_root = mode in {"auto", "files", "all"}
    if scan_root or settings.extra_paths():
        collectors.append(
            FileTailCollector(
                settings,
                root=settings.WINDOWS_LOG_ROOT,
                include_syslog=True,
                scan_root=scan_root,
            )
        )
    return collectors
