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

### Why activity tracking alone is not enough

The current MVP combines physical seat presence with application, browser, and
Accessibility metadata to classify time as learning, work, entertainment,
unknown, or background playback. This is useful, but it is not sufficiently
distinctive on its own. Existing tools such as ActivityWatch already collect
active-window, browser, and keyboard/mouse AFK events and support rule-based
categorization. A millimeter-wave sensor makes the `present`/`away` timeline
more accurate, but if presence is only stored as another data field, the result
is still an activity tracker with an additional sensor.

The next goal is therefore **Presence-Driven Agent Handoff**: use a physical
presence transition to coordinate a safe transfer of a bounded task between a
human and an AI agent. Instead of only reporting what the user did, AI Desk
should preserve the user's working state, let an authorized agent make useful
progress during an interruption, and make returning to the task fast and
understandable.

This is a planned extension. The repository already provides the presence state
machine, local activity evidence, semantic classification, privacy controls,
persistence, and dashboard foundation. Automatic checkpoint creation, agent
delegation, and return-time recovery are not yet complete.

### What the separate A2A agent is

The worker agent will run as a separate local service rather than being embedded
inside the presence runtime. The two applications will communicate using
**A2A (Agent-to-Agent)**, an open protocol for one agent or application to
discover another agent's capabilities, create a task, follow its state, and
receive structured results.

In this project, AI Desk is the **orchestrator** and retains control of presence,
privacy, permission policy, and the user interface. The separate A2A service is
the **worker**. For the Hackathon MVP, it can expose one narrow capability such
as `research_handoff`: read the supplied task context, investigate a question,
and return a concise research brief with sources, findings, uncertainties, and
suggested next steps. Later workers could support tests, code review, or proposed
patches without changing the sensor or activity-collection layers.

A2A does not give the worker unrestricted access to the user's computer. AI
Desk sends an explicit task package and accepts an explicit result artifact.
This boundary keeps the two projects independently runnable and makes the agent
replaceable: any compatible worker may be used if it advertises the required
capability and follows the same task contract.

### Planned handoff workflow

```text
User working at desk
        |
        | local, low-volume context collection
        v
Current goal + active project + recent semantic evidence
        |
        | sensor reports a stable away transition
        v
Checkpoint builder -> permission policy -> A2A task request
                                           |
                                           v
                                  Separate worker agent
                                           |
                                           v
                                  Structured result artifact
                                           |
        | sensor reports that the user has returned
        v
Safe stop / no new work -> resumption card -> user chooses next action
```

1. **Present and working.** AI Desk maintains a rolling, privacy-filtered view
   of the current goal and recent evidence. Useful evidence can include the
   active application, document or repository name, browser page title and URL
   domain, current classification, and recent task notes. Continuous screenshots
   and full-screen OCR are not required.
2. **Stable away transition.** The existing presence debounce remains important:
   a brief radar fluctuation must not delegate a task. After the configured
   absence threshold, AI Desk freezes a small checkpoint instead of sending the
   user's complete activity history.
3. **Checkpoint and authorization.** The checkpoint contains a task ID, declared
   goal, minimal relevant evidence, requested worker capability, allowed actions,
   time limit, and stop conditions. The policy rejects delegation when there is
   no clear goal, the evidence is sensitive, or the requested action is outside
   the user's pre-approved capability set.
4. **A2A delegation.** AI Desk discovers the worker's advertised capability,
   submits the checkpoint as an A2A task, and records task-state updates such as
   submitted, working, completed, failed, or cancelled. The initial MVP should
   use a research/read-only worker so that the full loop can be demonstrated
   without granting write access.
5. **Bounded work while away.** The worker produces a structured artifact rather
   than an unbounded chat transcript. Every outcome is linked to the checkpoint
   that caused it. Commits, pushes, external messages, purchases, deletion, and
   other consequential actions are denied by default and require explicit user
   approval.
6. **Return and safe interruption.** A stable return transition tells the
   orchestrator to cancel or stop starting additional work. If an atomic step is
   already running, it may finish only within its declared stop policy; the
   worker must not silently continue after ownership returns to the user.
7. **Resumption card.** The dashboard shows what the user was doing before the
   interruption, why the handoff occurred, what the worker attempted, the
   artifact it produced, unresolved questions, and one or two recommended next
   actions. The user can accept the result, inspect it, continue manually, or
   explicitly authorize another task.

### Hackathon MVP boundary

The demo should optimize for one complete, trustworthy loop rather than a large
set of autonomous tools:

- macOS remains the target desktop platform;
- the ESP32/radar input supplies debounced `present` and `away` events through
  the existing public API, without changing the external sensor interface;
- existing window, browser, and Accessibility metadata supplies a minimal
  checkpoint; screenshots and OCR remain out of scope;
- one read-only `research_handoff` A2A worker accepts a structured task and
  returns a structured artifact;
- leaving the seat triggers checkpoint and delegation only when the user has
  enabled it for the current goal;
- returning triggers safe stop and a dashboard resumption card;
- all handoff events and worker results are stored locally in an auditable
  timeline.

The intended demonstration is: start a declared research task, work briefly,
leave the seat, watch AI Desk create and delegate a checkpoint, let the worker
produce a cited brief, return, and immediately receive a compact explanation of
what changed and what decision is needed next.

### Most meaningful Hackathon innovations

1. **Physical presence becomes a task-ownership signal, not merely an AFK
   metric.** Existing activity trackers answer "what application was active?"
   and desktop agents answer "what task should I run?" AI Desk connects the two:
   a verified physical transition initiates a governed human-to-agent handoff,
   and the return transition gives ownership back to the human. The sensor is
   therefore part of the collaboration protocol rather than a reporting add-on.
2. **A reversible, evidence-minimized handoff loop.** AI Desk delegates a small
   semantic checkpoint under an explicit capability policy, receives a
   structured and auditable artifact through A2A, safely stops on return, and
   presents a resumption card. The innovation is not a new sensor, tracker, or
   model in isolation; it is a privacy-conscious interaction model that turns
   interruptions into controlled, reviewable agent work without requiring
   continuous screen recording or unrestricted computer access.
