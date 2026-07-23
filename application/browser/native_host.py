import argparse
import json
import os
from pathlib import Path
import socket
import struct
import sys
from typing import Any, BinaryIO

from application.activity import (
    ActivityObservation,
    ActivityStore,
    HeartbeatReducer,
)
from application.activity.privacy import sanitize_activity_data
from application.activity.privacy_settings import ActivityPrivacyStore
from database.connection import Database
from utils.time_utils import parse_datetime, utc_now


class NativeMessageProcessor:
    """Validate Chrome messages and persist them as heartbeat activity events."""

    def __init__(
        self,
        store: Any,
        pulsetime_seconds: float = 15.0,
        privacy_store: Any | None = None,
    ) -> None:
        self.store = store
        self.privacy_store = privacy_store
        self.reducer = HeartbeatReducer(pulsetime_seconds)
        self.bucket_id = f"ai-desk-chrome-{socket.gethostname()}"

    def process(self, message: dict[str, Any]) -> dict[str, Any]:
        if message.get("version") != 1 or message.get("type") != "web.semantic":
            raise ValueError("Unsupported native message schema.")
        data = sanitize_activity_data(dict(message.get("data") or {}))
        policy = self.privacy_store.load() if self.privacy_store else None
        if data.get("incognito") or (policy and not policy.allows(data)):
            return {"ok": True, "stored": False, "reason": "privacy"}
        timestamp_value = message.get("timestamp")
        timestamp = (
            parse_datetime(str(timestamp_value))
            if timestamp_value
            else utc_now()
        )
        observation = ActivityObservation(
            timestamp=timestamp,
            bucket_id=self.bucket_id,
            event_type="web.semantic",
            source="chrome_semantic",
            data=data,
        )
        spans = self.reducer.ingest(observation)
        ids = [self.store.upsert_span(span) for span in spans]
        return {"ok": True, "stored": True, "event_ids": ids}

    def close(self) -> None:
        for span in self.reducer.flush(utc_now()):
            self.store.upsert_span(span)


def read_native_message(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError("Incomplete native message header.")
    length = struct.unpack("=I", header)[0]
    if length > 1_000_000:
        raise ValueError("Native message exceeds the 1 MB limit.")
    payload = stream.read(length)
    if len(payload) != length:
        raise EOFError("Incomplete native message body.")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Native message must be a JSON object.")
    return value


def write_native_message(stream: BinaryIO, value: dict[str, Any]) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=os.getenv("AI_DESK_DB_PATH", "ai_desk_presence.db"),
    )
    args = parser.parse_args()
    database = Database(Path(args.db))
    database.initialize()
    processor = NativeMessageProcessor(
        ActivityStore(database),
        privacy_store=ActivityPrivacyStore(),
    )
    try:
        while True:
            message = read_native_message(sys.stdin.buffer)
            if message is None:
                break
            try:
                response = processor.process(message)
            except Exception as exc:
                response = {"ok": False, "error": str(exc)[:300]}
            write_native_message(sys.stdout.buffer, response)
    finally:
        processor.close()


if __name__ == "__main__":
    main()
