# AI Desk V2 — Owner-Authorized Handoff (`application/owner_handoff/`)

This package implements **Phases 1 through 5** of AI Desk V2: centralized
configuration, domain models, the persisted state machine, the
activity/context/OCR classification pipeline (Phase 1); presence fusion
(leave/return detection), radar/wearable simulators, and the handoff-question
lifecycle (Phase 2); conservative skill routing, physical workspace
duplication, the CODEX_DATA/PACKAGE_INSTALL permission flows, a deny-by-default
execution policy, the Research Agent executor adapter, and the sandboxed Codex
CLI executor with a deterministic fake coding executor (Phase 3); a Phase 3
repair gate plus the top-level orchestrator, the return coordinator, and a
deterministic Terminal demo tying every prior phase together into one real
end-to-end (fake-backed) handoff (Phase 4); and a Phase 4 repair gate
(permission binding tightened to exactly what the owner saw, a real
cancellable execution session replacing a `ThreadPoolExecutor`, fail-closed
original/duplicate verification, sanitized artifact persistence) plus
release-candidate demo polish -- a visibly meaningful default demo executor,
automatic package-install tracking, a responsive run/wait Terminal flow, and
a safe Codex preflight-only mode (Phase 5). See "Implemented vs. deferred"
below before assuming any behavior beyond what is listed there.

The target platform is macOS, but every test in this package is
deterministic and hardware-free — nothing here requires macOS, real
sensors, real BLE, a real Codex CLI, or a network connection to run.

Full requirements: [`docs/AI_DESK_V2_MASTER_SPEC.md`](../../docs/AI_DESK_V2_MASTER_SPEC.md).
Architecture and audit background: [`docs/AI_DESK_V2_PHASE0_REPORT.md`](../../docs/AI_DESK_V2_PHASE0_REPORT.md).
Phase 1's exact scope and verification: [`docs/AI_DESK_V2_PHASE1_REPORT.md`](../../docs/AI_DESK_V2_PHASE1_REPORT.md).
Phase 2's exact scope, the Phase 1 repair gate, and verification: [`docs/AI_DESK_V2_PHASE2_REPORT.md`](../../docs/AI_DESK_V2_PHASE2_REPORT.md).
Phase 3's exact scope, the Phase 2 repair gate, and verification: [`docs/AI_DESK_V2_PHASE3_REPORT.md`](../../docs/AI_DESK_V2_PHASE3_REPORT.md).
Phase 4's exact scope, the Phase 3 repair gate, and verification: [`docs/AI_DESK_V2_PHASE4_REPORT.md`](../../docs/AI_DESK_V2_PHASE4_REPORT.md).
Phase 5's exact scope, the Phase 4 repair gate, and verification: [`docs/AI_DESK_V2_PHASE5_REPORT.md`](../../docs/AI_DESK_V2_PHASE5_REPORT.md).

## Implemented in Phase 1

- `config.py` — one centralized, immutable configuration dataclass.
- `domain/activity.py` — `ActivityClassification`, `ActivitySample`.
- `domain/work_context.py` — `WorkContext`, `Evidence`, `WorkContextTracker`,
  and `redact_sensitive`.
- `state_machine.py` — the full 13-state Master Spec state machine and its
  transition rules (pure logic, no I/O, no side effects).
- `store.py` — `OwnerHandoffStore`: SQLite-backed persistence for the state
  machine, with an idempotent event ledger and restart recovery.
- `classification/activity_classifier.py` — Tier 1 classification.
- `classification/context_analyzer.py` — Tier 2 classification.
- `classification/ocr_policy.py` — the fixed Tier 1 -> Tier 2 -> OCR
  invocation order.
- `adapters/ocr.py` — `OCRProvider` protocol, `NoOpOCRProvider`,
  `MockOCRProvider`. **No real OCR is implemented** — no screenshot capture,
  no macOS Vision, no Tesseract, no cloud OCR dependency.

## Implemented in Phase 2

- `domain/presence.py` — `RadarState`, `WearableProximityState`,
  `WearableButton`, `RadarSample`, `WearableProximitySample`,
  `WearableAnswer`. Malformed values (empty ids, naive timestamps,
  out-of-family button values) fail at construction.
- `adapters/radar.py` — `RadarSensor` protocol, `SimulatorRadar` (no
  threads/sleeps/serial access — a test sets state explicitly).
- `adapters/wearable.py` — `WearableTransport` protocol, `WearableSimulator`
  (queues deterministic answers; `send_question` accepts only a question_id
  and letters; no BLE/network access).
- `fusion/leave_detector.py` — `LeaveDetector` (the 5s-idle + 10s-confirmation
  fusion described below) and `advance_state_machine_for_leave_evaluation`
  (thin OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED integration).
- `fusion/return_detector.py` — `ReturnDetector` (radar PRESENT + wearable
  NEAR confirms once; standalone, not wired into the state machine in
  Phase 2 — see that module's docstring).
- `domain/question.py` — `HandoffQuestion` (immutable, Master Spec section 9
  contract).
- `questions/generator.py` — `HandoffQuestionGenerator`: deterministic,
  context-derived options only.
- `questions/lifecycle.py` — `QuestionLifecycle` (wearable answer
  validation), plus thin QUESTION_PENDING -> AUTHORIZED/CANCELED
  integration and fail-closed restart recovery for pending questions.
- `questions/terminal.py` — `render_question` (full Terminal rendering) and
  `wearable_payload` (the minimal question_id + letters sent to the
  wearable).

## Implemented in Phase 3

- `domain/execution.py` — `SkillKind`, `RoutedOption`, `HandoffPlan` (a
  companion routing plan; the external `HandoffQuestion` JSON contract is
  never changed), `ExecutionResult`/`ExecutionStatus`.
- `domain/manifest.py` — `DuplicationManifest`, `FileRecord`,
  `ExcludedPathRecord`.
- `routing/skills.py` — conservative routing: requires a verb AND a
  supporting object from the same category before committing to
  RESEARCH/CODING; ambiguous or conflicting text fails closed to
  `SkillKind.UNSUPPORTED`, which never executes anything.
- `workspace/path_policy.py` — path-safety checks (root/home/boundary/
  session-root/overlap/symlink/existing-destination rejection), operating
  only on resolved, absolute paths.
- `workspace/duplicator.py` — `duplicate_workspace` (physical byte-copy with
  exclusions and pre/post-copy hashing) and `verify_original_unchanged`
  (independent re-hash proving nothing in the original changed).
- `execution/policy.py` — `ExecutionPolicy`, the one deny-by-default gate
  every executor consults before invoking any runner.
- `execution/permission.py` — `PermissionGate` (binds a granted YES to
  task/session/manifest-hash so it can never authorize a different task,
  duplicate, or modified manifest) and the CODEX_DATA/PACKAGE_INSTALL
  question builders and state-machine request functions.
- `execution/research_executor.py` — `ResearchAgentExecutor`, a thin adapter
  around the existing, unmodified `application.handoff.a2a_client.A2AHandoffClient`.
- `execution/coding_executor.py` — the `CodingAgentExecutor` interface and
  `FakeCodingExecutor` (deterministic, never touches a subprocess).
- `execution/codex_cli.py` — `CodexPreflight` (runs only `codex --version`
  and `codex exec --help`) and `CodexCLIExecutor` (safe argv construction,
  stdin-only prompt, JSONL streaming with timeout/step-cap enforcement).
- `execution/package_installer.py` — `PackageInstaller`, a strict parser
  (`parse_package_spec`) accepting only `name` or `name==version`, and
  `build_install_argv` targeting only the duplicate-local virtual
  environment interpreter.

## Implemented in Phase 4 (repair gate + final wiring)

Phase 4 first repaired seven Phase 3 issues (see
[`docs/AI_DESK_V2_PHASE4_REPORT.md`](../../docs/AI_DESK_V2_PHASE4_REPORT.md)
for the full list and exact fixes: one-time-use permission grants bound to a
freshly-computed live duplicate digest, case-insensitive/pruned workspace
exclusions with symlinks failing the whole duplication closed, live
(never-cached) manifest hashing, nested Codex JSONL event handling with a
genuinely non-blocking poll-based timeout, a completed `ExecutionPolicy`,
token/phrase-boundary routing, and real-but-uninvoked subprocess adapters),
then built:

- `orchestrator.py` — `OwnerHandoffOrchestrator`, composing every Phase 1-3
  component behind deterministic, explicitly-called methods (no background
  thread/sleep anywhere). Enforces "only one active handoff task at a time"
  and that an UNSUPPORTED/DO_NOTHING route never executes anything, even
  though `QuestionLifecycle` itself authorizes any non-D letter.
- `return_coordinator.py` — `ReturnCoordinator`: EXECUTING ->
  RETURN_REQUESTED -> READY_FOR_REVIEW -> RETURNED, a bounded grace period
  for the one current atomic step to finish, and a fresh
  `workspace.duplicator.scan_duplicate_changes` + `verify_original_unchanged`
  re-scan before ever producing a resume report.
- `domain/resume.py` — `ResumeReport`, the fixed external JSON shape.
- `demo_workspace/` + `run_owner_handoff_demo.py` — a tiny, dependency-free,
  one-bug coding fixture and a deterministic Terminal REPL demo (simulator
  radar/wearable, fake research/coding executors by default; real Research/
  Codex modes are explicit, human-invoked-only flags — see the root
  `README.md`'s "AI Desk V2 — macOS demo" section).

## Implemented in Phase 5 (Phase 4 repair gate + release-candidate polish)

Phase 5 first repaired nine Phase 4 issues -- see
[`docs/AI_DESK_V2_PHASE5_REPORT.md`](../../docs/AI_DESK_V2_PHASE5_REPORT.md)
for the exact defect and fix for each. In summary:

- **Permission binding tightened further**: `PermissionGate.grant_from_answer`
  now validates the caller-supplied `duplicate_path`/`package_specs`/`argv`
  against the pending `PermissionQuestion`'s own fields (`duplicate_path` is
  now a required field on every `PermissionQuestion`) -- a YES can only ever
  authorize exactly what was displayed, never a different command a caller
  passed in alongside it. `PermissionGate.consume` now requires
  `question_id` (no longer optional).
- **Pre-write path-safety validation**: `run_owner_handoff_demo.py`'s
  `build_demo_orchestrator` now calls
  `workspace.path_policy.validate_demo_output_paths` -- a pure, no-I/O check
  of source/allowed-boundary/session-root/db-path -- before any `mkdir` or
  SQLite connection.
- **A real cancellable execution session** (`return_coordinator.ExecutionSession`)
  replaces the previous `ThreadPoolExecutor`-based implementation, which had
  two real bugs: a `ThreadPoolExecutor` context manager (or `shutdown(wait=True)`)
  blocks on exit until every submitted task finishes -- including one that
  never will -- and `concurrent.futures.thread` registers an interpreter-exit
  hook that joins its worker threads even after `shutdown(wait=False)`, so a
  stuck task could hang process exit itself. `ExecutionSession` instead runs
  the step on a plain `daemon=True` thread: every wait is bounded and a
  still-running daemon thread can never block interpreter exit. A
  genuinely uncooperative step (an unresponsive fake, or a real A2A call
  with no cancellation API) is *abandoned*, never falsely reported as
  "terminated." `CodexCLIExecutor` additionally checks
  `CodingTaskRequest.stop_requested` between nested Codex work items --
  once return is confirmed, it lets any currently-open item finish, refuses
  to accept a new one, and retains a live process handle
  (`terminate_current()`) an external caller can reach for a SIGTERM ->
  (bounded wait) -> SIGKILL escalation. `MultiStepFakeCodingExecutor` gives
  the same cooperative-stop contract to a deterministic, non-subprocess fake
  for tests.
- **Fail-closed original/duplicate verification**: a failed
  `verify_original_unchanged` now transitions the task to FAILED (never
  READY_FOR_REVIEW) and returns a `SafetyFailureRecord` -- a structurally
  different, incompatible type from `ResumeReport` (whose
  `original_files_modified` is now enforced to always be `()`) -- so a
  verification failure can never be presented as a normal, reviewable
  result. `scan_duplicate_changes` now also detects deletions/renames-away
  of copied files, symlinks introduced into the duplicate, and unsafe
  file/directory type changes, routing all of them to the same
  `SafetyFailureRecord` path (the fixed `ResumeReport` shape has no field
  for a deleted file, and the Master Spec prohibits deleting user-created
  files).
- **Orchestrator terminal-state handling**: D and UNSUPPORTED routes always
  reach CANCELED (the demo no longer crashes calling `routed_skill()` after
  D); `acknowledge_terminal_task()` frees the orchestrator for a new
  `start_task()` after any CANCELED/FAILED task, not only the
  READY_FOR_REVIEW -> RETURNED happy path; duplication failures, Codex/
  research executor failures, and grace-period timeouts all transition to
  FAILED via a shared `_fail_current_task` helper; `run_owner_handoff_demo.py`
  now catches every exception at the command boundary (never a raw
  traceback) and scripted mode (`run_commands`) returns `False` if any
  command errored.
- **Sanitized artifact persistence** (`persistence.py`): the duplication
  manifest, routed selected task, sanitized execution result, approved
  package installs, and the final report/failure are written atomically
  (temp file + `os.replace`) under `<session_root>/<task_id>.artifacts/` --
  never inside the original source workspace, never containing an
  unredacted secret/token/environment value. `RETURN_REQUESTED` is now a
  `RUNTIME_BOUND_STATE`, so restart recovery fails it closed exactly like
  EXECUTING/WORKSPACE_DUPLICATING/PERMISSION_PENDING.
  `OwnerHandoffOrchestrator.attach_to_task` +
  `load_persisted_final_report()` let a fresh process reload and display
  the exact report for a task that reached READY_FOR_REVIEW before a
  restart.
- **Subprocess lifecycle hardening**: the real Codex process adapter now
  reads stdout and stderr on separate threads into a bounded queue/tail
  buffer (stderr is never parsed as JSONL), escalates SIGTERM -> (bounded
  wait) -> SIGKILL on POSIX/macOS, and every wait is bounded. A runner-start
  exception becomes a sanitized `ExecutionResult(FAILED)` -- the CODEX_DATA
  permit it already consumed stays consumed, so a retry needs a fresh YES.
- **Remaining privacy leaks fixed**: `ResearchAgentExecutor` now redacts
  exception text and A2A executive summaries before they ever reach a
  result's `summary` field.

Then, release-candidate demo polish:

- `run_owner_handoff_demo.py --executor fake` now uses
  `DemoCalculatorFixExecutor` by default (not the general
  `FakeCodingExecutor`) -- it actually fixes the seeded
  `calculator.py` bug inside the duplicate (never the source), or creates
  one clearly-named `AI_GENERATED_NOTE.txt` if that exact fixture/bug isn't
  present. It requires and consumes the same CODEX_DATA permit the real
  Codex path would.
- A successful, permitted package install now automatically accumulates
  into `OwnerHandoffOrchestrator`'s per-task state and appears in
  `ResumeReport.packages_installed` without the Terminal caller supplying
  it -- a failed/denied/mismatched attempt never appears.
- The demo gained `run` (starts the routed coding/research step on a
  background `ExecutionSession`, returning immediately) and `wait
  [timeout_seconds]` (blocks for the result) commands, so `status` and
  `return` remain usable while a step is actually running -- see the root
  `README.md`'s demo walkthrough for the exact command sequence.
- A new `--preflight-only` mode runs only `codex --version` / `codex exec
  --help` and reports supported/missing isolation flags, then exits --
  structurally, it never constructs an orchestrator, store, or workspace
  duplicator, so it cannot create a duplicate, a task database, a
  CODEX_DATA question, or contact A2A.

## Explicitly deferred (not implemented)

- A real serial radar reader (still simulator/domain logic only).
- Real BLE of any kind, and any BLE UUIDs (Service or Characteristic) — none
  are invented anywhere in this codebase.
- Codex CLI flag *discovery* beyond what `CodexPreflight` checks via
  `exec --help` output matching — the exact current CLI version's full
  capability surface is not otherwise explored.
- Real package installation, real Codex CLI execution, and real A2A network
  calls **in this repository's own automated tests** — every test uses a
  fake runner/client. The real subprocess adapters (`SubprocessCommandRunner`,
  `SubprocessCodexProcessRunner`, `SubprocessInstallerCommandRunner`) exist
  and are reachable only via the demo's explicit `--executor codex` /
  `--research real` flags, never by default and never automatically.
- Dashboard integration and any change to the existing daily-summary
  pipeline or the existing Research Handoff MVP.
- Real OCR of any kind.
- Real macOS notifications — Terminal remains the only display/notification
  surface.
- Git push, publication, deployment, and email/message sending — always
  denied by `ExecutionPolicy`, never implemented.
- Automatic writing of the resume report to a file, and any automatic
  merge/copy of duplicate-workspace changes back into the original —
  reviewing and applying approved changes remains a manual, human step.

None of the above is claimed as working, tested against real macOS
hardware, or verified in any way in this phase.

## Configuration

All configurable values live in one immutable dataclass,
`OwnerHandoffConfig`, loaded once via `load_owner_handoff_config()`.

| Field | Env var | Default | Notes |
|---|---|---|---|
| `owner_leave_confirmation_seconds` | `AI_DESK_V2_LEAVE_CONFIRM_SECONDS` | `10` | Full unanimous-absence confirmation window (Master Spec section 7). Consumed by `LeaveDetector` since Phase 2. |
| `input_idle_threshold_seconds` | `AI_DESK_V2_INPUT_IDLE_SECONDS` | `5` | How long mouse/keyboard must be idle before "mouse inactive"/"keyboard inactive" become true. See "Effective leave timing" below. Consumed by `LeaveDetector` since Phase 2. |
| `handoff_question_expiration_seconds` | `AI_DESK_V2_QUESTION_EXPIRY_SECONDS` | `60` | Question expiry window. Consumed by `HandoffQuestionGenerator` since Phase 2. |
| `coding_agent_max_runtime_seconds` | `AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS` | `300` | Codex CLI's overall runtime cap. Consumed by `CodexCLIExecutor`/`OwnerHandoffOrchestrator` since Phase 3/4. |
| `coding_agent_max_safe_steps` | `AI_DESK_V2_CODING_MAX_STEPS` | `20` | Codex CLI's unique-step cap (counted from nested `item.id`s). Consumed by `CodexCLIExecutor`/`OwnerHandoffOrchestrator` since Phase 3/4. |
| `owner_handoff_db_path` | `AI_DESK_V2_DB_PATH` | `data/owner_handoff.sqlite3` | Path for `OwnerHandoffStore`'s SQLite file. |
| `workspace_session_root` | `AI_DESK_V2_SESSION_ROOT` | `data/owner_handoff/sessions` | Root under which every physical duplicate AND every persisted task artifact (`persistence.TaskArtifactStore`) is written. Consumed by `workspace.duplicator`/`OwnerHandoffOrchestrator` since Phase 3/4. |
| `ocr_adapter_mode` | `AI_DESK_V2_OCR_MODE` | `noop` | `noop` (default, always available) or `mock` (deterministic, tests/simulator development only). Selects which `OCRProvider` class loads — **not** the invocation order, which is fixed (see below). |
| `wearable_device_allowlist` | `AI_DESK_V2_WEARABLE_DEVICE_IDS` | `()` (empty) | Comma-separated device ids, whitespace-trimmed, duplicates removed (first occurrence wins, order preserved). Empty means every real/simulated wearable answer is rejected until explicitly configured — see "Wearable allowlist" below. |

Every override is validated: durations must be positive and finite, the
step count must be a positive integer, paths must be non-empty and must not
be a filesystem root, and the OCR mode must be one of the two known values.
An invalid value raises `ConfigError` immediately rather than silently
falling back to an unsafe default.

### 5-second input idle threshold and effective leave timing

`input_idle_threshold_seconds` (default 5s) is **not** a second,
independent timer racing the 10-second confirmation window
(`owner_leave_confirmation_seconds`). It is what makes the "mouse
inactive"/"keyboard inactive" booleans that `LeaveDetector` consumes become
true in the first place: `LeaveDetector.evaluate()` treats mouse/keyboard as
idle once at least `input_idle_threshold_seconds` have elapsed since the
last `record_mouse_activity()`/`record_keyboard_activity()` call (or since
construction, if neither has ever been called — absence of evidence of
activity is treated as idle). The confirmation window itself only starts
counting once **all four** conditions (radar `PERSON_ABSENT`, wearable
`OWNER_AWAY`, mouse idle, keyboard idle) are simultaneously true.

**Combined effective timing with the defaults:** if radar and wearable are
already absent/away by the time input goes idle, leave is confirmed
approximately **15 seconds** after the last input (5s idle + 10s
confirmation) — not 10s and not 5s. The confirmation window resets to zero
(not merely pauses) on any renewed input, any `UNKNOWN` signal, any
contradictory signal, or any monotonic clock rollback; it never confirms at
9.999s and always confirms at exactly 10.0s once the window has genuinely
been open that long. See `LeaveDetector` in `fusion/leave_detector.py` and
`tests/test_presence_fusion.py` for the exhaustive boundary/edge-case
coverage.

### Safety invariants that cannot be changed through `.env`

The following are centralized as plain Python constants in `config.py` and
are **never** read from the environment. An override capability here would
defeat the exact property the constant exists to guarantee:

- wearable `UNKNOWN` never triggers a leave/return decision;
- contradictory sensor signals always mean "wait," never "act";
- external actions (message/push/publish/upload/deploy/install/delete)
  remain denied in v1;
- OCR is callable only after Tier 1 (activity) and Tier 2 (context) have
  both independently returned `AMBIGUOUS` — the fixed control flow is
  `activity_classifier -> context_analyzer -> ocr`;
- an unanswered or timed-out handoff question always resolves to D / "Do
  nothing," never to authorization;
- a missing or unusable Codex CLI always stops safely — fixed behavior, not
  a policy value a later environment variable could flip to an unsafe
  fallback;
- missing Codex CLI isolation support always fails closed (reserved for the
  phase that implements Codex CLI integration);
- the original workspace is never writable (reserved for the phase that
  implements workspace duplication);
- Git push/publish/message/deploy remain prohibited.

Six previously proposed environment variables that would have made these
invariants configurable have been removed and are **not** present in
`.env.example`: `AI_DESK_V2_WEARABLE_UNKNOWN_TRIGGERS`,
`AI_DESK_V2_CONTRADICTION_POLICY`, `AI_DESK_V2_EXTERNAL_ACTIONS_ENABLED`,
`AI_DESK_V2_OCR_POLICY`, `AI_DESK_V2_UNANSWERED_DEFAULT`, and
`AI_DESK_V2_MISSING_CODEX_POLICY`. See `docs/AI_DESK_V2_MASTER_SPEC.md`,
"Phase 1 Review Addendum," for the authoritative list and rationale. Every
invariant above is backed by a real constant in `config.py`
(`WEARABLE_UNKNOWN_NEVER_TRIGGERS`, `CONTRADICTORY_SIGNALS_POLICY`,
`EXTERNAL_ACTIONS_ENABLED`, `OCR_INVOCATION_ORDER`,
`UNANSWERED_QUESTION_DEFAULT`, `MISSING_CODEX_CLI_POLICY`) — this
documentation does not claim a constant exists unless it does.

## Activity classification (Tier 1)

`ActivityClassifier.classify(sample)` is deterministic and never invokes
OCR. It uses only aggregate interaction counts and the active application
name — never window titles, Chrome domains, or filenames (that is Tier 2's
job). Two small, bounded hint lists are used only as hints, never as
exhaustive "final truth": a short list of known task surfaces (required,
not merely sufficient, for a `WORKING` result — an unknown application
never resolves to `WORKING` at Tier 1, regardless of interaction level) and
a short list of known communication/entertainment surfaces (forced to stay
`AMBIGUOUS` even under high interaction). A locked screen, screensaver,
login window, or no active interface, combined with zero interaction,
resolves `NOT_WORKING`. Everything else stays `AMBIGUOUS` and proceeds to
Tier 2.

## Context analysis (Tier 2) and evidence/confidence rules

`ContextAnalyzer.analyze(sample, work_context)` runs only when Tier 1
returns `AMBIGUOUS`. It reads sanitized window title, Chrome **domain**
(never a full URL — `ActivitySample` itself rejects anything containing
`://`, `/`, or `?` in that field), visible filename, and the current
`WorkContext`'s existing evidence.

A signal resolves `WORKING` or `NOT_WORKING` only when either:

- the same `(source, detail)` evidence recurs at least twice (once
  historically, once now), or
- at least two distinct source types (window title, Chrome domain, visible
  filename) corroborate the same hint category within one sample.

A single matching keyword never resolves anything. A weak (unresolved)
entertainment/communication mention never overwrites stronger, already-
resolved task evidence — but if both categories are independently resolved
in the same evaluation, that is a genuine conflict and the result stays
`AMBIGUOUS`. `ContextAnalyzer` never claims a task was "completed" from
window/app evidence; it only ever returns a classification and candidate
observation evidence.

`WorkContextTracker` (in `domain/work_context.py`) is the only component
that mutates a `WorkContext`:

- evidence is deduplicated by `(kind, source, detail)`; a repeat increments
  `corroboration_count` in place instead of adding a new entry;
- `project`/`current_task` can only be set once backing evidence has been
  corroborated at least twice — a single observation can never establish
  them;
- confidence rises only when corroboration first reaches the required
  threshold (and rises more slowly on further corroboration), never from an
  uncorroborated observation;
- confidence falls on an explicit conflict (`register_conflict()`) and on
  staleness (`apply_staleness()`, using an injectable clock so this is
  testable without real sleeps);
- all list fields (`evidence`, `recent_actions`, `recent_files`,
  `unfinished_work`, `possible_next_steps`) are bounded — the oldest entries
  are dropped once the bound is exceeded;
- every evidence `detail` is passed through `redact_sensitive()` before
  storage, which strips likely tokens/passwords/credentials, secret-bearing
  query-string parameters, and any full URL.

## OCR: fixed invocation order, no real OCR implemented

`resolve_with_ocr()` implements the mandatory control flow: Tier 1, then
(only if `AMBIGUOUS`) Tier 2, then (only if still `AMBIGUOUS`) at most one
call to the configured `OCRProvider`. There is no environment variable that
can reorder or bypass this — it is a safety invariant, not a configurable
policy (see above).

**Real OCR is not implemented in this phase or any prior phase.** The only
two `OCRProvider` implementations that exist are `NoOpOCRProvider` (default;
always returns no evidence) and `MockOCRProvider` (deterministic text for
tests/simulator development only). Neither touches the filesystem, so
neither can ever leave behind a screenshot or a temporary image path. A
future real adapter must use the minimum screen region, must not persist
screenshots by default, must delete any temporary capture immediately, and
may only retain sanitized extracted text — none of that exists yet.

## Persisted state machine

`state_machine.py` defines all 13 Master Spec states and every allowed
transition (pure logic, no I/O). `store.py`'s `OwnerHandoffStore` persists
task state and a full transition-event ledger in a dedicated SQLite
database (`AI_DESK_V2_DB_PATH`), following the same pattern as the existing
`application/handoff/store.py`: WAL mode, atomic
`UPDATE ... WHERE state = ?` compare-and-swap, and no connection held open
between calls (so the database file is always safe to delete afterward,
including on Windows).

Every transition requires an `event_id`. Replaying the same `event_id` with
the same transition data is a no-op (idempotent, no second history row).
Reusing an `event_id` for *different* transition data raises
`EventConflictError`. A transition whose expected source state no longer
matches the task's actual persisted state raises `StaleTransitionError`.

**Corrected coding-path ordering** (Phase 1 review correction — Codex CLI
must not start before a separate, explicit data-authorization YES):

```
AUTHORIZED -> WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA) -> EXECUTING
```

Package-install permission is a distinct, later request that can only occur
after Codex data-authorization has already been granted once:

```
EXECUTING -> PERMISSION_PENDING(PACKAGE_INSTALL) -> EXECUTING (after approval)
```

Every `PERMISSION_PENDING` row always carries an explicit `PermissionKind`
(`CODEX_DATA` or `PACKAGE_INSTALL`) — there is no generic, kind-less pending
permission. Denied or expired permission transitions to `CANCELED` — since
Phase 3, this is fully implemented end to end via
`questions/lifecycle.py`'s `QuestionLifecycle.submit_answer` (NO),
`cancel_for_timeout`, `cancel_for_owner_return`, and `cancel_for_disconnect`,
all of which now handle `PermissionQuestion` (PERMISSION_PENDING) the same
way they handle `HandoffQuestion` (QUESTION_PENDING).

**Restart recovery is explicit, not automatic.** `recover_after_restart()`
force-fails any task found in `WORKSPACE_DUPLICATING`, `EXECUTING`, or
`PERMISSION_PENDING` — states a live subprocess/thread handle could
plausibly be attached to — because Phase 1 (and every phase before the one
that implements a real executor) can never have a genuinely live handle to
resume. Merely importing this module or constructing a store never triggers
recovery; only an explicit call does, because only the process that owns
runtime handles can know which ones are actually still live.

## Presence fusion (Phase 2)

`LeaveDetector.evaluate(radar=..., wearable=...)` is polled repeatedly (once
per tick, by whatever later phase builds the orchestrator loop) and returns
a `LeaveEvaluation` describing whether a candidate window is open, whether
it has been confirmed, or whether it just reverted. Every confirmed episode
gets one stable `episode_id` (generated once, when all four conditions
first become simultaneously true) that both `candidate_event_id` and
`confirmation_event_id` are derived from — this is what makes "one absence
event starts at most one task" and "the same confirmation_event_id must not
start two handoff tasks" hold: replaying the same `confirmation_event_id`
through `OwnerHandoffStore.apply_transition` is idempotent by construction
(see Phase 1's event ledger above). `advance_state_machine_for_leave_evaluation`
is the thin integration that actually drives
`OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED`; it silently no-ops
(catching `StaleTransitionError`) once a task has already moved past what a
given tick is trying to reach.

`ReturnDetector.evaluate(radar=..., wearable=...)` confirms a return only on
radar `PERSON_PRESENT` + wearable `OWNER_NEAR`; mouse/keyboard activity is
recorded as `supporting_evidence` but can never force a confirmation when
the hardware state disagrees or is `UNKNOWN`. It emits once per continuous
present episode (repeated samples don't re-emit) and requires an explicit
`reset()` — called once a new leave episode begins — before it will confirm
again. Unlike `LeaveDetector`, `ReturnDetector` is **not** wired into the
state machine in Phase 2 (the task scope for "thin state-machine
integration" covers leave confirmation and question authorization/
cancellation only) — it is a standalone, independently-tested component.

Both detectors use **monotonic** time internally, never wall-clock, and the
monotonic source is injectable for deterministic tests. A monotonic clock
rollback is handled by failing safe: the in-progress window is dropped
(never confirmed) rather than risking a corrupted elapsed-time computation.

## Handoff questions (Phase 2)

`HandoffQuestionGenerator.generate(work_context)` builds one
`HandoffQuestion` using only `WorkContext.possible_next_steps` and
`unfinished_work` — never a fixed, generic "things people usually do" list.
Equivalent candidates are deduplicated (case/whitespace-insensitive); if
fewer than two genuine distinct directions remain after dedup, or
`WorkContext.confidence` is below a documented threshold
(`MIN_CONFIDENCE_FOR_DIRECTIONS` in `generator.py`), the question offers
**D only** — a valid, honest outcome rather than padding with an unrelated
suggestion. `context_summary` is built purely from already-sanitized
`WorkContext` fields (every field is validated at construction — see
Phase 1's privacy enforcement above), and `HandoffQuestion` itself
independently re-validates every field for unsafe content at construction,
same as `WorkContext`/`Evidence`.

### Terminal vs. wearable information split

- **Terminal** (`questions/terminal.py`'s `render_question`) gets
  *everything*: the full context summary, the full question text, every
  option's text, the question id, and the expiration time. It accepts an
  injected `TextIO` stream and never reads a selection from the keyboard.
- **Wearable** (`questions/terminal.py`'s `wearable_payload`, sent via
  `WearableTransport.send_question`) gets only the **question_id and the
  available letters** — never the question text, context summary,
  filenames, or any workspace content. This split is enforced structurally:
  `send_question`'s signature has no parameter that could carry more than
  that.

### Wearable answer validation and lifecycle

`QuestionLifecycle.evaluate_answer(answer)` rejects, without side effects,
any answer that is: from a device not in `wearable_device_allowlist`; for
the wrong (or no) pending question; a duplicate of an already-accepted
answer; timestamped before the question was created or at/after its
expiration; the wrong button family (A/B/C/D for a YES/NO permission
question, or vice versa); for an option this particular question didn't
actually offer; or received while the lifecycle has been `disconnect()`ed.
A rejected answer leaves the task exactly `QUESTION_PENDING` — no
transition is attempted. **Timeout always resolves to D / "Do nothing" and
never to `AUTHORIZED`**: `cancel_for_timeout` only ever transitions to
`CANCELED`, and only once the question has genuinely expired.

For Phase 2, the validation contract is generic enough to support both
`QuestionKind.HANDOFF_SELECTION` (A/B/C/D) and `QuestionKind.PERMISSION`
(YES/NO), but only the handoff-selection lifecycle is exercised end-to-end
against the state machine (`advance_state_machine_for_answer`,
`cancel_for_timeout`, `cancel_for_owner_return`, `cancel_for_disconnect`).
Wiring the permission lifecycle to a real Codex-data/install-authorization
flow is Phase 3 work.

**Restart-safety (fail-closed, not persisted):** `QuestionLifecycle` holds
its one pending question in memory only. A restarted process constructs a
fresh instance with no pending question, so any incoming answer is rejected
as `WRONG_STATE` — it can never silently accept an answer for a question it
cannot reconstruct. `recover_pending_questions_after_restart` complements
this by explicitly cancelling any task the *persisted* state machine still
shows as `QUESTION_PENDING`, using the same per-lifecycle unique-event-id
scheme as `OwnerHandoffStore.recover_after_restart`.

### Wearable allowlist

`wearable_device_allowlist` (env `AI_DESK_V2_WEARABLE_DEVICE_IDS`) is a
comma-separated list of device ids, whitespace-trimmed, with duplicates
removed deterministically (first occurrence wins). The default is empty,
which means **every** answer — real or simulated — is rejected until the
caller explicitly configures at least one device id. There is no "accept
anything" fallback.

### Simulator usage

```python
from application.owner_handoff.adapters.radar import SimulatorRadar
from application.owner_handoff.adapters.wearable import WearableSimulator
from application.owner_handoff.domain.presence import (
    RadarState, WearableButton, WearableProximityState,
)

radar = SimulatorRadar()
radar.set_state(RadarState.PERSON_ABSENT)

wearable = WearableSimulator(device_id="wearable-001")
wearable.set_proximity(WearableProximityState.OWNER_AWAY)
wearable.send_question("question-1", ("A", "B", "D"))
wearable.queue_answer(WearableButton.A)  # defaults to the last-sent question_id
answer = wearable.poll_answer()
```

Neither simulator performs threads, sleeps, serial access, BLE, or network
I/O of any kind — every state change is explicit and immediate, which is
what makes them safe to use in deterministic tests.

## Skill routing (Phase 3)

`routing/skills.py`'s `classify_goal_text` requires **both** a clear action
verb **and** a supporting object/artifact keyword from the *same* category
(coding or research) before committing to that skill — a single weak
keyword is never enough. Text matching both categories (conflicting hints)
or neither (ambiguous) fails closed to `SkillKind.UNSUPPORTED`, which no
executor is ever built to run. `route_handoff_question` always maps option
D to `SkillKind.DO_NOTHING` and builds a `HandoffPlan` — a companion object
carrying this routing metadata that never changes `HandoffQuestion`'s own
JSON contract.

## Physical workspace duplication (Phase 3)

Coding execution always operates on a **physical byte-copy**, never a git
worktree — a worktree shares object/index state with the original, which is
exactly the kind of coupling this design exists to avoid.

**Path boundaries** (`workspace/path_policy.py`), checked only against
resolved, absolute paths (never raw strings, to defend against `..`
segments, symlinks, or case-normalization differences):

- the source workspace path must always be explicitly supplied — never
  defaulted to cwd, repository root, home, or a filesystem root;
- rejected: filesystem roots; the user home directory; a source outside the
  explicitly supplied allowed boundary; a source inside
  `workspace_session_root`; a `workspace_session_root` that would itself sit
  inside the selected source; source/destination overlap in either
  direction; an already-existing destination; **all** symlinks anywhere in
  the source path or tree (no "safe symlink" allowance in this MVP).

**Exclusions** applied while walking the source tree (each recorded with a
reason in the manifest, never silently dropped): `.git`; `.env` and
`*.env`; anything named `secrets*` or `credentials*`; `.venv`/`venv`/`env`;
`__pycache__`/`*.pyc`/`.pytest_cache`/`.cache`; `node_modules`;
`build`/`dist`/`*.egg-info`; `*.sqlite3`/`*.db`; any directory named the
same as `workspace_session_root`'s own directory (a defensive guard against
a prior AI Desk session directory nested inside the source).

**The manifest** (`domain/manifest.py`'s `DuplicationManifest`) records the
session/task id, source and duplicate paths, every copied relative path,
every exclusion with its reason, the SHA-256 of every original file
*before* copying, the SHA-256 of every copied file *immediately after*
copying (compared inline — a mismatch raises `WorkspaceDuplicationError`
before the duplication is considered to have succeeded), and — once
`verify_original_unchanged` actually runs — whether every original file
still matches its recorded hash. `original_files_modified` is only ever
readable once `verification_performed` is `True`; the manifest's own
validation refuses to construct a manifest that reports
`original_files_modified` before verification has actually happened, so it
can never be falsely reported as empty. File *contents* are never logged
or stored anywhere — only paths, hashes, and reasons. **AI Desk never
automatically deletes a duplicate** — nothing in this package does; tests
clean up only their own temporary directories.

## Research vs. Coding executor separation (Phase 3)

- `execution/research_executor.py`'s `ResearchAgentExecutor` wraps the
  existing, **unmodified** `application.handoff.a2a_client.A2AHandoffClient`
  and reuses its existing `TaskCapsule` schema unchanged. It sends only the
  routed goal text (and an optional expected-output hint) — never a
  workspace path, a duplicate path, or any file content. `application/handoff/`
  itself is untouched.
- `execution/coding_executor.py` defines the separate `CodingAgentExecutor`
  interface plus `FakeCodingExecutor` (deterministic, no subprocess ever).
  The real implementation, `execution/codex_cli.py`'s `CodexCLIExecutor`, is
  documented separately below. The Research Agent is never converted into a
  general coding agent, and the Coding executor never talks to the A2A
  protocol.

## Permission binding and expiry (Phase 3)

`execution/permission.py`'s `PermissionGate` binds a granted YES to the
*exact* task, question, session (duplicate), and manifest-hash it was
granted for. `compute_manifest_hash` hashes the duplicate's path together
with every copied file's recorded hash — if the duplicate changes in any
way after a grant, the hash changes, and `verify_authorization` (which
checks task id, session id, manifest hash, and expiry together) will not
match. A previous YES can therefore never authorize a different task, a
different duplicate, or a modified manifest.

### Codex data-upload authorization

The exact required Terminal prompt (`domain/question.py`'s
`CODEX_DATA_PROMPT`, Master Spec section 14):

> This task will use Codex CLI and may send relevant files from the
> duplicate workspace to the configured model service. Allow?

The state-machine path is
`AUTHORIZED -> WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA) ->
EXECUTING`, and Codex may start only after a matching YES
(`request_codex_data_permission` / `QuestionLifecycle.submit_answer`'s
PERMISSION-kind branch). NO, timeout, disconnect, a stale/wrong question
id, an unknown device, a duplicate answer, owner return, or a restart all
cancel to `CANCELED` and leave the Codex subprocess runner call count at
zero — verified directly in `tests/test_permission_flow.py` and
`tests/test_codex_executor.py`.

### Package installation authorization

`PermissionGate.build_package_install_question` builds a prompt that always
displays the exact package names, the exact argv, the duplicate workspace
path, and states installation happens only inside a local virtual
environment. The state-machine path is
`EXECUTING -> PERMISSION_PENDING(PACKAGE_INSTALL) -> EXECUTING` (only after
a matching YES). `execution/package_installer.py`'s `PackageInstaller` is
the only controlled path to an actual install call — see "Package installer
safety" below. NO/timeout/disconnect/owner-return cancels the whole coding
handoff the same way the CODEX_DATA flow does.

## Codex CLI preflight strategy (Phase 3)

`execution/codex_cli.py`'s `CodexPreflight` runs only two read-only,
introspection commands — `codex --version` and `codex exec --help` —
**never** `codex exec` itself, and never anything that could contact a
model service. It parses the actual `--help` output for the required flags
below and fails closed (`CodexPreflightError`, before any process runner is
ever invoked) if any are missing. It never claims Codex CLI universally
lacks a capability — only that the currently detected installation, at
preflight time, did not advertise it. The verified local reference version
during this work was `codex-cli 0.134.0`, but that version string is never
hardcoded or asserted against.

Required capabilities (detected from actual `--help` text, not assumed):
`exec`, `--sandbox`, `--cd`, `--ephemeral`, `--json`,
`--ignore-user-config`, `--skip-git-repo-check`, and generic `-c`/`--config`
support.

## Safe argv policy (Phase 3)

`CodexCLIExecutor` builds argv as a fixed Python list — never a shell
string:

```
codex exec --strict-config
  -c sandbox_workspace_write.network_access=false
  --sandbox workspace-write
  --cd <verified duplicate>
  --ephemeral --json --ignore-user-config --skip-git-repo-check
  -
```

`--skip-git-repo-check` is used deliberately: the physical duplicate always
excludes `.git` (see "Physical workspace duplication" above), so the
duplicate is never a git repository Codex could recognize as trusted in the
first place. The prompt is supplied over **stdin** (the trailing `-`),
never as an argv item; `cwd` is always exactly the verified duplicate;
network access is explicitly disabled via the `-c` config override; no
original (source) path is ever passed to Codex; and none of
`--dangerously-bypass-approvals-and-sandbox`,
`--dangerously-bypass-hook-trust`, `--add-dir`, `--search`,
`--ignore-rules`, or any `danger-full-access` sandbox mode is ever
constructed — checked both by never emitting them and, defensively, by
`ExecutionPolicy` rejecting them if they somehow appeared. Every environment
variable and every argv item is checked for secret-looking content before
any runner call; nothing here logs process environment variables.

**Hackathon limitation, documented plainly:** parsing Codex's JSONL output
after the fact is a defense-in-depth signal, not a guarantee against a
hostile or compromised model. The sandbox, disabled command network, the
physical duplicate, this fixed prompt policy, post-hoc manifest re-hashing,
and the explicit installer gate are independent layers — not a formal proof
that a sufficiently adversarial model inside the sandbox cannot cause some
effect this design did not anticipate.

### JSONL processing, timeout, and step-cap defaults

Each JSONL line is parsed incrementally. A `type` of `error`,
`turn.failed`, or `thread.error` fails the execution immediately (and
terminates the process handle). Steps are counted by unique event/item id
(`id` or `item_id`); `coding_agent_max_safe_steps` (default **20**) and
`coding_agent_max_runtime_seconds` (default **300**) are both enforced
during streaming — exceeding either calls `handle.terminate()` and produces
a `FAILED` result. A nonzero exit code, malformed JSON (even one line), or a
stream that ends without a recognizable `*.completed`/`*.succeeded` event
all produce `FAILED`. Every retained event is sanitized (`_sanitize_event`)
— known-sensitive keys (`env`, `api_key`, `authorization`, `token`,
`secret`, `credentials`, ...) are dropped and every string value is passed
through `redact_sensitive` before being kept for the result's `detail`.

## Package installer safety (Phase 3)

`execution/package_installer.py` supports **only** a tightly controlled
`python -m pip install` invocation against the duplicate-local virtual
environment. `parse_package_spec` accepts exactly `name` or
`name==version` and rejects everything else: URLs, local/relative paths,
extras (`pkg[extra]`), version ranges other than an exact pin, VCS refs
(`@`), whitespace, and shell metacharacters. `venv_python_path` resolves to
`<duplicate>/.venv/bin/python` on macOS/Linux or
`<duplicate>\.venv\Scripts\python.exe` on Windows (used by this project's
own tests) — never a global interpreter. `PackageInstaller.install`
additionally rejects `-r`/`--requirement`, `-e`/`--editable`, and any
custom index-url option, and refuses brew/npm/apt/yum/dnf outright. The
installer is invoked only after a matching PACKAGE_INSTALL YES — nothing in
this package calls it any earlier.

## Deny-by-default execution policy (Phase 3)

`execution/policy.py`'s `ExecutionPolicy` is the one shared gate both
`CodexCLIExecutor` and `PackageInstaller` consult before ever invoking a
runner. It always rejects: a raw shell string in place of an argv list;
`shell=True`; shell metacharacters/command substitution
(`&& || ; | `` $( ` >` `<`); `sudo`/`runas`/`doas`; `git push`/`fetch`/
`pull`/`clone`/`remote`; publish/deploy/message tools (`curl`, `wget`,
`scp`, `rsync`, `ssh`, `npm publish`, `twine upload`, `docker push`, `mail`,
`sendmail`, `smtp`, `slack`); brew/npm/apt/yum/dnf; any argv item
containing a secret/token/password-like value; and any path outside the
duplicate or any cwd that isn't exactly the duplicate. Every test that
exercises a denial demonstrates the fake runner was never called — the
check always happens first.

## macOS target paths and simulator/fake behavior (Phase 3)

The duplicate-local virtual environment interpreter path
(`.venv/bin/python`) and the Codex CLI invocation shape both target macOS
as the eventual real-hardware platform. Every test in this package,
however, runs on any OS: `CodexPreflight`/`CodexCLIExecutor` use an
injected `CommandRunner`/`CodexProcessRunner` (fakes only, in tests);
`PackageInstaller` uses an injected `InstallerCommandRunner` (a fake only,
in tests); `ResearchAgentExecutor` uses a fake A2A client. No test in this
package spawns a real subprocess, contacts a real network endpoint, or
installs a real package.

## Future radar / wearable requirements (not implemented through Phase 3)

- **Radar**: still simulator and domain logic only — no real serial reader.
  When a real radar adapter is eventually built, it must use **one
  serial-port owner/broker**; two independent readers must never open the
  same serial device concurrently (see `adapters/radar.py`'s module
  docstring for how this relates to the existing
  `application/presence/serial_adapter.py`).
- **Wearable/BLE**: still fully deferred. A future implementation still
  needs, and this project invents none of: BLE Service UUID, Characteristic
  UUIDs, payload encoding, device provisioning, RSSI/proximity policy,
  reconnect behavior, replay protection beyond the question_id/expiry/
  duplicate checks already enforced at the lifecycle layer, and button
  debounce behavior.
- **Display/notification surface**: Terminal remains the only display
  surface. No osascript notifier is added or extracted.

## Known hackathon limitations

- Codex JSONL post-hoc inspection is a defense-in-depth signal, not a proof
  against a hostile/compromised model — see "Safe argv policy" above. In
  particular, the prohibited-command check in `execution/codex_cli.py`
  inspects a command *after* the CLI has already reported it (or is
  reporting it as it happens); it can stop the stream and fail the task
  before accepting further steps, but it cannot prevent the sandbox from
  having allowed that first command in the first place.
- `CodexPreflight` checks a bounded, documented set of required flags via
  substring matching on `--help` text (inspecting both stdout and stderr);
  it does not exhaustively parse every flag Codex CLI supports, and a
  `--help` output whose wording changes in a way that still supports these
  flags but under different text could false-negative (fail closed, never
  false-accept an unsupported flag).
- The Codex CLI nested JSONL event schema assumed here (top-level `type`
  values like `item.started`/`item.completed`/`turn.completed`/
  `turn.failed`/`error`, with `item.id`/`item.type`/`item.command`/
  `item.text` nested under an `item` object) is a documented, reasonable
  approximation for this MVP, not verified against every possible real
  Codex CLI output shape — every test uses a fake process runner producing
  exactly the lines the test constructs.
- The real subprocess adapters (`SubprocessCommandRunner`,
  `SubprocessCodexProcessRunner`, `SubprocessInstallerCommandRunner`) exist
  and are wired into the demo's `--executor codex` flag, but have not been
  exercised against a real Codex CLI or a real package install in this
  repository's own development or automated tests.
- The return coordinator's "current atomic step" grace period assumes a
  step is exactly one `CodingAgentExecutor.execute()` /
  `ResearchAgentExecutor.execute()` call; there is no mid-step (e.g.
  mid-Codex-JSONL-event) preemption beyond that call's own internal
  timeout/step-cap.
- No macOS hardware verification has been performed for the real radar,
  real wearable/BLE, or a real Codex CLI invocation.

## macOS hardware verification

**No macOS hardware verification has been performed for anything in this
package.** Every test runs on any OS, using only in-memory domain logic,
injected clocks, deterministic simulators, and fake process runners/clients
— there is no platform-specific code path anywhere in this package to
verify against real hardware, a real Codex CLI, or a real network endpoint
in the first place. macOS is the target platform for the eventual real
radar/wearable/OCR adapters and for a real Codex/Research rehearsal; the
tests themselves are, and will remain, portable.
