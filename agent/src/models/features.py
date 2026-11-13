"""Числовые и булевы признаки записи для Isolation Forest и UI."""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_IPV4 = re.compile(
    r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?!\d)"
)
_IPV6 = re.compile(r"(?i)\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}\b")
_URL = re.compile(r"(?i)\bhttps?://[^\s]+")
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_PATH = re.compile(r"(?:(?:[A-Za-z]:\\|/)[^\s:\"']+)")
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_TOKEN = re.compile(r"\S+")
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/])")
_URL_ENCODED = re.compile(r"(?:%[0-9A-Fa-f]{2}){3,}")
_PATH_TRAVERSAL = re.compile(r"(?:\.\./|\.\.\\)")

_SQL = re.compile(
    r"(?i)(?:\b(?:select|insert|update|delete|drop|union|alter|truncate|exec|execute|"
    r"information_schema|sleep|benchmark)\b|\bor\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+"
    r"|'\s*or\s+'?\d|'?\s*;\s*(?:drop|shutdown)\b)"
)
_XSS = re.compile(
    r"(?i)(?:<script\b|</script\s*>|javascript:|vbscript:|data:text/html|"
    r"\bon(?:error|load|click|mouseover|focus|blur)\s*=|<iframe\b|<svg\b[^>]*onload)"
)
_SHELL = re.compile(
    r"(?i)(?:\b(?:sudo|chmod|chown|bash\s+-c|sh\s+-c|/bin/(?:ba)?sh|cmd\.exe|powershell|"
    r"pwsh|wget\b|curl\b|nc\s+-|ncat\b|python\s+-c|perl\s+-e|ruby\s+-e|"
    r"base64\s+-d|mkfifo|setuid|setgid)\b|`[^`]+`|\$\([^)]+\)|\|\s*(?:sh|bash)\b)"
)
_AUTH_FAILURE = re.compile(
    r"(?i)\b(?:failed\s+password|authentication\s+failure|invalid\s+user|"
    r"login\s+failure|access\s+denied|unauthorized|auth(?:entication)?\s+failed|"
    r"wrong\s+password|bad\s+credentials)\b"
)
_PRIV = re.compile(
    r"(?i)\b(?:sudoers|setuid|setgid|cap_(?:sys|dac|setuid)|privilege(?:s)?\s+escalat|"
    r"runas|pkexec|doas)\b"
)

_LEVEL_SEVERITY = {
    "emergency": 7,
    "alert": 6,
    "critical": 5,
    "error": 4,
    "warning": 3,
    "notice": 2,
    "info": 1,
    "debug": 0,
}

_ERROR_LEVELS = frozenset({"emergency", "alert", "critical", "error"})


def level_severity(level: str) -> int:
    return _LEVEL_SEVERITY.get(level.lower(), 1)


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def extract_ips(message: str) -> set[str]:
    return set(_IPV4.findall(message)) | {item.lower() for item in _IPV6.findall(message)}


@dataclass(slots=True)
class _WindowEvent:
    ts: float
    level: str
    ips: frozenset[str]


class SourceMinuteTracker:
    """Скользящее окно ~1 минуты по источнику/каналу для rate-признаков."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._events: dict[str, deque[_WindowEvent]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def observe(
        self,
        *,
        source: str,
        channel: str,
        ts: datetime,
        level: str,
        message: str,
    ) -> dict[str, float | int]:
        moment = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        stamp = moment.timestamp()
        key = f"{source}\0{channel}"
        event = _WindowEvent(stamp, level.lower(), frozenset(extract_ips(message)))
        with self._lock:
            queue = self._events[key]
            queue.append(event)
            cutoff = stamp - self._window
            while queue and queue[0].ts < cutoff:
                queue.popleft()
            total = len(queue)
            errors = sum(1 for item in queue if item.level in _ERROR_LEVELS)
            ips: set[str] = set()
            for item in queue:
                ips.update(item.ips)
            return {
                "logs_per_minute": total,
                "unique_ips_per_minute": len(ips),
                "error_rate_per_minute": round(errors / total, 6) if total else 0.0,
            }


DEFAULT_RATE_TRACKER = SourceMinuteTracker()


def extract_features(
    *,
    message: str,
    level: str,
    ts: datetime,
    process: str | None,
    pid: int | None,
    source: str = "",
    channel: str = "",
    rate_tracker: SourceMinuteTracker | None = DEFAULT_RATE_TRACKER,
) -> dict[str, Any]:
    moment = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    length = len(message)
    digits = sum(char.isdigit() for char in message)
    alphas = sum(char.isalpha() for char in message)
    spaces = sum(char.isspace() for char in message)
    upper = sum(char.isupper() for char in message)
    special = max(0, length - digits - alphas - spaces)
    denom = length or 1
    tokens = _TOKEN.findall(message)
    rates = (
        rate_tracker.observe(
            source=source or "unknown",
            channel=channel or "unknown",
            ts=moment,
            level=level,
            message=message,
        )
        if rate_tracker is not None
        else {
            "logs_per_minute": 0,
            "unique_ips_per_minute": 0,
            "error_rate_per_minute": 0.0,
        }
    )
    return {
        "message_len": length,
        "message_bytes": len(message.encode("utf-8", "replace")),
        "line_count": message.count("\n") + (1 if message else 0),
        "token_count": len(tokens),
        "avg_token_len": round(sum(len(token) for token in tokens) / len(tokens), 4) if tokens else 0.0,
        "digit_ratio": round(digits / denom, 6),
        "alpha_ratio": round(alphas / denom, 6),
        "space_ratio": round(spaces / denom, 6),
        "special_ratio": round(special / denom, 6),
        "uppercase_ratio": round(upper / denom, 6),
        "entropy": round(shannon_entropy(message), 6),
        "char_diversity": len(set(message)),
        "level_severity": level_severity(level),
        "hour_utc": moment.hour,
        "minute_utc": moment.minute,
        "second_utc": moment.second,
        "weekday": moment.weekday(),
        "logs_per_minute": rates["logs_per_minute"],
        "unique_ips_per_minute": rates["unique_ips_per_minute"],
        "error_rate_per_minute": rates["error_rate_per_minute"],
        "has_ipv4": bool(_IPV4.search(message)),
        "has_ipv6": bool(_IPV6.search(message)),
        "has_url": bool(_URL.search(message)),
        "has_email": bool(_EMAIL.search(message)),
        "has_path": bool(_PATH.search(message)),
        "has_hex": bool(_HEX.search(message)),
        "has_sql_keywords": bool(_SQL.search(message)),
        "has_xss_keywords": bool(_XSS.search(message)),
        "has_shell_keywords": bool(_SHELL.search(message)),
        "has_path_traversal": bool(_PATH_TRAVERSAL.search(message)),
        "has_base64": bool(_BASE64.search(message)),
        "has_url_encoding": bool(_URL_ENCODED.search(message)),
        "has_auth_failure": bool(_AUTH_FAILURE.search(message)),
        "has_priv_keywords": bool(_PRIV.search(message)),
        "pid_present": pid is not None,
        "process_len": len(process or ""),
    }
