# AI Desk Presence Runtime

This is the Python runtime layer for AI Desk Presence. It includes session
management, the Idle / Working / Break / Finished state machine, SQLite
persistence, daily statistics, an event runtime, observer/listener support, and
a single API surface for future modules.

This stage intentionally does not include AI, UI, serial communication,
millimeter-wave radar integration, or desktop context capture.

## Runtime Flow

```text
main.py
  -> AIDeskPresenceAPI
  -> Runtime
  -> Event queue
  -> SessionManager
  -> SQLite database
  -> Statistics
  -> EventDispatcher
  -> Listeners (logs, future AI, future UI, future dashboard)
```

## Directory Structure

```text
.
├── database/
│   ├── connection.py
│   └── repository.py
├── events/
│   ├── dispatcher.py
│   └── event_types.py
├── listeners/
│   └── event_log_listener.py
├── models/
│   ├── session_record.py
│   ├── state.py
│   └── stats.py
├── runtime/
│   └── runtime.py
├── services/
│   ├── ai_desk_api.py
│   └── stats_service.py
├── session/
│   └── manager.py
├── tests/
│   ├── test_runtime.py
│   └── test_session_flow.py
├── utils/
│   └── time_utils.py
└── main.py
```

## Events

External input events:

- `PresenceDetected`
- `PresenceLost`
- `Shutdown`

System events:

- `SessionStarted`
- `SessionEnded`
- `BreakStarted`
- `BreakEnded`
- `StateChanged`
- `StatisticsUpdated`

Future event types can be added in `events/event_types.py`, such as
`ContextCaptured`, `KeyboardActivity`, `MouseActivity`, or `CameraDetected`.

## Run

```bash
python3 main.py
```

## Test

```bash
python3 -m unittest
```

## Integration Example

Radar or other input modules should only post events:

```python
from events.event_types import PresenceDetected, PresenceLost
from services.ai_desk_api import AIDeskPresenceAPI

api = AIDeskPresenceAPI()
api.start()
api.post_event(PresenceDetected())
api.post_event(PresenceLost())
api.stop()
```

AI, UI, dashboard, and logger modules should subscribe to runtime events:

```python
def on_event(event):
    print(event.name, event.payload)

api.runtime.subscribe("*", on_event)
```
