# Application Layer (Developer C)

This package uses `AIDeskPresenceAPI` and runtime events without modifying the
Runtime, Session Manager, state machine, or database core.

Implemented:

- event-driven application controller;
- reconnecting ESP32 USB-serial presence adapter for macOS;
- macOS frontmost app/window capture while the state is `Working`;
- context deduplication with a periodic heartbeat;
- compact daily session, break, statistics, and activity aggregation.
- manual AI daily-summary generation with structured output and local fallback.
- minimal macOS-oriented dashboard for status, statistics, timeline, and summary.

## macOS permission

Window-title capture uses `/usr/bin/osascript` and System Events. Give the
terminal or packaged Python application permission under:

`System Settings → Privacy & Security → Accessibility`

If permission is denied, capture errors are logged and the system runtime keeps
running. No keyboard input, screenshots, or document contents are captured.

Typical construction:

```python
from application import ApplicationController
from application.context import ContextCollector, MacOSContextProvider
from services.ai_desk_api import AIDeskPresenceAPI

api = AIDeskPresenceAPI()
collector = ContextCollector(api, MacOSContextProvider())
controller = ApplicationController(api, collector)
controller.start()
```

`controller.stop()` only stops the application layer. Pass
`stop_runtime=True` only when closing the whole product and intentionally
ending the current backend lifecycle.

## Manual AI summary

Create `.env` in the repository root (it is ignored by Git):

```dotenv
OPENAI_API_KEY="..."
OPENAI_MODEL="gpt-5.4-mini"
```

`OpenAIResponsesClient` loads this file automatically. Existing process
environment variables take priority over `.env` values.

The service never subscribes to `SessionEnded`; a summary is generated only
when `generate_today()` or `generate_today_async()` is called:

```python
from application.summary import (
    DailyDataAggregator,
    ManualSummaryService,
    OpenAIResponsesClient,
    SummaryStore,
)

summary_service = ManualSummaryService(
    aggregator=DailyDataAggregator(api),
    llm_client=OpenAIResponsesClient(),
    store=SummaryStore(),
)

# Call this from a UI button handler. Poll the Future from the UI main thread.
future = summary_service.generate_today_async()
```

Successful and fallback summaries are saved under `data/summaries/`. If the
API key, network, or model output fails, the service records the error and
returns a deterministic local summary so the demo remains usable.

Normal tests use mocks and never call the real API:

```bash
python -m unittest -v
```

Run the single opt-in network test explicitly when you intend to spend an API
request:

```bash
RUN_OPENAI_INTEGRATION=1 \
  python -m unittest tests.test_openai_integration -v
```

Keep `RUN_OPENAI_INTEGRATION` out of `.env`; setting it only for the command
makes accidental API calls less likely.

## Demo dashboard

Install the Python dependency and start the application from the repository
root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_demo.py
```

The dashboard uses Tkinter and deliberately keeps a plain document/dashboard
style: system typography, neutral panels, one status color, and no chat or
assistant visual language. It shows the current state, today's work time,
longest focus period, break count, recent timeline, and a manually generated
daily summary. The header also reports whether the ESP32 sensor is connected.

The serial adapter accepts `PRESENT`/`ABSENT` lines at 115200 baud and
automatically reconnects to common `/dev/cu.usb*` device names. Set
`AI_DESK_SERIAL_PORT` in `.env` to force a specific device. The matching
firmware and wiring guide are under `firmware/`.

Closing the dashboard closes the whole product runtime and therefore ends the
active backend lifecycle. On macOS, grant Accessibility permission as
described above before expecting window-title capture.
