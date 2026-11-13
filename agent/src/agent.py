"""Связка коллекторов, батчера и отправки."""

import asyncio
import logging
import signal

from src.collectors import build_collectors
from src.collectors.base import LogCollector
from src.config import Settings, get_settings
from src.credentials import load_credentials
from src.errors import CollectorUnavailable
from src.pipeline.batcher import LogBatcher
from src.runtime import current_os_name
from src.transport.fanout import FanoutTransport

logger = logging.getLogger(__name__)


class Agent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.transport = FanoutTransport(self.settings)
        self.batcher = LogBatcher(
            max_logs=self.settings.BATCH_MAX_LOGS,
            interval_seconds=self.settings.BATCH_INTERVAL_SECONDS,
            max_buffer=self.settings.MAX_BUFFER_LOGS,
            retry_seconds=self.settings.HTTP_RETRY_SECONDS,
            sender=self.transport.send_records,
        )
        self.collectors = build_collectors(self.settings)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        if not self.collectors:
            await self.transport.aclose()
            raise RuntimeError("нет ни одного коллектора логов для этой системы")
        external_stop = stop is not None
        stop = stop or asyncio.Event()
        if not external_stop:
            _install_signals(stop)
        tasks = [
            asyncio.create_task(self._run_collector(collector), name=f"collector:{collector.name}")
            for collector in self.collectors
        ]
        tasks.append(asyncio.create_task(self.batcher.run(), name="batcher"))
        try:
            creds = load_credentials(self.settings.credentials_file())
            auth_mode = "off"
            if not self.settings.TRAINING_ENABLED:
                auth_mode = "disabled"
            elif creds and creds.can_train:
                auth_mode = f"user:{creds.user_id}"
            elif creds and creds.need_reauth:
                auth_mode = "need_reauth"
            else:
                auth_mode = "guest"
            logger.info(
                "агент запущен: os=%s ingest=%s training=%s auth=%s интервал=%s с, размер батча=%s, источники=%s",
                current_os_name(),
                self.settings.INGEST_URL,
                self.settings.TRAINING_URL if self.settings.TRAINING_ENABLED else "off",
                auth_mode,
                self.settings.BATCH_INTERVAL_SECONDS,
                self.settings.BATCH_MAX_LOGS,
                [collector.name for collector in self.collectors],
            )
            await stop.wait()
        finally:
            logger.info("остановка, отправляю остаток батча")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.batcher.flush(force=True)
            finally:
                await self.transport.aclose()

    async def _run_collector(self, collector: LogCollector) -> None:
        delay = self.settings.COLLECTOR_RESTART_SECONDS
        while True:
            try:
                async for record in collector.stream():
                    await self.batcher.add(record)
                logger.warning("коллектор %s завершился, перезапуск через %s с", collector.name, delay)
            except asyncio.CancelledError:
                raise
            except CollectorUnavailable as exc:
                logger.warning("коллектор %s недоступен: %s", collector.name, exc)
            except Exception:
                logger.exception("коллектор %s упал", collector.name)
            await asyncio.sleep(delay)


def _install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            continue
