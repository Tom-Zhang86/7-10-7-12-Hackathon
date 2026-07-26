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

## AI Desk V2 — Owner-Authorized Handoff (Phases 1-5)

`application/owner_handoff/` is a new, self-contained package implementing
the identity-aware, presence-triggered, sandboxed agent handoff orchestrator
described in
[`docs/AI_DESK_V2_MASTER_SPEC.md`](docs/AI_DESK_V2_MASTER_SPEC.md). It does
not change any behavior described elsewhere in this README — the Dashboard,
the presence-aware A2A Research Handoff MVP above, and every existing
public API are untouched. The target platform is macOS, but every test in
this package is deterministic and hardware-free.

**Phase 5 — Phase 4 repair gate + release-candidate demo polish.** Full
detail — including everything still deferred and every known limitation —
lives in
[`application/owner_handoff/README.md`](application/owner_handoff/README.md),
[`docs/AI_DESK_V2_PHASE1_REPORT.md`](docs/AI_DESK_V2_PHASE1_REPORT.md),
[`docs/AI_DESK_V2_PHASE2_REPORT.md`](docs/AI_DESK_V2_PHASE2_REPORT.md),
[`docs/AI_DESK_V2_PHASE3_REPORT.md`](docs/AI_DESK_V2_PHASE3_REPORT.md),
[`docs/AI_DESK_V2_PHASE4_REPORT.md`](docs/AI_DESK_V2_PHASE4_REPORT.md), and
[`docs/AI_DESK_V2_PHASE5_REPORT.md`](docs/AI_DESK_V2_PHASE5_REPORT.md). The
short version:

**Implemented:** centralized configuration (`config.py`), domain models
(`domain/activity.py`, `domain/work_context.py`, `domain/presence.py`,
`domain/question.py`, `domain/execution.py`, `domain/manifest.py`,
`domain/resume.py`), the full 13-state persisted state machine and its
SQLite-backed event ledger (`state_machine.py`, `store.py`), Tier 1 activity
classification and Tier 2 context analysis (`classification/`), the OCR
interface plus its fixed, mandatory invocation order (`adapters/ocr.py`,
`classification/ocr_policy.py` — no-op and mock only; **no real OCR is
implemented**), deterministic radar/wearable simulators (`adapters/radar.py`,
`adapters/wearable.py`), leave/return presence fusion (`fusion/`), the
handoff-question generator, wearable-answer lifecycle, and Terminal renderer
(`questions/`), conservative skill routing (`routing/`), physical workspace
duplication with path-safety policy (`workspace/`), the deny-by-default
execution policy, one-time-use permission binding bound to exactly what the
owner saw (`execution/permission.py`), Research Agent executor adapter,
Codex CLI preflight/executor with a real cancellable execution session
(`return_coordinator.ExecutionSession`) and a hardened subprocess adapter
(real subprocess adapters exist but are never invoked by any test), and
controlled package installer (`execution/`); the top-level orchestrator
(`orchestrator.py`) composing every one of the above behind deterministic,
explicitly-called methods, including fail-closed handling for duplication/
executor/verification failures and terminal-task acknowledgment; the return
coordinator (`return_coordinator.py`) and fixed-shape resume report
(`domain/resume.py`, plus `SafetyFailureRecord` for verification failures);
sanitized, atomic task-artifact persistence (`persistence.py`); and a fully
deterministic, hardware-free Terminal demo (`demo_workspace/`,
`run_owner_handoff_demo.py`) with a visibly meaningful default coding
executor, a responsive background `run`/`wait` flow, and a safe
`--preflight-only` mode.

**Explicitly deferred (not implemented):** a real serial radar reader, real
BLE (and any BLE UUIDs), real Codex CLI/package-install execution during
development or in any automated test (every test uses a fake runner; the
real subprocess adapters exist and are reachable only via explicit
human-invoked `--executor codex` / real package installs), real macOS
notifications, and any Dashboard/summary integration. **No macOS hardware
verification, and no real-Codex-CLI verification, has been performed for
any of this** — everything is fully deterministic, in-process, and
hardware-free in this repository's own test suite.

### Configuration

| Field | Env var | Default |
|---|---|---|
| `owner_leave_confirmation_seconds` | `AI_DESK_V2_LEAVE_CONFIRM_SECONDS` | `10` |
| `input_idle_threshold_seconds` | `AI_DESK_V2_INPUT_IDLE_SECONDS` | `5` |
| `handoff_question_expiration_seconds` | `AI_DESK_V2_QUESTION_EXPIRY_SECONDS` | `60` |
| `coding_agent_max_runtime_seconds` | `AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS` | `300` |
| `coding_agent_max_safe_steps` | `AI_DESK_V2_CODING_MAX_STEPS` | `20` |
| `owner_handoff_db_path` | `AI_DESK_V2_DB_PATH` | `data/owner_handoff.sqlite3` |
| `workspace_session_root` | `AI_DESK_V2_SESSION_ROOT` | `data/owner_handoff/sessions` |
| `ocr_adapter_mode` | `AI_DESK_V2_OCR_MODE` | `noop` (`mock` for tests/simulator dev) |
| `wearable_device_allowlist` | `AI_DESK_V2_WEARABLE_DEVICE_IDS` | `()` (empty — rejects every answer until configured) |

The 5-second input idle threshold is not a second timer racing the
10-second confirmation window: owner leave is confirmed (via
`LeaveDetector`, since Phase 2) after input has been idle for 5 seconds
*and* all four absence conditions then remain true, continuously, for the
full 10-second window — about 15 seconds total in the common case.

The following are **mandatory safety invariants, not configurable values**,
and have no environment variable: wearable `UNKNOWN` never triggers;
contradictory signals always wait; external actions remain denied in v1;
OCR is callable only after Tier 1 and Tier 2 both remain `AMBIGUOUS`; an
unanswered/timed-out handoff question always resolves to D ("Do nothing");
a missing/unusable Codex CLI always stops safely; missing Codex isolation
support always fails closed; the original workspace is never writable; Git
push/publish/message/deploy remain prohibited. See
`docs/AI_DESK_V2_MASTER_SPEC.md`, "Phase 1 Review Addendum."

### Running the tests

```bash
python -B -m unittest discover -s tests -v
```

Every test in this package is deterministic — no real network, hardware,
BLE, OCR, Codex CLI, or package installer is ever called.

See the **"AI Desk V2 — macOS demo"** section at the end of this README for
the full partner setup/run walkthrough, including the deterministic fake
demo, the optional real-Research and real-Codex modes, and every hard-coded
timing/limit default.

---

# AI Desk V2 — macOS demo

This is the Phase 5 release-candidate walkthrough for
`application/owner_handoff/`. Use this section for the current
Owner-Authorized Handoff product demo; the earlier `run_handoff_demo.py`
walkthrough is the retained, research-only MVP path.

**Target platform: macOS.** The deterministic path is platform-independent,
has been reviewed for macOS path/subprocess behavior, and runs with zero real
hardware, BLE, OCR, or (in the default mode) network access. A real Mac,
Codex CLI, BLE wearable, and radar have not all been exercised together yet;
see "macOS release gate" below for the exact remaining device-level check.

## 1. Set up a Python virtual environment

```bash
cd 7-10-7-12-Hackathon
python3 -m venv .venv
source .venv/bin/activate
```

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

`run_owner_handoff_demo.py` and its default (`--executor fake --research
fake`) path need nothing beyond this repo's own `requirements.txt` — no
extra package is required for the deterministic demo.

## 3. Configure environment

```bash
cp .env.example .env
```

Every `AI_DESK_V2_*` value in `.env` is optional (defaults match the Master
Spec — see the Configuration table above). **`OPENAI_API_KEY` and the A2A
agent settings are only required for the optional real-Research mode below
— the default fake demo needs no keys, no `.env` edits, and no network at
all.** `run_owner_handoff_demo.py` loads this repository-level `.env` at
startup and does not override variables already exported by the operator.

## 4. Optional: start the Research Agent (only for real Research mode)

Only needed if you intend to pass `--research real`. In a separate
terminal, from the `agent-skeleton` checkout:

```bash
# In agent-skeleton
python3 -m agent_skeleton --host 127.0.0.1 --port 9110
```

Check its Agent Card resolves before trusting real-Research mode:

```bash
curl -s http://127.0.0.1:9110/.well-known/agent-card.json | head -c 500
```

## 5. Optional: install and check Codex CLI (only for real Codex mode)

Only needed if you intend to pass `--executor codex`. Installation and login
are explicit, human-run prerequisites; AI Desk never installs Codex CLI for
the user. Follow the current official
[Codex CLI documentation](https://learn.chatgpt.com/docs/codex/cli). One
official macOS/Linux installation path is:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex login
codex login status
```

Then verify it manually (never done automatically by this repo):

```bash
codex --version
codex exec --help
```

`CodexPreflight` (see `execution/codex_cli.py`) re-runs exactly these two
commands at startup and fails closed if the installed CLI doesn't advertise
every flag this executor requires (`--sandbox`, `--cd`, `--ephemeral`,
`--json`, `--ignore-user-config`, `--skip-git-repo-check`, `--strict-config`,
`-c`/`--config`).

## 6. Run the deterministic demo (default, recommended first run)

```bash
python3 run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace/sample_project \
  --executor fake
```

This starts an interactive prompt (`aidesk>`). Useful commands (type `help`
for the full list):

| Command | Effect |
|---|---|
| `start <task_id>` | Create and activate one handoff task |
| `away` | Set radar/wearable to absent and deterministically advance the internal fake clock through the idle + confirmation windows |
| `ask` | Generate and display the full handoff question in Terminal |
| `select A` / `select B` / `select D` | Submit that option (the wearable only ever sends back `question_id` + the letter) |
| `grant` / `deny` | Answer YES/NO to whichever permission question (CODEX_DATA or PACKAGE_INSTALL) is currently pending |
| `run` | Start the routed coding/research step in the background and return immediately -- `status`/`return` remain usable while it runs |
| `wait [timeout_seconds]` | Block until the running step finishes (default 30s) and print its result |
| `install <spec>` | Request a PACKAGE_INSTALL authorization for an exact package spec, mid-execution |
| `return` | Simulate the owner returning |
| `finalize [text]` | Produce the resume report (JSON, exact fixed shape) |
| `deliver` | Return control to the owner |
| `status` | Print task id/state/routed skill/duplicate path/return-requested/execution status (safe to call with no active task) |
| `report` | Reload and print the exact persisted report for the current task (e.g. after `attach`-ing post-restart) |
| `acknowledge` | Free this process for a new `start` after a CANCELED/FAILED task (RETURNED already does this via `deliver`) |

A full scripted run (non-interactive, e.g. for a recording or a `--script
path/to/commands.txt` file of one command per line):

```
start demo-task
away
ask
select A
grant
run
status
wait
finalize Fixed the calculator add bug
deliver
```

`select A` routes to **coding** (the seeded bug in
`demo_workspace/sample_project/calculator.py` -- the default fake executor
actually fixes it in the duplicate); `select B` routes to **research**;
`select D` does nothing, ever, and cancels cleanly.

To exercise a return arriving mid-execution instead, call `return` right
after `run` (before `wait`) -- if it arrives before the step has actually
started, the step is correctly refused rather than started; either way,
`wait` reports the outcome honestly instead of assuming success.

### Exactly when the CODEX_DATA question appears

Only after: a coding-routed option is selected -> the workspace is
physically duplicated (`WORKSPACE_DUPLICATING`) -> **then** the CODEX_DATA
permission question is asked (`PERMISSION_PENDING`). Codex (real or fake)
never starts before a matching YES to that exact question, for that exact
task/session/duplicate.

### Where things are written

- Duplicate workspaces: under `--session-root` (default
  `data/owner_handoff/sessions/<session-id>/`) — never inside
  `demo_workspace/` itself, never deleted automatically by AI Desk.
- The task/event ledger: `--db-path` (default `data/owner_handoff.sqlite3`).
- Task artifacts (the duplication manifest, the routed selected task, the
  sanitized execution result, approved package installs, and the final
  report/failure) are persisted as sanitized JSON under
  `<session-root>/<task_id>.artifacts/` — never inside `demo_workspace/` —
  written atomically and never containing a secret/token/raw environment
  value. The `finalize` command also prints the report to the terminal as
  JSON; `report` reloads and reprints the persisted copy at any later time,
  including after a simulated restart (see `attach_to_task` in
  `orchestrator.py`).

### Inspecting duplicate changes, and why nothing is merged back

```bash
diff -ru demo_workspace/sample_project data/owner_handoff/sessions/<session-id>/
```

**Nothing is ever copied or merged back into `demo_workspace/`
automatically** — review the duplicate, then apply whatever you approve by
hand. This is intentional, not a missing feature.

## 7. Optional: real Research mode

Requires step 4 running and reachable.

```bash
python3 run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace/sample_project \
  --executor fake \
  --research real \
  --agent-url http://127.0.0.1:9110
```

## 8. Optional: real Codex mode

Requires step 5's Codex CLI installed and verified. **This actually invokes
a real `codex exec` subprocess** (sandboxed, network-disabled, scoped to the
physical duplicate, and only after a matching CODEX_DATA YES) — treat any
output as something that needs a human review before trusting it, exactly
like the rest of this section's "defense in depth, not proof" framing for
Codex JSONL inspection.

```bash
python3 run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace/sample_project \
  --executor codex \
  --codex-binary codex
```

## Hard-coded defaults (all documented, most overridable via `.env`)

| Parameter | Default | Overridable? |
|---|---|---|
| Owner-leave idle threshold | 5s | `AI_DESK_V2_INPUT_IDLE_SECONDS` |
| Owner-leave confirmation window | 10s | `AI_DESK_V2_LEAVE_CONFIRM_SECONDS` |
| Handoff/permission question expiry | 60s | `AI_DESK_V2_QUESTION_EXPIRY_SECONDS` |
| Codex max runtime | 300s | `AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS` |
| Codex max safe steps | 20 | `AI_DESK_V2_CODING_MAX_STEPS` |
| Return grace period (finish the current atomic step) | 30s | fixed constant (`ReturnCoordinator(return_grace_period_seconds=...)`), not env-configurable |
| Return-coordinator step poll interval | 0.05s | fixed constant (`return_coordinator._STEP_POLL_SECONDS`) |
| Post-terminate extra wait (only when a real `terminate_fn` was called) | 0.1s | fixed constant (`return_coordinator._POST_TERMINATE_GRACE_SECONDS`) |
| Clock-skew tolerance for wearable answers | 5s | fixed constant (`CLOCK_SKEW_TOLERANCE_SECONDS`) |
| Codex JSONL poll interval (how often the executor re-checks its own runtime budget) | 1.0s | fixed constant (`execution/codex_cli._POLL_INTERVAL_SECONDS`) |
| Codex JSONL: retained events / retained text per string | 200 events / 2000 chars | fixed constants in `execution/codex_cli.py` |
| Codex subprocess stdout event queue size / stderr tail buffer | 500 lines / 20,000 chars | fixed constants (`_SubprocessCodexProcessHandle`) |
| Codex process SIGTERM -> SIGKILL escalation wait | 5s (each stage) | fixed constant (`_SubprocessCodexProcessHandle._TERMINATE_WAIT_SECONDS`) |
| Codex preflight command timeout | 15s | fixed constant (`SubprocessCommandRunner`) |
| Package install subprocess timeout | 300s | fixed constant (`SubprocessInstallerCommandRunner`) |
| A2A (real Research mode) request timeout | 180s | `AI_DESK_A2A_TIMEOUT_SECONDS` (existing Research Handoff MVP setting, reused unchanged) |

## Optional: Codex preflight-only mode

To check an installed Codex CLI's isolation-flag support without creating
any duplicate, task database, CODEX_DATA question, or A2A contact:

```bash
python3 run_owner_handoff_demo.py \
  --workspace demo_workspace/sample_project \
  --executor codex \
  --preflight-only
```

This runs only `codex --version` and `codex exec --help`, reports which
required flags (`--sandbox`, `--cd`, `--ephemeral`, `--json`,
`--ignore-user-config`, `--skip-git-repo-check`, `--strict-config`,
`-c`/`--config`) the installed CLI advertises, and exits — structurally, it
never constructs an orchestrator, a store, or a workspace duplicator, so it
cannot reach any of those side effects even accidentally. `--workspace` is
still required (never defaulted) even though this mode doesn't read it, to
keep the CLI's "mandatory workspace" contract uniform across modes.

## Current limitations

- Wearable and desk radar are **simulators only** — no real BLE, no real
  serial reader (this stays true even in `--executor codex` mode).
- The only UI is this Terminal REPL — no Dashboard/summary integration.
- No OCR fallback is implemented (`ocr_adapter_mode` is `noop`/`mock` only).
- Real Codex execution (`--executor codex`) is a real subprocess call —
  every layer here (sandbox, network-disabled, physical duplicate, fixed
  prompt policy, post-hoc manifest re-hashing, the installer gate) is
  defense in depth, not a formal guarantee; **treat any real Codex rehearsal
  as requiring explicit human review of the diff before trusting it.** In
  particular, post-hoc JSONL event inspection (including the
  prohibited-command check) can stop the stream and fail the task before
  accepting further steps, but it cannot prevent the sandbox from having
  allowed the first occurrence of a command in the first place.
- Return/cancellation semantics: the "current atomic step" is one
  `CodingAgentExecutor`/`ResearchAgentExecutor` call. A cooperative
  executor (`CodexCLIExecutor`, the demo's fake executors) checks for a
  confirmed return between its own internal steps and stops promptly; an
  uncooperative or uncancelable step (e.g. a real in-flight A2A request)
  is bounded by the return grace period and then honestly reported as
  "did not finish in time" rather than falsely claimed as terminated — it
  may still be running, abandoned on its own background thread, which can
  never block process exit.
- Task artifacts (manifest, selected task, execution result, approved
  package installs, final report) are persisted under
  `<session-root>/<task_id>.artifacts/`, but this repository does not run
  a real multi-process restart drill — restart-recovery/reload behavior is
  covered by same-process, simulated-restart tests only (a fresh
  orchestrator instance sharing the same store/session-root).
- `--workspace` accepts any existing, non-root, non-home directory outside
  `workspace_session_root` — the safety boundary is path-policy validation
  (`validate_demo_output_paths`/`validate_source_path`), not a restriction
  to the checked-in `demo_workspace/` fixture; pointing it at a real
  project is supported, but review the resulting duplicate before trusting
  or applying anything from it.

## Product logic in one page

AI Desk V2 is not an activity logger that happens to start an agent. Presence
is part of the authorization control plane:

```text
mouse + keyboard + active app
             |
             v
  work classification/context -----> optional OCR only if still ambiguous
             |
radar absent + owner wearable away + input idle for 5s + stable for 10s
             |
             v
     OWNER_LEFT_CONFIRMED
             |
   contextual A/B/C/D question on Mac
             |
 owner answers on identity-bound wearable
       |                         |
       | research                | coding
       v                         v
  A2A Research Agent     physical workspace duplicate
                         + separate CODEX_DATA YES/NO
                         + sandboxed Codex CLI
       |                         |
       +------------+------------+
                    v
      owner returns -> stop new steps -> verify original
                    -> persist resume report -> return control
```

Important distinctions:

- The radar answers "is somebody at the desk?"; it does not identify the
  owner.
- The wearable answers "is the owner nearby?" and returns explicit
  A/B/C/D or YES/NO authorization.
- Conflicting or unknown signals wait. One missing/disconnected signal never
  silently becomes authorization.
- Research receives only a selected task capsule. Coding receives only an
  explicitly approved physical duplicate. The original workspace is never
  modified or automatically merged back.

## Current interfaces and integration boundary

The domain interfaces are implemented and tested; the real radar/BLE
transports are deliberately still replaceable adapters.

| Boundary | Current callable interface | Current implementation |
|---|---|---|
| Desk presence | `RadarSensor.read() -> RadarSample` | `SimulatorRadar`; real serial adapter deferred |
| Owner wearable | `read_proximity()`, `send_question(question_id, letters)`, `poll_answer()` | `WearableSimulator`; real BLE adapter deferred |
| Activity understanding | Tier 1 classifier -> Tier 2 context analyzer -> OCR provider | classifier/analyzer plus `noop`/`mock` OCR only |
| Research | `A2AClientProtocol.send_task(TaskCapsule) -> A2AResult` | wrapper around existing `A2AHandoffClient`; fake or localhost real mode |
| Coding | `CodingAgentExecutor.execute(CodingTaskRequest) -> ExecutionResult` | meaningful deterministic demo executor or real `CodexCLIExecutor` |
| Package installation | exact package specs + exact argv + separate YES/NO permit | simulated runner in the demo; real subprocess adapter exists but is not enabled by the demo CLI |
| User interface | commands documented in step 6 | Terminal only; Dashboard integration deferred |
| Persistence | SQLite state/event ledger plus sanitized atomic JSON artifacts | implemented under `data/owner_handoff/` by default |

Wearable privacy is intentionally narrow: `send_question` receives only a
`question_id` and the available letters. The full work context and option text
remain on the Mac. A future physical transport must produce these domain
values:

```text
proximity: OWNER_NEAR | OWNER_AWAY | UNKNOWN
answer:    device_id + question_id + (A|B|C|D|YES|NO) + timestamp
```

BLE Service/Characteristic UUIDs, pairing, RSSI hysteresis, reconnect rules,
transport replay protection, and button debounce are not finalized in this
repository. They must be documented with the firmware instead of being
invented independently in the Mac adapter.

## Recommended hackathon hardware configuration

| Role | Recommended hardware | Why |
|---|---|---|
| Desk presence | Existing Arduino + the already-wired presence sensor | Reuses the working sensor and keeps person-at-desk detection separate from identity |
| Primary wearable | LCKFB Huangshan Pi / HSPI-SF32LB52 | Watch-like form, touch display, battery support, buttons/vibration; strongest stage presentation |
| Low-risk wearable fallback | LCKFB ESP32-S3R8N8 + one/two buttons + LED + small USB power bank | Familiar Arduino BLE path and much lower SDK risk |
| Orchestrator | macOS laptop | Runs this repo, Terminal question UI, A2A client, and optional Codex CLI |
| Enclosure | 3D-printed wrist or badge enclosure | Build only after communication works; cosmetic, not a software dependency |

Official references:

- [Huangshan Pi hardware](https://wiki.lckfb.com/zh-hans/hspi-sf32lb52/hardware/board.html)
- [Huangshan Pi SDK setup](https://wiki.lckfb.com/zh-hans/hspi-sf32lb52/lckfb-hspi-sf32lb52/environment.html)
- [LCKFB ESP32-S3 Arduino BLE example](https://wiki.lckfb.com/zh-hans/esp32s3r8n8/arduino-beginner/bluetooth.html)

Use a two-hour Huangshan Pi go/no-go gate: keep it only if the team can flash
an example, use touch/buttons, run from battery, and deliver one message to
the Mac. Otherwise switch immediately to ESP32-S3. Do not add RDK vision or an
Insta360 camera to the MVP: neither is needed to demonstrate owner identity
and authorization, and both expand privacy and integration risk.

For the ESP32-S3 fallback, one button is sufficient: short press cycles
A/B/C/D, long press confirms, and double press cancels. The Mac always shows
the complete question. The wearable should never store OpenAI credentials or
call an AI API directly.

## Recommended hackathon demo sequence

### Reliable stage path available now

1. Run the deterministic demo from step 6 and show the original failing
   calculator test.
2. Enter `away`; explain that four signals are fused in production, while
   this command advances the deterministic sensor simulator.
3. Enter `ask`; show the contextual options and the minimal wearable payload.
4. Enter `select A`, then `grant`; distinguish task choice from the separate
   Codex data authorization.
5. Enter `run`, `status`, and `wait`; show that the change exists only in the
   duplicate workspace.
6. Enter `return`, `finalize`, and `deliver`; show
   `original_files_modified: []` and the resumable report.

### Intended physical stage path after adapters are added

1. Arduino sensor reports the desk empty while the wearable reports the owner
   away; mouse and keyboard remain idle through the configured windows.
2. Mac displays the full context-derived question; wearable displays or
   cycles only A/B/C/D.
3. Owner answers physically. Coding additionally asks YES/NO before Codex can
   see the duplicate; Research sends only the selected task capsule over A2A.
4. On return, AI Desk stops starting new steps, finishes or bounds the current
   step, verifies the original workspace, and presents the resume report.

Keep the deterministic Terminal path ready as the on-stage fallback even
after hardware is connected.

## Hackathon innovation points

Use these two claims; they correspond directly to implemented architecture
rather than a generic "AI productivity" pitch:

1. **Presence becomes an authorization primitive.** AI Desk combines physical
   desk presence, owner-bound proximity, computer input, and explicit wearable
   consent to decide when an agent may take over. It does not merely record
   that the user was away.
2. **Safe, reversible human-agent handoff across specialist agents.** A2A
   research gets a minimal task capsule; coding gets a freshly hashed physical
   duplicate only after a second data permit. Owner return closes the autonomy
   window and produces a verifiable resume artifact with the original-file
   modification list structurally empty.

The key contrast with ActivityWatch is therefore not finer activity
classification. Activity history supplies context; AI Desk turns identity,
presence, and physical consent into a bounded handoff protocol for acting
agents.

## macOS compatibility status and release gate

The code-level audit found no Windows-only dependency in the V2 default path:

- Python paths use `pathlib`; SQLite and atomic `os.replace` are available on
  macOS.
- Package-interpreter selection uses `<duplicate>/.venv/bin/python` on
  macOS/Linux.
- Real Codex processes start in their own POSIX session and use a bounded
  process-group `SIGTERM` then `SIGKILL` fallback.
- Research mode uses a localhost HTTP A2A endpoint.
- `.env` is now loaded by the V2 entry point without overriding already
  exported variables.
- The required Codex non-interactive, sandbox, JSONL, ephemeral, config, and
  git-check flags are checked at runtime by `--preflight-only`; see the
  current official
  [non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Repository verification at the Phase 5 audit point:

```text
454 passed, 4 skipped, 31 subtests passed
```

That run was performed on Windows. The skipped cases are environment-bound
(including symlink cases that should execute on macOS), so it is evidence of
portable logic, not proof that real Mac hardware works. Do not label the
physical configuration "macOS verified" until a teammate runs this exact gate
on the intended demo Mac:

```bash
cd 7-10-7-12-Hackathon
source .venv/bin/activate
python3 -B -m pytest tests -q

python3 run_owner_handoff_demo.py \
  --simulate \
  --workspace demo_workspace/sample_project \
  --executor fake

codex login status
python3 run_owner_handoff_demo.py \
  --workspace demo_workspace/sample_project \
  --executor codex \
  --preflight-only
```

Release criteria:

- all tests pass on the target Mac, with every unexpected skip investigated;
- the full fake coding transcript completes and the original fixture remains
  unchanged;
- Codex login succeeds and preflight advertises every required flag;
- for real Codex mode, one explicitly authorized rehearsal finishes inside a
  duplicate and its diff is reviewed manually;
- for physical mode, one leave/answer/return cycle succeeds with the intended
  radar and wearable firmware.

Until the last item passes, the deterministic simulator demo is ready, the
macOS software path is code-reviewed and testable, but real BLE/radar operation
remains an integration milestone rather than a completed feature.
