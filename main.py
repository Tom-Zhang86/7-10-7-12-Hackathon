from pathlib import Path

from events.event_types import PresenceDetected, PresenceLost
from services.ai_desk_api import AIDeskPresenceAPI
from utils.time_utils import format_seconds


def main() -> None:
    """Run the event runtime without serial, UI, or AI code."""

    db_path = Path("ai_desk_presence.db")
    api = AIDeskPresenceAPI(db_path)
    api.start()

    print("AI Desk Presence system layer")
    print(f"Database: {db_path.resolve()}")
    print(f"Current state: {api.get_current_state().value}")
    print()
    print("Demo commands:")
    print("  p = present=True")
    print("  a = present=False")
    print("  f = finish day")
    print("  s = show stats")
    print("  q = quit")

    while True:
        command = input("> ").strip().lower()

        if command == "p":
            api.post_event(PresenceDetected())
            api.wait_until_idle()
            print(f"State: {api.get_current_state().value}")
        elif command == "a":
            api.post_event(PresenceLost())
            api.wait_until_idle()
            print(f"State: {api.get_current_state().value}")
        elif command == "f":
            api.stop()
            print(f"State: {api.get_current_state().value}")
            break
        elif command == "s":
            stats = api.get_today_stats()
            print("Today:")
            print(
                "  total_work_time = "
                f"{format_seconds(stats['total_work_seconds'])}"
            )
            print(f"  session_count = {stats['session_count']}")
            print(f"  break_count = {stats['break_count']}")
            print(
                "  longest_focus_time = "
                f"{format_seconds(stats['longest_focus_seconds'])}"
            )
        elif command == "q":
            api.stop()
            break
        else:
            print("Unknown command.")


if __name__ == "__main__":
    main()
