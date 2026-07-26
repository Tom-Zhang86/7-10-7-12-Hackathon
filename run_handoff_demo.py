"""Presence-driven A2A demo with real serial or keyboard simulation."""
from __future__ import annotations

import argparse
import logging
import os
import time

from application.config import load_application_environment
from application.handoff import A2AHandoffClient, HandoffOrchestrator, HandoffStore
from application.handoff.macos_actions import MacOSHandoffActions
from application.presence import SerialPresenceAdapter
from events.event_types import PresenceDetected, PresenceLost
from services.ai_desk_api import AIDeskPresenceAPI


def _print_tasks(store: HandoffStore) -> None:
    records = store.list_all()
    if not records:
        print("No handoff tasks. Run arm_handoff.py first.")
        return
    for record in records:
        print(
            f"{record.capsule.handoff_id[:8]}  "
            f"{record.status.value:14}  {record.capsule.goal}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Desk Presence A2A demo")
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Use the ESP32 serial sensor instead of keyboard simulation",
    )
    parser.add_argument("--no-open", action="store_true")
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_application_environment()
    args = build_parser().parse_args()

    api = AIDeskPresenceAPI()
    store = HandoffStore(
        os.getenv("AI_DESK_HANDOFF_DB", "data/handoffs.sqlite3")
    )
    client = A2AHandoffClient(
        os.getenv("AI_DESK_A2A_AGENT_URL", "http://127.0.0.1:9110"),
        timeout_seconds=float(
            os.getenv("AI_DESK_A2A_TIMEOUT_SECONDS", "180")
        ),
    )
    actions = MacOSHandoffActions(
        output_root=os.getenv("AI_DESK_HANDOFF_OUTPUT", "data/handoffs"),
        auto_open=not args.no_open,
    )
    orchestrator = HandoffOrchestrator(
        api,
        store,
        client,
        actions,
        grace_seconds=float(
            os.getenv("AI_DESK_HANDOFF_GRACE_SECONDS", "3")
        ),
    )
    serial_adapter = None
    api.start()
    orchestrator.start()
    try:
        if args.serial:
            serial_adapter = SerialPresenceAdapter(
                api,
                port=os.getenv("AI_DESK_SERIAL_PORT") or None,
                baudrate=int(os.getenv("AI_DESK_SERIAL_BAUD", "115200")),
            )
            serial_adapter.start()
            print("Serial mode running. Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
        else:
            print("Simulation: p=PRESENT, a=ABSENT, l=LIST, q=QUIT")
            while True:
                command = input("> ").strip().lower()
                if command == "p":
                    api.post_event(PresenceDetected())
                    api.wait_until_idle()
                    print("Presence: Working")
                elif command == "a":
                    api.post_event(PresenceLost())
                    api.wait_until_idle()
                    print("Presence: Break; handoff grace timer started")
                elif command == "l":
                    _print_tasks(store)
                elif command == "q":
                    break
    except KeyboardInterrupt:
        pass
    finally:
        if serial_adapter is not None:
            serial_adapter.stop()
        orchestrator.stop()
        api.close()


if __name__ == "__main__":
    main()
