# AI Desk V2 — Phase 0 Report

Scope: repository audit, architecture design, contract definition only.
**No product code, tests, or hardware adapters were implemented in this
phase.** Only this file and `docs/AI_DESK_V2_MASTER_SPEC.md` were created.

- Base branch: `origin/MVP` @ `7a31fe0`
- Working branch: `feature/owner-authorized-handoff-v2` (created from
  `origin/MVP`, not yet pushed)
- Report date: 2026-07-25

---

## 1. Git preparation log

| Step | Result |
|---|---|
| `git status` before starting | Clean (`Friday` branch, "nothing to commit") |
| `git fetch origin --prune` | Completed, no new refs beyond existing ones |
| Local `MVP` vs `origin/MVP` | Identical (`7a31fe0c928b06c2c03b89bc74725cf68e9f0064`) |
| `feature/owner-authorized-handoff-v2` pre-existing? | No (local or remote) |
| Branch creation | `git checkout -b feature/owner-authorized-handoff-v2 origin/MVP` |
| Post-creation `git status` | Clean, tracking `origin/MVP` |

No files were modified, discarded, or overwritten. `main` and `Friday` were
not touched.

## 2. MVP test baseline

Command: `python -m pytest tests/ -v` on `feature/owner-authorized-handoff-v2`
(= `origin/MVP` content), Python 3.11.0 / pytest 9.0.3 / Windows.

**Result: 47 passed, 1 skipped, 0 failed, in 3.36s.**

The single skip is `tests/test_openai_integration.py::OpenAIIntegrationTest::
test_generates_structured_daily_summary`, which requires a real OpenAI key and
is intentionally excluded from the deterministic suite.

All 11 existing test modules pass, including `tests/test_handoff.py` (the
existing Research Handoff Agent MVP: store round-trip, orchestrator
delegation/return, grace-period cancellation, bad-URL rejection). This is the
baseline that Phase 1 must not regress.

## 3. Repository audit summary

### 3.1 Layout (MVP)

```
Stable system layer (unchanged surface, do not modify):
  database/, events/, listeners/, models/, runtime/, session/, services/,
  utils/, main.py

Application layer (existing features to build alongside, not replace):
  application/config.py            - .env loader, PROJECT_ROOT
  application/controller.py        - ApplicationController: reconciles
                                      PresenceState -> starts/stops
                                      ContextCollector on a worker thread
  application/context/             - MacOSContextProvider (osascript),
                                      ContextCollector (poll + heartbeat +
                                      dedupe, records macos_active_window
                                      context events)
  application/presence/             - SerialPresenceAdapter (ESP32 LD2410,
                                      boolean PRESENT/ABSENT -> PresenceDetected
                                      /PresenceLost events)
  application/handoff/               - EXISTING presence-aware A2A Research
                                      Handoff MVP (see 3.2)
  application/providers/, summary/, ui/ - Dashboard + LLM daily-summary path,
                                      unrelated to presence/handoff safety
                                      logic

Entry points: main.py (Dashboard), run_demo.py (headless system-layer demo),
run_handoff_demo.py (Research handoff demo), arm_handoff.py (arms one task)

firmware: only a root-level ai_desk_presence.ino exists; README references a
firmware/ directory and firmware/README.md that are NOT present in this repo
snapshot (pre-existing doc/repo mismatch, not introduced by Friday or this
report).
```

### 3.2 Existing Research Handoff Agent (`application/handoff/`)

This is the closest existing analog to what V2 must generalize, and is the
system V2 must keep working as a distinct "Research Agent" path:

- `models.py`: `TaskCapsule` (goal, inputs, constraints, schema-versioned
  payload), `HandoffRecord`, `HandoffStatus` enum (armed → delegating →
  running → ready/input_required/failed → returned, plus canceled).
- `store.py`: `HandoffStore`, a small SQLite queue with WAL mode, `BEGIN
  IMMEDIATE` + `UPDATE ... WHERE status = ?` atomic claims, and strict
  allowed-source-state transitions per method (`claim_next_armed`,
  `mark_running`, `complete`, `mark_failed`, `mark_returned`, `cancel`). This
  is the pattern V2's own persisted state machine should follow.
- `orchestrator.py`: `HandoffOrchestrator` subscribes to the **existing**
  `StateChanged` event from the legacy `PresenceState` (Idle/Working/Break/
  Finished) state machine, starts a grace-period `Timer` on `BREAK`, claims
  and delegates the next armed task, and delivers results on return to
  `WORKING`.
  - **Important:** this orchestrator's absence detection is driven entirely
    by the legacy `PresenceState`, which itself is driven by a single boolean
    presence signal (`ingest_presence`/serial adapter). It has none of V2's
    radar+wearable+keyboard/mouse fusion, no 10-second confirmation window,
    and no handoff-question/authorization step. It is a real MVP but is
    intentionally much simpler than what section 7–9 of the spec requires.
  - This module **must remain intact** and continue to be usable in its
    current form.
- `a2a_client.py`: `A2AHandoffClient`, a blocking facade over the async A2A
  1.x client; validates agent URL scheme up front (raises before any
  network call — exercised by `test_bad_agent_url_is_rejected_before_network`).
- `macos_actions.py` / `report.py`: `MacOSHandoffActions` (osascript
  `display notification` + `open`, via an injectable `runner`) and Markdown
  report rendering saved to `data/handoffs/<id>/{report.md,artifact.json}`.

**Reuse decision for V2:** the new `ResearchAgentExecutor` should wrap
`A2AHandoffClient` + `TaskCapsule` + `HandoffStore`'s record/report
conventions rather than reimplementing them, but V2's own leave/return
detection and state machine are new and independent of `PresenceState`/
`HandoffOrchestrator`'s grace timer (see §9, Unresolved Decisions).

### 3.3 Friday branch comparison

`git log origin/MVP..origin/Friday --oneline` → 2 commits: `Add files via
upload`, `Delete application directory` (i.e. Friday's history is MVP plus one
upload of a modified `application/` tree). Full-tree diff: **54 files
changed, 1515 insertions(+), 46 deletions(-)**, entirely confined to
`application/` (no changes to `database/`, `models/`, `events/`, `listeners/`,
`runtime/`, `session/`, `services/`, `utils/`, `main.py`, `run_demo.py`,
`run_handoff_demo.py`, `arm_handoff.py`, `requirements.txt`, `tests/`,
`README.md`, or `.gitignore` — those are byte-identical to MVP).

Verified findings for the five flagged concerns, plus others found during the
diff:

| # | Concern | Verified? | Evidence |
|---|---|---|---|
| 1 | Activity collector not wired into `run_demo.py` | **Confirmed, and broader** | `application/activity/collector.py` (`ActivityMetricsCollector`) is never imported outside its own module. `main.py`, `run_demo.py`, `run_handoff_demo.py`, `arm_handoff.py`, and `application/__init__.py` are all byte-identical to MVP. `ApplicationController.__init__` gained an optional `activity_collector` parameter that is `None` unless a caller passes one — and no caller does. The feature is fully dead code in every entry point. |
| 2 | Dependencies incomplete | **Confirmed** | `application/activity/collector.py._quartz_snapshot` does `import Quartz` (macOS `pyobjc-framework-Quartz`) lazily inside the method. `requirements.txt` is unchanged from MVP (`python-dotenv`, `a2a-sdk`, `pyserial` only) — `Quartz` is not declared anywhere. Because the import is lazy, `import application.activity.collector` does not fail, but any real run of `_run()`'s Quartz-polling branch would raise `ImportError` on a machine without pyobjc, with only a broad `except Exception` + log message masking it (collector silently degrades to interface-only metrics, not to keyboard/mouse metrics as advertised). |
| 3 | `clear_all_data` may not exist | **Confirmed — broken reference** | `application/ui/dashboard.py:684` calls `self.api.clear_all_data()`. `git grep -n "def clear_all_data"` across the entire Friday tree returns **no match**. `services/ai_desk_api.py` (identical on both branches) has no such method. This is a latent `AttributeError` waiting to fire the first time that Dashboard control path is exercised. |
| 4 | Summary regressions | **Confirmed — real regression, corrected 2026-07-25 review** | ~~Originally reported here as "unrelated scope creep, not a regression per se," reasoning that the code path was unreachable because its only producer (`ActivityMetricsCollector`) is dead.~~ **That reasoning was wrong and is retracted.** The summary-formatting changes are reachable through the *existing*, already-wired `ManualSummaryService`/`format_summary` path regardless of whether the activity collector ever runs. Running Friday's own test suite (`origin/Friday`, verified independently in a throwaway `git worktree` on 2026-07-25, without switching this branch) produces **44 passed, 3 failed, 1 skipped** — not the MVP baseline's 47/0/1. The three failures are confirmed regressions: (1) `tests/test_daily_summary.py::ManualSummaryServiceTest::test_async_manual_generation_returns_future` — `ManualSummaryService` returns a headline that does not match the LLM/fake-supplied `VALID_SUMMARY`, i.e. **it overwrites the generated LLM/fallback `DailySummary` with a fixed metrics-only object** instead of returning what the LLM produced; (2) `tests/test_daily_summary.py::ManualSummaryServiceTest::test_retries_then_uses_and_persists_fallback` — the fallback's `data_quality_note` is asserted to contain specific text and comes back empty, i.e. **fallback data-quality information is discarded**; (3) `tests/test_ui_presentation.py::UIPresentationTest::test_formats_summary_as_plain_document_not_chat` — `format_summary` no longer renders the expected activity/completed-items section, i.e. **UI summary formatting removes the existing activity, observation, suggestion, and data-quality sections**. This is audit documentation only — Friday's summary code is not being ported, fixed, or otherwise touched by AI Desk V2. |
| 5 | Tracked `__pycache__` | **Confirmed, and pre-existing on MVP too** | `origin/MVP` itself already tracks 25 `.pyc` files (e.g. `application/__pycache__/*.cpython-311.pyc`, `tests/__pycache__/*.pyc`). Friday adds **17 more** tracked `.pyc` files for its new/changed modules on top of that. This is a pre-existing MVP hygiene defect that Friday makes worse; it is not something Friday introduced from a clean baseline. Phase 0 does not fix this (only the two doc files may change). **Corrected 2026-07-25 review:** an earlier draft of this row said a later phase "must address" (i.e. clean up) the pre-existing MVP `.pyc` files — that was wrong and contradicted the approved decision recorded in §16 below. The approved decision is: **do not clean historical MVP bytecode in any phase of this work; only ensure no *new* tracked bytecode is created** for any module this work adds. |

No test files were added or changed anywhere on Friday relative to MVP —
`tests/` is byte-identical. `application/activity/analyzer.py` (417 lines) and
`collector.py` (359 lines) ship with **zero tests**, confirmed by the empty
diff on `tests/`.

**Other Friday changes noted (not part of the five flagged concerns):**

- `application/context/macos_provider.py`: `DesktopContext` gained
  `browser_title`/`browser_url` fields, and the AppleScript now reads the
  active Chrome tab's title/URL when the frontmost app is Google Chrome. The
  parser accepts either the old 2-field or new 4-field `osascript` output
  (`if len(fields) == 2: fields.extend(["", ""])`) — a quick backward-compat
  shim rather than a clean redesign.
- `application/activity/analyzer.py`: a 5-axis heuristic classifier
  (`MouseState`, `KeyboardState`, `ContentState`, `SwitchingPattern`,
  `AttentionState`) using per-app/per-domain keyword tables (task apps like
  VS Code/Terminal/PyCharm, entertainment domains, communication apps,
  bilingual YouTube title heuristics) — richer than, and not a drop-in
  replacement for, the spec's required 3-value `WORKING/NOT_WORKING/
  AMBIGUOUS` classification.
- `application/activity/collector.py`: aggregate keyboard/mouse metrics via
  macOS Quartz event-source **counters** (`CGEventSourceCounterForEventType`)
  and cursor position polling — deliberately never records individual key
  values or a stream of raw positions, only counts/deltas/distance. This is
  architecturally aligned with the spec's "never store actual keys" /
  "aggregate mouse and keyboard activity" requirement.
- `application/presence/serial_adapter.py`: added a small
  `reset_presence_state()` method (clears `_last_presence`/manual-pause
  after a full reset) — self-contained, low risk.
- `application/providers/`: added a `deepseek` provider (catalog entry +
  `DeepSeekChatClient`) to the Dashboard's daily-summary LLM selector —
  unrelated to owner-authorized handoff, no safety concerns, out of scope for
  V2 porting.

### 3.4 Friday concepts recommended for selective porting

| Concept | Recommendation | Rationale / required rework |
|---|---|---|
| Quartz-based aggregate keyboard/mouse counters (no key values, no raw positions retained) | **Port, after rewrite** | Matches spec §4 exactly ("aggregate mouse activity", "aggregate keyboard activity", "never store actual keys"). Must be rewritten as an `ActivityObserver` adapter behind a protocol (so non-macOS/simulator paths exist), with the lazy-import pattern made an explicit `HardwareUnavailable`-style capability check rather than a silently-degrading `except Exception`, and with `pyobjc-framework-Quartz` declared as an optional/macOS-only dependency. |
| Chrome active-tab title/domain capture | **Port, after rewrite** | Matches spec §4's "additional context" tier ("active Chrome tab title and domain"). Must extract only the **domain** (not full URL, to reduce sensitive-data surface) for anything persisted in `WorkContext.evidence`, replace the 2-vs-4-field backward-compat shim with a single versioned script/parser, and add tests (none exist today). |
| Per-app / per-domain task-vs-entertainment-vs-communication keyword tables | **Port as a seed list, with rework** | Useful as a starting point for the "additional context analysis" tier (spec §4) that only runs when activity is `AMBIGUOUS`, not as the primary classifier. Must be reduced/adapted to *inform* the required 3-value `WORKING/NOT_WORKING/AMBIGUOUS` result plus `WorkContext` evidence, not replace it with the 5-axis `AttentionState` model, which is out of scope for V2's contract. |
| `attention_window` context-event shape feeding daily summaries | **Do not port as-is** | This couples V2's activity signal directly into the *existing* Dashboard summary pipeline (`application/summary/aggregator.py`/`prompt.py`), which is out of scope for the owner-handoff feature and was never wired up or tested on Friday. If summary integration is wanted later, it should be a deliberate, separately-scoped follow-up, not inherited implicitly. |
| `reset_presence_state()` on `SerialPresenceAdapter` | **Optional, low-priority port** | Small, self-contained, not required by the spec, but harmless if the new radar adapter ends up wrapping this class (see §9 unresolved decision). |
| `clear_all_data`, DeepSeek provider, activity→summary wiring | **Do not port** | Broken reference, unrelated scope, and dead/untested wiring respectively; none relate to AI Desk V2's requirements. |

**No Friday commits will be merged or cherry-picked.** Any of the above will
be manually reimplemented from scratch inside the new `application/
owner_handoff/` package in Phase 1, reviewed and tested independently.

---

## 4. Architecture overview

### 4.1 New module: `application/owner_handoff/`

A new, self-contained application-layer package, sibling to the existing
`application/handoff/`, `application/context/`, etc. It does not modify any
existing module's public surface (see §8, Unchanged Public APIs). Its own
persistence is a separate SQLite database file, following the
`HandoffStore` pattern (WAL mode, `BEGIN IMMEDIATE` atomic transitions).

```
application/owner_handoff/
  __init__.py
  config.py                    # centralized config: every default in spec §3
                                # plus derived parameters; env-var overrides
  domain/
    __init__.py
    activity.py                 # ActivityClassification enum, ActivitySample
    presence.py                  # RadarState, WearableProximityState enums,
                                  # PresenceSnapshot
    work_context.py               # WorkContext dataclass + evidence rules
    question.py                    # HandoffQuestion, WearableAnswer
    manifest.py                     # ExecutionManifest, FileRecord, CommandRecord
    resume.py                        # ResumeReport
  state_machine.py             # OwnerHandoffState enum, transition table,
                                # OwnerHandoffStateMachine (pure logic)
  store.py                      # SQLite persistence for state machine +
                                # question + manifest (own .sqlite3 file)
  fusion/
    __init__.py
    leave_detector.py            # 10s unanimous-absence confirmation,
                                  # injected clock, one-task-per-event guard
    return_detector.py            # owner-return confirmation
  classification/
    __init__.py
    activity_classifier.py        # WORKING / NOT_WORKING / AMBIGUOUS tiering
    context_analyzer.py            # window title / Chrome domain / filename /
                                    # repeated-evidence tier (only on AMBIGUOUS)
    ocr_policy.py                   # invocation policy: only after activity +
                                     # context analysis remain ambiguous
  adapters/
    __init__.py
    radar.py                        # RadarSensor protocol + SimulatorRadar
    wearable.py                      # WearableTransport protocol + BLE stub
                                      # (interfaces/contracts only, no UUIDs)
                                      # + WearableSimulator
    ocr.py                            # OCRProvider protocol + NoOpOCR + MockOCR
  questions/
    __init__.py
    generator.py                      # builds HandoffQuestion from WorkContext,
                                       # maps options -> skill
  routing/
    __init__.py
    skills.py                          # Skill enum: RESEARCH, CODING, NONE
  workspace/
    __init__.py
    path_policy.py                      # reject roots/home/recursive/symlink-
                                         # escape/unsafe paths
    duplicator.py                        # physical copy + exclusions + hashing
                                          # + manifest + post-hoc verification
  execution/
    __init__.py
    research_executor.py                  # ResearchAgentExecutor (wraps
                                           # A2AHandoffClient/TaskCapsule)
    coding_executor.py                     # CodingAgentExecutor protocol,
                                            # FakeCodingExecutor
    codex_cli.py                            # CodexCLIExecutor: injectable
                                             # subprocess runner, sandbox flags,
                                             # runtime/step limits, preflight
    permission.py                            # PermissionGate: install +
                                              # Codex data-authorization prompts
  return_coordinator.py                  # owner-return handling: finish
                                          # current atomic step, persist,
                                          # build resume report
  orchestrator.py                        # top-level conductor wiring fusion ->
                                          # state machine -> questions ->
                                          # routing -> workspace -> execution ->
                                          # return coordination

run_owner_handoff_demo.py                 # new entry point (spec §11),
                                           # does not touch existing entry
                                           # points

demo_workspace/                           # new safe fixture directory
  README.md
  sample_project/...                      # placeholder files only

tests/
  test_activity_classifier.py
  test_work_context.py
  test_presence_fusion.py
  test_wearable_contract.py
  test_handoff_questions.py
  test_workspace_duplicator.py
  test_execution_policy.py
  test_codex_executor.py
  test_ocr_policy.py
  test_return_coordinator.py
  test_owner_handoff_e2e.py
  test_owner_handoff_state_machine.py      # not in spec's explicit list, but
                                            # required by §8's transition
                                            # guarantees; added for direct
                                            # coverage of the persisted machine
  test_path_policy.py                       # not in spec's explicit list;
                                             # added because path validation
                                             # is a distinct safety component
                                             # from the duplicator's copy logic
```

### 4.2 Component dependency diagram

```
                         ┌─────────────────────────┐
                         │        config.py          │  (all defaults/env)
                         └────────────┬──────────────┘
                                      │ read by everything below
                                      ▼
 ┌───────────────┐   ┌───────────────┐   ┌────────────────────┐
 │ RadarSensor    │   │ WearableTrans- │   │ ActivityObserver    │
 │ adapter        │   │ port adapter   │   │ (keyboard/mouse/    │
 │ (sim/serial)   │   │ (BLE stub/sim) │   │  interface, no OCR) │
 └───────┬────────┘   └───────┬───────┘   └──────────┬──────────┘
         │                    │                       │
         ▼                    ▼                       ▼
 ┌─────────────────────────────────────────┐   ┌──────────────────────┐
 │           fusion/leave_detector          │   │ classification/       │
 │  (radar + wearable + mouse + keyboard,   │   │  activity_classifier   │
 │   10s unanimity window, injected clock)  │   │  -> context_analyzer   │
 └───────────────────┬───────────────────────┘   │  -> ocr_policy (noop)  │
                     │ OWNER_LEFT_CONFIRMED       └───────────┬───────────┘
                     ▼                                        │
         ┌─────────────────────────────┐         updates      │
         │   state_machine.py + store   │◄──────────────────────┘
         │ (OwnerHandoffStateMachine,   │      WorkContext (domain/work_context)
         │  persisted, idempotent)      │
         └───────────────┬─────────────┘
                          │ OWNER_LEFT_CONFIRMED
                          ▼
              ┌────────────────────────┐
              │  questions/generator     │──▶ Terminal (full question)
              │  (freezes WorkContext)   │──▶ WearableTransport (id + letters)
              └────────────┬─────────────┘
                           │ matching answer -> AUTHORIZED
                           ▼
              ┌────────────────────────┐
              │   routing/skills.py      │  option -> {research, coding, none}
              └───────────┬────────────┘
             research      │      coding
        ┌──────────────────┴───────────────────┐
        ▼                                       ▼
┌────────────────────┐                ┌─────────────────────────┐
│ ResearchAgentExecutor│               │ workspace/path_policy +   │
│ (wraps existing      │               │ duplicator (physical copy,│
│  A2AHandoffClient +   │              │  hash manifest)            │
│  TaskCapsule/Store)   │              └────────────┬───────────────┘
└──────────┬────────────┘                            ▼
           │                              ┌─────────────────────────┐
           │                              │ execution/permission.py   │
           │                              │ (Codex data-auth YES/NO,  │
           │                              │  install YES/NO)           │
           │                              └────────────┬───────────────┘
           │                                            ▼
           │                              ┌─────────────────────────┐
           │                              │ execution/codex_cli.py     │
           │                              │ (FakeCodingExecutor in     │
           │                              │  tests / CodexCLIExecutor  │
           │                              │  in real demo; 300s/20-step│
           │                              │  caps; injectable runner)  │
           │                              └────────────┬───────────────┘
           └───────────────────────┬────────────────────┘
                                   ▼
                     ┌─────────────────────────────┐
                     │   return_coordinator.py       │
                     │ (owner-return -> finish atomic │
                     │  step -> verify original hashes│
                     │  -> resume report)              │
                     └───────────────┬─────────────────┘
                                     ▼
                        Terminal / macOS notification
                        (reuses application/handoff/macos_actions.py
                         style, via a shared thin notifier)

              orchestrator.py wires every arrow above together and is the
              only module that subscribes to adapter callbacks and drives
              the state machine's transitions.
```

Existing MVP modules referenced (read-only dependencies, never modified):
`application/handoff/a2a_client.py` (`A2AHandoffClient`), `application/
handoff/models.py` (`TaskCapsule`), `application/context/macos_provider.py`
(`DesktopContext`, for the "active window/app" activity signal — extended
only inside `owner_handoff`, not by editing this file), `utils/time_utils.py`
(`utc_now`), and the general SQLite-store pattern from `application/handoff/
store.py`.

---

## 5. State-transition table

> **Corrected 2026-07-25 review:** the original version of this table let
> `WORKSPACE_DUPLICATING` transition straight to `EXECUTING`, with Codex
> data-authorization folded into a single generic `PERMISSION_PENDING` state
> shared with package-install permission and no recorded distinction between
> the two. That was wrong: per Master Spec §14, Codex CLI must not start
> (must not enter `EXECUTING`) before a separate, explicit matching YES on
> the data-authorization question. The corrected order below is:
> `AUTHORIZED → WORKSPACE_DUPLICATING → PERMISSION_PENDING(CODEX_DATA) →
> EXECUTING`. Every `PERMISSION_PENDING` row now always records an explicit
> `permission_kind` ∈ `{CODEX_DATA, PACKAGE_INSTALL}`; `EXECUTING →
> PERMISSION_PENDING(PACKAGE_INSTALL)` remains valid only for a package
> request discovered during already-authorized execution (i.e. only after
> Codex data-authorization has already been granted once).

State enum: `OwnerHandoffState` = `OBSERVING`, `LEFT_CANDIDATE`,
`OWNER_LEFT_CONFIRMED`, `QUESTION_PENDING`, `AUTHORIZED`,
`WORKSPACE_DUPLICATING`, `EXECUTING`, `PERMISSION_PENDING`,
`RETURN_REQUESTED`, `READY_FOR_REVIEW`, `RETURNED`, `CANCELED`, `FAILED`.

`PermissionKind` = `CODEX_DATA`, `PACKAGE_INSTALL` — recorded on every
`PERMISSION_PENDING` row; there is no "generic" permission request.

Every row persists `{from_state, to_state, event_id, reason, occurred_at,
permission_kind (nullable, required when to_state or from_state is
PERMISSION_PENDING)}`. Transitions are applied via `UPDATE ... WHERE state =
<from> AND task_id = ?` (atomic compare-and-swap); a transition whose
`from_state` no longer matches current DB state is rejected as stale and
returns the current record unchanged (idempotent replay-safe).

| # | From | To | Trigger | Reason recorded | Notes |
|---|---|---|---|---|---|
| 1 | OBSERVING | LEFT_CANDIDATE | Any one of {radar=ABSENT, wearable=AWAY, mouse idle, keyboard idle} newly true | "partial absence signal detected" | Does not yet start a timer for confirmation-window purposes beyond entering the candidate state |
| 2 | LEFT_CANDIDATE | OBSERVING | Any absence signal reverts before 10s elapse, or signals become contradictory/UNKNOWN | "absence signals no longer unanimous" / "signal became UNKNOWN" | Implements §7's "if signals conflict or are UNKNOWN, wait" as a fallback to OBSERVING, not an error |
| 3 | LEFT_CANDIDATE | OWNER_LEFT_CONFIRMED | All four conditions (radar ABSENT, wearable AWAY, mouse inactive, keyboard inactive) hold continuously for `owner_leave_confirmation_seconds` (default 10) | "10s confirmation window elapsed with unanimous absence" | Generates a unique `confirmation_event_id`; unique DB constraint on this id guarantees exactly one downstream task row |
| 4 | OWNER_LEFT_CONFIRMED | QUESTION_PENDING | WorkContext frozen, question generated, displayed in Terminal, id+letters sent to wearable | "handoff question generated" | If confidence is insufficient, question may offer only D (per §9) |
| 5 | QUESTION_PENDING | AUTHORIZED | Matching wearable answer A/B/C: correct `device_id` (allowlisted), correct `question_id`, not expired, not a duplicate, state is exactly `QUESTION_PENDING` | "owner selected option <X>" | Any mismatch on device/question/expiry/duplicate is logged and does **not** transition (stays `QUESTION_PENDING` until expiry or a valid answer) |
| 6 | QUESTION_PENDING | CANCELED | D selected, OR 60s expiration (`handoff_question_expiration_seconds`) elapsed (defaults to D), OR owner return detected, OR wearable disconnect | "option D selected" / "question expired" / "owner returned before authorization" / "wearable disconnected" | No execution occurs; this is the fast, no-op path required by §9 |
| 7 | AUTHORIZED | WORKSPACE_DUPLICATING | Selected skill is `coding` | "coding skill selected, isolating workspace" | Research skill skips this state entirely (row 8) |
| 8 | AUTHORIZED | EXECUTING | Selected skill is `research` | "research task delegated to Research Agent" | No physical duplication for research; `ResearchAgentExecutor` operates on the frozen `WorkContext`/generated goal text only, never on the filesystem |
| 9 | WORKSPACE_DUPLICATING | PERMISSION_PENDING(CODEX_DATA) | Physical copy complete, manifest recorded, source-file hash baseline captured | "duplicate workspace ready; requesting codex data authorization" | **Corrected 2026-07-25:** no longer goes straight to `EXECUTING`. Codex CLI must not start, and the state machine must not enter `EXECUTING`, before this permission is separately granted (Master Spec §14) |
| 9b | PERMISSION_PENDING(CODEX_DATA) | EXECUTING | Matching wearable YES received for the Codex data-authorization question | "codex data authorization granted" | Codex CLI may only be invoked after this transition |
| 9c | PERMISSION_PENDING(CODEX_DATA) | CANCELED | NO, timeout, stale question id, owner return, or wearable disconnect | "codex data authorization denied" / "codex data authorization request expired" / "owner returned during codex data authorization" / "wearable disconnected" | Codex CLI is never invoked; no partial/ambiguous continuation |
| 10 | WORKSPACE_DUPLICATING | FAILED | Path validation rejected the source, copy I/O error, or destination verification mismatch | "workspace duplication failed: <detail>" | |
| 11 | EXECUTING | PERMISSION_PENDING(PACKAGE_INSTALL) | Coding executor discovers a required package install *during already-authorized execution* | "install permission requested" | Valid only after row 9b has already granted `CODEX_DATA`; this is the **only** other route into `PERMISSION_PENDING` from `EXECUTING` |
| 12 | PERMISSION_PENDING(PACKAGE_INSTALL) | EXECUTING | Matching wearable YES received | "install permission granted" | |
| 13 | PERMISSION_PENDING(PACKAGE_INSTALL) | CANCELED | NO, timeout, stale question id, owner return, or wearable disconnect | "install permission denied" / "install permission request expired" / "owner returned during install permission request" / "wearable disconnected" | Per §15, NO/timeout cancels installation **and** the whole task — no partial/ambiguous continuation |
| 14 | EXECUTING | RETURN_REQUESTED | Owner return confirmed (radar PRESENT + wearable NEAR, optionally corroborated by resumed input) while executing | "owner return confirmed during execution" | Does not stop the current atomic step immediately; signals the executor to stop after it |
| 15 | RETURN_REQUESTED | READY_FOR_REVIEW | Current atomic safe step finishes (or none was in flight), manifest + result persisted, execution halted | "current atomic step completed; execution halted" | |
| 16 | EXECUTING | READY_FOR_REVIEW | Task completes naturally, or the 300s runtime cap or 20-step cap is reached, before owner return | "task completed" / "runtime limit reached" / "step limit reached" | |
| 17 | EXECUTING | FAILED | Codex CLI missing at preflight, sandbox isolation flags unavailable (fail-closed), or unhandled executor error | "codex cli unavailable" / "sandbox isolation unavailable" / "executor error: <type>" | |
| 18 | READY_FOR_REVIEW | RETURNED | Resume report has been displayed/delivered to the user | "resume report delivered" | |
| 19 | {LEFT_CANDIDATE, OWNER_LEFT_CONFIRMED, QUESTION_PENDING, AUTHORIZED, WORKSPACE_DUPLICATING, PERMISSION_PENDING(CODEX_DATA), PERMISSION_PENDING(PACKAGE_INSTALL)} | CANCELED | Owner return confirmed before any atomic execution step has started | "owner returned before authorized execution began" | Fast-cancel path distinct from row 15 (which applies once execution has actually started); covers a return during either `permission_kind` |
| 20 | {FAILED, CANCELED, RETURNED} | OBSERVING | Operator/system acknowledges terminal state and the orchestrator is ready to observe again | "cycle reset after terminal state" | Only transition out of a terminal state; a new `confirmation_event_id` will be required for the next cycle |

Idempotency, staleness, and restart guarantees:

- Every transition write includes the `event_id` that caused it; replaying
  the same `event_id` against a record already in the target state is a
  no-op returning the existing record (never double-applies side effects
  like re-delegating or re-copying).
- A transition attempt whose expected `from_state` doesn't match the current
  persisted state is rejected outright (`changed != 1` on the `UPDATE`, same
  pattern as `HandoffStore._transition`), never silently coerced.
- On process restart, any task found in `WORKSPACE_DUPLICATING`,
  `EXECUTING`, or `PERMISSION_PENDING` **without** a live process/thread
  handle is force-transitioned to `FAILED` with reason "process restarted
  mid-execution" — it is never resumed against an untracked subprocess (see
  Threat #13 in §7).
- The unique constraint on `confirmation_event_id` (row 3) is what
  guarantees "one absence event starts at most one task."

---

## 6. JSON contracts

All contracts are versioned with a `schema_version`/`*_schema` style string
matching the existing `aidesk.research-handoff.request.v1` convention in
`application/handoff/models.py`.

### 6.1 WorkContext (persisted, per spec §5 — schema preserved verbatim)

```json
{
  "schema_version": "aidesk.owner_handoff.work_context.v1",
  "project": "",
  "current_task": "",
  "stage": "",
  "recent_actions": [],
  "recent_files": [],
  "unfinished_work": [],
  "possible_next_steps": [],
  "confidence": 0.0,
  "evidence": [],
  "updated_at": ""
}
```

`evidence` items are `{"kind": "observation"|"inference", "source": "",
"detail": "", "observed_at": "", "corroboration_count": 1}` — distinguishing
observations from inference per spec §5, and enabling dedup by `(kind,
source, detail)`.

### 6.2 Wearable answer (per spec §6 — schema preserved verbatim)

```json
{
  "device_id": "",
  "question_id": "",
  "button": "",
  "timestamp": ""
}
```

`button` ∈ `{A, B, C, D, YES, NO}`. Rejection reasons recorded internally
(not part of the wire contract, part of the adapter's audit log):
`unknown_device`, `stale_question_id`, `duplicate_answer`, `expired_question`,
`wrong_state`.

### 6.3 Handoff question (per spec §9 — schema preserved verbatim)

```json
{
  "question_id": "",
  "context_summary": "",
  "question": "",
  "options": {
    "A": "",
    "B": "",
    "C": "",
    "D": "Do nothing"
  },
  "created_at": "",
  "expires_at": ""
}
```

Internal-only (server-side, never sent to the wearable) option→skill map:

```json
{
  "schema_version": "aidesk.owner_handoff.option_routing.v1",
  "question_id": "",
  "routing": {
    "A": {"skill": "research", "goal": ""},
    "B": {"skill": "coding", "goal": ""},
    "C": {"skill": "research", "goal": ""},
    "D": {"skill": "none", "goal": ""}
  }
}
```

### 6.4 Execution manifest (new — not literally specified, designed to satisfy §10/§13/§15 requirements)

```json
{
  "schema_version": "aidesk.owner_handoff.manifest.v1",
  "task_id": "",
  "skill": "coding",
  "source_workspace_path": "",
  "duplicate_workspace_path": "",
  "created_at": "",
  "excluded_patterns": [".git", ".env", "*.env", "secrets*", ".venv", "venv",
                          "env", "__pycache__", "*.pyc", ".pytest_cache",
                          ".cache", "build", "dist", "*.egg-info", "*.sqlite3",
                          "*.db", "data/owner_handoff/sessions/*"],
  "original_files": [
    {"path": "", "size": 0, "sha256": "", "mtime": ""}
  ],
  "commands_executed": [
    {
      "step_index": 0,
      "argv": [],
      "cwd": "",
      "started_at": "",
      "ended_at": "",
      "exit_code": 0
    }
  ],
  "packages_installed": [
    {"package": "", "command": [], "approved_at": "", "venv_path": ""}
  ],
  "verification": {
    "verified_at": "",
    "original_files_modified": []
  }
}
```

`commands_executed[].argv` never contains secret values (Codex CLI reads
credentials from its own environment/config, never as CLI arguments the
adapter constructs); the adapter's subprocess-runner wrapper is responsible
for not logging environment variables at all.

### 6.5 Resume report (per spec §17 — schema preserved verbatim)

```json
{
  "selected_task": "",
  "work_completed": [],
  "files_created": [],
  "files_modified_in_duplicate": [],
  "original_files_modified": [],
  "packages_installed": [],
  "status": ""
}
```

`original_files_modified` is populated **only** from `manifest.verification.
original_files_modified`, which is computed by re-hashing every path in
`manifest.original_files` against the recorded `sha256` — never asserted as
`[]` without that check running.

### 6.6 Radar / wearable-proximity samples (internal, per spec §6)

```json
{"schema_version": "aidesk.owner_handoff.radar_sample.v1",
 "state": "PERSON_PRESENT|PERSON_ABSENT|UNKNOWN", "observed_at": ""}
```

```json
{"schema_version": "aidesk.owner_handoff.wearable_proximity.v1",
 "state": "OWNER_NEAR|OWNER_AWAY|UNKNOWN", "observed_at": "",
 "device_id": ""}
```

---

## 7. Configuration / default table

All values live in `application/owner_handoff/config.py` as a single
frozen dataclass loaded once at startup, with each **configurable** field
overridable by an environment variable documented in `.env.example` and the
README parameter table (Phase 1 deliverables). No magic numbers elsewhere in
the package.

> **Corrected 2026-07-25 review:** the four rows that originally expressed
> mandatory safety invariants as environment-overridable config
> (`AI_DESK_V2_WEARABLE_UNKNOWN_TRIGGERS`, `AI_DESK_V2_CONTRADICTION_POLICY`,
> `AI_DESK_V2_EXTERNAL_ACTIONS_ENABLED`, `AI_DESK_V2_OCR_POLICY`) have been
> removed. An environment variable that could relax "wearable UNKNOWN never
> triggers," "contradictory signals always wait," "external actions denied,"
> or the fixed OCR invocation order would let a misconfigured `.env` defeat
> the exact safety property the value exists to guarantee. These are now
> centralized, hardcoded constants documented in code and in the Master
> Spec's Phase 1 Review Addendum, not config fields — see that addendum for
> the authoritative list of non-overridable invariants. They are **not**
> added to `.env.example`.
>
> **Further corrected in this review round:** `unanswered_question_default`
> and `missing_codex_cli_policy` were also wrongly listed below as
> configurable env vars. The unanswered-question timeout behavior (always
> D / "Do nothing") and the missing-Codex-CLI behavior (always stop safely)
> are themselves mandatory safety invariants for the same reason as the
> four above — an operator-configurable "what happens on timeout" or "what
> happens when Codex is missing" is exactly the kind of switch that could
> later be set to something unsafe. Both rows have been removed from the
> table below and are now centralized constants
> (`UNANSWERED_QUESTION_DEFAULT`, `MISSING_CODEX_CLI_POLICY` in
> `application/owner_handoff/config.py`), documented in the Master Spec's
> Phase 1 Review Addendum. Neither `AI_DESK_V2_UNANSWERED_DEFAULT` nor
> `AI_DESK_V2_MISSING_CODEX_POLICY` may be added to `.env.example`.

| Config field | Env var | Default | Spec source |
|---|---|---|---|
| `owner_leave_confirmation_seconds` | `AI_DESK_V2_LEAVE_CONFIRM_SECONDS` | `10` | §3, §7 |
| `handoff_question_expiration_seconds` | `AI_DESK_V2_QUESTION_EXPIRY_SECONDS` | `60` | §3, §9 |
| `coding_agent_max_runtime_seconds` | `AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS` | `300` | §3, §13 |
| `coding_agent_max_safe_steps` | `AI_DESK_V2_CODING_MAX_STEPS` | `20` | §3, §13 |
| `missing_hardware_policy` | `AI_DESK_V2_MISSING_HARDWARE_POLICY` | `"simulator_usable"` | §3 |
| `activity_window_seconds` | `AI_DESK_V2_ACTIVITY_WINDOW_SECONDS` | `60` | §4 (aggregate window; not literally specified, derived to match existing `ContextCollector`/Friday's collector convention) |
| `input_idle_threshold_seconds` | `AI_DESK_V2_INPUT_IDLE_SECONDS` | `5` (resolved default — see §16) | §7 |
| `ocr_adapter_mode` | `AI_DESK_V2_OCR_MODE` | `"noop"` (`"mock"` selectable explicitly for tests/simulator dev) | §4, §8 (selects which `OCRProvider` implementation loads; the invocation **order** itself — Tier 1 → Tier 2 → OCR — is a fixed invariant, not a config value; see removed-rows note above) |
| `workspace_session_root` | `AI_DESK_V2_SESSION_ROOT` | `"data/owner_handoff/sessions"` | §10 |
| `demo_workspace_path` | `AI_DESK_V2_DEMO_WORKSPACE` | `"demo_workspace"` | §11 (contents deferred to Phase 4 — see §16) |
| `codex_cli_binary` | `AI_DESK_V2_CODEX_BINARY` | `"codex"` | §13 |
| `codex_sandbox_mode` | `AI_DESK_V2_CODEX_SANDBOX_MODE` | *(deferred to Phase 3 — see §16)* | §13 |
| `wearable_device_allowlist` | `AI_DESK_V2_WEARABLE_DEVICE_IDS` | `""` (empty = reject all until configured) | §6 |
| `radar_adapter_mode` | `AI_DESK_V2_RADAR_MODE` | `"simulator"` (Phase 1 implements simulator/domain logic only — see §16) | §3, §6 |
| `wearable_adapter_mode` | `AI_DESK_V2_WEARABLE_MODE` | `"simulator"` | §3, §6 |
| `owner_handoff_db_path` | `AI_DESK_V2_DB_PATH` | `"data/owner_handoff.sqlite3"` | mirrors `AI_DESK_HANDOFF_DB` convention |

**Mandatory safety invariants (no environment override, ever):** wearable
`UNKNOWN` never triggers; contradictory signals always wait; external actions
remain denied in v1; OCR is callable only after Tier 1 and Tier 2 both remain
`AMBIGUOUS`; an unanswered/timed-out handoff question always resolves to D
("Do nothing"); a missing/unusable Codex CLI always stops safely; missing
Codex isolation support always fails closed; the
original workspace is never writable; Git push/publish/message/deploy remain
prohibited. Full list and rationale: Master Spec, "Phase 1 Review Addendum."

---

## 8. Existing public APIs that will remain unchanged

- `services/ai_desk_api.py` (`AIDeskPresenceAPI`) — all methods, signatures,
  and event payload keys.
- `models/state.py` (`PresenceState`: Idle/Working/Break/Finished) — not
  extended, not repurposed; V2 owns a wholly separate `OwnerHandoffState`.
- `events/event_types.py` — no new event types added here; new adapters post
  through their own internal callbacks/queues, not through the system-layer
  `Runtime`/`Event` bus, to avoid coupling V2's fusion timing to the
  system-layer event loop's semantics.
- `application/controller.py` (`ApplicationController`) — untouched; V2 does
  not reuse or extend its `activity_collector` parameter path (that path was
  Friday's unwired experiment, not adopted).
- `application/context/macos_provider.py` / `collector.py` — untouched;
  Chrome-tab capture is reimplemented inside `owner_handoff/adapters/` as its
  own provider rather than editing `DesktopContext`.
- `application/handoff/*` (existing Research Handoff Agent: `TaskCapsule`,
  `HandoffRecord`, `HandoffStatus`, `HandoffStore`, `HandoffOrchestrator`,
  `A2AHandoffClient`, `MacOSHandoffActions`, `report.py`) — untouched;
  `ResearchAgentExecutor` composes with these from the outside.
- `application/presence/serial_adapter.py` (`SerialPresenceAdapter`) —
  untouched.
- `database/`, `runtime/`, `session/`, `listeners/`, `models/session_record.py`,
  `models/context_event.py`, `models/stats.py`, `utils/time_utils.py` — all
  untouched.
- `main.py`, `run_demo.py`, `run_handoff_demo.py`, `arm_handoff.py` — none
  modified; the new demo gets its own entry point (`run_owner_handoff_demo.py`)
  per spec §11.
- `requirements.txt` — no new **required** dependency in v1 (OCR/BLE are
  no-op/simulator only). If `pyobjc-framework-Quartz` is used for the real
  activity observer, it must be added as an optional/macOS-conditional
  dependency, not a hard requirement, since simulator-mode tests must run on
  any OS per §18.

## 9. Unavoidable compatibility concerns

- **Two independent "leave" concepts will coexist.** The legacy
  `HandoffOrchestrator` treats `PresenceState.BREAK` (from one boolean
  sensor) as "left"; V2's fusion requires radar+wearable+keyboard+mouse
  unanimity over 10 seconds. If both systems are ever run against the same
  physical sensors simultaneously, they can reach different conclusions at
  different times. This is expected and acceptable (they are deliberately
  separate features per §2), but must be documented clearly so a demo
  operator doesn't confuse the two "presence" notions.
- **`demo_workspace/` becomes a tracked, repo-owned fixture directory.**
  It must contain only inert placeholder content (no real credentials, no
  `.git` of its own) since it will be duplicated by tests and the demo.
- **Firmware/README location mismatch is pre-existing.** The root README
  references `firmware/ai_desk_presence/ai_desk_presence.ino` and
  `firmware/README.md`, neither of which exists (only a root-level
  `ai_desk_presence.ino` does). Phase 1 documentation should either note
  this gap explicitly or (out of scope for this report) reconcile it — not
  silently perpetuate a broken path reference in new docs.
- **Tracked `.pyc` files already exist on `origin/MVP`.** Any new package
  added in Phase 1 must be careful not to get its `__pycache__` accidentally
  `git add -A`'d given the existing (bad) precedent in the repo; this is a
  process risk, not a code risk.
- **`AI_DESK_CURRENT_TASK` env var** existed unused-by-MVP but referenced by
  Friday's dead collector as a default "current task" hint. If V2 wants a
  similar manual override, it should mint its own `AI_DESK_V2_*`-namespaced
  variable rather than silently adopting Friday's name, to avoid implying
  continuity with unshipped, untested code.

---

## 10. Security / threat analysis

| # | Threat | Mitigation |
|---|---|---|
| 1 | Spoofed or replayed wearable answer authorizes execution | Device allowlist (`wearable_device_allowlist`), `question_id` must match the currently pending question, expiry enforced, duplicate answers rejected; BLE-layer replay protection is explicitly deferred and documented as a TODO at the hardware boundary (no fabricated UUIDs/crypto claimed) |
| 2 | Coding execution starts without explicit data-authorization | Separate `PERMISSION_PENDING` state gate before Codex CLI ever starts (§14); A/B/C task selection alone never suffices |
| 3 | Original workspace corrupted or partially modified | Physical copy (never git worktree) + pre-copy and post-execution SHA-256 hash verification of every original file; all writes are scoped to the duplicate path only |
| 4 | Path traversal / unsafe workspace path (filesystem root, home dir, symlink escape, self-referential copy of a prior session dir) | `path_policy.py` resolves and validates the path before any copy: rejects roots, home directory, paths that resolve (after symlink resolution) outside an explicit allowed boundary, and paths inside `workspace_session_root` itself |
| 5 | Secrets/credentials leak into the duplicate, the manifest, or the resume report | Exclusion list covers `.git`, `.env`/`*.env`, `secrets*`, virtualenvs, caches, build artifacts, databases, prior session dirs; manifest command records store `argv` only, never environment variables |
| 6 | Arbitrary/unsandboxed shell execution via the coding agent | `CodexCLIExecutor` uses an injectable subprocess runner, argv lists only (never `shell=True`, never string-built shell commands), `cwd` pinned to the duplicate, strictest available workspace-write sandbox flag required, and **fails closed** (refuses to start) if the CLI doesn't support the required isolation flags |
| 7 | Runaway or costly agent execution | Hard 300-second runtime cap and 20-safe-step cap enforced by the executor's own controller loop, independent of what the CLI itself reports |
| 8 | Unauthorized external side effects (git push, message send, publish, install, deploy) | A single deny-by-default policy layer intercepts every "external action" category listed in §16; none are implemented in v1, all require a separate, not-yet-built authorization path |
| 9 | One absence event triggers multiple concurrent tasks | Unique DB constraint on `confirmation_event_id`; state-machine transitions are atomic compare-and-swap, so a race between two fusion callbacks can only ever create one `OWNER_LEFT_CONFIRMED` row |
| 10 | Sensor flapping / contradictory signals cause a false leave or false return | Any single contradicting or `UNKNOWN` signal during the confirmation window reverts to `OBSERVING` (row 2 of the transition table); "wait" is the explicit default policy, never "proceed on partial evidence" |
| 11 | OCR captures and persists sensitive screen content | v1 ships only `NoOpOCR`/`MockOCR`; the invocation policy itself refuses to run OCR for any but the doubly-ambiguous case; the interface contract (tested) requires no persisted screenshots and immediate deletion of any temporary capture in the (future) real adapter |
| 12 | Crash mid-execution leaves orphaned duplicate directories or ambiguous state | Duplicate directories and manifests are never auto-deleted by the system (preserves evidence for review); the persisted state machine records enough to inspect what happened, and restart handling (Threat 13) prevents silently resuming |
| 13 | Process restart resumes into a state referencing a dead subprocess | Any task found in `WORKSPACE_DUPLICATING`/`EXECUTING`/`PERMISSION_PENDING` at startup without a live process handle is force-transitioned to `FAILED` ("process restarted mid-execution"); it is never silently treated as still running |
| 14 | Duplicate workspace changes are silently merged back into the original | Explicitly out of scope/prohibited by §17 ("never auto-merge duplicate changes into the original"); no code path in the design performs a merge or copy-back |
| 15 | Malicious or malformed manifest/resume JSON crafted by a compromised executor is trusted blindly | `original_files_modified` is never taken from executor output — it is independently recomputed by the orchestrator's own hash-verification step over `manifest.original_files`, which was captured before the executor ever ran |

## 11. Test matrix

Per spec §18: every new public component needs at least one normal-path test
and one rejection/error-path test; safety components additionally need an
explicit unauthorized-path test. None of the tests below may call real
OpenAI, real Codex CLI, the Research Agent's network service, real OCR, BLE,
serial hardware, package installers, external network, or `git push`.

| Test module | Normal-path case | Rejection / error-path case | Unauthorized-path case (safety components) |
|---|---|---|---|
| `test_activity_classifier.py` | Clear typing+mouse activity over app X classifies WORKING and updates WorkContext | Idle mouse/keyboard classifies NOT_WORKING and WorkContext is left unchanged | — |
| `test_work_context.py` | Repeated corroborating evidence raises confidence and is deduplicated | Single uncorroborated keyword does not set `project`; conflicting evidence lowers confidence | — |
| `test_presence_fusion.py` | All four signals unanimous for the injected-clock 10s window → `OWNER_LEFT_CONFIRMED` | Any one signal flips or is `UNKNOWN` mid-window → reverts to `OBSERVING`, never confirms | Radar absent + wearable near (owner nearby) never confirms leave; radar present + wearable away never confirms leave |
| `test_wearable_contract.py` | Valid answer (allowlisted device, matching non-expired question id) transitions state | Unknown device / stale question id / duplicate answer / expired question / wrong-state answer are all rejected without side effects | Replayed old valid answer after a new question has been generated is rejected as stale |
| `test_handoff_questions.py` | Options generated from `unfinished_work`/`possible_next_steps`, D always present and maps to `none` | Low-confidence WorkContext yields a D-only (or clarification) question, never generic unrelated options | Expired question or owner return before an answer performs zero execution |
| `test_workspace_duplicator.py` | Valid explicit path copies files, excludes `.git`/`.env`/venvs/caches/dbs/prior sessions, manifest hashes match post-copy | Filesystem root, home directory, symlink-escaping path, or path inside `workspace_session_root` is rejected before any copy occurs | Attempted write to a path outside the duplicate during a simulated executor call is blocked |
| `test_execution_policy.py` | Research skill routes to `ResearchAgentExecutor`; coding skill routes to `CodingAgentExecutor` | Selecting coding with a source workspace that fails validation never reaches an executor | Skill selected without a prior `AUTHORIZED` transition can never invoke an executor |
| `test_codex_executor.py` | `FakeCodingExecutor` completes a deterministic scripted run within step/time caps, producing a manifest | Missing Codex CLI at preflight stops safely with a clear error, no partial execution; runtime/step cap forcibly halts a long-running fake step sequence | Codex CLI is never invoked without a prior matching YES on the data-authorization question |
| `test_ocr_policy.py` | Clear WORKING/NOT_WORKING never invokes OCR (no-op adapter untouched) | Doubly-ambiguous case invokes the mock OCR exactly once and only sanitized text is retained | No screenshot artifact exists on disk after any code path under test |
| `test_return_coordinator.py` | Owner return after execution completes naturally produces a resume report with `status="returned"` | Owner return mid-execution finishes only the current atomic step, never starts a new one | `original_files_modified` is asserted empty **and** independently verified via re-hash, not merely defaulted |
| `test_owner_handoff_e2e.py` | Full simulated cycle: leave → question → A selected (research) → resume report, using fakes/sims throughout | Full simulated cycle with D selected performs zero delegation/copy/execution | Full simulated cycle where owner returns during `QUESTION_PENDING` cancels cleanly with no execution |
| `test_owner_handoff_state_machine.py` *(added; not in spec's list, needed for direct §8 coverage)* | Each allowed transition in §5's table succeeds exactly once per event id | Replaying the same event id is idempotent; a transition attempted from a non-matching `from_state` is rejected | Simulated process-restart with a task stuck in `EXECUTING` and no live handle force-transitions to `FAILED`, never silently resumes |
| `test_path_policy.py` *(added; not in spec's list, split out from the duplicator's own tests as a distinct safety unit)* | A normal project directory outside any protected boundary validates successfully | Root paths, home directory, and recursive/self-referential targets are all rejected with specific reasons | A symlink inside an otherwise-valid directory that resolves outside the allowed boundary is rejected |

`tests/test_handoff.py` and all 10 other existing MVP test modules remain
unmodified and must continue to pass unchanged (already verified as the
Phase 0 baseline in §2).

## 12. Requirement-to-component mapping

| Spec section | Requirement | Primary component(s) |
|---|---|---|
| §4 Activity classification | 3-tier classification, never store keys | `classification/activity_classifier.py`, `adapters/` (activity observer) |
| §4 Additional context / OCR | Window title, Chrome domain, filename, OCR fallback policy | `classification/context_analyzer.py`, `classification/ocr_policy.py`, `adapters/ocr.py` |
| §5 WorkContext | Structured, evidence-based context with confidence | `domain/work_context.py` |
| §6 Hardware roles | Radar/wearable/button contracts | `adapters/radar.py`, `adapters/wearable.py`, `domain/presence.py` |
| §7 Leave detection | 10s unanimous confirmation, wait on conflict | `fusion/leave_detector.py` |
| §8 Persisted state machine | 13-state machine, idempotent, restart-safe | `state_machine.py`, `store.py` |
| §9 Handoff question | Question generation, terminal display, wearable minimal payload | `questions/generator.py`, `orchestrator.py` |
| §10 Workspace duplication | Physical copy, exclusions, manifest, hash verification | `workspace/path_policy.py`, `workspace/duplicator.py` |
| §11 Demo workspace | Safe fixture, explicit entry point | `demo_workspace/`, `run_owner_handoff_demo.py` |
| §12 Agent routing | Research/coding separation | `routing/skills.py`, `execution/research_executor.py`, `execution/coding_executor.py` |
| §13 Codex CLI safety | Sandboxing, caps, fail-closed | `execution/codex_cli.py` |
| §14 Codex data authorization | Separate YES/NO gate before CLI start | `execution/permission.py`, `state_machine.py` (`PERMISSION_PENDING`) |
| §15 Installation permission | Wearable-gated installs in duplicate venv only | `execution/permission.py` |
| §16 External actions | Deny-by-default for messages/push/publish/etc. | `execution/permission.py` (policy layer), no implementing code |
| §17 Owner return | Confirm return, finish atomic step, resume report | `fusion/return_detector.py`, `return_coordinator.py` |
| §18 Testing policy | Fakes/sims only, no real network/hardware in normal tests | All `tests/test_*` modules (§11 above) |
| §19 Documentation policy | README parameter table, safety comments | Phase 1 README updates (not created in Phase 0) |

## 13. Requirement-to-test mapping

See §11's Test Matrix — every row is annotated with which spec requirement it
exercises via its module name; the mapping is 1:1 with the table in §12
(e.g. §7 leave detection ↔ `test_presence_fusion.py`, §14 Codex data
authorization ↔ `test_codex_executor.py`'s unauthorized-path case, §17 owner
return ↔ `test_return_coordinator.py`).

---

## 14. Exact proposed file list (Phase 1)

**New files to create:**

```
application/owner_handoff/__init__.py
application/owner_handoff/config.py
application/owner_handoff/state_machine.py
application/owner_handoff/store.py
application/owner_handoff/orchestrator.py
application/owner_handoff/return_coordinator.py
application/owner_handoff/domain/__init__.py
application/owner_handoff/domain/activity.py
application/owner_handoff/domain/presence.py
application/owner_handoff/domain/work_context.py
application/owner_handoff/domain/question.py
application/owner_handoff/domain/manifest.py
application/owner_handoff/domain/resume.py
application/owner_handoff/fusion/__init__.py
application/owner_handoff/fusion/leave_detector.py
application/owner_handoff/fusion/return_detector.py
application/owner_handoff/classification/__init__.py
application/owner_handoff/classification/activity_classifier.py
application/owner_handoff/classification/context_analyzer.py
application/owner_handoff/classification/ocr_policy.py
application/owner_handoff/adapters/__init__.py
application/owner_handoff/adapters/radar.py
application/owner_handoff/adapters/wearable.py
application/owner_handoff/adapters/ocr.py
application/owner_handoff/questions/__init__.py
application/owner_handoff/questions/generator.py
application/owner_handoff/routing/__init__.py
application/owner_handoff/routing/skills.py
application/owner_handoff/workspace/__init__.py
application/owner_handoff/workspace/path_policy.py
application/owner_handoff/workspace/duplicator.py
application/owner_handoff/execution/__init__.py
application/owner_handoff/execution/research_executor.py
application/owner_handoff/execution/coding_executor.py
application/owner_handoff/execution/codex_cli.py
application/owner_handoff/execution/permission.py
application/owner_handoff/README.md
run_owner_handoff_demo.py
demo_workspace/README.md
demo_workspace/sample_project/ (placeholder files, TBD contents)
tests/test_activity_classifier.py
tests/test_work_context.py
tests/test_presence_fusion.py
tests/test_wearable_contract.py
tests/test_handoff_questions.py
tests/test_workspace_duplicator.py
tests/test_execution_policy.py
tests/test_codex_executor.py
tests/test_ocr_policy.py
tests/test_return_coordinator.py
tests/test_owner_handoff_e2e.py
tests/test_owner_handoff_state_machine.py
tests/test_path_policy.py
```

**Existing files to modify (documentation/config only, no behavior changes to
existing features):**

```
.env.example        - append new AI_DESK_V2_* variables (§7 table)
README.md            - append architecture/state-machine/parameter/simulator
                       documentation sections (§19)
requirements.txt      - only if a real (non-simulator) adapter needs a new
                       dependency; expected to stay unchanged for v1 since
                       BLE/OCR/real-Codex paths are stubs/external in v1
```

**Files this design deliberately does NOT modify:** everything under
`database/`, `models/`, `events/`, `listeners/`, `runtime/`, `session/`,
`services/`, `utils/`, all of `application/handoff/`, `application/context/`,
`application/presence/`, `application/providers/`, `application/summary/`,
`application/ui/`, `main.py`, `run_demo.py`, `run_handoff_demo.py`,
`arm_handoff.py`, `tests/test_*.py` (the 11 existing modules).

---

## 15. Conflicts with existing public APIs

**None identified.** Every new component lives in a new package
(`application/owner_handoff/`) with its own database file, its own state
enum, and its own entry point. No existing public method signature, event
payload key, or module-level export is changed. The one adjacent surface
worth flagging explicitly is `application/context/macos_provider.py`'s
`DesktopContext` — V2 needs Chrome tab/domain data too, but per §8 this will
be captured by a **new** provider inside `owner_handoff/adapters/`, not by
extending the existing `DesktopContext` dataclass (which Friday did do, and
which this report recommends against reusing directly — see §3.4).

---

## 16. Unresolved decisions (resolved 2026-07-25 review)

Each item below was flagged in the original Phase 0 report as needing
explicit user input. All seven have now been approved with an explicit
decision; the original question is kept for context, followed by the
resolution.

1. **Radar adapter reuse vs. independence.**
   *Original question:* should the new `RadarSensor` adapter read from the
   same physical serial connection as `SerialPresenceAdapter`, or own an
   independent reader?
   **Resolved:** Phase 1 implements simulator/domain logic only — no real
   radar serial reader is built in this phase. When a real radar
   implementation is built (a later phase), it must use **one serial-port
   owner/broker**; two independent readers must never open the same serial
   device concurrently.
2. **Input-idle threshold value.**
   **Resolved:** the default is **5 seconds**. Combined effect: owner leave
   is confirmed after input has been idle for 5 seconds *and* all four
   absence conditions (radar `PERSON_ABSENT`, wearable `OWNER_AWAY`, mouse
   inactive, keyboard inactive) then remain true continuously for the full
   10-second confirmation window — the 5-second idle threshold is what
   makes "mouse inactive"/"keyboard inactive" become true in the first
   place, not a separate or shorter timer running in parallel with the 10s
   window.
3. **Codex CLI sandbox flag name(s).**
   **Resolved:** flag discovery is deferred to **Phase 3**. Phase 1 must not
   guess Codex CLI flag names; no Codex CLI integration code is written in
   Phase 1 at all (see Phase 1's strict scope).
4. **Wearable BLE UUIDs and firmware protocol.**
   **Resolved (unchanged):** remain deferred, as originally decided by
   Master Spec §2/§6. No UUIDs or firmware transport details are invented in
   Phase 1 or this report.
5. **`demo_workspace/` fixture contents.**
   **Resolved:** deferred to **Phase 4**. Phase 1 does not create
   `demo_workspace/` or `run_owner_handoff_demo.py`.
6. **Shared vs. duplicated osascript notifier.**
   **Resolved:** Phase 1's display/notification surface is **Terminal
   only**. Do not add or extract an osascript notifier in Phase 1 — this
   decision (shared helper vs. duplication vs. something else) is deferred
   to whichever later phase actually implements macOS notification delivery.
7. **Pre-existing tracked `.pyc` files on `origin/MVP`.**
   **Resolved:** do not remove the pre-existing tracked MVP `.pyc` files in
   Phase 1 (out of Phase 1's allowed-files scope). Phase 1 must, however,
   ensure it creates **no new** tracked `__pycache__`/`.pyc` files for any of
   its own modules.

## 17. Phase 1 implementation plan (proposed order)

1. `config.py` + `.env.example`/README parameter table stub — everything
   else depends on centralized defaults existing first.
2. `domain/` dataclasses (pure, no I/O) + their unit tests
   (`test_work_context.py` partially, plus contract-shape assertions used by
   later tests).
3. `state_machine.py` + `store.py` + `test_owner_handoff_state_machine.py` —
   the persisted core everything else attaches to.
4. `adapters/radar.py`, `adapters/wearable.py` simulators + contracts +
   `test_wearable_contract.py`.
5. `fusion/leave_detector.py` + `fusion/return_detector.py` +
   `test_presence_fusion.py` + `test_return_coordinator.py` (detector half).
6. `classification/` (activity classifier, context analyzer, OCR policy) +
   `adapters/ocr.py` (no-op/mock) + `test_activity_classifier.py` +
   `test_ocr_policy.py`.
7. `questions/generator.py` + `routing/skills.py` +
   `test_handoff_questions.py`.
8. `workspace/path_policy.py` + `workspace/duplicator.py` +
   `test_path_policy.py` + `test_workspace_duplicator.py`.
9. `execution/permission.py`, `execution/coding_executor.py`
   (`FakeCodingExecutor` first), `execution/research_executor.py` +
   `test_execution_policy.py`.
10. `execution/codex_cli.py` (real adapter, behind the fake in tests) +
    `test_codex_executor.py`.
11. `return_coordinator.py` completion + `orchestrator.py` wiring everything
    together + `test_owner_handoff_e2e.py`.
12. `run_owner_handoff_demo.py` + `demo_workspace/` fixture.
13. README/`.env.example` documentation pass (§19 requirements) +
    `application/owner_handoff/README.md`.
14. Full-suite run (`legacy 47 passed / 1 skipped` baseline + all new tests),
    `git diff --check`, tracked-file hygiene check, human review gate before
    any real-hardware or real-Codex-CLI rehearsal.

---

## 18. End-of-phase checklist

- [x] `git status` clean before starting; no discarded work.
- [x] Fetched latest `origin`; confirmed local `MVP` == `origin/MVP`.
- [x] Created `feature/owner-authorized-handoff-v2` from `origin/MVP` (did not
      exist before; not deleted/reset).
- [x] `main`, `Friday`, `MVP` untouched.
- [x] Full MVP test suite run and recorded: 47 passed, 1 skipped.
- [x] Friday vs MVP diff analyzed; all 5 flagged concerns verified with
      direct evidence (not assumed).
- [x] No Friday commits merged or cherry-picked.
- [x] Only `docs/AI_DESK_V2_MASTER_SPEC.md` and
      `docs/AI_DESK_V2_PHASE0_REPORT.md` created; no product code, tests, or
      adapters implemented.
- [x] `git diff --check` — ran, no output (pass). *(Corrected 2026-07-25:
      this item originally referenced a nonexistent "§19 below" — this
      report has no §19; that dangling reference is removed and the actual
      result is recorded inline here instead.)*
- [x] `git status` — ran; only the two new files under `docs/` were
      untracked. Running the test suite had incidentally regenerated the
      repo's pre-existing tracked `.pyc` files (bytecode recompilation, not
      an intentional edit); these were reverted with `git checkout --` so
      that only the two documentation files changed, per Phase 0's allowed
      scope.
- [x] Stopped for human review; no commit; no push.
