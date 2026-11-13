"""Локальные credentials (~/.oko-efora/credentials.json).

После входа в UI хранится только device-bound session_token (без API-ключа).
Агент читает файл и шлёт Bearer session на training/auth сервер.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CREDENTIALS_PATH = Path.home() / ".oko-efora" / "credentials.json"


@dataclass(frozen=True, slots=True)
class UserCredentials:
    mode: str
    device_id: str
    user_id: str = ""
    display_name: str = ""
    training_allowed: bool = False
    model: str = "base"
    session_token: str = ""
    session_expires_at: str = ""
    need_reauth: bool = False
    auth_message: str = ""
    # Только для dev-override из .env (не из credentials.json)
    api_key_override: str = ""

    @property
    def can_train(self) -> bool:
        if self.need_reauth or self.mode != "user" or not self.training_allowed:
            return False
        if not self.user_id.strip():
            return False
        # Предпочитаем session; env override — запасной путь для разработки
        return bool(self.session_token.strip() or self.api_key_override.strip())

    @property
    def bearer_token(self) -> str:
        return self.session_token.strip() or self.api_key_override.strip()


def load_credentials(path: Path | None = None) -> UserCredentials | None:
    target = path or DEFAULT_CREDENTIALS_PATH
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("не удалось прочитать credentials %s: %s", target, exc)
        return None
    if not isinstance(raw, dict):
        return None
    # api_key в файле игнорируем намеренно (устаревший небезопасный формат)
    return UserCredentials(
        mode=str(raw.get("mode") or "guest"),
        device_id=str(raw.get("device_id") or ""),
        user_id=str(raw.get("user_id") or ""),
        display_name=str(raw.get("display_name") or ""),
        training_allowed=bool(raw.get("training_allowed")),
        model=str(raw.get("model") or "base"),
        session_token=str(raw.get("session_token") or ""),
        session_expires_at=str(raw.get("session_expires_at") or ""),
        need_reauth=bool(raw.get("need_reauth")),
        auth_message=str(raw.get("auth_message") or ""),
    )
