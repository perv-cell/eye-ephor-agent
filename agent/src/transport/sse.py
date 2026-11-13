"""Отправка одного батча на Next.js как одного SSE-события.

POST {INGEST_URL}
Content-Type: text/event-stream; charset=utf-8
X-Agent-Id / X-Batch-Id / X-User-Id

Тело — ровно одно событие log-batch. В data:
batch_id, agent_id, user_id, host, os, sent_at, count, schema_version,
purpose, groups[], records[] (полные записи с features).
"""

import json
import logging

import httpx

from src.config import Settings
from src.credentials import load_credentials
from src.errors import PermanentSendError, RetryableSendError
from src.models.record import LogRecord
from src.pipeline.grouper import build_batch
from src.runtime import current_os_name

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {408, 409, 425, 429}


def encode_sse(payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    frame = f"event: log-batch\nid: {payload['batch_id']}\ndata: {data}\n\n"
    return frame.encode("utf-8")


class SseTransport:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(settings.HTTP_TIMEOUT_SECONDS))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_records(self, records: list[LogRecord]) -> dict:
        if not records:
            return {}
        creds = load_credentials(self._settings.credentials_file())
        user_id = ""
        if creds and creds.can_train:
            user_id = creds.user_id
        elif self._settings.TRAINING_USER_ID.strip():
            user_id = self._settings.TRAINING_USER_ID.strip()
        payload = build_batch(
            records,
            agent_id=self._settings.resolved_agent_id(),
            host=self._settings.resolved_hostname(),
            os_name=current_os_name(),
            user_id=user_id,
        )
        headers = {
            "Content-Type": "text/event-stream; charset=utf-8",
            "Accept": "application/json, text/plain",
            "X-Agent-Id": payload["agent_id"],
            "X-Batch-Id": payload["batch_id"],
        }
        if user_id:
            headers["X-User-Id"] = user_id
        if creds and creds.device_id:
            headers["X-Device-Id"] = creds.device_id
        token = self._settings.INGEST_TOKEN.strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self._client.post(
                self._settings.INGEST_URL,
                content=encode_sse(payload),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise RetryableSendError(str(exc)) from exc
        if response.status_code in _RETRY_STATUSES or response.status_code >= 500:
            raise RetryableSendError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            detail = response.text[:300].replace("\n", " ")
            raise PermanentSendError(f"HTTP {response.status_code}: {detail}")
        logger.debug("веб-батч %s отправлен, записей: %s", payload["batch_id"], payload["count"])
        return payload
