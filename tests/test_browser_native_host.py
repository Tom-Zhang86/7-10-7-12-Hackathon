from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
import unittest

from application.browser.install_native_host import install
from application.browser.native_host import (
    NativeMessageProcessor,
    read_native_message,
    write_native_message,
)


class MemoryStore:
    def __init__(self) -> None:
        self.spans = []

    def upsert_span(self, span):
        self.spans.append(span)
        return len(self.spans)


class NativeMessageProcessorTest(unittest.TestCase):
    def test_persists_semantic_message_and_rejects_incognito(self) -> None:
        store = MemoryStore()
        processor = NativeMessageProcessor(store)
        timestamp = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)

        result = processor.process(
            {
                "version": 1,
                "type": "web.semantic",
                "timestamp": timestamp.isoformat(),
                "data": {
                    "url": "https://example.com/lesson",
                    "page_title": "Lesson",
                },
            }
        )
        private = processor.process(
            {
                "version": 1,
                "type": "web.semantic",
                "timestamp": timestamp.isoformat(),
                "data": {"incognito": True},
            }
        )

        self.assertTrue(result["stored"])
        self.assertFalse(private["stored"])
        self.assertEqual(store.spans[0].event_type, "web.semantic")

    def test_native_message_framing_round_trip(self) -> None:
        stream = BytesIO()
        write_native_message(stream, {"ok": True})
        stream.seek(0)

        self.assertEqual(read_native_message(stream), {"ok": True})

    def test_installer_writes_runner_and_manifest_to_selected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner, manifest = install(
                project_root=root / "project",
                database_path=root / "desk.db",
                extension_id="abc123",
                base_dir=root / "install",
            )
            value = json.loads(manifest.read_text())

            self.assertTrue(runner.exists())
            self.assertEqual(value["name"], "com.ai_desk.activity")
            self.assertEqual(
                value["allowed_origins"],
                ["chrome-extension://abc123/"],
            )

