"""Отправка батча на сервер обучения Isolation Forest.

Credentials: session_token из ~/.oko-efora/credentials.json (без API-ключа).
Dev-override: TRAINING_API_KEY + TRAINING_USER_ID в .env.

POST {TRAINING_URL}
Authorization: Bearer {session_token}
X-Device-Id / X-User-Id / X-Model-Id
"""

from __future__ import annotations

import logging

import httpx

from src.config import Settings
from src.credentials import UserCredentials, load_credentials
from src.errors import PermanentSendError, RetryableSendError
from src.models.record import LogRecord
from src.pipeline.grouper import build_training_batch
from src.runtime import current_os_name

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {408, 409, 425, 429}


class TrainingTransport:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(settings.HTTP_TIMEOUT_SECONDS))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def resolve_credentials(self) -> UserCredentials | None:
        file_creds = load_credentials(self._settings.credentials_file())
        env_key = self._settings.TRAINING_API_KEY.strip()
        env_user = self._settings.TRAINING_USER_ID.strip()
        if file_creds and file_creds.can_train:
            return file_creds
        if env_key and env_user:
            return UserCredentials(
                mode="user",
                device_id=self._settings.resolved_agent_id(),
                user_id=env_user,
                training_allowed=True,
                model=f"user:{env_user}",
                api_key_override=env_key,
            )
        return file_creds

    async def send_records(self, records: list[LogRecord], *, batch_id: str | None = None) -> None:
        if not records or not self._settings.TRAINING_ENABLED:
            return
        creds = self.resolve_credentials()
        if creds is None or not creds.can_train:
            logger.debug(
                "обучение пропущено: нет валидной session (гость или нужен повторный вход)"
            )
            return
        token = creds.bearer_token
        payload = build_training_batch(
            records,
            agent_id=self._settings.resolved_agent_id(),
            host=self._settings.resolved_hostname(),
            os_name=current_os_name(),
            user_id=creds.user_id,
            batch_id=batch_id,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-User-Id": creds.user_id,
            "X-Device-Id": creds.device_id,
            "X-Agent-Id": payload["agent_id"],
            "X-Batch-Id": payload["batch_id"],
            "X-Model-Hint": "isolation_forest",
            "X-Model-Id": creds.model,
        }
        # Dev override: если шлём сырой API-ключ из .env — пометим явно
        if creds.api_key_override and not creds.session_token:
            headers["X-Auth-Mode"] = "api_key_override"
        else:
            headers["X-Auth-Mode"] = "session"
        try:
            response = await self._client.post(
                self._settings.TRAINING_URL,
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise RetryableSendError(str(exc)) from exc
        if response.status_code in _RETRY_STATUSES or response.status_code >= 500:
            raise RetryableSendError(f"HTTP {response.status_code}")
        if response.status_code == 401:
            raise PermanentSendError("HTTP 401: сессия отозвана, нужен повторный вход в UI")
        if response.status_code >= 400:
            detail = response.text[:300].replace("\n", " ")
            raise PermanentSendError(f"HTTP {response.status_code}: {detail}")
        logger.debug(
            "обучающий батч %s отправлен пользователю %s (model=%s), записей: %s",
            payload["batch_id"],
            creds.user_id,
            creds.model,
            payload["count"],
        )
