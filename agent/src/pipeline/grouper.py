"""Сборка батча: записи внутри пачки лежат группами по источнику."""

from datetime import datetime, timezone
from uuid import uuid4

from src.models.record import LogRecord


def group_records(records: list[LogRecord]) -> list[dict]:
    order: list[str] = []
    buckets: dict[str, list[LogRecord]] = {}
    for record in records:
        key = f"{record.source}\0{record.channel}"
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(record)
    groups = []
    for key in order:
        items = buckets[key]
        first = items[0]
        groups.append(
            {
                "source": first.source,
                "channel": first.channel,
                "count": len(items),
                "logs": [item.to_payload() for item in items],
            }
        )
    return groups


def build_batch(
    records: list[LogRecord],
    *,
    agent_id: str,
    host: str,
    os_name: str,
    user_id: str = "",
) -> dict:
    payloads = [item.to_payload() for item in records]
    return {
        "batch_id": str(uuid4()),
        "agent_id": agent_id,
        "user_id": user_id or None,
        "host": host,
        "os": os_name,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "schema_version": 2,
        "purpose": ["ui", "isolation_forest"],
        "groups": group_records(records),
        "records": payloads,
    }


def build_training_batch(
    records: list[LogRecord],
    *,
    agent_id: str,
    host: str,
    os_name: str,
    user_id: str,
    batch_id: str | None = None,
) -> dict:
    """Плоский батч для ML: без группировки, с features на каждой записи."""
    return {
        "batch_id": batch_id or str(uuid4()),
        "agent_id": agent_id,
        "user_id": user_id,
        "host": host,
        "os": os_name,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "schema_version": 2,
        "model_hint": "isolation_forest",
        "records": [item.to_payload() for item in records],
    }
