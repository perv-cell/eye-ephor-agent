"""Точка входа: python -m src.main из каталога agent."""

import asyncio
import logging
import sys

from src.agent import Agent
from src.config import get_settings


def main() -> None:
    settings = get_settings()
    level = logging.DEBUG if settings.DEBUG else getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(Agent(settings).run())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
