# AI Desk Presence Runtime

This is the Python runtime layer for AI Desk Presence. It includes session
management, the Idle / Working / Break / Finished state machine, SQLite
persistence, daily statistics, an event runtime, observer/listener support, and
a single API surface for future modules.

The stable system layer intentionally does not contain AI, UI, serial
communication, millimeter-wave radar integration, or desktop context capture.
The separate `application/` package adds macOS context capture, a reconnecting
ESP32 USB-serial presence adapter, daily-data aggregation, manually triggered
AI summaries, and a minimal Tkinter dashboard through this public API without
changing the system-layer core.

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
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_demo.py
```

For the hardware demo, wire the radar `OUT` pin to ESP32 `D27`, flash
`firmware/ai_desk_presence/ai_desk_presence.ino`, and then start the dashboard.
The application auto-detects common macOS USB serial ports and shows the
sensor connection state in the header. See `firmware/README.md` for wiring and
upload instructions.

Set `AI_DESK_SERIAL_PORT` in `.env` only when automatic port discovery is not
sufficient. The default baud rate is 115200.

## Presence-aware A2A Handoff MVP

This branch also contains a focused hackathon path that does **not** depend on
the Dashboard, process monitoring, window capture, or OCR. The user explicitly
arms one research task. A confirmed absence claims that task exactly once and
delegates it to an external A2A 1.0 agent. When presence returns, AI Desk saves
the structured result, sends a macOS notification, and opens a resumable
Markdown brief.

```text
explicit task -> ARMED -> presence lost -> A2A Research Agent
                                           |
presence returns <- READY/FAILED <---------+
       |
macOS notification + report.md + artifact.json
```

The A2A task database is separate from the existing presence database, and the
orchestrator only subscribes to the existing `StateChanged` event. This keeps
the system and Dashboard interfaces unchanged.

## Partner macOS Setup and Demo Runbook

This is the complete start-to-finish procedure for running the hackathon demo
on one Mac. The two repositories should be sibling folders:

```text
<workspace>/
├── 7-10-7-12-Hackathon/   # AI Desk: presence trigger and orchestration
└── agent-skeleton/         # Research Handoff Agent: A2A worker
```

The handoff-only demo does not start the Dashboard, process monitoring, screen
capture, or OCR. It needs two Terminal windows: Terminal 1 runs the Research
Agent; Terminal 2 runs AI Desk.

### 1. One-time Mac prerequisites

Both projects require Python 3.11 or newer. Check it first:

```bash
python3 --version
```

If Python is missing and Homebrew is already installed:

```bash
brew install python@3.11
```

Confirm both repositories are present before continuing:

```bash
cd <workspace>
test -d 7-10-7-12-Hackathon && echo "AI Desk found"
test -d agent-skeleton && echo "Research Agent found"
```

Replace `<workspace>` with the real parent directory; do not type the angle
brackets literally.

### 2. Create the two API keys

Only the Research Agent needs API credentials:

1. Create or copy an OpenAI API key from the official
   [API keys page](https://platform.openai.com/settings/organization/api-keys).
2. Create a free OpenAlex account and copy its key from
   [OpenAlex API settings](https://openalex.org/settings/api).

OpenAI recommends keeping keys out of source code and public repositories and
supplying them through environment variables; see its
[API-key guidance](https://developers.openai.com/api/docs/guides/production-best-practices#api-keys).
OpenAlex documents its current authentication and free allowance on the
[Authentication & Pricing page](https://developers.openalex.org/api-reference/authentication).

Never paste either key into the README, Python files, Git commits, screenshots,
issues, or team chat. If a real key is exposed, revoke it at the provider and
create a replacement.

### 3. Configure and start the Research Agent — Terminal 1

From the `agent-skeleton` repository:

```bash
cd <workspace>/agent-skeleton
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp -n .env.example .env
chmod 600 .env
nano .env
```

In `nano`, replace only the two placeholder values. A minimal working file is:

```dotenv
OPENAI_API_KEY=replace-with-the-real-openai-key
OPENALEX_API_KEY=replace-with-the-real-openalex-key
AGENT_MODEL=gpt-4o-mini
AGENT_A2A_HOST=127.0.0.1
AGENT_A2A_PORT=9110
AGENT_A2A_URL=http://127.0.0.1:9110/
```

Save with `Control-O`, press Return, then exit with `Control-X`. The `.env` file
is ignored by Git. Export it into the current Terminal session, verify that the
two values exist without printing the secrets, and start the agent:

```bash
set -a
source .env
set +a
python -c 'import os; print("OPENAI_API_KEY:", "set" if os.getenv("OPENAI_API_KEY") else "missing"); print("OPENALEX_API_KEY:", "set" if os.getenv("OPENALEX_API_KEY") else "missing")'
python -m agent_skeleton.serve check
python -m agent_skeleton.serve serve-a2a
```

Keep this Terminal open. The final command should report that the Research
Handoff Agent is serving on `127.0.0.1:9110`. In another Terminal, this command
can verify Agent Card discovery:

```bash
curl http://127.0.0.1:9110/.well-known/agent-card.json
```

If Terminal 1 is closed or restarted, activate and export the environment again:

```bash
cd <workspace>/agent-skeleton
source .venv/bin/activate
set -a; source .env; set +a
python -m agent_skeleton.serve serve-a2a
```

### 4. Configure AI Desk — Terminal 2

Open a second Terminal and leave Terminal 1 running:

```bash
cd <workspace>/7-10-7-12-Hackathon
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp -n .env.example .env
```

For the handoff-only demo, AI Desk does not need an OpenAI or OpenAlex key. It
calls the local Research Agent over A2A. Confirm these values in AI Desk's
`.env`:

```dotenv
AI_DESK_A2A_AGENT_URL=http://127.0.0.1:9110
AI_DESK_A2A_TIMEOUT_SECONDS=180
AI_DESK_HANDOFF_GRACE_SECONDS=3
AI_DESK_HANDOFF_DB=data/handoffs.sqlite3
AI_DESK_HANDOFF_OUTPUT=data/handoffs
AI_DESK_SERIAL_BAUD=115200
```

The unrelated `OPENAI_API_KEY` placeholder in AI Desk's template belongs to
the older Dashboard-summary path and may be left unchanged for this demo.

### 5. Recommended first run: keyboard presence simulation

First create one explicit task capsule:

```bash
python arm_handoff.py \
  --task "Compare two current approaches to presence-aware agent handoff" \
  --expected-output "A short cited brief with a recommendation and next steps" \
  --max-sources 5 \
  --time-budget 120
```

The command should print JSON and end with `Task armed`. Now start the
orchestrator:

```bash
python run_handoff_demo.py
```

The interactive commands are:

- `a`: simulate ABSENT. After the grace period, AI Desk claims one armed task
  and delegates it to the Research Agent.
- `l`: list handoff states. Normal progression is `armed` -> `running` ->
  `ready` -> `returned`.
- `p`: simulate PRESENT. AI Desk delivers a completed result. If the agent is
  still working, macOS reports that and opens the report when it finishes.
- `q`: stop the demo.

Suggested presentation sequence:

1. Press `l` and show the task in `armed` state.
2. Press `a` and explain that physical absence transfers execution ownership.
3. Press `l` until the task becomes `ready`; meanwhile Terminal 1 shows the A2A
   request.
4. Press `p`. macOS should display a notification and open the research brief.
5. Show that the result is a resumable artifact with findings, sources, open
   questions, next actions, and resume context—not a generic chat summary.

Generated files are stored at:

```text
data/handoffs/<handoff-id>/report.md
data/handoffs/<handoff-id>/artifact.json
data/handoffs.sqlite3
```

Use `python run_handoff_demo.py --no-open` if the report should be saved without
automatically opening it. Run `arm_handoff.py` again to queue another task; it
is not necessary to delete the database between demos.

### 6. Run with the real ESP32 presence sensor

Flash `firmware/ai_desk_presence/ai_desk_presence.ino`, connect radar `OUT` to
ESP32 `D27`, and connect the ESP32 to the Mac over USB. Check the available
serial devices:

```bash
ls /dev/cu.*
```

AI Desk attempts to auto-detect common macOS USB serial ports. If that fails,
add the exact port to AI Desk's `.env`, for example:

```dotenv
AI_DESK_SERIAL_PORT=/dev/cu.usbserial-0001
AI_DESK_SERIAL_BAUD=115200
```

Arm a fresh task, then start serial mode:

```bash
python arm_handoff.py --task "Research presence-aware A2A orchestration and prepare a cited handoff"
python run_handoff_demo.py --serial
```

In serial mode there are no `a`/`p` commands: leaving and returning are supplied
by the sensor. Delegation begins only after the sensor reports absence and the
AI Desk grace period expires. If the sensor itself takes about 10 seconds to
report absence and `AI_DESK_HANDOFF_GRACE_SECONDS=3`, the visible delay will be
about 13 seconds. For a faster stage demo, set the grace period to `1`; this
does not remove the sensor firmware's own delay.

Stop serial mode with `Control-C`.

### 7. Troubleshooting checklist

- `Connection refused` or Agent Card errors: Terminal 1 is not running, port
  `9110` differs between the two `.env` files, or another process owns the port.
- `Missing OPENAI_API_KEY`: run `set -a; source .env; set +a` again in Terminal
  1. Creating `.env` alone does not export it.
- `OPENALEX_API_KEY is not configured`: verify the key is in
  `agent-skeleton/.env`, then restart Terminal 1 after sourcing it.
- HTTP `401`: a provider key is invalid or revoked. Replace it locally and
  restart the Research Agent.
- HTTP `429`: the provider's current usage or rate limit has been reached. Check
  the corresponding provider usage page before retrying.
- Task stays `armed`: no confirmed absence was received, or the grace timer was
  canceled because presence returned too quickly.
- Task becomes `failed`: press `p` to deliver the failure report, then inspect
  Terminal 1 and `artifact.json` for the preserved error/limitations.
- No report window opens: check `data/handoffs/<handoff-id>/report.md` and open
  it manually with `open <path-to-report.md>`.
- Sensor is not detected: reconnect the USB cable, use `ls /dev/cu.*`, and set
  `AI_DESK_SERIAL_PORT` explicitly.

### 8. Optional pre-demo verification

Run these before presenting. Stop the Research Agent first if its Terminal is
needed for the commands, then restart it afterward.

```bash
# In agent-skeleton
source .venv/bin/activate
python -m agent_skeleton.serve check
python -m pytest --pyargs agent_skeleton.tests -q
```

```bash
# In 7-10-7-12-Hackathon
source .venv/bin/activate
python -m unittest discover -s tests -v
```

The protocol integration test uses a deterministic model stub; the final stage
rehearsal should still be performed once with the real OpenAI and OpenAlex keys,
the real Mac notification/report flow, and the intended ESP32 hardware.
