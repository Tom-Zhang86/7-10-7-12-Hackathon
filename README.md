# AI Desk Presence Runtime

This is the Python runtime layer for AI Desk Presence. It includes session
management, the Idle / Working / Break / Finished state machine, SQLite
persistence, daily statistics, an event runtime, observer/listener support, and
a single API surface for future modules.

The stable system layer intentionally does not contain AI, UI, serial
communication, millimeter-wave radar integration, or desktop context capture.
The separate `application/` package adds an ActivityWatch-inspired local
activity layer, macOS context capture, optional Chrome page semantics,
session/activity fusion, rule-first classification, manually triggered AI
summaries, privacy controls, and a minimal Tkinter dashboard through this public
API without changing the system-layer core. It does not require ActivityWatch
and does not use screenshots or OCR.

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
│   ├── context_event.py
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
│   ├── test_database_cleanup.py
│   ├── test_public_api.py
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

### Stable System Event Payloads

System event payloads use these stable keys:

- `StateChanged`: `old_state`, `new_state`
- `SessionStarted`: `session_id`, `start_time`
- `BreakStarted`: `break_id`, `session_id`, `start_time`
- `BreakEnded`: `break_id`, `session_id`, `start_time`, `end_time`, `duration_seconds`
- `SessionEnded`: `session_id`, `start_time`, `end_time`, `duration_seconds`, `break_count`
- `StatisticsUpdated`: `total_work_seconds`, `session_count`, `break_count`, `longest_focus_seconds`

State values are strings. Time values are ISO-8601 strings. Durations and
counts are integers.

Listener failures are logged and isolated so one external module cannot stop
the runtime or prevent other listeners from receiving an event.

## Public Context and Timeline APIs

The system-layer API exposes context capture and timeline methods for UI and AI
summary modules:

```python
api.record_context_event(
    session_id=None,
    source="macos_active_window",
    payload={"app": "Terminal", "title": "AI Desk"},
)

api.get_context_events_for_day(date=None)
api.get_today_timeline()
api.get_timeline_for_day(target_date)
api.close()
```

`record_context_event` stores records in `context_events` with `id`,
`session_id`, `timestamp`, `source`, and `payload_json`. `session_id` may be
`None` when no work session is active. `payload` must be a dictionary and is
stored as JSON.

`get_context_events_for_day` returns structured dictionaries. If `date` is
`None`, it returns today's UTC context events.

`get_today_timeline` and `get_timeline_for_day` return one time-ordered list
containing `session`, `break`, and `context_event` items. Every item includes
`type` and `timestamp`; session and break items include `start_time`,
`end_time`, and `session_id`; context items include `session_id`, `source`, and
`payload`. Public query methods return timezone-aware Python `datetime`
objects; callers crossing a JSON boundary should convert them to ISO-8601
strings.

All daily boundaries use UTC. After `finish_day()` moves the state to
`Finished`, presence remains ignored for the rest of that UTC day. The first
presence detection on a later UTC day automatically starts a new lifecycle.

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

## Application Demo

```bash
python3 -m pip install -r requirements.txt
python3 run_demo.py
```

The current MVP targets macOS. Active-window and bounded Accessibility capture
require Accessibility permission. Chrome semantic capture is optional and has
a one-time unpacked-extension/native-host setup described in
`browser_extension/README.md`. Remote classification is opt-in; local rules and
the rest of the runtime work offline.

## Project Direction: Presence-Driven Agent Handoff

The current MVP combines physical seat presence with application, browser, and
Accessibility metadata to classify time as learning, work, entertainment,
unknown, or background playback. This is useful, but it is not sufficiently
distinctive on its own. Existing activity trackers such as ActivityWatch
already collect active-window, browser, and AFK events and support rule-based
categorization. Adding a physical `present`/`away` signal mainly improves the
accuracy of those timelines; if presence is used only as another field, the
product remains an activity tracker with an additional sensor.

The next project goal is therefore **Presence-Driven Agent Handoff**. Physical
presence transitions will become control signals for collaboration between the
human and a bounded coding agent:

- while the user is present, the system maintains the current goal, evidence,
  progress, and a resumable task context;
- when the user leaves, it creates a checkpoint and may delegate only
  pre-authorized work such as reading code, running tests, linting, or preparing
  a proposed patch;
- while the user is away, agent actions and outcomes are recorded in an
  auditable timeline rather than being treated as human activity;
- when the user returns, the system presents what was happening before the
  interruption, what the agent completed, what remains uncertain, and the next
  recommended action;
- commits, pushes, external messages, and other consequential actions remain
  outside the default agent capability policy and require explicit approval.

This direction makes the sensor essential to the product instead of merely
improving AFK detection. The primary unit also changes from time spent in an
application to a safe, observable handoff around a declared goal and measurable
outcomes. Activity collection, semantic classification, privacy controls, and
the existing session state machine remain useful infrastructure, but they now
support human-agent coordination rather than being the final product.
