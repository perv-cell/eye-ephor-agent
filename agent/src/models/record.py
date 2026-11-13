"""Одна строка лога в общем виде, независимо от ОС.

Пейлоад рассчитан и на веб-интерфейс, и на обучение Isolation Forest:
текст, метаданные источника и числовые признаки features.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, PrivateAttr

from src.models.features import extract_features

_LEVELS = {
    "0": "emergency",
    "1": "alert",
    "2": "critical",
    "3": "error",
    "4": "warning",
    "5": "notice",
    "6": "info",
    "7": "debug",
    "emerg": "emergency",
    "emergency": "emergency",
    "alert": "alert",
    "crit": "critical",
    "critical": "critical",
    "fatal": "critical",
    "fault": "critical",
    "err": "error",
    "error": "error",
    "warn": "warning",
    "warning": "warning",
    "notice": "notice",
    "info": "info",
    "information": "info",
    "informational": "info",
    "debug": "debug",
    "default": "info",
}


def normalize_level(value: object) -> str:
    if value is None:
        return "info"
    return _LEVELS.get(str(value).strip().lower(), "info")


def clip_message(message: str, limit: int) -> str:
    text = message.replace("\r\n", "\n").strip("\n")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class LogRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    host: str
    os: str
    source: str
    channel: str
    level: str = "info"
    message: str
    process: str | None = None
    pid: int | None = None
    uid: int | None = None
    gid: int | None = None
    executable: str | None = None
    cmdline: str | None = None
    facility: str | None = None
    unit: str | None = None
    container: str | None = None
    tags: list[str] = Field(default_factory=list)
    fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    _features_cache: dict[str, Any] | None = PrivateAttr(default=None)

    def to_payload(self) -> dict[str, Any]:
        """Полный снимок записи для UI и ML."""
        moment = self.ts if self.ts.tzinfo else self.ts.replace(tzinfo=timezone.utc)
        collected = (
            self.collected_at
            if self.collected_at.tzinfo
            else self.collected_at.replace(tzinfo=timezone.utc)
        )
        features = self._features_cache
        if features is None:
            features = extract_features(
                message=self.message,
                level=self.level,
                ts=moment,
                process=self.process,
                pid=self.pid,
                source=self.source,
                channel=self.channel,
            )
            self._features_cache = features
        payload: dict[str, Any] = {
            "id": self.id,
            "ts": moment.astimezone(timezone.utc).isoformat(),
            "collected_at": collected.astimezone(timezone.utc).isoformat(),
            "host": self.host,
            "os": self.os,
            "source": self.source,
            "channel": self.channel,
            "level": self.level,
            "message": self.message,
            "process": self.process,
            "pid": self.pid,
            "uid": self.uid,
            "gid": self.gid,
            "executable": self.executable,
            "cmdline": self.cmdline,
            "facility": self.facility,
            "unit": self.unit,
            "container": self.container,
            "tags": list(self.tags),
            "features": features,
        }
        if self.fields:
            payload["fields"] = self.fields
        return payload
