"""Параллельная отправка: веб-интерфейс (SSE) + сервер Isolation Forest."""

import logging

import httpx

from src.config import Settings
from src.errors import PermanentSendError, RetryableSendError
from src.models.record import LogRecord
from src.transport.sse import SseTransport
from src.transport.training import TrainingTransport

logger = logging.getLogger(__name__)


class FanoutTransport:
    """Сначала UI (обязательно), затем обучающий сервер (по флагу)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(settings.HTTP_TIMEOUT_SECONDS))
        self.web = SseTransport(settings, client=self._client)
        self.training = TrainingTransport(settings, client=self._client)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_records(self, records: list[LogRecord]) -> None:
        if not records:
            return
        payload = await self.web.send_records(records)
        if not self._settings.TRAINING_ENABLED:
            return
        batch_id = str(payload.get("batch_id") or "") or None
        try:
            await self.training.send_records(records, batch_id=batch_id)
        except PermanentSendError as exc:
            if self._settings.TRAINING_FAIL_SOFT:
                logger.error("обучающий сервер отклонил батч, UI уже принял: %s", exc)
                return
            raise
        except RetryableSendError as exc:
            if self._settings.TRAINING_FAIL_SOFT:
                logger.warning("обучающий сервер временно недоступен, UI уже принял: %s", exc)
                return
            raise
