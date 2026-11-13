"""Настройки агента сбора логов.

Значения читаются из окружения и из файла `.env` в корне агента.
API-ключ и user_id пользователь вводит в веб-UI; они сохраняются в
~/.oko-efora/credentials.json — в .env их больше не нужно прописывать.
"""

from functools import lru_cache
from pathlib import Path
import socket

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_CREDENTIALS = Path.home() / ".oko-efora" / "credentials.json"

_ENVIRONMENTS = {"development", "staging", "production"}
_LOG_SOURCES = {"auto", "journal", "files", "all"}
_READ_FROM = {"end", "start"}


class Settings(BaseSettings):
    """Рабочие параметры агента. Имена полей совпадают с переменными окружения."""

    APP_NAME: str = Field(default="Агент Око Эфора", description="Название агента")
    APP_VERSION: str = Field(default="1.0.0", description="Версия агента")
    DEBUG: bool = Field(default=False, description="Подробный журнал самого агента")
    ENVIRONMENT: str = Field(
        default="development",
        description="Окружение: development, staging или production",
    )
    LOG_LEVEL: str = Field(default="INFO", description="Уровень журнала самого агента")

    INGEST_URL: str = Field(
        default="http://localhost:3000/api/logs/ingest",
        description="Куда слать батчи в веб-интерфейс (SSE POST)",
    )
    INGEST_TOKEN: str = Field(
        default="",
        description="Необязательный Bearer-токен для веб-ingest",
    )

    TRAINING_ENABLED: bool = Field(
        default=True,
        description="Разрешить отправку на обучение, если в credentials есть валидный ключ",
    )
    TRAINING_URL: str = Field(
        default="http://localhost:8000/api/v1/train/logs",
        description="URL сервера обучения модели для пользователя",
    )
    TRAINING_API_KEY: str = Field(
        default="",
        description="Опциональный override API-ключа (обычно берётся из credentials UI)",
    )
    TRAINING_USER_ID: str = Field(
        default="",
        description="Опциональный override user_id (обычно приходит с сервера в credentials)",
    )
    TRAINING_FAIL_SOFT: bool = Field(
        default=True,
        description="Если True — сбой обучения не откатывает уже принятый UI-батч",
    )
    CREDENTIALS_PATH: str = Field(
        default=str(DEFAULT_CREDENTIALS),
        description="Путь к credentials.json, который пишет веб-интерфейс",
    )

    BATCH_INTERVAL_SECONDS: float = Field(
        default=3,
        gt=0,
        le=3600,
        description="Отправлять накопленное не реже чем раз в N секунд",
    )
    BATCH_MAX_LOGS: int = Field(
        default=100,
        ge=1,
        le=100_000,
        description="Отправить батч раньше интервала, когда записей стало столько",
    )
    MAX_BUFFER_LOGS: int = Field(
        default=10_000,
        ge=1,
        description="Сколько записей держать, если сервер не принимает батчи",
    )
    MAX_MESSAGE_CHARS: int = Field(
        default=8_000,
        ge=200,
        le=200_000,
        description="Обрезать текст одной записи до стольких символов",
    )

    HTTP_TIMEOUT_SECONDS: float = Field(default=10, gt=0, le=120)
    HTTP_RETRY_SECONDS: float = Field(
        default=2,
        gt=0,
        le=300,
        description="Пауза перед повтором, если отправка не удалась",
    )
    COLLECTOR_RESTART_SECONDS: float = Field(default=5, gt=0, le=300)

    AGENT_ID: str = Field(default="", description="Идентификатор агента, по умолчанию имя хоста")
    HOSTNAME: str = Field(default="", description="Имя хоста в батче, по умолчанию системное")

    LOG_SOURCES: str = Field(
        default="auto",
        description="auto, journal, files или all",
    )
    READ_FROM: str = Field(
        default="end",
        description="end — только новые строки, start — короткая история и дальше слежение",
    )
    LINUX_LOG_ROOT: str = Field(default="/var/log", description="Корень файловых логов Linux")
    WINDOWS_LOG_ROOT: str = Field(
        default=r"C:\Windows\Logs",
        description="Корень текстовых логов Windows, если включён файловый источник",
    )
    MACOS_LOG_ROOT: str = Field(default="/var/log", description="Корень файловых логов macOS")
    EXTRA_LOG_PATHS: str = Field(
        default="",
        description="Дополнительные файлы через запятую, следить всегда",
    )
    MAX_TAIL_FILES: int = Field(default=500, ge=1, le=20_000)
    FILE_RESCAN_SECONDS: float = Field(
        default=30,
        gt=0,
        le=3600,
        description="Как часто искать новые файлы логов",
    )
    JOURNAL_BACKFILL: int = Field(
        default=200,
        ge=1,
        le=100_000,
        description="Сколько записей journald взять при READ_FROM=start",
    )
    WINDOWS_CHANNELS: str = Field(
        default="",
        description="Каналы Event Log через запятую. Пусто — все включённые",
    )
    WINDOWS_POLL_SECONDS: float = Field(default=2, gt=0, le=300)
    MACOS_LOG_LEVEL: str = Field(
        default="debug",
        description="Уровень log stream на macOS: debug, info или default",
    )

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        if value not in _ENVIRONMENTS:
            allowed = ", ".join(sorted(_ENVIRONMENTS))
            raise ValueError(f"ENVIRONMENT должен быть одним из: {allowed}")
        return value

    @field_validator("LOG_SOURCES")
    @classmethod
    def validate_log_sources(cls, value: str) -> str:
        if value not in _LOG_SOURCES:
            allowed = ", ".join(sorted(_LOG_SOURCES))
            raise ValueError(f"LOG_SOURCES должен быть одним из: {allowed}")
        return value

    @field_validator("READ_FROM")
    @classmethod
    def validate_read_from(cls, value: str) -> str:
        if value not in _READ_FROM:
            raise ValueError("READ_FROM должен быть end или start")
        return value

    @field_validator("INGEST_URL", "TRAINING_URL")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("URL должен начинаться с http:// или https://")
        return cleaned

    @field_validator("MACOS_LOG_LEVEL")
    @classmethod
    def validate_macos_level(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"debug", "info", "default"}:
            raise ValueError("MACOS_LOG_LEVEL должен быть debug, info или default")
        return cleaned

    @model_validator(mode="after")
    def buffer_holds_a_batch(self) -> "Settings":
        if self.MAX_BUFFER_LOGS < self.BATCH_MAX_LOGS:
            raise ValueError("MAX_BUFFER_LOGS должен быть не меньше BATCH_MAX_LOGS")
        return self

    def extra_paths(self) -> list[str]:
        return [item.strip() for item in self.EXTRA_LOG_PATHS.split(",") if item.strip()]

    def windows_channels(self) -> list[str]:
        return [item.strip() for item in self.WINDOWS_CHANNELS.split(",") if item.strip()]

    def resolved_agent_id(self) -> str:
        return self.AGENT_ID.strip() or socket.gethostname()

    def resolved_hostname(self) -> str:
        return self.HOSTNAME.strip() or socket.gethostname()

    def credentials_file(self) -> Path:
        return Path(self.CREDENTIALS_PATH).expanduser()


@lru_cache
def get_settings() -> Settings:
    """Один объект настроек на процесс."""
    return Settings()
