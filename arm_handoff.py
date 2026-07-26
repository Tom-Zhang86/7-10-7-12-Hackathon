"""Arm one explicit research task without opening the Dashboard."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess

from application.config import load_application_environment
from application.handoff import HandoffInput, HandoffStore, TaskCapsule


def _clipboard_text() -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("--clipboard currently requires macOS pbpaste")
    result = subprocess.run(
        ["/usr/bin/pbpaste"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("clipboard is empty")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arm an AI Desk research handoff")
    parser.add_argument("--task", help="Research goal")
    parser.add_argument("--url", action="append", default=[], help="Source URL")
    parser.add_argument("--clipboard", action="store_true", help="Use macOS clipboard")
    parser.add_argument(
        "--expected-output",
        default="A cited research brief with concrete next steps.",
    )
    parser.add_argument("--skill", default="research/handoff")
    parser.add_argument("--max-sources", type=int, default=8)
    parser.add_argument("--time-budget", type=int, default=120)
    return parser


def main() -> None:
    load_application_environment()
    args = build_parser().parse_args()
    inputs = [HandoffInput("url", value) for value in args.url]
    clipboard = _clipboard_text() if args.clipboard else ""
    if clipboard:
        kind = "url" if clipboard.startswith(("http://", "https://")) else "text"
        inputs.append(HandoffInput(kind, clipboard))
    goal = (args.task or "").strip()
    if not goal and clipboard:
        goal = f"Research and prepare a handoff for: {clipboard[:240]}"
    if not goal:
        raise SystemExit("Provide --task or --clipboard.")

    capsule = TaskCapsule.create(
        goal,
        inputs=inputs,
        expected_output=args.expected_output,
        agent_skill=args.skill,
        constraints={
            "max_sources": max(1, min(args.max_sources, 20)),
            "time_budget_seconds": max(10, args.time_budget),
        },
    )
    store = HandoffStore(
        os.getenv("AI_DESK_HANDOFF_DB", "data/handoffs.sqlite3")
    )
    store.create(capsule)
    print(json.dumps(capsule.as_payload(), ensure_ascii=False, indent=2))
    print("\nTask armed. It will be delegated after the next confirmed absence.")


if __name__ == "__main__":
    main()
