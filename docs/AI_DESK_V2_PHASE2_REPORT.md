# AI Desk V2 — Phase 2 Report

Scope: (A) a repair gate fixing six independently verified Phase 1 defects,
gated behind a full green test run before any Phase 2 code was written; then
(B) presence domain models, deterministic radar/wearable simulators,
leave/return fusion, the handoff-question domain model/generator/lifecycle,
Terminal rendering, thin state-machine integration for leave confirmation
and question authorization/cancellation, tests, and documentation.

- Branch: `feature/owner-authorized-handoff-v2` (unchanged base; not
  switched, not merged/rebased/committed/pushed).
- Report date: 2026-07-25.
- Starting state verified clean: `git status` showed only the expected
  uncommitted Phase 0/Phase 1 files (`.env.example`, `README.md` modified;
  `application/owner_handoff/`, `docs/`, and six Phase 1 test modules
  untracked) before any change in this turn.

---

## Part A — Phase 1 repair gate

All six defects were fixed and verified with a full green test run
(`python -B -m unittest discover -s tests -v` → 152 tests, 151 passed, 1
skipped, 0 failed) **before** any Phase 2 file was created.

### 1. Context classification wrongly equated communication/dual-use media with NOT_WORKING

`classification/context_analyzer.py` previously let a single
`_ENTERTAINMENT_COMMUNICATION_HINTS` category (mixing Slack/mail with
YouTube/Netflix) resolve `NOT_WORKING` purely from a repeated domain name.
Fixed by splitting into three distinct categories:

- `_COMMUNICATION_DOMAIN_HINTS` (Slack, mail, Teams, Zoom, WeChat) — now
  recorded as candidate evidence but **never** contributes to a
  `NOT_WORKING` resolution, regardless of repetition.
- `_DUAL_USE_ENTERTAINMENT_DOMAIN_HINTS` (YouTube, Twitch, TikTok,
  Instagram, Netflix) — same: bare domain repetition alone can never
  resolve `NOT_WORKING`.
- `_ENTERTAINMENT_CONTENT_MARKERS` (e.g. "gameplay", "trailer", "episode",
  "season", "watch party") — genuinely content-specific substrings; **only
  these**, corroborated the same way task evidence is (same-detail repeat
  or two distinct source types), may resolve `NOT_WORKING`.

A real bug was also caught and fixed in the same file while writing
regression tests: the `.go` file-extension hint matched as a naive
substring inside `mail.go`**`ogle`**`.com`, mis-categorizing that domain as
"task." Fixed with a word-boundary-aware regex
(`\.(py|ts|tsx|js|md|json|java|go|rs)(?![a-zA-Z0-9])`) so `.go` matches
`main.go` but not `google.com`.

New regression tests in `tests/test_context_analyzer.py`: repeated YouTube
domain alone stays `AMBIGUOUS` (including corroborated across two source
types); repeated Slack/mail domain alone stays `AMBIGUOUS`; a corroborated
content-specific marker resolves `NOT_WORKING`; a single uncorroborated
content marker does not resolve; corroborated task evidence still resolves
`WORKING`; genuine task-vs-entertainment-content conflicts stay `AMBIGUOUS`;
a weak entertainment mention and a repeated communication domain both never
overwrite strong task evidence.

### 2. WorkContext privacy bypass

Sanitization previously happened only inside
`WorkContextTracker.record_evidence` — every other entry point
(`append_recent_action`, `append_recent_file`, `append_unfinished_work`,
`append_possible_next_step`, `set_project_and_task`, and direct
`Evidence`/`WorkContext` construction) accepted raw, unsanitized text.
Fixed with two layers:

- **Domain boundary (fail loudly):** `Evidence.__post_init__` and
  `WorkContext.__post_init__` now reject (raise `ValueError`) any
  `source`/`detail`/`project`/`current_task`/`stage`/list-item that
  contains a full URL, a URL query parameter, or a token/password/API-key/
  bearer-like string — checked via a new `_reject_unsafe_text` helper built
  on the existing `redact_sensitive`.
- **Tracker (normalize silently):** every `WorkContextTracker` mutation
  method now sanitizes its input with `redact_sensitive`/`_sanitized`
  before constructing anything, so legitimate tracker usage never raises —
  it silently stores the redacted value instead.

This is a deliberate, documented split: going through the tracker never
raises (it normalizes); constructing the frozen domain objects directly
with raw unsafe text fails clearly. `HandoffQuestion` (new in Phase 2) was
built with the same enforcement from the start.

**A real bug was caught while writing these tests**: the privacy check was
initially implemented as `redact_sensitive(value) != value`, which flags
*any* string with leading/trailing whitespace as "unsafe" purely because
`redact_sensitive` also strips whitespace — unrelated to actual secret/URL
detection. Fixed by comparing against `value.strip()` instead of `value`.

New tests in `tests/test_work_context.py` cover every field category
(direct-construction rejection for `Evidence.source`/`detail`,
`WorkContext.project`/`current_task`/`stage`/all four list fields;
tracker-level silent sanitization for all five mutation methods).

### 3. Permission-kind invariants were not fully enforced or audited

Fixed in `state_machine.py` and `store.py`:

- `validate_transition` now has three branches: entering
  `PERMISSION_PENDING` requires the one correct kind for that source state
  (unchanged from Phase 1); **leaving** `PERMISSION_PENDING` now also
  requires a non-null kind be supplied (new); every other transition now
  **rejects any non-null** `permission_kind` (new) — via a new
  `PermissionKindError` (a `TransitionError` subclass).
- `OwnerHandoffStore.apply_transition` now fetches the task's actual
  persisted `permission_kind` and, when leaving `PERMISSION_PENDING`, calls
  a new `assert_permission_kind_matches` to confirm the supplied kind
  matches what's actually pending — not merely trusted from the caller. The
  event that records this transition stores the *confirmed* kind (audit
  trail), and the task's own `permission_kind` column is explicitly cleared
  to `NULL` once no longer pending.
- `OwnerHandoffStore.create_task` no longer accepts an `initial_state`
  argument — every task now starts at `OBSERVING` and must be driven
  through `apply_transition`, closing the bypass that previously let tests
  (and any future caller) fabricate a task already sitting in an invalid
  state (e.g. `PERMISSION_PENDING` with no kind).

`tests/test_owner_handoff_state_machine.py` was refactored throughout:
every test that previously called `create_task(..., initial_state=...)`
now drives the task through a realistic sequence of `apply_transition`
calls (helper functions `_research_path`, `_coding_path_to_executing`,
`_coding_path_to_permission_pending`, `_drive`). New tests cover: a
mismatched kind when leaving `PERMISSION_PENDING` is rejected and changes
nothing; the confirmed kind is recorded on the leaving event; the kind is
cleared from the task row afterward; `CANCELED` from `PERMISSION_PENDING`
also requires a matching kind; `create_task` always starts at `OBSERVING`
and no longer accepts `initial_state`.

### 4. Event replay conflict detection ignored `reason`

`OwnerHandoffStore.apply_transition`'s idempotency check now also compares
`reason`; a replay with the same `event_id` but a different `reason` raises
`EventConflictError`. `occurred_at` semantics are now explicitly documented
and tested: it is a store-assigned timestamp recorded the *first* time an
`event_id` is written, never caller identity data — a replay's
`occurred_at` (or lack of one) never overwrites what was originally
recorded, and differing `occurred_at` values across replays are not part of
the conflict comparison.

### 5. Restart recovery collided across repeated task lifecycles

`recover_after_restart`'s event_id scheme (`task_id:state`) collided when
the *same* task went through `EXECUTING`/`PERMISSION_PENDING` more than
once across separate lifecycles (enter EXECUTING → recover to FAILED →
reset to OBSERVING → complete another lifecycle → enter EXECUTING again),
silently no-op'ing the second, genuine recovery. Fixed: the default
event_id now incorporates the task's current `updated_at` (which changes
every transition, making it a natural per-lifecycle discriminator), and an
optional `event_id_factory` parameter allows fully deterministic ids in
tests, independent of timestamps. Recovery from `PERMISSION_PENDING` now
also records the kind that was pending on the recovery event and clears it
from the resulting `FAILED` task row (tying into repair 3).

New test `test_two_independent_restart_recovery_lifecycles_both_reach_failed`
drives a task through two full independent lifecycles, confirming both
recoveries succeed, produce distinct event ids, and remain idempotent
within each lifecycle.

### 6. Documentation corrections

- **Pycache contradiction fixed.** The Phase 0 Report's audit finding #5
  said a later phase "must address" (clean up) the pre-existing tracked
  MVP `.pyc` files — contradicting the already-approved §16 decision to
  leave them alone. Corrected to state the approved decision plainly: do
  not clean historical MVP bytecode in any phase; only ensure no *new*
  tracked bytecode is created.
- **Unanswered-question-timeout and missing-Codex-CLI are now real,
  centralized invariant constants**, not (as the Phase 0 Report's
  configuration table incorrectly listed them) ordinary environment
  variables: `UNANSWERED_QUESTION_DEFAULT = "D"` and
  `MISSING_CODEX_CLI_POLICY = "stop_safely"` were added to `config.py`
  alongside the four existing invariants, and `AI_DESK_V2_UNANSWERED_DEFAULT`
  / `AI_DESK_V2_MISSING_CODEX_POLICY` are confirmed absent from the
  codebase by test (`test_no_env_vars_exist_for_removed_unsafe_overrides`).
  The Master Spec's "Phase 1 Review Addendum," the Phase 0 Report's
  configuration table and invariants list, and the Phase 1 Report's safety
  invariants table were all updated to match — no documentation in this
  project claims an invariant is "centralized as a constant" unless that
  constant now actually exists in `config.py`.

**Full repair-gate test run:** `python -B -m unittest discover -s tests -v`
→ **152 tests, 151 passed, 1 skipped, 0 failed** (47 legacy + 105 Phase 1
tests, including the new repair-gate regression tests, +1 skipped). Phase 2
work did not begin until this was green.

---

## Part B — Phase 2 implementation

### Files changed

**New:**

```
application/owner_handoff/domain/presence.py
application/owner_handoff/domain/question.py
application/owner_handoff/adapters/radar.py
application/owner_handoff/adapters/wearable.py
application/owner_handoff/fusion/__init__.py
application/owner_handoff/fusion/leave_detector.py
application/owner_handoff/fusion/return_detector.py
application/owner_handoff/questions/__init__.py
application/owner_handoff/questions/generator.py
application/owner_handoff/questions/lifecycle.py
application/owner_handoff/questions/terminal.py
tests/test_presence_fusion.py
tests/test_wearable_contract.py
tests/test_handoff_questions.py
docs/AI_DESK_V2_PHASE2_REPORT.md
```

**Modified** (repair gate and/or Phase 2 integration — all within the
allowed list):

```
application/owner_handoff/config.py         - 2 new invariant constants
                                               (repair 6); wearable_device_
                                               allowlist field + parsing
                                               (Phase 2)
application/owner_handoff/state_machine.py    - PermissionKindError, three-
                                               branch validate_transition,
                                               assert_permission_kind_matches
                                               (repair 3)
application/owner_handoff/store.py             - create_task restricted to
                                               OBSERVING; permission-kind
                                               matching/clearing/auditing;
                                               reason in conflict check;
                                               event_id_factory + per-
                                               lifecycle uniqueness for
                                               recovery; list_tasks_in_state
                                               (repairs 3, 4, 5; Phase 2
                                               integration)
application/owner_handoff/domain/work_context.py - _reject_unsafe_text;
                                               sanitizing tracker methods;
                                               whitespace false-positive fix
                                               (repair 2)
application/owner_handoff/classification/context_analyzer.py - communication/
                                               dual-use/content-specific
                                               category split; word-boundary
                                               extension-hint fix (repair 1)
application/owner_handoff/README.md            - repair corrections +
                                               full Phase 2 documentation
tests/test_context_analyzer.py                  - repair 1 regression tests
tests/test_work_context.py                       - repair 2 regression tests
tests/test_owner_handoff_config.py                - repair 6 invariant tests;
                                               Phase 2 allowlist-parsing tests
tests/test_owner_handoff_state_machine.py          - repairs 3/4/5 tests;
                                               create_task usage refactored
                                               throughout
docs/AI_DESK_V2_MASTER_SPEC.md                       - addendum: 2 more
                                               invariants, 2 more retracted
                                               env vars
docs/AI_DESK_V2_PHASE0_REPORT.md                      - pycache contradiction
                                               fixed; config table corrected
docs/AI_DESK_V2_PHASE1_REPORT.md                       - safety invariants
                                               table updated; repair-gate
                                               note added
README.md                                               - Phase 2 summary
.env.example                                             - wearable allowlist
                                               variable + invariant list
                                               update
```

**Not modified:** `requirements.txt` (no new dependency needed — confirmed
below), every existing test module outside `application/owner_handoff/`'s
own suite, and every existing application/system-layer module. No file
outside the allowed lists (repair gate or Phase 2) was touched; nothing
required stepping outside them, so no stop-and-explain was needed.

### Architecture implemented

- **Presence domain models** (`domain/presence.py`): `RadarState`,
  `WearableProximityState`, `WearableButton`, `RadarSample`,
  `WearableProximitySample`, `WearableAnswer` — all fail clearly on
  malformed construction (empty ids, naive timestamps, out-of-family button
  values); no raw BLE packet representation or device-secret field exists
  anywhere.
- **Radar/wearable simulators** (`adapters/radar.py`, `adapters/wearable.py`):
  `SimulatorRadar` and `WearableSimulator` perform no threads, sleeps,
  serial, BLE, or network access — every state change is an explicit,
  immediate method call. `WearableSimulator.send_question` accepts only a
  question_id and letters; `WearableTransport` documents the full future
  real-BLE requirement list (UUIDs, provisioning, RSSI policy, reconnect,
  replay protection, debounce) without inventing any of it.
- **Leave detection** (`fusion/leave_detector.py`): `LeaveDetector` fuses
  radar/wearable/mouse-idle/keyboard-idle into a confirmation window that
  only opens once all four are simultaneously true, confirms at exactly
  10.0s (never 9.999s), fails safe on monotonic clock rollback, and
  guarantees one stable `episode_id` (and therefore one
  `confirmation_event_id`) per continuous episode —
  `advance_state_machine_for_leave_evaluation` is the thin, tested
  integration driving `OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED`.
- **Return detection** (`fusion/return_detector.py`): `ReturnDetector`
  confirms only on radar `PERSON_PRESENT` + wearable `OWNER_NEAR`,
  never re-emits while present, and requires an explicit `reset()` to
  re-arm. Deliberately **not** wired into the state machine in Phase 2 (the
  task scope names only leave confirmation and question authorization/
  cancellation for "thin integration") — kept standalone and independently
  tested.
- **Handoff question domain model** (`domain/question.py`):
  `HandoffQuestion`, immutable (`options` wrapped in `MappingProxyType`),
  enforcing the same privacy boundary as `WorkContext`/`Evidence` and
  validating its option-key shape against the Master Spec's three valid
  cases (`{D}`, `{A,B,D}`, `{A,B,C,D}`).
- **Question generator** (`questions/generator.py`):
  `HandoffQuestionGenerator` builds options only from
  `possible_next_steps`/`unfinished_work`, deduplicates
  case/whitespace-insensitively, and falls back to D-only both on low
  confidence and on an insufficient (<2) genuine direction count after
  dedup — never padding with a generic suggestion.
- **Wearable answer lifecycle** (`questions/lifecycle.py`):
  `QuestionLifecycle` validates device allowlist membership, question_id
  match, duplicate/expiry/timing, and button-family/option-offered
  correctness; `advance_state_machine_for_answer`,
  `cancel_for_timeout`, `cancel_for_owner_return`, `cancel_for_disconnect`,
  and `recover_pending_questions_after_restart` provide the thin,
  fail-closed state-machine integration.
- **Terminal rendering** (`questions/terminal.py`): `render_question`
  writes the complete question to an injected `TextIO`; `wearable_payload`
  returns only `(question_id, letters)` — the two are structurally
  incapable of leaking into each other.

### Test results

```
python -B -m unittest discover -s tests -v
```

**Result: 215 tests run, 214 passed, 1 skipped, 0 failed.**

| Module | Tests |
|---|---|
| 11 legacy MVP modules (unchanged) | 47 (+1 skipped) |
| `test_owner_handoff_config.py` | 13 |
| `test_activity_classifier.py` | 10 |
| `test_context_analyzer.py` | 13 |
| `test_work_context.py` | 33 |
| `test_ocr_policy.py` | 8 |
| `test_owner_handoff_state_machine.py` | 29 (one test carries 21 `subTest`s) |
| `test_presence_fusion.py` | 23 |
| `test_wearable_contract.py` | 22 |
| `test_handoff_questions.py` | 16 |
| **Total** | **215** (214 passed + 1 skipped) |

No test calls real hardware, real serial ports, BLE, network, OCR, Codex
CLI, package installation, or a real sleep — every timing test uses an
injected fake monotonic/wall clock.

### Safety decisions

- **Leave confirmation timing is exact, not approximate**: `>=` comparison
  against both the idle threshold and the confirmation window, verified at
  the literal 9.999s/10.0s boundary.
- **One episode, one event id, enforced structurally**: `episode_id` is
  generated once per continuous all-true window and reused for every tick
  of that window, so replaying `confirmation_event_id` through the
  idempotent store can never double-apply a transition — this was verified
  end-to-end (detector → integration function → store) in
  `test_presence_fusion.py`, not just at the detector layer.
- **Return detection never lets supporting evidence override hardware**:
  `mouse_active`/`keyboard_active` are recorded on the result but have zero
  influence on the `confirmed` decision.
- **Question timeout is fail-safe by construction**: `cancel_for_timeout`
  is a no-op unless `now >= question.expires_at`, and there is no code path
  anywhere that transitions a timed-out question to `AUTHORIZED`.
- **Wearable authorization is fail-closed on restart**: `QuestionLifecycle`
  is intentionally in-memory only; a restarted process cannot answer a
  question it doesn't remember, and `recover_pending_questions_after_restart`
  explicitly cancels any task the persisted state machine still shows as
  `QUESTION_PENDING` rather than leaving it stranded.
- **Empty wearable allowlist means reject everything**, not "allow
  everything" — verified by a dedicated test.

### Dependency and interface confirmation

- `requirements.txt` is unchanged — confirmed via `git status --short`
  below; every Phase 2 component uses only the Python standard library plus
  already-imported project modules (`utils.time_utils`).
- No existing public API outside `application/owner_handoff/` was touched;
  no existing (legacy) test file was modified.
- No real hardware, network, BLE, OCR, or Codex CLI invocation exists
  anywhere in the new code — confirmed by code review of every new module
  (no `subprocess`, `socket`, `serial`, or BLE-library import appears
  anywhere in `application/owner_handoff/`).

### Explicitly deferred (unchanged from, or newly clarified since, Phase 1)

Real serial radar reader; real BLE and all BLE UUIDs; `ResearchAgentExecutor`
/`CodingAgentExecutor` and any Codex CLI integration or discovery; physical
workspace duplication and path-safety policy; package installation; the
CODEX_DATA/PACKAGE_INSTALL permission lifecycle's *execution* wiring (the
generic validation contract exists and is tested; only the
handoff-selection lifecycle is exercised against the state machine); the
top-level orchestrator and the return coordinator (a confirmed return is
detected but not yet wired into `EXECUTING -> RETURN_REQUESTED` or any
executor stop/resume behavior); `demo_workspace/` and
`run_owner_handoff_demo.py`; Dashboard/summary integration; real OCR; real
macOS notifications (Terminal remains the only display surface). None of
the above is claimed as implemented anywhere in this phase's documentation.

### Known limitations

- `ReturnDetector` is standalone and not yet consulted by anything that
  would stop an in-progress leave episode or executor — that wiring is
  explicitly future work (return coordinator / top-level orchestrator).
- The question generator's `MIN_CONFIDENCE_FOR_DIRECTIONS` threshold (0.2)
  and the entertainment-content marker list are both small, documented,
  reasoned choices for this phase, not empirically tuned — a later phase
  may need to revisit them once real usage data exists.
- `advance_state_machine_for_leave_evaluation`'s `StaleTransitionError`
  catches are deliberately permissive (silently no-op) for a task that has
  already progressed past what a given tick is trying to reach; this is
  correct for "thin" integration but means the function does not itself
  detect or report a genuinely unexpected state — a later orchestrator
  should log or otherwise surface that case if it ever occurs.
- No macOS hardware verification has been performed for anything in this
  phase or Phase 1.

### Recommended Phase 3 scope

1. Codex CLI preflight/flag discovery (deliberately deferred from Phase 1
   and Phase 2 — do not guess flags).
2. Wire the CODEX_DATA/PACKAGE_INSTALL permission lifecycle (already
   validated generically by `QuestionLifecycle`) to a real
   `CodingAgentExecutor` preflight/authorization flow.
3. `ResearchAgentExecutor`/`CodingAgentExecutor` themselves, plus
   `FakeCodingExecutor` for deterministic tests.
4. Physical workspace duplication and path-safety policy (Master Spec
   section 10).
5. Wire `ReturnDetector` into `EXECUTING -> RETURN_REQUESTED` and build the
   return coordinator's atomic-step-completion and resume-report logic.
6. Only after the above: the top-level orchestrator that ties presence
   fusion, question lifecycle, routing, and execution together end-to-end.

---

## End-of-phase verification

- [x] Repair gate green before Phase 2 began: 152 tests, 151 passed, 1
      skipped, 0 failed.
- [x] Full suite after Phase 2: **215 tests, 214 passed, 1 skipped, 0
      failed** (`python -B -m unittest discover -s tests -v`).
- [x] `git diff --check` — ran, no whitespace errors (only a benign
      LF/CRLF line-ending notice, not a `--check` failure).
- [x] Every new (untracked) text file explicitly scanned for trailing
      whitespace with a line-by-line regex pass — none found.
- [x] `git status --short` — only the files listed above appear as
      modified/untracked; `application/owner_handoff/`'s new
      `__pycache__` directories show as `!!` ignored (via
      `git status --ignored`), never tracked or even untracked.
- [x] All legacy tests still pass, unchanged, unmodified.
- [x] No new tracked `.pyc`/`__pycache__`, database file, screenshot,
      `.env` file, or temporary artifact exists anywhere in the tree.
- [x] `requirements.txt` unchanged — no new dependency.
- [x] No external interface changed — every existing public API outside
      `application/owner_handoff/` is untouched.
- [x] No real hardware/network/BLE/OCR/Codex CLI invocation exists
      anywhere in the new code.
- [x] This report created.
- [x] No commit created.
- [x] No push performed.
