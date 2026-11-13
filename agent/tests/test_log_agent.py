import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from src.collectors.darwin import parse_unified_entry
from src.collectors.files import discover_log_files
from src.collectors.linux import parse_journal_entry
from src.collectors.windows import parse_windows_event
from src.config import Settings, get_settings
from src.errors import PermanentSendError
from src.credentials import load_credentials
from src.models.record import LogRecord
from src.models.features import DEFAULT_RATE_TRACKER, SourceMinuteTracker, extract_features
from src.pipeline.batcher import LogBatcher
from src.pipeline.grouper import build_batch, group_records
from src.transport.sse import encode_sse
from src.transport.training import TrainingTransport


def _record(channel: str, message: str, source: str = "file") -> LogRecord:
    return LogRecord(
        ts=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
        host="host",
        os="linux",
        source=source,
        channel=channel,
        message=message,
    )


class DiscoverTests(unittest.TestCase):
    def test_skips_archives_binary_and_journal_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "app.log").write_text("ok\n", encoding="utf-8")
            (root / "syslog").write_text("sys\n", encoding="utf-8")
            (root / "old.log.1").write_text("rot\n", encoding="utf-8")
            (root / "old.gz").write_bytes(b"\x1f\x8b")
            (root / "binary.log").write_bytes(b"a\x00b")
            journal = root / "journal"
            journal.mkdir()
            (journal / "system.log").write_text("nope\n", encoding="utf-8")
            extra = root / "notes.txt"
            extra.write_text("extra\n", encoding="utf-8")

            without_syslog = discover_log_files(
                root,
                include_syslog=False,
                extra=[extra],
                limit=20,
            )
            self.assertEqual(
                [path.name for path in without_syslog],
                ["app.log", "notes.txt"],
            )

            with_syslog = discover_log_files(
                root,
                include_syslog=True,
                extra=[],
                limit=20,
            )
            self.assertEqual([path.name for path in with_syslog], ["app.log", "syslog"])

    def test_scan_root_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "app.log").write_text("ok\n", encoding="utf-8")
            extra = root / "only.txt"
            extra.write_text("x\n", encoding="utf-8")
            found = discover_log_files(
                root,
                include_syslog=True,
                extra=[extra],
                limit=20,
                scan_root=False,
            )
            self.assertEqual(found, [extra])


class ParserTests(unittest.TestCase):
    def test_journal_entry(self) -> None:
        record = parse_journal_entry(
            {
                "MESSAGE": [104, 105],
                "__REALTIME_TIMESTAMP": "1700000000000000",
                "_SYSTEMD_UNIT": "ssh.service",
                "_COMM": "sshd",
                "_PID": "42",
                "PRIORITY": "3",
                "SYSLOG_IDENTIFIER": "sshd",
            },
            host="box",
            max_chars=100,
        )
        assert record is not None
        self.assertEqual(record.message, "hi")
        self.assertEqual(record.channel, "ssh.service")
        self.assertEqual(record.level, "error")
        self.assertEqual(record.pid, 42)
        self.assertEqual(record.source, "journal")

    def test_windows_event(self) -> None:
        record = parse_windows_event(
            {
                "ts": "2026-11-10T12:00:00.0000000Z",
                "channel": "System",
                "level": "Warning",
                "event_id": 100,
                "provider": "Disk",
                "pid": 7,
                "record_id": 5,
                "message": "disk",
            },
            host="win",
            max_chars=100,
        )
        assert record is not None
        self.assertEqual(record.os, "windows")
        self.assertEqual(record.channel, "System")
        self.assertEqual(record.level, "warning")
        self.assertEqual(record.fields["event_id"], 100)

    def test_unified_entry(self) -> None:
        record = parse_unified_entry(
            {
                "timestamp": "2026-11-10 12:00:00+0000",
                "eventMessage": "hello",
                "subsystem": "com.apple.ssh",
                "category": "auth",
                "processID": 9,
                "processImagePath": "/usr/bin/sshd",
                "messageType": "Error",
            },
            host="mac",
            max_chars=100,
        )
        assert record is not None
        self.assertEqual(record.os, "macos")
        self.assertEqual(record.channel, "com.apple.ssh")
        self.assertEqual(record.process, "sshd")
        self.assertEqual(record.level, "error")


class FeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        DEFAULT_RATE_TRACKER.reset()

    def test_payload_has_features_and_identity(self) -> None:
        record = _record("ssh.service", "ERROR login from 10.0.0.8 /var/log/auth", source="journal")
        payload = record.to_payload()
        self.assertIn("id", payload)
        self.assertIn("collected_at", payload)
        self.assertEqual(payload["host"], "host")
        self.assertEqual(payload["os"], "linux")
        self.assertIn("features", payload)
        self.assertGreater(payload["features"]["message_len"], 0)
        self.assertTrue(payload["features"]["has_ipv4"])
        self.assertTrue(payload["features"]["has_path"])
        self.assertEqual(payload["features"]["level_severity"], 1)
        self.assertEqual(payload["features"]["logs_per_minute"], 1)
        self.assertEqual(payload["features"]["unique_ips_per_minute"], 1)
        self.assertEqual(payload["features"]["char_diversity"], len(set(record.message)))
        # повторный to_payload не должен удваивать rate-признаки
        again = record.to_payload()
        self.assertEqual(again["features"]["logs_per_minute"], 1)

    def test_extract_features_entropy(self) -> None:
        features = extract_features(
            message="aaaa",
            level="error",
            ts=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc),
            process="sshd",
            pid=1,
            rate_tracker=None,
        )
        self.assertEqual(features["level_severity"], 4)
        self.assertEqual(features["entropy"], 0.0)
        self.assertEqual(features["char_diversity"], 1)
        self.assertEqual(features["logs_per_minute"], 0)

    def test_security_and_rate_features(self) -> None:
        tracker = SourceMinuteTracker()
        base = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        first = extract_features(
            message="GET /?q=SELECT * FROM users UNION SELECT password",
            level="info",
            ts=base,
            process="nginx",
            pid=10,
            source="file",
            channel="/var/log/nginx/access.log",
            rate_tracker=tracker,
        )
        self.assertTrue(first["has_sql_keywords"])
        self.assertFalse(first["has_xss_keywords"])
        self.assertEqual(first["logs_per_minute"], 1)
        self.assertEqual(first["error_rate_per_minute"], 0.0)

        second = extract_features(
            message='<script>alert(1)</script> from 10.0.0.2 failed password',
            level="error",
            ts=base,
            process="nginx",
            pid=10,
            source="file",
            channel="/var/log/nginx/access.log",
            rate_tracker=tracker,
        )
        self.assertTrue(second["has_xss_keywords"])
        self.assertTrue(second["has_auth_failure"])
        self.assertEqual(second["logs_per_minute"], 2)
        self.assertEqual(second["unique_ips_per_minute"], 1)
        self.assertEqual(second["error_rate_per_minute"], 0.5)

        shell = extract_features(
            message="user ran sudo bash -c 'chmod u+s /tmp' setuid ../../etc/passwd",
            level="warning",
            ts=base,
            process="audit",
            pid=11,
            source="journal",
            channel="audit.service",
            rate_tracker=tracker,
        )
        self.assertTrue(shell["has_shell_keywords"])
        self.assertTrue(shell["has_path_traversal"])
        self.assertTrue(shell["has_priv_keywords"])


class GroupAndSseTests(unittest.TestCase):
    def test_groups_keep_channel_order(self) -> None:
        groups = group_records(
            [
                _record("/var/log/a.log", "one"),
                _record("ssh.service", "two", source="journal"),
                _record("/var/log/a.log", "three"),
            ]
        )
        self.assertEqual([item["channel"] for item in groups], ["/var/log/a.log", "ssh.service"])
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[1]["logs"][0]["message"], "two")
        self.assertIn("features", groups[0]["logs"][0])

    def test_batch_includes_flat_records(self) -> None:
        batch = build_batch(
            [_record("ssh.service", "two", source="journal")],
            agent_id="agent-1",
            host="host",
            os_name="linux",
            user_id="user-1",
        )
        self.assertEqual(batch["user_id"], "user-1")
        self.assertEqual(batch["schema_version"], 2)
        self.assertEqual(len(batch["records"]), 1)
        self.assertIn("features", batch["records"][0])

    def test_sse_frame_is_one_data_line(self) -> None:
        body = encode_sse(
            {
                "batch_id": "batch-1",
                "count": 1,
                "groups": [{"channel": "ssh.service", "logs": [{"message": "line"}]}],
            }
        ).decode()
        self.assertTrue(body.startswith("event: log-batch\n"))
        self.assertIn("id: batch-1\n", body)
        self.assertTrue(body.endswith("\n\n"))
        data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(len(data_lines), 1)
        payload = json.loads(data_lines[0].removeprefix("data: "))
        self.assertEqual(payload["batch_id"], "batch-1")


class BatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_flushes_when_batch_is_full(self) -> None:
        sent: list[list[str]] = []

        async def sender(records: list[LogRecord]) -> None:
            sent.append([record.message for record in records])

        batcher = LogBatcher(
            max_logs=2,
            interval_seconds=30,
            max_buffer=10,
            retry_seconds=5,
            sender=sender,
        )
        await batcher.add(_record("a", "1"))
        self.assertEqual(sent, [])
        await batcher.add(_record("a", "2"))
        self.assertEqual(sent, [["1", "2"]])

    async def test_flushes_on_interval(self) -> None:
        sent: list[int] = []

        async def sender(records: list[LogRecord]) -> None:
            sent.append(len(records))

        batcher = LogBatcher(
            max_logs=50,
            interval_seconds=0.05,
            max_buffer=10,
            retry_seconds=5,
            sender=sender,
        )
        task = asyncio.create_task(batcher.run())
        await batcher.add(_record("a", "1"))
        await asyncio.sleep(0.25)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sent, [1])

    async def test_retry_keeps_records_until_force(self) -> None:
        calls = 0

        async def sender(records: list[LogRecord]) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("down")

        batcher = LogBatcher(
            max_logs=1,
            interval_seconds=30,
            max_buffer=10,
            retry_seconds=30,
            sender=sender,
        )
        await batcher.add(_record("a", "1"))
        await batcher.add(_record("a", "2"))
        self.assertEqual(calls, 1)
        await batcher.flush(force=True)
        self.assertEqual(calls, 2)

    async def test_permanent_error_drops_batch(self) -> None:
        async def sender(records: list[LogRecord]) -> None:
            raise PermanentSendError("HTTP 400")

        batcher = LogBatcher(
            max_logs=1,
            interval_seconds=30,
            max_buffer=10,
            retry_seconds=30,
            sender=sender,
        )
        await batcher.add(_record("a", "1"))
        await batcher.flush(force=True)
        self.assertEqual(batcher._buffer, [])


class SettingsTests(unittest.TestCase):
    def test_env_file_has_ingest_url_and_batch_limits(self) -> None:
        get_settings.cache_clear()
        settings = get_settings()
        self.assertEqual(settings.INGEST_URL, "http://localhost:3000/api/logs/ingest")
        self.assertEqual(settings.BATCH_INTERVAL_SECONDS, 3)
        self.assertEqual(settings.BATCH_MAX_LOGS, 100)
        self.assertEqual(settings.LOG_SOURCES, "auto")

    def test_rejects_non_http_ingest_url(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, INGEST_URL="ftp://logs.example")


class CredentialsTests(unittest.TestCase):
    def test_load_user_credentials_enables_training(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "device_id": "dev_1",
                        "mode": "user",
                        "session_token": "ses_test_token",
                        "user_id": "usr_abc",
                        "training_allowed": True,
                        "model": "user:usr_abc",
                        "need_reauth": False,
                    }
                ),
                encoding="utf-8",
            )
            creds = load_credentials(path)
            self.assertIsNotNone(creds)
            assert creds is not None
            self.assertTrue(creds.can_train)
            self.assertEqual(creds.bearer_token, "ses_test_token")
            settings = Settings(
                _env_file=None,
                TRAINING_ENABLED=True,
                CREDENTIALS_PATH=str(path),
            )
            transport = TrainingTransport(settings)
            resolved = transport.resolve_credentials()
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.user_id, "usr_abc")

    def test_legacy_api_key_in_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "device_id": "dev_1",
                        "mode": "user",
                        "api_key": "secret-key-123",
                        "user_id": "usr_abc",
                        "training_allowed": True,
                        "model": "user:usr_abc",
                    }
                ),
                encoding="utf-8",
            )
            creds = load_credentials(path)
            assert creds is not None
            self.assertFalse(creds.can_train)

    def test_guest_or_evicted_cannot_train(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "device_id": "dev_1",
                        "mode": "guest",
                        "training_allowed": False,
                        "model": "base",
                        "need_reauth": True,
                    }
                ),
                encoding="utf-8",
            )
            creds = load_credentials(path)
            self.assertIsNotNone(creds)
            assert creds is not None
            self.assertFalse(creds.can_train)


class IngestTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_line_is_posted_as_sse(self) -> None:
        received: list[bytes] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                received.append(self.rfile.read(length))
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                (root / "app.log").write_text("hello from agent\n", encoding="utf-8")
                settings = Settings(
                    _env_file=None,
                    INGEST_URL=f"http://127.0.0.1:{port}/api/logs/ingest",
                    LOG_SOURCES="files",
                    READ_FROM="start",
                    LINUX_LOG_ROOT=str(root),
                    BATCH_MAX_LOGS=1,
                    BATCH_INTERVAL_SECONDS=1,
                    FILE_RESCAN_SECONDS=0.2,
                    HTTP_RETRY_SECONDS=0.2,
                    COLLECTOR_RESTART_SECONDS=0.5,
                )
                from src.agent import Agent

                agent = Agent(settings)
                stop = asyncio.Event()
                task = asyncio.create_task(agent.run(stop))
                for _ in range(50):
                    if received:
                        break
                    await asyncio.sleep(0.1)
                stop.set()
                await task
        finally:
            server.shutdown()
            server.server_close()

        self.assertTrue(received, "сервер не получил ни одного батча")
        body = received[0].decode()
        self.assertIn("event: log-batch", body)
        self.assertIn("hello from agent", body)
        payload = json.loads(next(line.removeprefix("data: ") for line in body.splitlines() if line.startswith("data: ")))
        self.assertEqual(payload["groups"][0]["channel"].endswith("app.log"), True)
        self.assertEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main()
