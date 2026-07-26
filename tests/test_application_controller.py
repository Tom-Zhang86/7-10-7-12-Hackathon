from collections import defaultdict
from types import SimpleNamespace
import unittest

from application.controller import ApplicationController
from events.event_types import StateChanged, StatisticsUpdated
from models.state import PresenceState


class FakeRuntime:
    def __init__(self) -> None:
        self.listeners = defaultdict(list)

    def subscribe(self, event_name, listener) -> None:
        self.listeners[event_name].append(listener)

    def unsubscribe(self, event_name, listener) -> None:
        self.listeners[event_name].remove(listener)

    def publish(self, event) -> None:
        for listener in list(self.listeners[event.name]):
            listener(event)


class FakeAPI:
    def __init__(self, state=PresenceState.IDLE) -> None:
        self.runtime = FakeRuntime()
        self.state = state
        self.session = SimpleNamespace(id=41)
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> None:
        self.start_count += 1

    def stop(self) -> None:
        self.stop_count += 1

    def get_current_state(self):
        return self.state

    def get_active_session(self):
        return self.session


class SpyCollector:
    def __init__(self) -> None:
        self.started_sessions = []
        self.stop_count = 0

    def start(self, session_id) -> None:
        self.started_sessions.append(session_id)

    def stop(self) -> None:
        self.stop_count += 1


class ApplicationControllerTest(unittest.TestCase):
    def test_reconciles_initial_working_state_and_runtime_changes(self) -> None:
        api = FakeAPI(PresenceState.WORKING)
        collector = SpyCollector()
        controller = ApplicationController(api, collector)
        controller.start()
        controller.wait_until_idle()

        self.assertEqual(api.start_count, 1)
        self.assertEqual(collector.started_sessions, [41])

        api.runtime.publish(
            StateChanged(
                old_state=PresenceState.WORKING,
                new_state=PresenceState.BREAK,
                payload={"old_state": "Working", "new_state": "Break"},
            )
        )
        controller.wait_until_idle()
        self.assertEqual(collector.stop_count, 1)

        controller.stop()
        self.assertEqual(api.stop_count, 0)

    def test_listener_only_queues_updates_and_ignores_bad_state(self) -> None:
        api = FakeAPI()
        collector = SpyCollector()
        controller = ApplicationController(api, collector)
        controller.start(start_runtime=False)
        controller.wait_until_idle()

        stats_event = StatisticsUpdated(payload={"total_work_seconds": 10})
        api.runtime.publish(stats_event)
        bad_event = StateChanged(payload={"new_state": "NotAState"})
        with self.assertLogs("application.controller", level="WARNING"):
            api.runtime.publish(bad_event)

        self.assertEqual(controller.get_updates(), [stats_event, bad_event])
        self.assertEqual(collector.started_sessions, [])
        controller.stop()

    def test_controller_can_be_restarted(self) -> None:
        api = FakeAPI(PresenceState.WORKING)
        collector = SpyCollector()
        controller = ApplicationController(api, collector)

        controller.start(start_runtime=False)
        controller.wait_until_idle()
        controller.stop()
        controller.start(start_runtime=False)
        controller.wait_until_idle()

        self.assertEqual(collector.started_sessions, [41, 41])
        controller.stop()


if __name__ == "__main__":
    unittest.main()
