# Application Layer (Developer C)

This package uses `AIDeskPresenceAPI` and runtime events without modifying the
Runtime, Session Manager, state machine, or database core.

Implemented:

- event-driven application controller;
- macOS frontmost app/window capture while the state is `Working`;
- context deduplication with a periodic heartbeat;
- internal ActivityWatch-inspired buckets, events, and heartbeat spans without
  requiring ActivityWatch to be installed;
- additive activity storage while legacy `context_events` remain compatible;
- optional Chrome semantic capture through a local native-messaging bridge;
- rule-first learning/work/entertainment classification, with optional remote
  classification through the existing provider settings;
- session/presence/activity fusion and compact daily category aggregation;
- throttled macOS Accessibility capture with no screenshots or OCR;
- manual AI daily-summary generation with structured output and local fallback;
- minimal macOS-oriented dashboard with capture pause and AI settings.

## macOS permission

Window-title capture uses `/usr/bin/osascript` and System Events. Give the
terminal or packaged Python application permission under:

`System Settings → Privacy & Security → Accessibility`

If permission is denied, capture errors are logged and the system runtime keeps
running. No keyboard input, screenshots, OCR, form values, or full document
contents are captured. The optional Accessibility source reads only a bounded
set of visible labels, headings, and links every 15 seconds.

The demo routes the macOS sources through `ActivityCoordinator`. It persists
compact activity spans and continues to emit the existing active-window context
events, so the public system API, timeline, and summary pipeline are unchanged.
The activity layer has no ActivityWatch runtime or package dependency.

Typical construction:

```python
from application import ApplicationController
from application.activity import (
    ActivityCoordinator,
    ActivityStore,
    MacOSWindowSource,
)
from application.context import MacOSContextProvider
from services.ai_desk_api import AIDeskPresenceAPI

api = AIDeskPresenceAPI()
collector = ActivityCoordinator(
    api,
    [MacOSWindowSource(MacOSContextProvider())],
    ActivityStore(api.database),
)
controller = ApplicationController(api, collector)
controller.start()
```

`controller.stop()` only stops the application layer. Pass
`stop_runtime=True` only when closing the whole product and intentionally
ending the current backend lifecycle.

## Manual AI summary

The demo's **AI Settings** button stores the selected provider, model, and API
key in macOS Keychain. Remote activity classification is disabled by default;
the local rules remain usable without a key or network connection.

The older direct OpenAI client is still available for integrations that prefer
environment variables. Create `.env` in the repository root (it is ignored by
Git):

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

## Chrome semantic capture

Chrome support is optional and does not require ActivityWatch. On macOS:

1. Load `browser_extension/` as an unpacked extension at
   `chrome://extensions`.
2. Copy its generated extension ID.
3. Run `python3 -m application.browser.install_native_host <extension-id>`.
4. Restart Chrome.

The extension sends the page origin/path, title, description, bounded headings,
language, and media state. It omits query strings, fragments, forms, cookies,
and full HTML. Incognito observations are rejected. The native host reads the
same pause and exclusion policy as the desktop collector.

## Fusion and classification

`ActivityFusionService` intersects the compact activity spans with the public
session/break timeline. Browser semantics take priority over Accessibility
labels, which take priority over the active-window title. Away-time media is
reported separately as `background_playback`.

Classification is rule-first and cached by a stable segment hash. Known
learning domains and development applications are handled locally. Ambiguous
segments remain `unknown` unless remote classification is explicitly enabled in
the activity privacy settings. No OCR fallback is included in this MVP.

The privacy file is stored at
`~/Library/Application Support/AI Desk/activity-privacy.json` on macOS. It can
pause capture, disable remote classification, and exclude application names or
hostnames.

## Demo dashboard

Install the Python dependency and start the application from the repository
root:

```bash
python3 -m pip install -r requirements.txt
python3 run_demo.py
```

The dashboard uses Tkinter and deliberately keeps a plain document/dashboard
style: system typography, neutral panels, one status color, and no chat or
assistant visual language. It shows the current state, today's work time,
longest focus period, break count, recent timeline, and a manually generated
daily summary.

Closing the dashboard closes the whole product runtime and therefore ends the
active backend lifecycle. On macOS, grant Accessibility permission as
described above before expecting window-title capture.
